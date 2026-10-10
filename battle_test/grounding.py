"""Ground drafts in the local law index.

Before drafting, each side lists the legal questions it needs answered as
statute-style search queries (research), and picks the relevant sections
from what the index returns (selection). Those sections' text goes into the
drafting prompt as the only authority the model may cite. After drafting,
every citation in the draft is checked against the index in code.
"""

import json
from dataclasses import dataclass
from itertools import zip_longest
from typing import Callable, Protocol

from battle_test.citations import (
    CitationRef,
    code_name,
    find_citation_refs,
    find_unverified_citations,
    unwrap_citation_placeholders,
)
from battle_test.law_index import LawSection

# How many sections go into a drafting prompt, and how much of each. Sized so
# the reply (three prior documents + authorities) fits in num_ctx 12288.
MAX_AUTHORITIES = 10
EXCERPT_CHARS = 700
# 8, not 4: in the baseline evaluation, even good topic queries missed ~40%
# of the expected authorities at 4 results each.
RESULTS_PER_QUERY = 8
# How much of each candidate the model sees when selecting. Benchmarked on
# the three sample cases, 2026-10-09: 40 words found more expected
# authorities than 25 or 80 on both qwen2.5:7b and 14b.
CANDIDATE_WORDS = 40

# Every summary judgment brief needs the state's own summary judgment rule.
# Looked up directly, not left to search and selection: the first grounded
# Utah run never gave the plaintiff Utah R. Civ. P. 56.
SUMMARY_JUDGMENT_RULES = {
    "ut": "Utah R. Civ. P. 56",
    "ca": "Cal. CCP § 437c",
    "tx": "Tex. R. Civ. P. 166a",
}


class LawSearch(Protocol):
    def search(self, text: str, state: str, *, limit: int = 10) -> list[LawSection]: ...
    def resolve(self, ref: CitationRef, state: str) -> list[LawSection]: ...
    def lookup(self, citation: str) -> list[LawSection]: ...


class JsonChat(Protocol):
    def __call__(self, task: str) -> str: ...


# One research query and the sections the search found for it, best first.
Topic = tuple[str, list[LawSection]]


def parse_json_list(reply: str, key: str) -> list:
    """The list under `key` in a JSON reply, or [] if the reply isn't usable.

    Ollama's JSON mode returns bare JSON. Hosted models are only asked for
    it, and often wrap it in a code fence or a sentence, so anything outside
    the outermost braces is ignored.
    """
    start, end = reply.find("{"), reply.rfind("}")
    try:
        value = json.loads(reply[start:end + 1]).get(key, [])
    except (json.JSONDecodeError, AttributeError):
        return []
    return value if isinstance(value, list) else []


# Procedural topics every civil contract suit needs, whatever the facts, in
# the wording statutes use. Searched alongside the model's own queries: the
# 7B model's topic queries ("venue proper", "jurisdiction exists") are too
# generic for keyword search. In the offline replay of the evaluation cases,
# adding these took search from ~1 to 8 of 22 expected authorities (the
# summary judgment rules, supplied directly, excluded). Case-specific law
# (licensing, consumer protection, construction defects) still has to come
# from the model's research.
STANDARD_QUERIES = [
    "limitation of actions contract obligation instrument in writing",
    "venue county where action brought",
    "original jurisdiction district court civil",
    "attorney fees breach of contract",
    "prejudgment interest contract legal rate",
    "measure of damages breach of contract",
]


def research(ask: JsonChat, task: str) -> list[str]:
    """The model's research queries, then the standard procedural ones."""
    queries = [q.strip() for q in parse_json_list(ask(task), "queries") if isinstance(q, str) and q.strip()]
    return list(dict.fromkeys(queries + STANDARD_QUERIES))


def summary_judgment_rule(index: LawSearch, state: str) -> list[LawSection]:
    return [s for s in index.lookup(SUMMARY_JUDGMENT_RULES[state]) if s.in_force][:1]


def gather_by_topic(index: LawSearch, queries: list[str], state: str) -> list[Topic]:
    """Search each query. Returns (query, sections found) for each, best first.

    A section found by several queries stays only with the one that ranked
    it highest, so the model is asked about it once.

    An index that can also search by meaning (semantic.SemanticLaw) is asked
    both ways, and each query's two result lists alternate: keyword's best,
    semantic's best, keyword's second, and so on.
    """
    per_query = [index.search(q, state, limit=RESULTS_PER_QUERY) for q in queries]
    by_meaning = getattr(index, "semantic_search", None)
    if by_meaning:
        semantic = by_meaning(queries, state, limit=RESULTS_PER_QUERY)
        per_query = [[s for pair in zip_longest(keyword, meaning) for s in pair if s is not None]
                     for keyword, meaning in zip(per_query, semantic)]
    owner: dict[str, int] = {}
    for rank in range(max(map(len, per_query), default=0)):
        for n, results in enumerate(per_query):
            if rank < len(results):
                owner.setdefault(results[rank].citation, n)
    return [(query, list({s.citation: s for s in results if owner[s.citation] == n}.values()))
            for n, (query, results) in enumerate(zip(queries, per_query))]


def _by_rank(lists: list[list[LawSection]]) -> list[LawSection]:
    """Every list's first section, then every list's second, and so on."""
    return [s for row in zip_longest(*lists) for s in row if s is not None]


def all_candidates(topics: list[Topic]) -> list[LawSection]:
    """Every section the search found, each topic's best hit first."""
    return _by_rank([sections for _, sections in topics])


def gather_candidates(index: LawSearch, queries: list[str], state: str) -> list[LawSection]:
    return all_candidates(gather_by_topic(index, queries, state))


