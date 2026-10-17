"""Keep the law up to date, with an admin's approval before it changes.

    python -m battle_test.corpus refresh    # weekly, from Task Scheduler on the server
    python -m battle_test.corpus promote    # after reading the report: put the new law live
    python -m battle_test.corpus rollback   # put the law from before the last change back

Open US Law publishes a new snapshot about every quarter, and only the
latest can be downloaded. `refresh` checks for one. If there is one, it
builds the new index, and its section vectors if this setup has semantic
search, in data/staging/, beside the live law, which keeps serving cases.
The vectors reuse the live ones for every unchanged section. It then checks
the new law against the live law and writes a report. Nothing changes for
users until an admin runs `promote`, which swaps the index and its vectors
in together and keeps the old ones in data/archive/, since they can't be
downloaded again.

Every outcome is recorded in the web UI's activity log (Admin, Activity
log), so a new snapshot, a failure, or a missed weekly check shows there.
"""

import dataclasses
import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from battle_test import corpus, grounding, semantic
from battle_test.config import CorpusConfig, load_web_config
from battle_test.law_index import LawIndex
from battle_test.models import ModelError

# A jurisdiction whose count of in-force sections changes by more than this
# is flagged: Open US Law had Alaska's statuses wrong (see corpus.py), and a
# change like that should be looked at before it goes live.
COUNT_CHANGE_WARN = 0.05
# Citations listed per kind of change and jurisdiction in the report.
MAX_EXAMPLES = 15
# Who the activity log says did a refresh.
BY = "law refresh"
PROMOTE_COMMAND = "python -m battle_test.corpus promote"


def staging_dir(cfg: CorpusConfig) -> Path:
    return cfg.data_dir / "staging"


def archive_dir(cfg: CorpusConfig) -> Path:
    return cfg.data_dir / "archive"


def _state_path(cfg: CorpusConfig) -> Path:
    return staging_dir(cfg) / "refresh.json"


def _report_path(cfg: CorpusConfig) -> Path:
    return staging_dir(cfg) / "report.md"


def _index_files(db_path: Path) -> list[Path]:
    """The law index and its section vectors: they change together or not at all."""
    return [db_path, *semantic.vector_files(db_path)]


def _meta(db_path: Path) -> dict:
    if not db_path.exists():
        return {}
    with LawIndex(db_path) as law:
        return law.meta()


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def log(config_path: Path, kind: str, detail: str, by: str = BY) -> None:
    """Record it in the web UI's activity log. A setup without the web UI has none."""
    from battle_test.web.auth import AuthStore
    try:
        web = load_web_config(config_path)
    except KeyError:
        return
    store = AuthStore(web.data_dir / "users.sqlite")
    try:
        store.log(kind, "", by=by, detail=detail)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------

def refresh(cfg: CorpusConfig, config_path: Path, *, queries_for=None, embedder=None) -> str:
    """Check for a new snapshot and, if there is one, build and check it in
    staging. Returns what happened, as the activity log records it, or the
    report when there's a new snapshot.

    `queries_for(case)` gives the research queries for an evaluation case
    (by default the config's plaintiff model writes them), and `embedder`
    makes the section vectors (by default the config's embedding model).
    """
    try:
        return _refresh(cfg, config_path, queries_for, embedder)
    except (Exception, SystemExit) as e:
        log(config_path, "law update failed", f"{type(e).__name__}: {e}"[:500])
        raise


