"""Find, parse and normalise legal citations in model output.

Pure text handling, no database: law_index.py resolves the parsed citations
against the local law index, and grounding.py decides what to do with them.
"""

import re
from dataclasses import dataclass

PLACEHOLDER = re.compile(r"\[(?:CITATION|FACT) NEEDED:[^\]]*\]")

# The "=== TITLE ===" markers prompts.wrap() puts around input documents.
# Models sometimes echo them at the end of their own draft.
_ECHOED_MARKER = re.compile(r"^[ \t]*=== [^\n]* ===[ \t]*$\n?", re.MULTILINE)


def strip_echoed_markers(text: str) -> str:
    return _ECHOED_MARKER.sub("", text).strip()


# ---------------------------------------------------------------------------
# Finding citations the checker can parse
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitationRef:
    start: int
    end: int
    text: str  # exactly as written in the draft
    prefix: str  # the code/rules part, e.g. "Utah Code Ann." ("" if bare "§ 12")
    section: str  # normalised section/rule number, e.g. "78b-2-309"
    bare_ok: bool  # may be resolved by section number alone (bare "§" form)


_SEC = r"\d[\w.\-:]*(?:\(\w+\))*"
_WORD = r"[A-Z][\w.'&\-]*,?"
_ART = r"art\.\s+[IVXLC\d]+[A-Z]?,?"
_PREFIX_RUN = (
    rf"(?:\d{{1,2}}\s+U\.?\s?S\.?\s?C\.?(?:\s?A\.?)?"
    rf"|{_WORD}(?:\s+(?:{_WORD}|of|and|&|{_ART}))*)"
)
_RULE_BODY = r"(?:Civil|Appellate|Criminal)\s+Procedure|Evidence|Professional\s+Conduct"
_JURIS_WORD = r"Federal|Utah|Texas|California"

# (pattern, bare_ok). Order doesn't matter; overlapping matches keep the longest.
_PATTERNS = [
    # "Utah Code Ann. § 78B-2-309", "42 U.S.C. § 1983", bare "§ 437c"
    (re.compile(rf"(?:(?P<prefix>{_PREFIX_RUN})\s*)?§§?\s*(?P<sec>{_SEC})"), True),
    # "Civil Code section 1542": needs a prefix, or "section 4 of the contract" would count
    (re.compile(rf"(?P<prefix>{_PREFIX_RUN})\s+[Ss]ections?\s+(?P<sec>{_SEC})"), False),
    # "Utah R. Civ. P. 56(a)", "Cal. R. Ct. 3.1350", "Utah R. Bus. & Ch. Ct. P. 1"
    (re.compile(rf"(?P<prefix>\b(?:Fed|Utah|Ut|Cal|Tex)\.?\s+R\.(?:\s+(?:[A-Z][\w.\-]*|&))*?)\s+(?P<sec>{_SEC})"), False),
    # "Utah Rule(s) of Civil Procedure 56"
    (re.compile(rf"\b(?P<prefix>(?:{_JURIS_WORD})\s+Rules?\s+of\s+(?:{_RULE_BODY}))\s+(?:[Rr]ule\s+)?(?P<sec>{_SEC})"), False),
    # "Rule 56 of the Utah Rules of Civil Procedure"
    (re.compile(rf"\b[Rr]ule\s+(?P<sec>{_SEC})\s+of\s+the\s+(?P<prefix>(?:{_JURIS_WORD})\s+Rules\s+of\s+(?:{_RULE_BODY}))"), False),
    # "URCP 56", "FRCP 56(a)"
    (re.compile(rf"\b(?P<prefix>URCP|FRCP|TRCP|URE|FRE|TRE)\s+(?:[Rr]ule\s+)?(?P<sec>{_SEC})"), False),
    # "Cal. Rules of Court, rule 3.1350"
    (re.compile(rf"\b(?P<prefix>Cal(?:\.|ifornia)\s+Rules\s+of\s+Court),?\s+[Rr]ule\s+(?P<sec>{_SEC})"), False),
]


# The statute patterns (§ and "section") can start a list of sections:
# "Utah Code § 78B-6-2705, 78B-5-826, and 70A-2-712", "§§ 1983 and 1988".
_LIST_PATTERNS = {0, 1}
_LIST_NEXT = re.compile(rf"(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)(?P<sec>{_SEC})")


def _section_shape(section: str) -> str:
    s = section.rstrip(".,;:")
    return "-" if "-" in s else "." if "." in s else "plain"


