"""Download the Open US Law files we need and build the local law index.

    python -m battle_test.corpus build        # download (if needed) + index
    python -m battle_test.corpus info         # snapshot date and row counts
    python -m battle_test.corpus search --state UT "summary judgment"

Data: Open US Law by Vaquill AI, CC BY 4.0 (https://www.vaquill.ai/open-us-law).
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from battle_test.citations import split_corpus_citation
from battle_test.config import DEFAULT_CONFIG_PATH, CorpusConfig, load_corpus_config
from battle_test.law_index import LawIndex, citation_key

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


def build_index(cfg: CorpusConfig, manifest: dict, files: list[Path]) -> dict[str, int]:
    import pyarrow.parquet as pq  # build-time only dependency

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
                rows.append((
                    r["act_id"], jurisdiction, r["document_type"] or "", r["citation"],
                    citation_key(r["citation"]), *split_corpus_citation(r["citation"]),
                    _heading(r), r["section_title"] or "",
                    r["display_path"] or "", r["act_status"] or "unknown", r["text"],
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


def _cmd_search(cfg: CorpusConfig, state: str, query: str, limit: int) -> None:
    with LawIndex(cfg.db_path) as index:
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
    search.add_argument("--state", required=True, type=str.lower, choices=("ut", "ca", "tx"))
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("query")
    args = parser.parse_args(argv)

    cfg = load_corpus_config(args.config)
    try:
        if args.command == "build":
            _cmd_build(cfg)
        elif args.command == "info":
            _cmd_info(cfg)
        else:
            _cmd_search(cfg, args.state, args.query, args.limit)
    except FileNotFoundError as e:  # no index built yet
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