def _refresh(cfg: CorpusConfig, config_path: Path, queries_for, embedder) -> str:
    manifest = corpus.fetch_manifest(cfg)
    version, date = manifest["version"], manifest["snapshot_date"]
    new_cfg = dataclasses.replace(cfg, snapshot=version)
    fingerprint = corpus.files_fingerprint(new_cfg, manifest)

    live = _meta(cfg.db_path)
    # Indexes built before the fingerprint was kept are compared by version alone.
    if live.get("snapshot") == version and live.get("files_sha256", fingerprint) == fingerprint:
        message = f"Up to date: Open US Law {version} ({date})."
        log(config_path, "law checked", message)
        return message
    state = _read_json(_state_path(cfg))
    if state and state.get("status") == "ready" and state.get("files_sha256") == fingerprint:
        message = (f"Open US Law {version} was built on {state['built_at'][:10]} and is waiting for "
                   f"approval. Read {_report_path(cfg)}, then run: {PROMOTE_COMMAND}")
        log(config_path, "law checked", message)
        return message

    staging = staging_dir(cfg)
    staging.mkdir(parents=True, exist_ok=True)
    _state_path(cfg).unlink(missing_ok=True)
    stage_cfg = dataclasses.replace(new_cfg, data_dir=staging)
    for path in _index_files(stage_cfg.db_path):
        path.unlink(missing_ok=True)

    print(f"Open US Law {version} ({date}) is new. Building it in {staging}; the live law is unchanged.")
    files = corpus.download(new_cfg, manifest)  # kept under data/raw/<version>, like `build`
    corpus.build_index(stage_cfg, manifest, files)
    vectors = _embed(stage_cfg.db_path, cfg.db_path, config_path, embedder)
    checks = _check(cfg.db_path, stage_cfg.db_path, config_path, queries_for)

    built_at = datetime.now().isoformat(timespec="seconds")
    report = render_report(version, date, live.get("snapshot", ""), built_at, checks, vectors)
    _report_path(cfg).write_text(report, encoding="utf-8")
    _state_path(cfg).write_text(json.dumps({
        "status": "ready", "snapshot": version, "snapshot_date": date, "files_sha256": fingerprint,
        "built_at": built_at, "replaces": live.get("snapshot", ""), "warnings": checks["warnings"],
    }, indent=2), encoding="utf-8")
    log(config_path, "law update ready", _ready_detail(version, date, checks, _report_path(cfg)))
    return report


def _embed(new_db: Path, live_db: Path, config_path: Path, embedder) -> dict | None:
    """Section vectors for the new index, if this setup has semantic search
    (or vectors already, to keep up to date). None if it has neither."""
    search = semantic.load_search_config(config_path)
    live_vectors = semantic.vectors_path(live_db)
    if embedder is None:
        if not search.embedding_model or not (search.semantic or live_vectors.exists()):
            return None
        embedder = semantic.Embedder(search.embedding_url, search.embedding_model)
    print(f"Embedding new and changed sections with {embedder.model}.")

    def on_progress(done: int, total: int) -> None:
        if done == total or done % (semantic.BATCH * 1000) == 0:
            print(f"  {done:,} / {total:,} sections", flush=True)

    return semantic.build(new_db, embedder, on_progress, reuse_from=live_vectors)


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def _check(live_db: Path, new_db: Path, config_path: Path, queries_for) -> dict:
    from battle_test import evaluate

    warnings: list[str] = []
    old, new = _section_hashes(live_db), _section_hashes(new_db)
    changes = law_changes(old, new)
    for jurisdiction, row in changes.items():
        before, after = row["before"], row["after"]
        if before and abs(after - before) > COUNT_CHANGE_WARN * before:
            warnings.append(f"{jurisdiction.upper()}: in-force sections went from {before:,} to {after:,} "
                            f"({(after - before) / before:+.0%}). Check the data before approving.")

    cases = evaluate.load_cases()
    with LawIndex(new_db) as law:
        invalid = {c.name: problems for c in cases if (problems := evaluate.validate(c, law))}
    for name, problems in invalid.items():
        warnings.append(f"Expected-authority list {name}: {'; '.join(problems)}.")

    search, search_note = [], ""
    if not live_db.exists():
        search_note = "Not run: there's no live law to compare with."
    else:
        try:
            search = search_check(live_db, new_db, config_path, cases, queries_for)
        except ModelError as e:
            search_note = f"Not run: {e}"
            warnings.append(f"The search check didn't run ({e}).")
        if search and sum(r[2] for r in search) < sum(r[1] for r in search):
            warnings.append(f"Search finds fewer core authorities in the new law "
                            f"({sum(r[2] for r in search)} against {sum(r[1] for r in search)}).")
    return {"changes": changes, "invalid": invalid, "cases": len(cases), "search": search,
            "search_note": search_note, "warnings": warnings}


def _section_hashes(db_path: Path) -> dict[tuple[str, str], bytes]:
    """(jurisdiction, citation) -> a hash of the text, for every in-force section."""
    if not db_path.exists():
        return {}
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {(j, c): hashlib.blake2b(t.encode("utf-8"), digest_size=8).digest() for j, c, t in
                db.execute("SELECT jurisdiction, citation, text FROM sections WHERE act_status = 'in_force'")}
    finally:
        db.close()


def law_changes(old: dict, new: dict) -> dict[str, dict]:
    """Per jurisdiction: in-force sections before and after, and the
    citations added, amended, and no longer in force (repealed, or gone)."""
    before, after = Counter(j for j, _ in old), Counter(j for j, _ in new)
    changes = {}
    for jurisdiction in sorted(set(before) | set(after)):
        changes[jurisdiction] = {
            "before": before[jurisdiction], "after": after[jurisdiction],
            "added": sorted(c for j, c in new.keys() - old.keys() if j == jurisdiction),
            "removed": sorted(c for j, c in old.keys() - new.keys() if j == jurisdiction),
            "amended": sorted(c for (j, c), h in new.items() if j == jurisdiction and old.get((j, c), h) != h),
        }
    return changes


