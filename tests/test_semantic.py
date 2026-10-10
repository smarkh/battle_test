import tempfile
import unittest
from pathlib import Path
from unittest import mock

from battle_test.config import CorpusConfig
from battle_test.law_index import LawIndex

try:
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    from battle_test import semantic
    from battle_test.corpus import build_index
except ImportError:  # numpy and pyarrow are optional
    np = None


def _row(act_id, citation, text, *, status="in_force"):
    return {
        "act_id": act_id, "citation": citation, "document_type": "statute", "title_name": "Code",
        "chapter_name": "None", "section_title": citation, "display_path": citation, "act_status": status,
        "text": text, "source_url": "https://example.gov", "last_amended_year": 2020,
    }


# Each topic is a direction. A text points along the topics whose words it has,
# so "time limit to sue" and "four-year limitations period" share no words
# but land together, which is what a real embedding model does.
TOPICS = [("limitations", "time limit to sue", "years after"), ("venue", "county", "where to file"),
          ("attorney", "fees", "lawyer costs")]


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, fail_after=None):
        self.embedded = []  # every document text, in the order it was asked for
        self.fail_after = fail_after

    def _vectors(self, texts):
        rows = [[float(any(word in t.lower() for word in topic)) for topic in TOPICS] + [0.01] for t in texts]
        m = np.asarray(rows, dtype=np.float32)
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    def documents(self, texts):
        if self.fail_after is not None and len(self.embedded) + len(texts) > self.fail_after:
            raise semantic.SemanticError("Ollama stopped")
        self.embedded += texts
        return self._vectors(texts)

    def queries(self, texts):
        return self._vectors(texts)


