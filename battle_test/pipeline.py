"""Run the plaintiff-vs-defendant document flow from plans/plan.md."""

from dataclasses import dataclass, field
from typing import Callable, Protocol

from battle_test import grounding, prompts
from battle_test.citations import strip_echoed_markers
from battle_test.config import Config
from battle_test.grounding import CitationCheck, CheckedText, LawSearch
from battle_test.law_index import LawSection

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
    documents: list[Document] = field(default_factory=list)

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

    run = CaseRun(state_code, rounds, cfg.plaintiff_model, cfg.defendant_model, law.meta())
    state, sc = run.state, state_code.lower()
    roles = {
        "plaintiff": (cfg.plaintiff_model, prompts.plaintiff_system(state)),
        "defendant": (cfg.defendant_model, prompts.defendant_system(state)),
    }

    def stage(title: str, role: str) -> None:
        if on_stage:
            on_stage(title, role)

    def asker(role: str) -> Callable[[str], str]:
        model, system = roles[role]
        return lambda task: client.chat(model, system, task, json_mode=True)

    def gather_authorities(role: str, research_task: str, materials_title: str,
                           materials: str) -> list[LawSection]:
        stage("Legal research", role)
        queries = grounding.research(asker(role), research_task)
        run.research[role] = queries
        candidates = grounding.gather_candidates(law, queries, sc)
        if not candidates:
            return []
        stage("Selecting authorities", role)
        task = prompts.select_task(role, materials_title, materials,
                                   grounding.candidate_list(candidates), grounding.MAX_AUTHORITIES)
        return grounding.select(asker(role), task, candidates, grounding.MAX_AUTHORITIES)

    def check(text: str, provided: list[LawSection] | None) -> CheckedText:
        cites = None if provided is None else {s.citation for s in provided}
        return grounding.check_citations(text, law, sc, state, cites)

    def draft(title: str, role: str, task: str, authorities: list[LawSection]) -> CheckedText:
        stage(title, role)
        model, system = roles[role]
        raw = client.chat(model, system, task, on_token=on_token)
        checked = check(strip_echoed_markers(raw).strip(), authorities)
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
        complaint_doc = check(complaint.strip(), None)
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
        r_auth = grounding.merge(sj_rule, grounding.cited_sections(opposition_doc)[:MAX_OTHER_SIDE_CITED],
                                 p_auth, limit=MAX_COMBINED_AUTHORITIES)
        draft(
            "Reply in Support of Motion for Summary Judgment", "plaintiff",
            prompts.reply_task(state, complaint, motion_doc.text, opposition_doc.text,
                               grounding.format_authorities(r_auth)),
            r_auth,
        )

    return run