def candidate_list(candidates: list[LawSection]) -> str:
    lines = []
    for i, s in enumerate(candidates, 1):
        start = " ".join(s.text.split()[:CANDIDATE_WORDS])
        lines.append(f"[{i}] {s.citation} — {s.section_title}\n    {start}…")
    return "\n".join(lines)


def select(ask: JsonChat, task_for: Callable[[str, str], str], topics: list[Topic],
           limit: int) -> list[LawSection]:
    """Ask the model, one topic at a time, which of that topic's candidates
    states the rule for this case. `task_for(topic, candidate list)` writes
    each prompt.

    At most one pick per topic. When there are more picks than `limit`, the
    model's own topics and the standard ones take turns, so the cut never
    falls on one kind alone. Falls back to the top-ranked candidates if the
    model picks nothing at all.
    """
    picks: dict[str, LawSection] = {}
    for query, sections in topics:
        if not sections:
            continue
        for n in parse_json_list(ask(task_for(query, candidate_list(sections))), "selected"):
            # bool is an int in Python, and `true` isn't a pick
            if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(sections):
                picks[query] = sections[n - 1]
                break
    own = [picks[q] for q, _ in topics if q in picks and q not in STANDARD_QUERIES]
    standard = [picks[q] for q, _ in topics if q in picks and q in STANDARD_QUERIES]
    return (_by_rank([own, standard]) or all_candidates(topics))[:limit]


def merge(*groups: list[LawSection], limit: int = MAX_AUTHORITIES) -> list[LawSection]:
    seen: dict[str, LawSection] = {}
    for group in groups:
        for s in group:
            seen.setdefault(s.citation, s)
    return list(seen.values())[:limit]


def format_authorities(sections: list[LawSection]) -> str:
    if not sections:
        return "(none found — use [CITATION NEEDED: …] placeholders for all legal authority)"
    blocks = []
    for s in sections:
        text = " ".join(s.text.split())
        if len(text) > EXCERPT_CHARS:
            text = text[:EXCERPT_CHARS].rsplit(" ", 1)[0] + " …[excerpt]"
        blocks.append(f"{s.citation} — {s.section_title}\n{text}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Checking citations in a finished draft
# ---------------------------------------------------------------------------

VERIFIED = "verified"  # in force, and its text was given to the model
IN_FORCE_NOT_PROVIDED = "in_force_not_provided"  # real, but the model never saw its text
NOT_IN_FORCE = "not_in_force"
NOT_FOUND = "not_found"
WRONG_JURISDICTION = "wrong_jurisdiction"
AMBIGUOUS = "ambiguous"

PROBLEM_STATUSES = {NOT_IN_FORCE, NOT_FOUND, WRONG_JURISDICTION}


@dataclass(frozen=True)
class CitationCheck:
    text: str  # as written in the draft
    status: str
    section: LawSection | None  # the matched section, if exactly one


@dataclass(frozen=True)
class CheckedText:
    text: str  # the draft, with problem citations marked inline
    checks: tuple[CitationCheck, ...]
    unchecked: tuple[str, ...]  # citation-like lines the checker couldn't parse


def _classify(ref: CitationRef, hits: list[LawSection], state: str,
              provided: set[str] | None) -> CitationCheck:
    jurisdiction = code_name(ref.prefix).jurisdiction
    if not hits and jurisdiction not in (None, state, "federal"):
        return CitationCheck(ref.text, WRONG_JURISDICTION, None)
    if not hits:
        return CitationCheck(ref.text, NOT_FOUND, None)
    in_force = [h for h in hits if h.in_force]
    if not in_force:
        return CitationCheck(ref.text, NOT_IN_FORCE, hits[0])
    if len({h.citation for h in in_force}) > 1:
        return CitationCheck(ref.text, AMBIGUOUS, None)
    section = in_force[0]
    # User-written text has no provided list: in force is all we can say.
    if provided is None or section.citation in provided:
        return CitationCheck(ref.text, VERIFIED, section)
    return CitationCheck(ref.text, IN_FORCE_NOT_PROVIDED, section)


def _inline_mark(check: CitationCheck, state_name: str) -> str | None:
    if check.status == NOT_FOUND:
        return "[⚠ NOT FOUND: no such section in the law index]"
    if check.status == NOT_IN_FORCE:
        return f"[⚠ NOT IN FORCE: {check.section.act_status.replace('_', ' ')}]"
    if check.status == WRONG_JURISDICTION:
        return f"[⚠ NOT {state_name.upper()} OR FEDERAL LAW]"
    return None


def check_citations(text: str, index: LawSearch, state: str, state_name: str,
                    provided: set[str] | None) -> CheckedText:
    """Check every parseable citation in `text` against the index.

    `provided` is the set of citations whose text the model was given, or
    None for user-written text.
    """
    text = unwrap_citation_placeholders(text)
    refs = find_citation_refs(text)
    checks = [_classify(ref, index.resolve(ref, state), state, provided) for ref in refs]
    unchecked = find_unverified_citations(text, skip=refs)

    # Insert marks from the end so earlier offsets stay valid.
    marked = text
    for ref, check in sorted(zip(refs, checks), key=lambda rc: rc[0].start, reverse=True):
        mark = _inline_mark(check, state_name)
        if mark:
            marked = f"{marked[:ref.end]} {mark}{marked[ref.end:]}"
    return CheckedText(marked, tuple(checks), tuple(unchecked))


def cited_sections(checked: CheckedText) -> list[LawSection]:
    """In-force sections a draft cites, so the other side can see their text."""
    return merge([c.section for c in checked.checks
                  if c.section is not None and c.status in (VERIFIED, IN_FORCE_NOT_PROVIDED)],
                 limit=MAX_AUTHORITIES)

