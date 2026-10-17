import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from battle_test.config import load_corpus_config
from battle_test.law_index import LawIndex
from battle_test.web.auth import AuthStore

try:
    import numpy  # noqa: F401
    import pyarrow as pa
    import pyarrow.parquet as pq

    from battle_test import corpus, evaluate, refresh, semantic
    from tests.test_semantic import FakeEmbedder, _row
except ImportError:  # numpy and pyarrow are optional
    pa = None

TEXAS = [
    ("TX1", "Tex. CPRC § 16.004", "A person must bring suit not later than four years after the day."),
    ("TX2", "Tex. CPRC § 15.002", "All lawsuits shall be brought in the county of the events."),
    ("TX3", "Tex. CPRC § 38.001", "A person may recover reasonable attorney's fees."),
]
UTAH = [("UT1", "Utah Code § 78B-2-309", "An action may be brought within six years after accrual.")]

CASE = """\
name = "tx_case"
state = "TX"
facts = "../facts.md"

[[expected]]
importance = "core"
citations = ["Tex. CPRC § 16.004"]
why = "Limitations."

[[expected]]
importance = "core"
citations = ["Tex. CPRC § 38.001"]
why = "Fees."
"""


@unittest.skipIf(pa is None, "numpy or pyarrow not installed")
class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.published = self.root / "published"
        self.published.mkdir()
        self.publish("v1", "2026-06-30", TEXAS, UTAH)
        self.config = self.root / "config.toml"
        self.config.write_text(f"""\
[corpus]
snapshot = "v1"
base_url = "{self.published.as_uri()}"
data_dir = "data"
jurisdictions = ["tx", "ut"]
document_types = ["statutes"]

[web]
host = "127.0.0.1"
port = 8001
data_dir = "web"
""", encoding="utf-8")
        self.cfg = load_corpus_config(self.config)
        with mock.patch("sys.stdout"):
            self.assertEqual(corpus.main(["--config", str(self.config), "build"]), 0)
        semantic.build(self.cfg.db_path, FakeEmbedder())  # the live law has vectors

        cases = self.root / "cases" / "eval"
        cases.mkdir(parents=True)
        (cases / "tx_case.toml").write_text(CASE, encoding="utf-8")
        (self.root / "cases" / "facts.md").write_text("Unpaid fees.", encoding="utf-8")
        patch = mock.patch.object(evaluate, "CASES_DIR", cases)
        patch.start()
        self.addCleanup(patch.stop)
        semantic._open_vectors.cache_clear()

    def tearDown(self):
        semantic._open_vectors.cache_clear()
        self.tmp.cleanup()

    def publish(self, version, date, texas, utah):
        """Put a snapshot where corpus.fetch_manifest looks, as Open US Law does."""
        files = []
        for name, rows in (("us_tx_statutes.parquet", texas), ("us_ut_statutes.parquet", utah)):
            path = self.published / version / name
            path.parent.mkdir(exist_ok=True)
            pq.write_table(pa.Table.from_pylist([_row(*r) if len(r) == 3 else _row(*r[:3], status=r[3])
                                                 for r in rows]), path)
            files.append({"file": name, "url": path.as_uri(), "bytes": path.stat().st_size,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        manifest = {"version": version, "snapshot_date": date, "files": files}
        (self.published / "index.json").write_text(json.dumps(manifest), encoding="utf-8")

    def publish_v2(self):
        texas = [
            (*TEXAS[0][:2], TEXAS[0][2], "repealed"),                      # § 16.004 repealed
            (*TEXAS[2][:2], "A person may recover reasonable attorney's fees, as amended."),  # § 15.002 gone
            ("TX4", "Tex. CPRC § 38.002", "To recover fees, the claimant must present the claim."),
        ]
        self.publish("v2", "2026-09-30", texas, UTAH)

    def refresh(self, embedder=None, queries=("four years after", "attorney fees")):
        with mock.patch("sys.stdout"):
            return refresh.refresh(self.cfg, self.config, queries_for=lambda case: list(queries),
                                   embedder=embedder or FakeEmbedder())

    def activity(self):
        store = AuthStore(self.root / "web" / "users.sqlite")
        try:
            return [(e.kind, e.detail) for e in reversed(store.events())]
        finally:
            store.close()

    def live_snapshot(self):
        with LawIndex(self.cfg.db_path) as law:
            return law.meta()["snapshot"]

    def test_nothing_new_is_logged_as_up_to_date(self):
        self.assertEqual(self.refresh(), "Up to date: Open US Law v1 (2026-06-30).")
        self.assertEqual(self.activity(), [("law checked", "Up to date: Open US Law v1 (2026-06-30).")])
        self.assertFalse(refresh.staging_dir(self.cfg).exists())

    def test_a_new_snapshot_is_built_beside_the_live_law_and_waits(self):
        self.publish_v2()
        embedder = FakeEmbedder()
        report = self.refresh(embedder)

        self.assertEqual(self.live_snapshot(), "v1")  # users still get the old law
        self.assertEqual(sorted(t[:18] for t in embedder.embedded), ["Tex. CPRC § 38.001", "Tex. CPRC § 38.002"])
        self.assertIn("| TX | 3 | 2 | 1 | 1 | 2 |", report)  # before, after, added, amended, gone
        self.assertIn("**TX, amended:** `Tex. CPRC § 38.001`", report)
        self.assertIn("2 sections embedded, 1 kept from the live law.", report)
        self.assertIn("| tx_case | 2 | 1 | 2 |", report)  # core found: live law, new law, core
        self.assertIn("Tex. CPRC § 16.004: in the index but not in force (repealed)", report)
        self.assertIn("TX: in-force sections went from 3 to 2 (-33%)", report)
        self.assertIn("Search finds fewer core authorities in the new law (1 against 2).", report)

        kind, detail = self.activity()[-1]
        self.assertEqual(kind, "law update ready")
        self.assertIn("1 sections added, 1 amended, 2 no longer in force. 3 warnings", detail)
        self.assertIn(refresh.PROMOTE_COMMAND, detail)

        again = self.refresh(embedder := FakeEmbedder())  # the next week, before anyone approves
        self.assertIn("waiting for approval", again)
        self.assertEqual(embedder.embedded, [])

    def test_promote_puts_the_new_law_live_and_rollback_brings_the_old_back(self):
        on = self.root / "semantic.toml"
        on.write_text('[search]\nsemantic = true\nembedding_model = "fake-embed"\n', encoding="utf-8")
        with LawIndex(self.cfg.db_path) as law:
            semantic.attach(law, on, self.cfg.db_path)  # as the web UI has the live vectors open

        self.publish_v2()
        self.refresh()
        with mock.patch("sys.stdout"):
            message = refresh.promote(self.cfg, self.config)
        self.assertIn("Open US Law v2 (2026-09-30) is live, replacing v1.", message)
        self.assertEqual(self.live_snapshot(), "v2")
        with LawIndex(self.cfg.db_path) as law:  # the new vectors, not the cached old ones
            self.assertIsInstance(semantic.attach(law, on, self.cfg.db_path), semantic.SemanticLaw)
        self.assertTrue((self.cfg.data_dir / "law-update-v2.md").exists())
        archived = list(refresh.archive_dir(self.cfg).iterdir())
        self.assertEqual(len(archived), 1)
        self.assertTrue(archived[0].name.endswith("-v1"))
        self.assertEqual(sorted(p.name for p in archived[0].iterdir()),
                         ["law-vectors-hashes.npy", "law-vectors-ids.npy", "law-vectors.json",
                          "law-vectors.npy", "law.sqlite"])
        self.assertEqual(self.activity()[-1][0], "law update promoted")
        self.assertEqual(self.refresh(), "Up to date: Open US Law v2 (2026-09-30).")

        with mock.patch("sys.stdout"):
            self.assertIn("Rolled back to Open US Law v1, replacing v2", refresh.rollback(self.cfg, self.config))
        self.assertEqual(self.live_snapshot(), "v1")
        with LawIndex(self.cfg.db_path) as law:
            self.assertIsInstance(semantic.attach(law, on, self.cfg.db_path), semantic.SemanticLaw)
        self.assertEqual(self.activity()[-1][0], "law rolled back")

    def test_promote_needs_a_finished_refresh(self):
        with self.assertRaisesRegex(SystemExit, "No new law is waiting"):
            refresh.promote(self.cfg, self.config)

        self.publish_v2()
        self.refresh()
        for path in semantic.vector_files(refresh.staging_dir(self.cfg) / "law.sqlite"):
            path.unlink()
        with self.assertRaisesRegex(SystemExit, "semantic search would stop"):
            refresh.promote(self.cfg, self.config)
        self.assertEqual(self.live_snapshot(), "v1")

    def test_a_swap_that_fails_part_way_is_undone(self):
        self.publish_v2()
        self.refresh()
        real_replace, calls = refresh.os.replace, []

        def replace(src, dst):  # the third move fails, as a file open on Windows does
            calls.append(src)
            if len(calls) == 3:
                raise PermissionError(13, "in use", str(src))
            real_replace(src, dst)

        with mock.patch.object(refresh.os, "replace", replace):
            with self.assertRaisesRegex(SystemExit, "Nothing was changed"):
                refresh.promote(self.cfg, self.config)
        self.assertEqual(self.live_snapshot(), "v1")
        self.assertTrue(all(p.exists() for p in semantic.vector_files(self.cfg.db_path)))
        self.assertFalse(refresh.archive_dir(self.cfg).exists() and any(refresh.archive_dir(self.cfg).iterdir()))
        with mock.patch("sys.stdout"):
            refresh.promote(self.cfg, self.config)  # still waiting, and works once nothing holds the file
        self.assertEqual(self.live_snapshot(), "v2")

    def test_a_failed_refresh_is_logged_and_leaves_the_live_law(self):
        self.publish_v2()
        manifest = json.loads((self.published / "index.json").read_text(encoding="utf-8"))
        manifest["files"][0]["sha256"] = "0" * 64
        (self.published / "index.json").write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "Checksum mismatch"):
            self.refresh()
        kind, detail = self.activity()[-1]
        self.assertEqual(kind, "law update failed")
        self.assertIn("Checksum mismatch", detail)
        self.assertEqual(self.live_snapshot(), "v1")

    def test_without_semantic_search_no_vectors_are_built(self):
        for path in semantic.vector_files(self.cfg.db_path):
            path.unlink()
        self.publish_v2()
        with mock.patch("sys.stdout"):
            report = refresh.refresh(self.cfg, self.config, queries_for=lambda case: ["fees"])
        self.assertIn("Not built: this setup has no semantic search.", report)
        with mock.patch("sys.stdout"):
            refresh.promote(self.cfg, self.config)
        self.assertEqual(self.live_snapshot(), "v2")
