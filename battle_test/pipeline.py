"""Run the plaintiff-vs-defendant document flow from plans/plan.md."""

from dataclasses import dataclass, field
from typing import Callable, Protocol

from battle_test import grounding, prompts
from battle_test.citations import strip_echoed_markers
from battle_test.config import Config
from battle_test.grounding import CitationCheck, CheckedText, FitCheck, LawSearch
from battle_test.law_index import LawSection
from battle_test.models import Usage, add_usage

# Authorities in one drafting prompt when a side also gets the sections the
# other side cited (so it can check what they actually say).
MAX_COMBINED_AUTHORITIES = 12
MAX_OTHER_SIDE_CITED = 4


class ChatClient(Protocol):
    def chat(
        self,
        model: str,
        system: str,
        user: str,
        on_token: Callable[[str], None] | None = None,
        json_mode: bool = False,
        on_usage: Callable[[Usage], None] | None = None,
        temperature: float | None = None,  # None = the client's own
    ) -> str: ...


class Law(LawSearch, Protocol):
    def meta(self) -> dict[str, str]: ...


@dataclass(frozen=True)
class Document:
    title: str
    role: str  # "plaintiff" or "defendant"
    text: str  # with problem citations marked inline
    generated: bool  # False only for a complaint the user supplied
    checks: tuple[CitationCheck, ...] = ()
    unchecked: tuple[str, ...] = ()  # citation-like lines the checker couldn't parse
    authorities: tuple[LawSection, ...] = ()  # sections quoted in the drafting prompt


@dataclass
class CaseRun:
    state_code: str
    rounds: int
    plaintiff_model: str
    defendant_model: str
    law_meta: dict[str, str] = field(default_factory=dict)
    research: dict[str, list[str]] = field(default_factory=dict)  # role -> search queries
    candidates: dict[str, list[str]] = field(default_factory=dict)  # role -> citations search found
    documents: list[Document] = field(default_factory=list)
    # model -> {"calls", "input_tokens", "output_tokens"}, as the provider
    # reported them. Empty when the client reports nothing (the demo model).
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    cost_usd: float | None = None  # estimated from the config's prices. None = no price known.
    checker_model: str = ""  # "" = the "does it say that?" check didn't run
    # role -> the checker's verdict on each section selected for that side,
    # before drafting. Empty when the fit check didn't run.
    fit: dict[str, list[FitCheck]] = field(default_factory=dict)

    @property
    def state(self) -> str:
        return prompts.SUPPORTED_STATES[self.state_code]