@unittest.skipIf(np is None, "numpy or pyarrow not installed")
class SemanticSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        files = {
            "us_tx_statutes.parquet": [
                _row("TX1", "Tex. CPRC § 16.004", "A person must bring suit not later than four years after the day."),
                _row("TX2", "Tex. CPRC § 15.002", "All lawsuits shall be brought in the county of the events."),
                _row("TX3", "Tex. CPRC § 38.001", "A person may recover reasonable attorney's fees."),
                _row("TX4", "Tex. CPRC § 16.999", "Old limitations rule, years after accrual.", status="repealed"),
                _row("TX5", "Tex. CPRC § 1.001", "This code is a recodification."),
            ],
            "us_ut_statutes.parquet": [
                _row("UT1", "Utah Code § 78B-2-309", "An action may be brought within six years after accrual."),
            ],
            "us_federal_statutes.parquet": [
                _row("F1", "28 U.S.C. § 1658", "A civil action may not be commenced later than 4 years after."),
            ],
        }
        paths = []
        for name, rows in files.items():
            pq.write_table(pa.Table.from_pylist(rows), self.root / name)
            paths.append(self.root / name)
        self.cfg = CorpusConfig("vTEST", "http://unused", self.root, ("tx", "ut", "federal"), ("statutes",))
        build_index(self.cfg, {"snapshot_date": "2026-08-14"}, paths)
        self.db = self.cfg.db_path
        self.law = LawIndex(self.db)
        semantic._open_vectors.cache_clear()

    def tearDown(self):
        self.law.close()
        semantic._open_vectors.cache_clear()  # lets go of the vectors file, so the folder can be removed
        self.tmp.cleanup()

    def searcher(self):
        return semantic.SemanticLaw(self.law, semantic.VectorIndex(semantic.vectors_path(self.db)), FakeEmbedder())

    def config(self, text):
        path = self.root / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_finds_a_section_that_shares_no_words_with_the_query(self):
        meta = semantic.build(self.db, FakeEmbedder())
        self.assertEqual((meta["count"], meta["dimensions"], meta["model"]), (6, 4, "fake-embed"))
        self.assertEqual(self.law.search("time limit to sue", "tx"), [])  # keyword search has nothing

        hits = self.searcher().semantic_search(["time limit to sue", "where to file"], "tx", limit=2)
        self.assertEqual({s.citation for s in hits[0]}, {"Tex. CPRC § 16.004", "28 U.S.C. § 1658"})
        self.assertEqual(hits[1][0].citation, "Tex. CPRC § 15.002")
        self.assertEqual(self.searcher().semantic_search([], "tx"), [])

    def test_state_law_ranks_ahead_of_equally_close_federal_law(self):
        semantic.build(self.db, FakeEmbedder())
        vectors = semantic.VectorIndex(semantic.vectors_path(self.db))
        query = FakeEmbedder().queries(["time limit to sue"])
        cite = lambda ids: self.law.by_ids(ids)[0].citation

        self.assertEqual(cite(vectors.nearest(query, ["federal", "tx"], 1, boost={"tx": 1.04})[0]),
                         "Tex. CPRC § 16.004")
        self.assertEqual(cite(vectors.nearest(query, ["federal", "tx"], 1, boost={"federal": 1.04})[0]),
                         "28 U.S.C. § 1658")
        hits = self.searcher().semantic_search(["time limit to sue"], "tx", limit=2)[0]
        self.assertEqual([s.citation for s in hits], ["Tex. CPRC § 16.004", "28 U.S.C. § 1658"])

    def test_only_the_state_and_federal_law_in_force_is_searched(self):
        semantic.build(self.db, FakeEmbedder())
        cites = [s.citation for s in self.searcher().semantic_search(["time limit to sue"], "tx", limit=10)[0]]
        self.assertEqual(len(cites), 5)                       # every Texas and federal section in force
        self.assertNotIn("Utah Code § 78B-2-309", cites)      # another state
        self.assertNotIn("Tex. CPRC § 16.999", cites)         # repealed, so never embedded
        self.assertEqual(self.searcher().semantic_search(["time limit to sue"], "ca", limit=10)[0][0].citation,
                         "28 U.S.C. § 1658")                  # a state with no sections still gets federal law

    def test_everything_else_passes_through_to_the_index(self):
        semantic.build(self.db, FakeEmbedder())
        law = self.searcher()
        self.assertEqual(law.meta()["snapshot"], "vTEST")
        self.assertEqual([s.citation for s in law.lookup("Tex. CPRC § 38.001")], ["Tex. CPRC § 38.001"])
        self.assertEqual([s.citation for s in law.search("attorney fees", "tx")], ["Tex. CPRC § 38.001"])

    def test_an_interrupted_build_resumes_where_it_stopped(self):
        with mock.patch.object(semantic, "BATCH", 2), mock.patch.object(semantic, "CHECKPOINT_BATCHES", 1):
            with self.assertRaises(semantic.SemanticError):
                semantic.build(self.db, FakeEmbedder(fail_after=4))
            self.assertFalse(semantic.vectors_path(self.db).exists())  # nothing half-built is ever used
            second = FakeEmbedder()
            semantic.build(self.db, second)
        self.assertEqual(len(second.embedded), 2)  # only the two sections the first run didn't reach
        cites = [s.citation for s in self.searcher().semantic_search(["lawyer costs"], "tx", limit=1)[0]]
        self.assertEqual(cites, ["Tex. CPRC § 38.001"])
        self.assertEqual([p.name for p in self.root.glob("*building*")], [])

    def test_a_build_for_a_different_model_starts_again(self):
        with mock.patch.object(semantic, "BATCH", 2), mock.patch.object(semantic, "CHECKPOINT_BATCHES", 1):
            with self.assertRaises(semantic.SemanticError):
                semantic.build(self.db, FakeEmbedder(fail_after=4))
            other = FakeEmbedder()
            other.model = "another-model"
            semantic.build(self.db, other)
        self.assertEqual(len(other.embedded), 6)

    def test_attach_follows_the_config(self):
        off = self.config('[ollama]\nurl = "http://o:1"\n')
        self.assertIs(semantic.attach(self.law, off, self.db), self.law)

        on = self.config('[ollama]\nurl = "http://o:1/"\n[search]\nsemantic = true\nembedding_model = "fake-embed"\n')
        with self.assertRaisesRegex(semantic.SemanticError, "corpus embed"):  # no vectors yet
            semantic.attach(self.law, on, self.db)
        semantic.build(self.db, FakeEmbedder())
        law = semantic.attach(self.law, on, self.db)
        self.assertIsInstance(law, semantic.SemanticLaw)
        self.assertEqual((law._embedder.url, law._embedder.model), ("http://o:1", "fake-embed"))

        wrong = self.config('[search]\nsemantic = true\nembedding_model = "nomic-embed-text"\n')
        with self.assertRaisesRegex(semantic.SemanticError, "built with fake-embed"):
            semantic.attach(self.law, wrong, self.db)
        with self.assertRaisesRegex(ValueError, "embedding_model"):
            semantic.attach(self.law, self.config("[search]\nsemantic = true\n"), self.db)

    def test_vectors_from_before_an_index_rebuild_are_refused(self):
        semantic.build(self.db, FakeEmbedder())
        on = self.config('[search]\nsemantic = true\nembedding_model = "fake-embed"\n')

        class Rebuilt:
            def meta(self):
                return {"built_at": "2030-01-01T00:00:00"}

        with self.assertRaisesRegex(semantic.SemanticError, "rebuilt since"):
            semantic.attach(Rebuilt(), on, self.db)