def search_check(live_db: Path, new_db: Path, config_path: Path, cases, queries_for=None) -> list[tuple]:
    """For each evaluation case: (name, core found in the live law, core found
    in the new law, core). Both are searched with the same queries, written
    once per case, so a difference is the law's, not the model's."""
    if queries_for is None:
        queries_for = _model_queries(config_path)
    rows = []
    with LawIndex(live_db) as live, LawIndex(new_db) as new:
        live_law = semantic.attach(live, config_path, live_db, fallback=True)
        new_law = semantic.attach(new, config_path, new_db, fallback=True)
        for case in cases:
            queries = queries_for(case)
            core = [e for e in case.expected if e.importance == "core"]

            def found(law) -> int:
                candidates = {s.citation for s in grounding.gather_candidates(law, queries, case.state.lower())}
                return sum(any(c in candidates for c in e.citations) for e in core)

            rows.append((case.name, found(live_law), found(new_law), len(core)))
    return rows


def _model_queries(config_path: Path):
    from battle_test import evaluate
    from battle_test.config import load_config
    from battle_test.models import make_client

    cfg = load_config(config_path)
    client = make_client(cfg)
    return lambda case: evaluate.research_queries(cfg, client, case)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _totals(changes: dict) -> tuple[int, int, int]:
    return tuple(sum(len(row[kind]) for row in changes.values()) for kind in ("added", "amended", "removed"))


def _ready_detail(version: str, date: str, checks: dict, report: Path) -> str:
    added, amended, removed = _totals(checks["changes"])
    warnings = checks["warnings"]
    flagged = f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
    if warnings:
        flagged += ": " + " ".join(w[:200] for w in warnings[:3]) + (" …" if len(warnings) > 3 else "")
    return (f"Open US Law {version} ({date}) is built and waiting for approval. {added:,} sections added, "
            f"{amended:,} amended, {removed:,} no longer in force. {flagged} Report: {report}. "
            f"To put it live: {PROMOTE_COMMAND}")


