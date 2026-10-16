"""Download the Open US Law files we need and build the local law index.

    python -m battle_test.corpus build        # download (if needed) + index
    python -m battle_test.corpus info         # snapshot date and row counts
    python -m battle_test.corpus search --state UT "summary judgment"
    python -m battle_test.corpus embed        # section vectors for semantic search
    python -m battle_test.corpus search --state TX --semantic "time limit to sue on a contract"
    python -m battle_test.corpus ak-headings  # refresh Alaska chapter names (rarely)

Data: Open US Law by Vaquill AI, CC BY 4.0 (https://www.vaquill.ai/open-us-law).
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from battle_test.citations import split_corpus_citation
from battle_test.config import DEFAULT_CONFIG_PATH, CorpusConfig, load_corpus_config
from battle_test.law_index import LawIndex, citation_key
from battle_test.models import ModelError

ATTRIBUTION = "Open US Law by Vaquill AI, CC BY 4.0"

_SOURCE_COLUMNS = [
    "act_id", "citation", "document_type", "title_name", "chapter_name", "section_title",
    "display_path",
    "act_status", "text", "source_url", "last_amended_year",
]

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sections (
    id INTEGER PRIMARY KEY,
    act_id TEXT NOT NULL UNIQUE,
    jurisdiction TEXT NOT NULL,
    document_type TEXT NOT NULL,
    citation TEXT NOT NULL,
    citation_key TEXT NOT NULL,
    code_prefix TEXT NOT NULL,  -- citation minus the section, e.g. "Utah R. Civ. P."
    section_key TEXT NOT NULL,  -- normalised section/rule number, e.g. "78b-2-309"
    heading TEXT NOT NULL,  -- title and chapter names, e.g. "Statutes of Limitations"
    section_title TEXT NOT NULL,
    display_path TEXT NOT NULL,
    act_status TEXT NOT NULL,
    text TEXT NOT NULL,
    source_url TEXT NOT NULL,
    last_amended_year INTEGER
);
CREATE INDEX sections_citation_key ON sections (citation_key);
CREATE INDEX sections_section_key ON sections (section_key, code_prefix);
CREATE VIRTUAL TABLE sections_fts USING fts5(
    citation, heading, section_title, display_path, text,
    content='sections', content_rowid='id', tokenize='porter unicode61'
);
"""


def _open(url: str):
    # The data host returns 403 for Python's default "Python-urllib" agent.
    request = urllib.request.Request(url, headers={"User-Agent": "battle_test-corpus/1.0"})
    return urllib.request.urlopen(request, timeout=120)


def _fetch_manifest(cfg: CorpusConfig) -> dict:
    with _open(f"{cfg.base_url}/index.json") as r:
        manifest = json.load(r)
    if manifest["version"] != cfg.snapshot:
        # The manifest (and its checksums) only describes the latest snapshot.
        raise SystemExit(
            f"config.toml asks for snapshot {cfg.snapshot}, but the latest published "
            f"snapshot is {manifest['version']} ({manifest['snapshot_date']}). "
            "Update corpus.snapshot in config.toml and rebuild."
        )
    return manifest


def _wanted_files(cfg: CorpusConfig, manifest: dict) -> list[dict]:
    wanted = {f"us_{j}_{t}.parquet" for j in cfg.jurisdictions for t in cfg.document_types}
    files = [f for f in manifest["files"] if f["file"] in wanted]
    # Not every jurisdiction publishes every type (e.g. no Utah regulations).
    missing = wanted - {f["file"] for f in files}
    for name in sorted(missing):
        print(f"  (not in snapshot, skipped: {name})")
    return sorted(files, key=lambda f: f["file"])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(cfg: CorpusConfig, manifest: dict) -> list[Path]:
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for entry in _wanted_files(cfg, manifest):
        path = cfg.raw_dir / entry["file"]
        if path.exists() and _sha256(path) == entry["sha256"]:
            print(f"  have      {entry['file']}")
        else:
            print(f"  download  {entry['file']} ({entry['bytes'] / 1e6:.1f} MB)", flush=True)
            part = path.with_suffix(".part")
            with _open(entry["url"]) as r, open(part, "wb") as f:
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    f.write(chunk)
            if _sha256(part) != entry["sha256"]:
                part.unlink()
                raise SystemExit(f"Checksum mismatch for {entry['file']}; download discarded.")
            os.replace(part, path)
        paths.append(path)
    return paths