@unittest.skipIf(np is None, "numpy or pyarrow not installed")
class EmbedderTest(unittest.TestCase):
    def embedded(self, model, kind, text):
        embedder = semantic.Embedder("http://o:1", model)
        with mock.patch.object(embedder, "_embed", side_effect=lambda texts: texts):
            return getattr(embedder, kind)([text])[0]

    def test_each_model_family_gets_its_own_prefixes(self):
        self.assertEqual(self.embedded("nomic-embed-text:latest", "documents", "x"), "search_document: x")
        self.assertEqual(self.embedded("nomic-embed-text", "queries", "x"), "search_query: x")
        self.assertEqual(self.embedded("qwen3-embedding:0.6b", "documents", "x"), "x")
        self.assertTrue(self.embedded("qwen3-embedding:0.6b", "queries", "x").endswith("Query: x"))
        self.assertEqual(self.embedded("some-other-model", "queries", "x"), "x")

    def test_long_sections_are_cut_and_carry_their_citation(self):
        text = semantic.document_text("Utah Code § 1", "Title 1 > Chapter 1", "Short title", "word " * 2000)
        self.assertTrue(text.startswith("Utah Code § 1. Title 1 > Chapter 1. Short title.\n"))
        self.assertLess(len(text), semantic.EMBED_CHARS + 100)

    def test_ollama_not_running_is_a_model_error(self):
        from battle_test.models import ModelError
        with self.assertRaisesRegex(ModelError, "Could not reach Ollama"):
            semantic.Embedder("http://127.0.0.1:9", "nomic-embed-text", timeout_seconds=2).queries(["x"])

    def test_localhost_is_called_by_address(self):
        self.assertEqual(semantic.Embedder("http://localhost:11434", "m").url, "http://127.0.0.1:11434")
        self.assertEqual(semantic.Embedder("http://host.docker.internal:11434", "m").url,
                         "http://host.docker.internal:11434")

    def replies(self, *replies):
        """Patch Ollama to give these replies in turn: an HTTP status, or a list of vectors."""
        import io
        import json
        import urllib.error

        def reply(request, timeout):
            r = next(queue)
            if isinstance(r, int):
                raise urllib.error.HTTPError(request.full_url, r, "refused", {}, io.BytesIO(b"no reason given"))
            return io.BytesIO(json.dumps({"embeddings": r}).encode())

        queue = iter(replies)
        return mock.patch("urllib.request.urlopen", side_effect=reply)

    def test_a_refused_request_is_sent_again(self):
        embedder = semantic.Embedder("http://o:1", "nomic-embed-text")
        with self.replies(400, [[3.0, 4.0]]) as urlopen, mock.patch.object(semantic, "RETRY_WAIT_SECONDS", 0):
            vectors = embedder.queries(["x"])
        self.assertEqual(urlopen.call_count, 2)
        np.testing.assert_allclose(vectors, [[0.6, 0.8]])

    def test_a_request_refused_every_time_is_an_error(self):
        embedder = semantic.Embedder("http://o:1", "nomic-embed-text")
        with self.replies(400, 400, 400) as urlopen, mock.patch.object(semantic, "RETRY_WAIT_SECONDS", 0):
            with self.assertRaisesRegex(semantic.SemanticError, "HTTP 400.*no reason given"):
                embedder.queries(["x"])
        self.assertEqual(urlopen.call_count, semantic.ATTEMPTS)

    def test_a_missing_model_is_not_retried(self):
        embedder = semantic.Embedder("http://o:1", "nomic-embed-text")
        with self.replies(404) as urlopen:
            with self.assertRaisesRegex(semantic.SemanticError, "ollama pull nomic-embed-text"):
                embedder.queries(["x"])
        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
