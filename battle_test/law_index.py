"""Query the local law index built by battle_test.corpus.

Standard library only: the pipeline uses this at run time, while pyarrow is
needed only to build the index.
"""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from battle_test.citations import CitationRef, code_name, tokens_match

# Words too common in legal text to help ranking.
_STOPWORDS = {
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "law", "of", "on", "or", "shall", "that", "the", "this", "to", "under",
    "with",
}

# How much to favour the selected state's law over federal law in ranking.
# Tuned on sample queries: 1.15 lifts state rules (e.g. Utah R. Civ. P. 56
# from #4 to #2) without pushing federal law down on federal topics; 1.25+
# starts burying bankruptcy and federal-jurisdiction statutes.
STATE_BOOST = 1.15

# The index cites California codes by short code ("Cal. CIV § 1542"), but
# briefs spell them out ("Cal. Civ. Code § 1542", "Code of Civil Procedure
# section 437c"). These names let either form match.
_CA_CODE_NAMES = {
    "BPC": "Business and Professions", "CCP": "Civil Procedure", "CIV": "Civil",
    "COM": "Commercial", "CORP": "Corporations", "EDC": "Education", "ELEC": "Elections",
    "EVID": "Evidence", "FAC": "Food and Agricultural", "FAM": "Family", "FGC": "Fish and Game",
    "FIN": "Financial", "GOV": "Government", "HNC": "Harbors and Navigation",
    "HSC": "Health and Safety", "INS": "Insurance", "LAB": "Labor", "MVC": "Military and Veterans",
    "PCC": "Public Contract", "PEN": "Penal", "PROB": "Probate", "PRC": "Public Resources",
    "PUC": "Public Utilities", "RTC": "Revenue and Taxation", "SHC": "Streets and Highways",
    "UIC": "Unemployment Insurance", "VEH": "Vehicle", "WAT": "Water",
    "WIC": "Welfare and Institutions",
}

_COLUMNS = (
    "citation, jurisdiction, document_type, section_title, display_path, "
    "act_status, text, source_url, last_amended_year"
)


@dataclass(frozen=True)
class LawSection:
    citation: str
    jurisdiction: str  # "ut", "ca", "tx" or "federal"
    document_type: str  # e.g. "statute", "court_rule", "constitution"
    section_title: str
    display_path: str
    act_status: str  # "in_force", "repealed", "superseded", ...
    text: str
    source_url: str
    last_amended_year: int | None

    @property
    def in_force(self) -> bool:
        return self.act_status == "in_force"

    @property
    def official_source(self) -> bool:
        # Records taken from commercial aggregators have no official URL;
        # the plan says citations resting on them get flagged.
        return bool(self.source_url)


def citation_key(citation: str) -> str:
    """Normalise a citation for exact lookup: case, spacing, '§', '.' and ','
    don't matter, but hyphens and digits do (78B-2-309 != 78B-23-09)."""
    return re.sub(r"[\s.,§]+", "", citation.lower())


def fts_query(text: str) -> str:
    """Turn free text into an FTS5 query that matches any meaningful word."""
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1 and w not in _STOPWORDS]
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(words))


class LawIndex:
    def __init__(self, db_path: Path):
        if not db_path.exists():
            raise FileNotFoundError(
                f"No law index at {db_path}. Build it with: python -m battle_test.corpus build"
            )
        self._db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self._code_names: list[tuple[str, str, tuple[str, ...]]] | None = None

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "LawIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def meta(self) -> dict[str, str]:
        return dict(self._db.execute("SELECT key, value FROM meta"))

    def search(
        self, text: str, state: str, *, limit: int = 10, include_inactive: bool = False
    ) -> list[LawSection]:
        """Best-matching sections from `state` (e.g. "ut") and federal law."""
        query = fts_query(text)
        if not query:
            return []
        status_filter = "" if include_inactive else "AND s.act_status = 'in_force'"
        rows = self._db.execute(
            f"""
            SELECT {", ".join("s." + c.strip() for c in _COLUMNS.split(","))}
            FROM sections_fts
            JOIN sections s ON s.id = sections_fts.rowid
            WHERE sections_fts MATCH ?
              AND s.jurisdiction IN (?, 'federal')
              {status_filter}
            -- Weights: citation, heading (title/chapter names), section
            -- title, path, text. Section titles matter most, then headings.
            -- bm25 is negative (lower = better), so scaling it up favours
            -- the state: cases are filed in state court, so its own rules
            -- and statutes should outrank similar federal ones.
            ORDER BY bm25(sections_fts, 2.0, 3.0, 5.0, 1.0, 1.0)
                     * (CASE WHEN s.jurisdiction = ? THEN ? ELSE 1.0 END)
            LIMIT ?
            """,
            (query, state.lower(), state.lower(), STATE_BOOST, limit),
        ).fetchall()
        return [LawSection(*row) for row in rows]

    def lookup(self, citation: str) -> list[LawSection]:
        """Sections whose citation matches exactly (after normalisation).

        Returns every match regardless of status, so callers can tell
        "doesn't exist" apart from "exists but repealed".
        """
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM sections WHERE citation_key = ?", (citation_key(citation),)
        ).fetchall()
        return [LawSection(*row) for row in rows]

    def resolve(self, ref: CitationRef, state: str) -> list[LawSection]:
        """Sections a parsed citation from a draft refers to, in `state` or
        federal law. Several matches means several versions of one section
        or an ambiguous citation; none means it isn't in the index."""
        state = state.lower()
        name = code_name(ref.prefix)
        jurisdiction = name.jurisdiction or state
        if jurisdiction not in (state, "federal"):
            return []
        prefixes = [p for j, p, tokens in self._catalogue()
                    if j == jurisdiction and tokens_match(name.tokens, tokens)]
        if prefixes:
            marks = ", ".join("?" * len(prefixes))
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM sections WHERE section_key = ? AND code_prefix IN ({marks})",
                (ref.section, *prefixes),
            ).fetchall()
        elif ref.bare_ok and not name.tokens and name.jurisdiction is None:
            # Bare "§ 437c": only the state's own law, by section number.
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM sections WHERE section_key = ? AND jurisdiction = ?",
                (ref.section, state),
            ).fetchall()
        else:
            rows = []
        return [LawSection(*row) for row in rows]

    def _catalogue(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """(jurisdiction, code_prefix, name tokens) for every code in the index,
        with California's short codes also listed under their full names."""
        if self._code_names is None:
            self._code_names = []
            for jurisdiction, prefix in self._db.execute(
                "SELECT DISTINCT jurisdiction, code_prefix FROM sections"
            ):
                self._code_names.append((jurisdiction, prefix, code_name(prefix).tokens))
                short = prefix.removeprefix("Cal. ")
                if jurisdiction == "ca" and short in _CA_CODE_NAMES:
                    full = code_name("Cal. " + _CA_CODE_NAMES[short]).tokens
                    self._code_names.append((jurisdiction, prefix, full))
        return self._code_names