def _jurisdiction(parquet_name: str) -> str:
    # us_ut_statutes.parquet -> "ut"; us_federal_court_rules.parquet -> "federal"
    return parquet_name.split("_")[1]


def _heading(row: dict) -> str:
    # The dataset stores missing values as the string "None".
    parts = [row.get(k) for k in ("title_name", "chapter_name")]
    return " / ".join(p for p in parts if p and p != "None")


# Open US Law gives every Alaska statute the heading "AK Code" and no chapter
# name, so keyword search can't match a topic a section's own title doesn't
# name: "consumer protection" never found AS 45.50.471 ("Unlawful acts and
# practices."), whose chapter is "Unfair Trade Practices and Consumer
# Protection". The names come from akleg.gov's table of contents, saved by
# `python -m battle_test.corpus ak-headings` so a build doesn't depend on it.
AK_HEADINGS_PATH = Path(__file__).with_name("ak_headings.json")
_AKLEG_TOC = "https://www.akleg.gov/basis/statutes.asp"


def _load_ak_headings(path: Path = AK_HEADINGS_PATH) -> dict:
    if not path.exists():
        return {"titles": {}, "chapters": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _ak_heading(section_key: str, names: dict) -> str | None:
    """'45.50.471' -> 'Trade and Commerce / Unfair Trade Practices and Consumer Protection'."""
    parts = section_key.split(".")
    if len(parts) < 3:
        return None
    title, chapter = names["titles"].get(parts[0]), names["chapters"].get(".".join(parts[:2]))
    return " / ".join(p for p in (title, chapter) if p) or None


def _akleg_toc(title: str = "") -> str:
    url = f"{_AKLEG_TOC}?media=js&type=TOC&title={title}" if title else _AKLEG_TOC
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


_TOC_ENTRY = re.compile(r'loadTOC\(\s*"?([\w.]+)"?\s*\);[^>]*>(?:<b>)?\s*([^<]+)')


def fetch_ak_headings(fetch=_akleg_toc, pause: float = 1.0) -> dict:
    """Alaska's title and chapter names from akleg.gov's table of contents.

    The main page lists the titles; each title's entry lists its chapters,
    or parts ("45p2") that list them in turn.
    """
    import html
    import time

    names: dict = {"source": _AKLEG_TOC, "fetched": datetime.now().date().isoformat(),
                   "titles": {}, "chapters": {}}
    pending = [key for key, _ in _TOC_ENTRY.findall(fetch())]
    seen = set()
    while pending:
        key = pending.pop(0)
        if key in seen:
            continue
        seen.add(key)
        time.sleep(pause)
        for entry, label in _TOC_ENTRY.findall(fetch(key)):
            label = " ".join(html.unescape(label).split())
            name = re.sub(r"^(?:Title|Chapter|Article|Part)\s+[\w.]+\.\s*", "", label).rstrip(". ").strip()
            if "." in entry and "p" not in entry:
                names["chapters"][entry] = name
            elif "p" in entry:
                pending.append(entry)
            else:
                names["titles"][entry.zfill(2)] = name
    return names


# Open US Law's status for Alaska statutes is wrong (checked against
# akleg.gov, 2026-10-16, snapshot v2026.09.1): all 30 sampled "reserved"
# sections and all 6 "repealed" ones are current law. "Reserved" marks a
# section with one repealed subsection ("(b) [Repealed, § 28 ch 90 SLA
# 1991.]") or the word "reserved"; "repealed" marks one that mentions repeal
# ("Construction against implied repeal"). That hid the Unfair Trade
# Practices Act (AS 45.50.471) from search. Wholly repealed sections aren't
# in the data at all. Alaska's constitution and court rule statuses are right.
_STATUS_UNRELIABLE = {("ak", "statute")}
_REPEAL_NOTE = re.compile(r"\s*\[?Repealed\b")


def _act_status(jurisdiction: str, document_type: str, status: str, text: str) -> str:
    if ((jurisdiction, document_type) in _STATUS_UNRELIABLE and status in ("reserved", "repealed")
            and not _REPEAL_NOTE.match(text)):
        return "in_force"
    return status


def build_index(cfg: CorpusConfig, manifest: dict, files: list[Path],
                ak_headings: dict | None = None) -> dict[str, int]:
    import pyarrow.parquet as pq  # build-time only dependency

    ak_names = ak_headings if ak_headings is not None else _load_ak_headings()

    tmp = cfg.db_path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript(_SCHEMA)

    counts: dict[str, int] = {}
    skipped_duplicates = 0
    for path in files:
        jurisdiction = _jurisdiction(path.name)
        before = db.total_changes
        rows_seen = 0
        for batch in pq.ParquetFile(path).iter_batches(columns=_SOURCE_COLUMNS, batch_size=5000):
            rows = []
            for r in batch.to_pylist():
                rows_seen += 1
                if not r["citation"] or not r["text"]:
                    continue
                prefix, section_key = split_corpus_citation(r["citation"])
                heading = _heading(r)
                if jurisdiction == "ak" and r["document_type"] == "statute":
                    heading = _ak_heading(section_key, ak_names) or heading
                rows.append((
                    r["act_id"], jurisdiction, r["document_type"] or "", r["citation"],
                    citation_key(r["citation"]), prefix, section_key,
                    heading, r["section_title"] or "",
                    r["display_path"] or "",
                    _act_status(jurisdiction, r["document_type"] or "", r["act_status"] or "unknown", r["text"]),
                    r["text"],
                    r["source_url"] or "", r["last_amended_year"],
                ))
            db.executemany(
                "INSERT OR IGNORE INTO sections (act_id, jurisdiction, document_type, citation, "
                "citation_key, code_prefix, section_key, heading, section_title, display_path, "
                "act_status, text, source_url, last_amended_year) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        added = db.total_changes - before
        skipped_duplicates += rows_seen - added
        counts[path.name] = added
        print(f"  indexed   {path.name}: {added:,} sections", flush=True)

    db.execute("INSERT INTO sections_fts (sections_fts) VALUES ('rebuild')")
    meta = {
        "snapshot": cfg.snapshot,
        "snapshot_date": manifest["snapshot_date"],
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "attribution": ATTRIBUTION,
        "jurisdictions": ",".join(cfg.jurisdictions),
        "document_types": ",".join(cfg.document_types),
    }
    db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    db.commit()
    db.close()
    os.replace(tmp, cfg.db_path)  # only replace a working index with a finished one
    if skipped_duplicates:
        print(f"  (skipped {skipped_duplicates:,} rows with no citation/text or a duplicate id)")
    return counts


def _cmd_build(cfg: CorpusConfig) -> None:
    print(f"Open US Law {cfg.snapshot}: {', '.join(cfg.jurisdictions)} / {', '.join(cfg.document_types)}")
    manifest = _fetch_manifest(cfg)
    files = download(cfg, manifest)
    counts = build_index(cfg, manifest, files)
    size = cfg.db_path.stat().st_size / 1e6
    print(f"Built {cfg.db_path} ({sum(counts.values()):,} sections, {size:.0f} MB)")


def _cmd_info(cfg: CorpusConfig) -> None:
    with LawIndex(cfg.db_path) as index:
        for key, value in index.meta().items():
            print(f"{key:15} {value}")
        rows = index._db.execute(
            "SELECT jurisdiction, document_type, act_status, COUNT(*), SUM(source_url = '') "
            "FROM sections GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
        ).fetchall()
    print(f"\n{'jurisdiction':12} {'type':14} {'status':12} {'sections':>9} {'no official URL':>16}")
    for j, t, s, n, unofficial in rows:
        print(f"{j:12} {t:14} {s:12} {n:9,} {unofficial:16,}")


def _cmd_embed(cfg: CorpusConfig, config_path: Path) -> None:
    from battle_test import semantic

    search = semantic.load_search_config(config_path)
    if not search.embedding_model:
        raise SystemExit(f"Set search.embedding_model in {config_path} first.")
    print(f"Embedding the in-force sections of {cfg.db_path} with {search.embedding_model}. "
          "This takes a while, and can be stopped and resumed.")
    started = datetime.now()

    def on_progress(done: int, total: int) -> None:
        if done == total or done % (semantic.BATCH * 100) == 0:
            minutes = (datetime.now() - started).total_seconds() / 60
            print(f"  {done:,} / {total:,} sections  ({minutes:.0f} min)", flush=True)

    meta = semantic.build(cfg.db_path, semantic.Embedder(search.embedding_url, search.embedding_model), on_progress)
    path = semantic.vectors_path(cfg.db_path)
    print(f"Built {path} ({meta['count']:,} sections, {meta['dimensions']} dimensions, "
          f"{path.stat().st_size / 1e6:.0f} MB)")


def _cmd_search(cfg: CorpusConfig, state: str, query: str, limit: int, config_path: Path | None = None) -> None:
    with LawIndex(cfg.db_path) as index:
        if config_path:  # --semantic
            from battle_test import semantic
            search = semantic.load_search_config(config_path)
            law = semantic.SemanticLaw(index, semantic.VectorIndex(semantic.vectors_path(cfg.db_path)),
                                       semantic.Embedder(search.embedding_url, search.embedding_model))
            results = law.semantic_search([query], state, limit=limit)[0]
        else:
            results = index.search(query, state, limit=limit)
    for s in results:
        flag = "" if s.official_source else "  [no official source URL]"
        print(f"\n{s.citation} — {s.section_title}{flag}")
        print(f"  {s.display_path}")
        print(f"  {' '.join(s.text.split())[:240]}…")
    if not results:
        print("No matches.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="battle_test.corpus", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="Download the configured files and (re)build the index.")
    sub.add_parser("info", help="Show the index's snapshot and section counts.")
    search = sub.add_parser("search", help="Full-text search over state + federal law.")
    search.add_argument("--state", required=True, type=str.lower, choices=("ut", "ca", "tx", "ak"))
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--semantic", action="store_true",
                        help="Search by meaning (needs `embed` to have been run), not by keyword.")
    search.add_argument("query")
    sub.add_parser("embed", help="Build the section vectors for semantic search (slow; resumable).")
    sub.add_parser("ak-headings", help=f"Refresh Alaska's title and chapter names from akleg.gov "
                                       f"into {AK_HEADINGS_PATH.name}, then rebuild.")
    args = parser.parse_args(argv)

    cfg = load_corpus_config(args.config)
    try:
        if args.command == "ak-headings":
            names = fetch_ak_headings()
            AK_HEADINGS_PATH.write_text(json.dumps(names, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            print(f"Saved {len(names['titles'])} titles and {len(names['chapters'])} chapters "
                  f"to {AK_HEADINGS_PATH}")
        elif args.command == "build":
            _cmd_build(cfg)
        elif args.command == "info":
            _cmd_info(cfg)
        elif args.command == "embed":
            _cmd_embed(cfg, args.config)
        else:
            _cmd_search(cfg, args.state, args.query, args.limit, args.config if args.semantic else None)
    except (FileNotFoundError, ModelError) as e:  # no index built yet, or Ollama isn't reachable
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
