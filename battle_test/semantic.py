"""Semantic search over the law index: find sections by meaning, not shared words.

Keyword search misses a statute that words a topic differently from the
query ("time limit to sue" vs "limitation of actions"), and each state words
its statutes its own way. Here every in-force section is turned once into a
vector by an embedding model (`python -m battle_test.corpus embed`), and each
search query is turned into one the same way. The nearest sections are the
ones closest in meaning. It runs alongside keyword search, not instead of it
(see grounding.gather_candidates).

The embedding model runs on Ollama, whichever provider drafts the documents.
The same model must embed the sections and the queries.

Needs numpy, imported only here: a setup with semantic search off doesn't
have to install it.
"""

import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from battle_test.law_index import LawIndex, LawSection
from battle_test.models import ModelError

# How much of each section is embedded, after its citation, path and title.
# Most sections are shorter. The start of a long one says what it's about.
EMBED_CHARS = 1500
BATCH = 32
# Save progress this often while building, so an interrupted build resumes.
CHECKPOINT_BATCHES = 50
# Sections scored per step of a search. Vectors are stored as 16-bit floats
# and converted a block at a time, so a search never holds them all in memory.
SCORE_BLOCK = 16384
# Ollama now and then refuses an embedding request it accepts when sent again
# (about one HTTP 400 in 1,900 batches on the Windows server, 2026-10-09: no
# free socket for its internal call). Without a retry, that stops an
# hours-long build.
ATTEMPTS = 3
# How much to favour the selected state's law over federal law, as
# law_index.STATE_BOOST does for keyword search. Unboosted, federal sections
# were 37% of the top 8 results for the three sample cases' queries, all
# state-law contract disputes (2026-10-09, nomic-embed-text). At 1.04 they
# are 12%, at 1.06 6%, at 1.12 under 1%. It didn't change how many expected
# authorities were found, so it's set low enough to leave federal law in.
STATE_BOOST = 1.04
RETRY_WAIT_SECONDS = 5

# What each model family wants in front of a (document, query). The models
# were trained with these, and retrieve worse without them.
_PREFIXES = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
    "qwen3-embedding": ("", "Instruct: Given a legal topic, retrieve the statutes and court rules "
                            "that govern it\nQuery: "),
}


class SemanticError(ModelError):
    pass


@dataclass(frozen=True)
class SearchConfig:
    semantic: bool
    embedding_model: str
    embedding_url: str


def load_search_config(path: Path) -> SearchConfig:
    """The [search] section. Left out, semantic search is off."""
    import tomllib
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    search = raw.get("search", {})
    semantic = search.get("semantic", False)
    model = search.get("embedding_model", "")
    if semantic and not model:
        raise ValueError(f"{path} has search.semantic = true but no search.embedding_model.")
    url = search.get("embedding_url") or raw.get("ollama", {}).get("url") or "http://localhost:11434"
    return SearchConfig(semantic, model, url.rstrip("/"))


def vectors_path(db_path: Path) -> Path:
    return db_path.with_name("law-vectors.npy")


def _ids_path(vectors: Path) -> Path:
    return vectors.with_name(vectors.stem + "-ids.npy")


def _meta_path(vectors: Path) -> Path:
    return vectors.with_suffix(".json")


def document_text(citation: str, display_path: str, section_title: str, text: str) -> str:
    return f"{citation}. {display_path}. {section_title}.\n{text[:EMBED_CHARS]}"