def run_case(
    cfg: Config,
    client: ChatClient,
    law: Law,
    state_code: str,
    *,
    facts: str | None = None,
    complaint: str | None = None,
    rounds: int | None = None,
    on_stage: Callable[[str, str], None] | None = None,
    on_token: Callable[[str], None] | None = None,
) -> CaseRun:
    """Produce complaint -> motion -> opposition (-> reply), grounded in `law`.

    Pass exactly one of `facts` (the complaint is generated from them) or
    `complaint` (a user-written complaint, used as-is but still checked).
    """
    if state_code not in prompts.SUPPORTED_STATES:
        supported = ", ".join(prompts.SUPPORTED_STATES)
        raise ValueError(f"Unsupported state {state_code!r}; v1 supports {supported}")
    if (facts is None) == (complaint is None):
        raise ValueError("Provide exactly one of facts or complaint")
    rounds = cfg.rounds if rounds is None else rounds
    if rounds not in (1, 2):
        raise ValueError(f"rounds must be 1 or 2, got {rounds}")

    run = CaseRun(state_code, rounds, cfg.plaintiff_model, cfg.defendant_model, law.meta(),
                  checker_model=cfg.checker_model)
    state, sc = run.state, state_code.lower()
    roles = {
        "plaintiff": (cfg.plaintiff_model, prompts.plaintiff_system(state)),
        "defendant": (cfg.defendant_model, prompts.defendant_system(state)),
    }

    def stage(title: str, role: str) -> None:
        if on_stage:
            on_stage(title, role)

    def record(used: Usage) -> None:
        add_usage(run.usage, used)

    def asker(role: str) -> Callable[[str], str]:
        model, system = roles[role]
        return lambda task: client.chat(model, system, task, json_mode=True, on_usage=record)

    def ask_checker(task: str) -> str:
        return client.chat(cfg.checker_model, prompts.SUPPORT_SYSTEM, task, json_mode=True, on_usage=record,
                           temperature=cfg.checker_temperature)

    # The fit check's verdicts so far, by citation, from either side. A
    # section one side's check left out is left out for the other side too.
    set_aside: dict[str, FitCheck] = {}

    def gather_authorities(role: str, research_task: str, materials_title: str,
                           materials: str) -> list[LawSection]:
        stage("Legal research", role)
        queries = grounding.research(asker(role), research_task, sc)
        run.research[role] = queries
        topics = grounding.gather_by_topic(law, queries, sc)
        run.candidates[role] = [s.citation for s in grounding.all_candidates(topics)]
        if not run.candidates[role]:
            return []
        stage("Selecting authorities", role)
        picked = grounding.select_with_topics(
            asker(role),
            lambda topic, candidates: prompts.select_task(role, materials_title, materials, topic, candidates),
            topics, grounding.MAX_AUTHORITIES)
        selected = [s for _, s in picked]
        if not (cfg.checker_model and cfg.fit_check):
            return selected
        stage("Checking the authorities fit the case", role)
        topic_of = {s.citation: topic for topic, s in picked}

        def fits(section: LawSection) -> tuple[str, str] | None:
            earlier = set_aside.get(section.citation)
            if earlier:
                return earlier.verdict, earlier.reason
            task = prompts.fit_task(state, materials_title, materials, section.citation, section.section_title,
                                    grounding.excerpt(section.text, grounding.SUPPORT_EXCERPT_CHARS),
                                    topic_of.get(section.citation))
            return grounding.parse_verdict(ask_checker(task), grounding.FIT_VERDICTS)

        selected, run.fit[role] = grounding.fit_filter(fits, selected)
        set_aside.update({c.section.citation: c for c in run.fit[role] if not c.kept})
        return selected

    def judge(passage: str, section: LawSection) -> tuple[str, str] | None:
        task = prompts.support_task(section.citation, section.section_title,
                                    grounding.excerpt(section.text, grounding.SUPPORT_EXCERPT_CHARS), passage)
        return grounding.parse_verdict(ask_checker(task))

    def check(title: str, role: str, text: str, provided: list[LawSection] | None) -> CheckedText:
        cites = None if provided is None else {s.citation for s in provided}
        if not cfg.checker_model:
            return grounding.check_citations(text, law, sc, state, cites)
        stage(f"Checking citations: {title}", role)
        return grounding.check_citations(text, law, sc, state, cites, judge)

    def draft(title: str, role: str, task: str, authorities: list[LawSection]) -> CheckedText:
        stage(title, role)
        model, system = roles[role]
        raw = client.chat(model, system, task, on_token=on_token, on_usage=record)
        checked = check(title, role, strip_echoed_markers(raw).strip(), authorities)
        run.documents.append(Document(title, role, checked.text, True, checked.checks,
                                      checked.unchecked, tuple(authorities)))
        return checked

    # Plaintiff: research from the facts (or the user's complaint), then draft.
    materials = ("Case information", facts) if facts is not None else ("Complaint", complaint)
    p_auth = gather_authorities(
        "plaintiff", prompts.plaintiff_research_task(state, *materials), *materials
    )
    p_text = grounding.format_authorities(p_auth)
    if facts is not None:
        court = prompts.TRIAL_COURTS[state_code]
        complaint_doc = draft("Complaint", "plaintiff", prompts.complaint_task(court, facts, p_text), p_auth)
    else:
        complaint_doc = check("Complaint", "plaintiff", complaint.strip(), None)
        run.documents.append(Document("Complaint", "plaintiff", complaint_doc.text, False,
                                      complaint_doc.checks, complaint_doc.unchecked))
    complaint = complaint_doc.text
    sj_rule = grounding.summary_judgment_rule(law, sc)
    motion_auth = grounding.merge(sj_rule, p_auth, limit=MAX_COMBINED_AUTHORITIES)
    motion_doc = draft("Motion for Summary Judgment", "plaintiff",
                       prompts.motion_task(state, complaint, grounding.format_authorities(motion_auth)),
                       motion_auth)

    # Defendant: its own research, plus the text of what the plaintiff cited.
    d_selected = gather_authorities(
        "defendant", prompts.defendant_research_task(state, complaint, motion_doc.text),
        "Complaint", complaint,
    )
    d_auth = grounding.merge(sj_rule, grounding.cited_sections(motion_doc)[:MAX_OTHER_SIDE_CITED],
                             d_selected, limit=MAX_COMBINED_AUTHORITIES)
    opposition_doc = draft(
        "Opposition to Motion for Summary Judgment", "defendant",
        prompts.opposition_task(state, complaint, motion_doc.text, grounding.format_authorities(d_auth)),
        d_auth,
    )

    if rounds == 2:
        # The motion is already written, but a section the defendant's check
        # set aside after the plaintiff's kept it stays out of the reply.
        p_auth = [s for s in p_auth if s.citation not in set_aside]
        r_auth = grounding.merge(sj_rule, grounding.cited_sections(opposition_doc)[:MAX_OTHER_SIDE_CITED],
                                 p_auth, limit=MAX_COMBINED_AUTHORITIES)
        draft(
            "Reply in Support of Motion for Summary Judgment", "plaintiff",
            prompts.reply_task(state, complaint, motion_doc.text, opposition_doc.text,
                               grounding.format_authorities(r_auth)),
            r_auth,
        )

    run.cost_usd = cfg.cost(run.usage)
    return run