def render_report(version: str, date: str, replaces: str, built_at: str, checks: dict,
                  vectors: dict | None) -> str:
    added, amended, removed = _totals(checks["changes"])
    lines = [
        f"# Law update: Open US Law {version} ({date})",
        "",
        f"Built {built_at.replace('T', ' ')} beside the live law ({replaces or 'none'}). "
        "**Nothing has changed for users yet.**",
        "",
        f"- To put it live: `{PROMOTE_COMMAND}`",
        "- To go back after that: `python -m battle_test.corpus rollback`",
        "- To leave it: do nothing. The next refresh replaces it if a newer snapshot comes out.",
        "",
        "## Warnings",
        "",
        *([f"- {w}" for w in checks["warnings"]] or ["None."]),
        "",
        "## What changed (in-force sections)",
        "",
        f"{added:,} added, {amended:,} amended, {removed:,} no longer in force.",
        "",
        "| Jurisdiction | Before | After | Added | Amended | No longer in force |",
        "|---|---|---|---|---|---|",
    ]
    for jurisdiction, row in checks["changes"].items():
        lines.append(f"| {jurisdiction.upper()} | {row['before']:,} | {row['after']:,} | {len(row['added']):,} "
                     f"| {len(row['amended']):,} | {len(row['removed']):,} |")
    for jurisdiction, row in checks["changes"].items():
        for kind, label in (("amended", "Amended"), ("added", "Added"), ("removed", "No longer in force")):
            citations = row[kind]
            if citations:
                more = f" (and {len(citations) - MAX_EXAMPLES:,} more)" if len(citations) > MAX_EXAMPLES else ""
                lines += ["", f"**{jurisdiction.upper()}, {label.lower()}:** "
                          + ", ".join(f"`{c}`" for c in citations[:MAX_EXAMPLES]) + more]

    lines += ["", "## Expected-authority lists", ""]
    if checks["invalid"]:
        lines += [f"- **{name}:** {'; '.join(problems)}" for name, problems in checks["invalid"].items()]
        lines += ["", "Fix these lists (examples/eval/) once the new law is live."]
    else:
        lines.append(f"All {checks['cases']} are valid against the new law.")

    lines += ["", "## Search check", ""]
    if checks["search"]:
        lines += [
            "Core authorities that search finds for each evaluation case, with the same queries "
            "against both laws.",
            "",
            "| Case | Live law | New law | Core |",
            "|---|---|---|---|",
            *(f"| {name} | {live} | {new} | {core} |" for name, live, new, core in checks["search"]),
            f"| **Total** | {sum(r[1] for r in checks['search'])} | {sum(r[2] for r in checks['search'])} "
            f"| {sum(r[3] for r in checks['search'])} |",
        ]
    else:
        lines.append(checks["search_note"])

    lines += ["", "## Section vectors", ""]
    if vectors is None:
        lines.append("Not built: this setup has no semantic search.")
    else:
        lines.append(f"{vectors['embedded']:,} sections embedded, {vectors['reused']:,} kept from the live law.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# promote and rollback
# ---------------------------------------------------------------------------

def promote(cfg: CorpusConfig, config_path: Path, by: str = "command line") -> str:
    """Put the law that `refresh` built live, and archive the law it replaces."""
    state = _read_json(_state_path(cfg))
    staged_db = staging_dir(cfg) / cfg.db_path.name
    if not state or state.get("status") != "ready" or not staged_db.exists():
        raise SystemExit("No new law is waiting. `python -m battle_test.corpus refresh` builds one "
                         "when Open US Law publishes a new snapshot.")
    if semantic.vectors_path(cfg.db_path).exists() and not semantic.vectors_path(staged_db).exists():
        raise SystemExit("The live law has section vectors and the new law doesn't, so semantic search "
                         "would stop. Run refresh again with the embedding model reachable.")
    archived = _swap_in(cfg, staging_dir(cfg))
    version = state["snapshot"]
    os.replace(_report_path(cfg), cfg.data_dir / f"law-update-{version}.md")
    _state_path(cfg).unlink()
    message = (f"Open US Law {version} ({state['snapshot_date']}) is live, replacing "
               f"{state.get('replaces') or 'nothing'}.")
    if archived:
        message += f" The previous law is kept in {archived}; `corpus rollback` puts it back."
    message += f" Set corpus.snapshot = \"{version}\" in the config files at the next deploy."
    log(config_path, "law update promoted", message, by=by)
    return message


def rollback(cfg: CorpusConfig, config_path: Path, by: str = "command line") -> str:
    """Put the most recently archived law back, and archive the live law in its place."""
    archives = (sorted(d for d in archive_dir(cfg).iterdir() if (d / cfg.db_path.name).exists())
                if archive_dir(cfg).exists() else [])
    if not archives:
        raise SystemExit(f"There's no earlier law in {archive_dir(cfg)} to go back to.")
    target = archives[-1]
    restored = _meta(target / cfg.db_path.name).get("snapshot", "?")
    replaced = _meta(cfg.db_path).get("snapshot", "none")
    archived = _swap_in(cfg, target)
    if not any(target.iterdir()):
        target.rmdir()
    message = f"Rolled back to Open US Law {restored}, replacing {replaced}"
    message += f" (kept in {archived})." if archived else "."
    log(config_path, "law rolled back", message, by=by)
    return message


def _swap_in(cfg: CorpusConfig, source: Path) -> Path | None:
    """Move the live index and vectors to a new archive folder, then the ones
    in `source` into their place. Returns the archive folder, if there was a
    live law to keep. Moves within one volume, so each takes milliseconds,
    and a case that starts in between fails rather than mixing two laws.

    If a move fails, the ones done are undone, so the live law is never left
    half swapped. On Windows a file another process has open can't be moved:
    stop the web UI there first."""
    semantic._open_vectors.cache_clear()  # this process's own hold on the vectors
    live = [p for p in _index_files(cfg.db_path) if p.exists()]
    archived = None
    if live:
        snapshot = _meta(cfg.db_path).get("snapshot", "unknown")
        archived = archive_dir(cfg) / f"{datetime.now():%Y%m%d-%H%M%S}-{snapshot}"
        archived.mkdir(parents=True)
    moves = [(p, archived / p.name) for p in live]
    moves += [(p, cfg.data_dir / p.name) for p in _index_files(source / cfg.db_path.name) if p.exists()]
    done: list[tuple[Path, Path]] = []
    try:
        for src, dst in moves:
            os.replace(src, dst)
            done.append((src, dst))
    except OSError as e:
        for src, dst in reversed(done):
            os.replace(dst, src)
        if archived and not any(archived.iterdir()):
            archived.rmdir()
        raise SystemExit(f"Nothing was changed: couldn't move {e.filename} ({e.strerror}). If the web UI "
                         "is running on Windows, stop it and try again.") from e
    return archived