class Embedder:
    """Turns text into unit-length vectors with an Ollama embedding model."""

    def __init__(self, url: str, model: str, timeout_seconds: int = 600):
        # On Windows "localhost" is tried over IPv6 first, where Ollama isn't
        # listening, and falling back costs 2 seconds a request. Measured
        # 2026-10-09: 2.5 s a batch against 0.4 s, a 9-hour build against 1.5.
        parts = urllib.parse.urlsplit(url)
        if parts.hostname == "localhost":
            url = parts._replace(netloc=parts.netloc.replace("localhost", "127.0.0.1")).geturl()
        self.url = url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._document_prefix, self._query_prefix = next(
            (p for name, p in _PREFIXES.items() if model.startswith(name)), ("", ""))

    def documents(self, texts: list[str]):
        return self._embed([self._document_prefix + t for t in texts])

    def queries(self, texts: list[str]):
        return self._embed([self._query_prefix + t for t in texts])

    def _embed(self, texts: list[str]):
        import numpy as np
        request = urllib.request.Request(
            f"{self.url}/api/embed",
            data=json.dumps({"model": self.model, "input": texts, "truncate": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        for attempt in range(1, ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    vectors = np.asarray(json.load(response)["embeddings"], dtype=np.float32)
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")
                if e.code != 404 and attempt < ATTEMPTS:
                    time.sleep(RETRY_WAIT_SECONDS * attempt)
                    continue
                hint = f" Get the model with: ollama pull {self.model}" if e.code == 404 else ""
                raise SemanticError(f"Ollama returned HTTP {e.code} for the embedding model "
                                    f"{self.model}: {detail}.{hint}") from e
            except urllib.error.URLError as e:
                raise SemanticError(f"Could not reach Ollama at {self.url} for semantic search "
                                    f"({e.reason}). Is it running?") from e
        if len(vectors) != len(texts):
            raise SemanticError(f"Ollama returned {len(vectors)} embeddings for {len(texts)} texts.")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)


def build(db_path: Path, embedder, on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Embed every in-force section of the index at db_path, and save the
    vectors next to it. Returns the saved description of them.

    Sections are stored grouped by jurisdiction, so a search reads only the
    state's block and the federal one. A build that was interrupted resumes
    where it stopped, as long as the index and the model haven't changed.
    """
    import numpy as np

    final = vectors_path(db_path)
    part = final.with_name(final.stem + ".building.npy")
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        index_built_at = dict(db.execute("SELECT key, value FROM meta")).get("built_at", "")
        counts = db.execute("SELECT jurisdiction, COUNT(*) FROM sections WHERE act_status = 'in_force' "
                            "GROUP BY jurisdiction ORDER BY jurisdiction").fetchall()
        total = sum(n for _, n in counts)
        if not total:
            raise SemanticError(f"The law index at {db_path} has no in-force sections to embed.")
        ranges, start = {}, 0
        for jurisdiction, n in counts:
            ranges[jurisdiction] = [start, start + n]
            start += n

        meta = {"model": embedder.model, "index_built_at": index_built_at, "count": total,
                "embed_chars": EMBED_CHARS, "jurisdictions": ranges}
        done, vectors = 0, None
        previous = _read_json(_meta_path(part))
        if previous and part.exists() and {k: previous.get(k) for k in meta} == meta:
            done = previous["done"]
            vectors = np.lib.format.open_memmap(part, mode="r+")
            ids = np.load(_ids_path(part))

        rows = db.execute(
            "SELECT id, citation, display_path, section_title, substr(text, 1, ?) FROM sections "
            "WHERE act_status = 'in_force' ORDER BY jurisdiction, id LIMIT -1 OFFSET ?", (EMBED_CHARS, done))
        batches = 0
        while batch := rows.fetchmany(BATCH):
            embedded = embedder.documents([document_text(*row[1:]) for row in batch])
            if vectors is None:
                vectors = np.lib.format.open_memmap(part, mode="w+", dtype=np.float16,
                                                    shape=(total, embedded.shape[1]))
                ids = np.zeros(total, dtype=np.int64)
            vectors[done:done + len(batch)] = embedded
            ids[done:done + len(batch)] = [row[0] for row in batch]
            done += len(batch)
            batches += 1
            if batches % CHECKPOINT_BATCHES == 0:
                _checkpoint(part, vectors, ids, {**meta, "done": done})
            if on_progress:
                on_progress(done, total)
    finally:
        db.close()

    meta["dimensions"] = int(vectors.shape[1])
    vectors.flush()
    del vectors  # closes the file, so Windows lets it be renamed
    np.save(_ids_path(final), ids)
    os.replace(part, final)
    _meta_path(final).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _ids_path(part).unlink(missing_ok=True)
    _meta_path(part).unlink(missing_ok=True)
    _open_vectors.cache_clear()
    return meta


def _checkpoint(part: Path, vectors, ids, meta: dict) -> None:
    import numpy as np
    vectors.flush()
    np.save(_ids_path(part), ids)
    _meta_path(part).write_text(json.dumps(meta), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class VectorIndex:
    """The saved section vectors, read from disk as they're needed."""

    def __init__(self, path: Path):
        import numpy as np
        self.meta = _read_json(_meta_path(path))
        if self.meta is None or not path.exists():
            raise SemanticError(f"No section vectors at {path}. Build them with: "
                                "python -m battle_test.corpus embed")
        self._vectors = np.load(path, mmap_mode="r")
        self._ids = np.load(_ids_path(path))

    def nearest(self, queries, jurisdictions: list[str], limit: int,
                boost: dict[str, float] | None = None) -> list[list[int]]:
        """For each query vector, the ids of the `limit` closest sections in
        those jurisdictions, closest first. `boost` multiplies a
        jurisdiction's scores, to favour it."""
        import numpy as np
        spans = [(self.meta["jurisdictions"][j], (boost or {}).get(j, 1.0))
                 for j in jurisdictions if j in self.meta["jurisdictions"]]
        rows = sum(end - start for (start, end), _ in spans)
        if not rows:
            return [[] for _ in queries]
        scores = np.empty((rows, len(queries)), dtype=np.float32)
        ids = np.concatenate([self._ids[start:end] for (start, end), _ in spans])
        at = 0
        for (start, end), factor in spans:
            for a in range(start, end, SCORE_BLOCK):
                block = np.asarray(self._vectors[a:min(a + SCORE_BLOCK, end)], dtype=np.float32)
                scores[at:at + len(block)] = block @ queries.T * factor
                at += len(block)
        limit = min(limit, rows)
        results = []
        for q in range(len(queries)):
            column = scores[:, q]
            top = np.argpartition(-column, limit - 1)[:limit]
            results.append([int(ids[i]) for i in top[np.argsort(-column[top])]])
        return results


@lru_cache(maxsize=4)
def _open_vectors(path: Path) -> VectorIndex:
    # Shared by every run in the process: it's read-only, and opening it
    # once keeps the web UI's concurrent cases from each mapping the file.
    return VectorIndex(path)


class SemanticLaw:
    """A LawIndex that can also search by meaning. Everything else (keyword
    search, citation lookup) passes through to the index."""

    def __init__(self, law: LawIndex, vectors: VectorIndex, embedder: Embedder):
        self._law = law
        self._vectors = vectors
        self._embedder = embedder

    def __getattr__(self, name: str):
        return getattr(self._law, name)

    def semantic_search(self, queries: list[str], state: str, *, limit: int = 10) -> list[list[LawSection]]:
        """For each query, the sections of `state` and federal law closest to it in meaning."""
        if not queries:
            return []
        state = state.lower()
        nearest = self._vectors.nearest(self._embedder.queries(queries), [state, "federal"], limit,
                                        boost={state: STATE_BOOST})
        return [self._law.by_ids(ids) for ids in nearest]


def attach(law: LawIndex, config_path: Path, db_path: Path):
    """`law` with semantic search added, if the config turns it on. Otherwise
    `law` itself, and the pipeline searches by keyword alone."""
    cfg = load_search_config(config_path)
    if not cfg.semantic:
        return law
    vectors = _open_vectors(vectors_path(db_path))
    if vectors.meta["model"] != cfg.embedding_model:
        raise SemanticError(f"The section vectors were built with {vectors.meta['model']}, but the config "
                            f"asks for {cfg.embedding_model}. Rebuild them: python -m battle_test.corpus embed")
    if vectors.meta["index_built_at"] != law.meta().get("built_at", ""):
        raise SemanticError("The law index has been rebuilt since the section vectors were made. "
                            "Rebuild them: python -m battle_test.corpus embed")
    return SemanticLaw(law, vectors, Embedder(cfg.embedding_url, cfg.embedding_model))