def _list_continuation(text: str, masked: str, pos: int, prefix: str, first_sec: str,
                       bare_ok: bool) -> list[CitationRef]:
    """Further sections after a statute citation, each under the same code.

    A number only counts if it's shaped like the first section (Utah's
    "78B-5-826" hyphens, Texas's "16.004" dots, or plain digits), so
    "§ 78B-2-309, 2026" or "§ 12, 3 days later" don't pick up stray numbers.
    """
    shape = _section_shape(first_sec)
    refs = []
    while m := _LIST_NEXT.match(masked, pos):
        raw = m.group("sec").rstrip(".,;:")
        if _section_shape(raw) != shape or (shape == "plain" and not re.fullmatch(r"\d+[a-z]?", raw)):
            break
        start = m.start("sec")
        end = start + len(raw)
        shown = f"{prefix} § {text[start:end]}".strip()
        refs.append(CitationRef(start, end, shown, prefix, normalize_section(raw), bare_ok))
        pos = end
    return refs


_JURISDICTION_START = re.compile(
    r"\b(?:Utah|Ut\.|U\.\s?C\.\s?A|Cal(?:\.|ifornia)|Tex(?:\.|as)|Fed(?:\.|eral)|\d{1,2}\s+U\.?\s?S\.?\s?C)"
)


def normalize_section(section: str) -> str:
    """'78B-2-309(1)(b).' -> '78b-2-309'; '56(a)' -> '56'."""
    s = re.sub(r"\(.*$", "", section.strip().lower())
    return s.rstrip(".,;:")


_SEPARATORS = re.compile(r"[\s,;&]+|\band\b|\bsee\b|\bcf\.", re.IGNORECASE)


def unwrap_citation_placeholders(text: str) -> str:
    """Turn "[CITATION NEEDED: Utah Code § 70A-2-715]" into the bare citation.

    Models sometimes wrap a real citation in a placeholder, which would hide
    it from the checker. Only placeholders holding nothing but citations are
    unwrapped. "[CITATION NEEDED: a statute like § 12]" stays a placeholder.
    """
    def unwrap(m: re.Match) -> str:
        body = m.group()[len("[CITATION NEEDED:"):-1].strip()
        refs = find_citation_refs(body)
        leftover = body
        for ref in reversed(refs):
            leftover = leftover[:ref.start] + leftover[ref.end:]
        if refs and not _SEPARATORS.sub("", leftover).strip(".()"):
            return body
        return m.group()

    return re.sub(r"\[CITATION NEEDED:[^\]]*\]", unwrap, text)


def _mask_placeholders(text: str) -> str:
    # Same length and same line breaks, so offsets and lines still line up
    # with the original text.
    return PLACEHOLDER.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def find_citation_refs(text: str) -> list[CitationRef]:
    """Every statute/rule/constitution citation the checker can parse."""
    masked = _mask_placeholders(text)
    found: list[CitationRef] = []
    for i, (pattern, bare_ok) in enumerate(_PATTERNS):
        for m in pattern.finditer(masked):
            prefix = (m.group("prefix") or "").strip()
            start = m.start()
            # Drop prose caught in the prefix: "See U.C.A. § 1" -> "U.C.A. § 1".
            lead = list(_JURISDICTION_START.finditer(prefix))
            if lead and m.start("prefix") == start:
                start += lead[-1].start()
            # Sentence punctuation after the number isn't part of the citation.
            cited = text[start:m.end()].rstrip(".,;:")
            found.append(CitationRef(
                start, start + len(cited), cited, prefix, normalize_section(m.group("sec")), bare_ok,
            ))
            if i in _LIST_PATTERNS:
                found += _list_continuation(text, masked, start + len(cited), prefix,
                                            m.group("sec"), bare_ok)
    # Keep the longest of any overlapping matches.
    found.sort(key=lambda r: (r.start, -(r.end - r.start)))
    kept: list[CitationRef] = []
    for ref in found:
        if kept and ref.start < kept[-1].end:
            continue
        kept.append(ref)
    return kept


# ---------------------------------------------------------------------------
# Normalising code names so different spellings compare equal
# ---------------------------------------------------------------------------

_JURISDICTION_TOKENS = {
    "utah": "ut", "ut": "ut",
    "cal": "ca", "california": "ca",
    "tex": "tx", "texas": "tx",
    "fed": "federal", "federal": "federal",
}
_FILLER = {"ann", "annotated", "code", "of", "and", "the", "vernon", "vernons", "west", "wests"}
_TOKEN_ALIASES = {"rules": "r", "rule": "r", "court": "ct", "courts": "ct", "govt": "gov",
                  "constitution": "const"}
_INITIALISMS = {
    "urcp": "Utah R. Civ. P.", "frcp": "Fed. R. Civ. P.", "trcp": "Tex. R. Civ. P.",
    "ure": "Utah R. Evid.", "fre": "Fed. R. Evid.", "tre": "Tex. R. Evid.",
}
_USC = re.compile(r"^(\d{1,2})\s*U\.?\s?S\.?\s?C\.?(?:\s?A\.?)?$")
_UCA = re.compile(r"\bU\.?\s?C\.?\s?A\.?(?=\s|$)")


