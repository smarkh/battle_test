"""Ground drafts in the local law index.

Before drafting, each side lists the legal questions it needs answered as
statute-style search queries (research), and picks the relevant sections
from what the index returns (selection). Those sections' text goes into the
drafting prompt as the only authority the model may cite. After drafting,
every citation in the draft is checked against the index in code.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace
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
    "ak": "Alaska R. Civ. P. 56",
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
    value = parse_json_object(reply).get(key, [])
    return value if isinstance(value, list) else []


def parse_json_object(reply: str) -> dict:
    """The JSON object in a reply, or {} if there isn't a usable one."""
    start, end = reply.find("{"), reply.rfind("}")
    try:
        value = json.loads(reply[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


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


# The same topics in the words a state's own law uses, where the defaults
# miss. Alaska has no counties and its trial court is the superior court, so
# the venue and jurisdiction queries found federal statutes and the limited
# District Court's rules, and the first Alaska run (2026-10-16) cited the
# District Court venue rule in a superior court complaint. Its limitation
# period says "contract actions to be brought in three years", and attorney
# fees come from Alaska R. Civ. P. 82. Each query below ranked the right
# section first in the v2026.09.1 index.
STATE_STANDARD_QUERIES = {
    "ak": [
        "contract actions brought within three years",
        "venue judicial district superior court civil rule",
        "superior court original jurisdiction civil",
        "attorney fees prevailing party civil rule",
        "prejudgment interest contract legal rate",
        "measure of damages breach of contract",
    ],
}
_ALL_STANDARD = {q for qs in (STANDARD_QUERIES, *STATE_STANDARD_QUERIES.values()) for q in qs}


def standard_queries(state: str | None = None) -> list[str]:
    return STATE_STANDARD_QUERIES.get((state or "").lower(), STANDARD_QUERIES)


def research(ask: JsonChat, task: str, state: str | None = None) -> list[str]:
    """The model's research queries, then the standard procedural ones."""
    queries = [q.strip() for q in parse_json_list(ask(task), "queries") if isinstance(q, str) and q.strip()]
    return list(dict.fromkeys(queries + standard_queries(state)))


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
    return [s for _, s in select_with_topics(ask, task_for, topics, limit)]


def select_with_topics(ask: JsonChat, task_for: Callable[[str, str], str], topics: list[Topic],
                       limit: int) -> list[tuple[str, LawSection]]:
    """Ask the model, one topic at a time, which of that topic's candidates
    states the rule for this case. `task_for(topic, candidate list)` writes
    each prompt.

    At most one pick per topic. When there are more picks than `limit`, the
    model's own topics and the standard ones take turns, so the cut never
    falls on one kind alone. Falls back to the top-ranked candidates if the
    model picks nothing at all.

    Returns (the topic each section was picked for, the section).
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
    own = [(q, picks[q]) for q, _ in topics if q in picks and q not in _ALL_STANDARD]
    standard = [(q, picks[q]) for q, _ in topics if q in picks and q in _ALL_STANDARD]
    chosen = _by_rank([own, standard])
    if not chosen:
        chosen = _by_rank([[(q, s) for s in sections] for q, sections in topics])
    return chosen[:limit]


def merge(*groups: list[LawSection], limit: int = MAX_AUTHORITIES) -> list[LawSection]:
    seen: dict[str, LawSection] = {}
    for group in groups:
        for s in group:
            seen.setdefault(s.citation, s)
    return list(seen.values())[:limit]


def excerpt(text: str, limit: int = EXCERPT_CHARS, more: str = " …[excerpt]") -> str:
    """The start of a section's text on one line, cut at a word, with `more`
    added when something was left off."""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + more
    return text


# Long sections: show the parts that match what the section is wanted for,
# not just its opening. Cal. CIV § 1950.5 is 26,182 characters, and its
# 21-day itemized statement rule and bad-faith rule start past character
# 20,000, so a checker shown the first 3,000 said the section doesn't say
# them (lease evaluation, 2026-10-16). Plan step 3b, "Passages, stage 1".
OPENING_SHARE = 4  # a quarter of a long excerpt is the section's opening
BLOCK_CHARS = 600  # long paragraphs are split into pieces about this size
MIN_PIECE_CHARS = 80  # leftover room smaller than this isn't filled
GAP = " […] "  # marks text left out between the parts shown
_EXCERPT_STOPWORDS = {
    "a", "an", "and", "any", "are", "as", "at", "be", "been", "by", "does", "for", "from",
    "has", "have", "in", "is", "its", "law", "may", "not", "of", "on", "or", "section",
    "shall", "such", "that", "the", "their", "this", "to", "under", "was", "which", "who",
    "with",
}
_SUBDIVISION = re.compile(r"\s(?=\(\w{1,4}\)\s)")  # before "(a) ", "(12) ", "(iv) "
# The summary judgment rule is supplied directly, not found by a search, so
# its excerpt is matched against the standard itself.
SUMMARY_JUDGMENT_QUERY = ("summary judgment no genuine issue triable issue material fact "
                          "moving party entitled to judgment as a matter of law")


def _terms(text: str) -> set[str]:
    """Rough word stems for matching: "deposits" and "deposit" match, as do
    "retained" and "retain". Numbers count ("21 days"), single digits don't."""
    words = re.findall(r"[a-z]+|\d+", text.lower())
    return {w.rstrip("s")[:6] for w in words
            if (w.isdigit() and len(w) >= 2) or (len(w) >= 3 and w not in _EXCERPT_STOPWORDS)}


def _split_long(paragraph: str) -> list[str]:
    """A paragraph cut into pieces of about BLOCK_CHARS, at subdivisions
    ("(a)", "(1)") where it has them, else at sentence ends."""
    if len(paragraph) <= BLOCK_CHARS:
        return [paragraph]
    pieces: list[str] = []
    for part in _parts(paragraph):
        if pieces and len(pieces[-1]) + 1 + len(part) <= BLOCK_CHARS:
            pieces[-1] += " " + part
        else:
            pieces.append(part)
    return pieces


def _parts(block: str) -> list[str]:
    parts = _SUBDIVISION.split(block)
    return parts if len(parts) > 1 else re.split(r"(?<=[.;:])\s+", block)


def _trim_around_match(block: str, room: int, weight: dict[str, float]) -> tuple[str, bool]:
    """The best-matching part of a block too long for `room`, widened with
    its neighbours while it fits. Returns (text, whether it starts where the
    block does)."""
    parts = _parts(block)
    scores = [sum(weight.get(t, 0.0) for t in _terms(p)) for p in parts]
    best = max(range(len(parts)), key=lambda i: (scores[i], -i))
    lo = hi = best
    if len(parts[best]) > room:
        return excerpt(parts[best], room, ""), best == 0
    size = len(parts[best])
    while True:
        grown = False
        for side in (hi + 1, lo - 1):
            if 0 <= side < len(parts) and size + 1 + len(parts[side]) <= room:
                size += 1 + len(parts[side])
                lo, hi = min(lo, side), max(hi, side)
                grown = True
        if not grown:
            return " ".join(parts[lo:hi + 1]), lo == 0


def _blocks(text: str) -> list[str]:
    paragraphs = [" ".join(p.split()) for p in text.splitlines() if p.strip()]
    return [piece for p in paragraphs for piece in _split_long(p)]


def relevant_excerpt(text: str, query: str | None, limit: int = EXCERPT_CHARS,
                     more: str = " …[excerpt]") -> str:
    """Up to `limit` characters of a section for a reader who needs it for
    `query` (a research topic, or the passage citing it).

    A short section, or one where nothing matches, comes back as excerpt()
    gives it: its start. A long one gives its opening (what the section is
    about), then the pieces that best match the query, in the section's
    order, with GAP where text is left out. Matching weighs words rare in
    the section over words in every paragraph of it.
    """
    if len(" ".join(text.split())) <= limit or not query:
        return excerpt(text, limit, more)
    blocks = _blocks(text)
    wanted = _terms(query)
    block_terms = [_terms(b) for b in blocks]
    df = Counter(t for terms in block_terms for t in terms)
    # A word in more than half the section's pieces says nothing about which
    # piece matters, so it counts for nothing.
    weight = {t: math.log(1 + len(blocks) / df[t]) for t in wanted if 0 < df[t] <= len(blocks) / 2}
    scores = [sum(weight.get(t, 0.0) for t in terms) for terms in block_terms]
    if len(blocks) < 2 or not any(scores[1:]):
        return excerpt(text, limit, more)

    opening = excerpt(blocks[0], limit // OPENING_SHARE, "")
    # block -> (text shown, starts where the block does, is the whole block)
    shown = {0: (opening, True, opening == blocks[0])}
    room = limit - len(opening)
    for i in sorted(range(1, len(blocks)), key=lambda i: (-scores[i], i)):
        if scores[i] <= 0 or room < MIN_PIECE_CHARS:
            break
        if len(blocks[i]) + len(GAP) <= room:
            shown[i] = (blocks[i], True, True)
        else:
            piece, at_start = _trim_around_match(blocks[i], room - len(GAP), weight)
            if not piece:
                continue
            shown[i] = (piece, at_start, False)
        room -= len(shown[i][0]) + len(GAP)

    out, prev = "", None
    for i in sorted(shown):
        piece, at_start, _ = shown[i]
        if prev is not None:
            out += " " if i == prev + 1 and shown[prev][2] and at_start else GAP
        elif not at_start:
            out += GAP.lstrip()
        out += piece
        prev = i
    if prev < len(blocks) - 1 or not shown[prev][2]:
        out += more
    return out


def format_authorities(sections: list[LawSection], queries: dict[str, str] | None = None) -> str:
    """The sections for a drafting prompt. `queries` maps a citation to what
    it's wanted for (its research topic, or the other side's passage citing
    it), so a long section shows the part that matters."""
    if not sections:
        return "(none found — use [CITATION NEEDED: …] placeholders for all legal authority)"
    queries = queries or {}
    return "\n\n".join(f"{s.citation} — {s.section_title}\n{relevant_excerpt(s.text, queries.get(s.citation))}"
                       for s in sections)


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
CITED_STATUSES = (VERIFIED, IN_FORCE_NOT_PROVIDED)  # a draft cites a real, in-force section


# The "does it say that?" check (plan step 3a): a model's verdict on whether
# a cited section's text supports the passage citing it. Existence checks
# can't catch a real statute cited for something it doesn't say: the first
# grounded Utah run cited an attorney-fee statute as the source of the
# court's jurisdiction.
SUPPORTS = "supports"
PARTLY = "partly"
DOES_NOT_SUPPORT = "does_not_support"
DOUBTED = (PARTLY, DOES_NOT_SUPPORT)
SUPPORT_LABELS = {
    PARTLY: "may only partly support what it's cited for",
    DOES_NOT_SUPPORT: "may not support what it's cited for",
}
# Only "does not support" is marked in the draft. "Partly" is listed in the
# citation check and left unmarked: on qwen2.5:14b, once the fit check had
# kept the wrong law out, nearly every "partly" was a quibble about a good
# citation (evaluation of 2026-10-12).
_SUPPORT_MARKS = {DOES_NOT_SUPPORT: "[⚠ MAY NOT SUPPORT THIS]"}
# How much of the draft around a citation, and how much of the section, the
# checker reads (for a long section, the parts matching the passage: see
# relevant_excerpt). The drafting model saw only EXCERPT_CHARS of it.
PASSAGE_CHARS = 600
SUPPORT_EXCERPT_CHARS = 3000
# A citation on a line of its own (a heading, a list of authorities) is cited
# for nothing in particular, so there's nothing to judge.
MIN_PASSAGE_WORDS = 4
# A pasted complaint can cite far more than a draft does.
MAX_SUPPORT_CHECKS = 40


@dataclass(frozen=True)
class CitationCheck:
    text: str  # as written in the draft
    status: str
    section: LawSection | None  # the matched section, if exactly one
    support: str | None = None  # SUPPORTS, PARTLY or DOES_NOT_SUPPORT. None = not judged.
    support_reason: str = ""  # the checker's one line of reasoning


# (passage citing the section, the section) -> (verdict, reason), or None if
# the model's reply wasn't usable.
Judge = Callable[[str, LawSection], tuple[str, str] | None]


def parse_verdict(reply: str, allowed: tuple[str, ...] = (SUPPORTS, PARTLY, DOES_NOT_SUPPORT)
                  ) -> tuple[str, str] | None:
    data = parse_json_object(reply)
    verdict, reason = data.get("verdict"), data.get("reason")
    if isinstance(verdict, str):
        verdict = verdict.strip().lower().replace(" ", "_")
    if verdict not in allowed:
        return None
    return verdict, " ".join(reason.split())[:300] if isinstance(reason, str) else ""


# The fit check: before drafting, a model's verdict on whether each selected
# section applies to this kind of case at all. The 7B and 14B cite whatever
# they're given (sale-of-goods sections for a roofing contract, a theft
# statute for jurisdiction), so a section that doesn't fit is kept out of
# the drafting prompt instead of being caught afterwards.
APPLIES = "applies"
UNSURE = "unsure"
DOES_NOT_APPLY = "does_not_apply"
FIT_VERDICTS = (APPLIES, UNSURE, DOES_NOT_APPLY)


@dataclass(frozen=True)
class FitCheck:
    section: LawSection
    verdict: str | None  # APPLIES, UNSURE or DOES_NOT_APPLY. None = the reply wasn't usable.
    reason: str = ""

    @property
    def kept(self) -> bool:
        """Whether the section goes to the drafting model. An unusable reply
        keeps it: a checker that fails shouldn't empty every prompt."""
        return self.verdict in (APPLIES, None)


def fit_filter(judge: Callable[[LawSection], tuple[str, str] | None],
               sections: list[LawSection]) -> tuple[list[LawSection], list[FitCheck]]:
    """Ask `judge` about each section. Returns (the sections to draft with,
    every verdict). Only "applies" is kept: when the model isn't sure a
    section belongs, it's left out of the draft."""
    checks = []
    for s in sections:
        verdict, reason = judge(s) or (None, "")
        checks.append(FitCheck(s, verdict, reason))
    return [c.section for c in checks if c.kept], checks


def passage_around(text: str, start: int, end: int) -> str:
    """The paragraph holding the citation at text[start:end], cut to about
    PASSAGE_CHARS around it. Paragraphs, not sentences: the periods in
    "Utah Code Ann." and "R. Civ. P." make sentences hard to find."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    lo, hi = max(line_start, start - PASSAGE_CHARS // 2), min(line_end, end + PASSAGE_CHARS // 2)
    before, after = text[lo:start], text[end:hi]
    if lo > line_start:
        before = "…" + before.partition(" ")[2]
    if hi < line_end:
        after = after.rpartition(" ")[0] + "…"
    return (before + text[start:end] + after).strip()


@dataclass(frozen=True)
class CheckedText:
    text: str  # the draft, with problem citations marked inline
    checks: tuple[CitationCheck, ...]
    unchecked: tuple[str, ...]  # citation-like lines the checker couldn't parse
    # citation -> the draft's passages citing that section, for matching the
    # section's text to what it's cited for. Not kept in the case record.
    passages: dict[str, str] = field(default_factory=dict)


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
    return _SUPPORT_MARKS.get(check.support)


def _judge_support(text: str, refs: list[CitationRef], checks: list[CitationCheck],
                   judge: Judge) -> list[CitationCheck]:
    """Ask `judge` about each in-force citation and the passage citing it."""
    verdicts: dict[tuple[str, str], tuple[str, str] | None] = {}
    judged = []
    for ref, check in zip(refs, checks):
        if check.section is not None and check.status in CITED_STATUSES:
            passage = passage_around(text, ref.start, ref.end)
            key = (check.section.citation, passage)
            if key not in verdicts and len(verdicts) < MAX_SUPPORT_CHECKS \
                    and len(passage.replace(text[ref.start:ref.end], "").split()) >= MIN_PASSAGE_WORDS:
                verdicts[key] = judge(passage, check.section)
            if verdicts.get(key):
                check = replace(check, support=verdicts[key][0], support_reason=verdicts[key][1])
        judged.append(check)
    return judged


def check_citations(text: str, index: LawSearch, state: str, state_name: str,
                    provided: set[str] | None, judge: Judge | None = None) -> CheckedText:
    """Check every parseable citation in `text` against the index.

    `provided` is the set of citations whose text the model was given, or
    None for user-written text. With a `judge`, each citation that's in
    force is also checked against the passage citing it.
    """
    text = unwrap_citation_placeholders(text)
    refs = find_citation_refs(text)
    checks = [_classify(ref, index.resolve(ref, state), state, provided) for ref in refs]
    if judge:
        checks = _judge_support(text, refs, checks, judge)
    unchecked = find_unverified_citations(text, skip=refs)

    # Insert marks from the end so earlier offsets stay valid.
    marked = text
    for ref, check in sorted(zip(refs, checks), key=lambda rc: rc[0].start, reverse=True):
        mark = _inline_mark(check, state_name)
        if mark:
            marked = f"{marked[:ref.end]} {mark}{marked[ref.end:]}"
    passages: dict[str, list[str]] = {}
    for ref, check in zip(refs, checks):
        if check.section is not None and check.status in CITED_STATUSES:
            found = passages.setdefault(check.section.citation, [])
            passage = passage_around(text, ref.start, ref.end)
            if passage not in found:
                found.append(passage)
    return CheckedText(marked, tuple(checks), tuple(unchecked),
                       {c: "\n".join(ps) for c, ps in passages.items()})


def cited_sections(checked: CheckedText) -> list[LawSection]:
    """In-force sections a draft cites, so the other side can see their text."""
    return merge([c.section for c in checked.checks
                  if c.section is not None and c.status in CITED_STATUSES],
                 limit=MAX_AUTHORITIES)

