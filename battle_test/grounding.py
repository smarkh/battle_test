"""Ground drafts in the local law index.

Before drafting, each side lists the legal questions it needs answered as
statute-style search queries (research), and picks the relevant sections
from what the index returns (selection). Those sections' text goes into the
drafting prompt as the only authority the model may cite. After drafting,
every citation in the draft is checked against the index in code.
"""

import json
from dataclasses import dataclass
from typing import Protocol

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
RESULTS_PER_QUERY = 4

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


def parse_json_list(reply: str, key: str) -> list:
    """The list under `key` in a JSON reply, or [] if the reply isn't usable."""
    try:
        value = json.loads(reply).get(key, [])
    except (json.JSONDecodeError, AttributeError):
        return []
    return value if isinstance(value, list) else []


def research(ask: JsonChat, task: str) -> list[str]:
    queries = [q.strip() for q in parse_json_list(ask(task), "queries") if isinstance(q, str) and q.strip()]
    return list(dict.fromkeys(queries))


def summary_judgment_rule(index: LawSearch, state: str) -> list[LawSection]:
    return [s for s in index.lookup(SUMMARY_JUDGMENT_RULES[state]) if s.in_force][:1]


def gather_candidates(index: LawSearch, queries: list[str], state: str) -> list[LawSection]:
    """Search each query and interleave the results by rank (every query's
    best hit first), so a cut-off keeps the best of each query."""
    per_query = [index.search(q, state, limit=RESULTS_PER_QUERY) for q in queries]
    seen: dict[str, LawSection] = {}
    for rank in range(RESULTS_PER_QUERY):
        for results in per_query:
            if rank < len(results):
                seen.setdefault(results[rank].citation, results[rank])
    return list(seen.values())


def candidate_list(candidates: list[LawSection]) -> str:
    lines = []
    for i, s in enumerate(candidates, 1):
        start = " ".join(s.text.split()[:25])
        lines.append(f"[{i}] {s.citation} — {s.section_title}\n    {start}…")
    return "\n".join(lines)


def select(ask: JsonChat, task: str, candidates: list[LawSection], limit: int) -> list[LawSection]:
    """The candidates the model picked, falling back to the top-ranked ones
    if it returns nothing usable."""
    picked = []
    for n in parse_json_list(ask(task), "selected"):
        if isinstance(n, int) and 1 <= n <= len(candidates) and candidates[n - 1] not in picked:
            picked.append(candidates[n - 1])
    return (picked or candidates)[:limit]


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