@dataclass(frozen=True)
class CodeName:
    jurisdiction: str | None  # None when the citation doesn't say (e.g. "Civil Code")
    tokens: tuple[str, ...]  # e.g. ("r", "civ", "p") for "Utah R. Civ. P."


def code_name(prefix: str) -> CodeName:
    """Reduce a code/rules prefix to comparable tokens.

    "Utah Code Ann." and "U.C.A." -> (ut, ()); "Tex. Civ. Prac. & Rem. Code"
    -> (tx, (civ, prac, rem)); "42 U.S.C." -> (federal, (usc, 42)).
    """
    prefix = prefix.strip().rstrip(",")
    prefix = _INITIALISMS.get(prefix.lower(), prefix)
    m = _USC.match(prefix)
    if m:
        return CodeName("federal", ("usc", m.group(1)))
    prefix = _UCA.sub("Utah Code", prefix)

    words = re.findall(r"[a-z0-9]+", prefix.lower().replace("&", " and ").replace("'", ""))
    # Anything before the last jurisdiction word is surrounding prose, e.g.
    # "The Defendant Violated Utah Code" -> "Utah Code".
    jurisdiction = None
    for i in range(len(words) - 1, -1, -1):
        if words[i] in _JURISDICTION_TOKENS:
            jurisdiction = _JURISDICTION_TOKENS[words[i]]
            words = words[i + 1:]
            break
    tokens = [_TOKEN_ALIASES.get(w, w) for w in words if w not in _FILLER]
    if len(tokens) > 1 and tokens[-1] == "r":  # "Rules of Court, rule 3.1350"
        tokens.pop()
    return CodeName(jurisdiction, tuple(tokens))


def tokens_match(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Same length, and each pair equal or one an abbreviation (prefix) of the
    other: "civ" ~ "civil", "p" ~ "procedure". Numbers and constitution
    article numerals must match exactly ("i" is not "ii")."""
    if len(a) != len(b):
        return False
    for i, (x, y) in enumerate(zip(a, b)):
        exact = any(c.isdigit() for c in x + y) or (i > 0 and a[i - 1] == "art")
        if x != y and (exact or not (x.startswith(y) or y.startswith(x))):
            return False
    return True


def split_corpus_citation(citation: str) -> tuple[str, str]:
    """Split an index citation into (prefix, normalised section).

    "42 U.S.C. § 1983 (2024)" -> ("42 U.S.C.", "1983");
    "Utah R. Civ. P. 56" -> ("Utah R. Civ. P.", "56").
    """
    c = re.sub(r"\s*\((?:\d{4}|version superseded)\)\s*$", "", citation.strip())
    if "§" in c:
        prefix, section = c.rsplit("§", 1)
    else:
        prefix, _, section = c.rpartition(" ")
    return prefix.strip().rstrip(",").strip(), normalize_section(section)


# ---------------------------------------------------------------------------
# Citation-like text the checker can't parse (e.g. case citations)
# ---------------------------------------------------------------------------

_UNPARSED_PATTERNS = [
    r"§",
    r"\bU\.S\.C\b",
    r"\bC\.F\.R\b",
    r"\bU\.C\.A\b",
    r"\bCode Ann\b",
    r"\bUtah Code\b",
    r"\bUtah Admin\. Code\b",
    r"\bCal\. [A-Z][A-Za-z.& ]{0,30}? Code\b",
    r"\bTex\. [A-Z][A-Za-z.& ]{0,30}? Code\b",
    r"\b(?:Fed\.|Utah|Cal\.|Tex\.) R\.",
    # Case reporter citations: volume, a reporter abbreviation containing a
    # period, then a page, e.g. "550 U.S. 544", "123 P.3d 456",
    # "45 F. Supp. 2d 789", "12 Cal. App. 4th 345".
    r"\b\d{1,4} [A-Z][A-Za-z]*\.[A-Za-z0-9. ]{0,20}? \d{1,5}\b",
]
_UNPARSED = re.compile("|".join(_UNPARSED_PATTERNS))


def find_unverified_citations(text: str, skip: list[CitationRef] = ()) -> list[str]:
    """Lines with citation-like text outside placeholders and outside `skip`
    (citations already checked), e.g. case citations, which step 4 checks."""
    chars = list(_mask_placeholders(text))
    for ref in skip:
        chars[ref.start:ref.end] = " " * (ref.end - ref.start)
    masked = "".join(chars)
    found = []
    for original, line in zip(text.splitlines(), masked.splitlines()):
        if _UNPARSED.search(line):
            snippet = PLACEHOLDER.sub("", original).strip().strip("*").strip()
            if snippet not in found:
                found.append(snippet)
    return found
