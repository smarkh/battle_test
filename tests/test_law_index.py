import tempfile
import unittest
from pathlib import Path

from battle_test.citations import find_citation_refs
from battle_test.config import CorpusConfig
from battle_test.law_index import LawIndex, citation_key, fts_query

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from battle_test.corpus import build_index
except ImportError:  # pyarrow is only needed to build the corpus
    pa = None


def _row(act_id, citation, section_title, text, *, status="in_force", url="https://example.gov",
         chapter="None", doc_type="statute"):
    return {
        "act_id": act_id, "citation": citation, "document_type": doc_type,
        "title_name": "Code", "chapter_name": chapter, "section_title": section_title,
        "display_path": citation, "act_status": status, "text": text, "source_url": url,
        "last_amended_year": 2020,
    }


@unittest.skipIf(pa is None, "pyarrow not installed")
class LawIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        files = {
            "us_ut_statutes.parquet": [
                _row("UT1", "Utah Code § 78B-2-309", "§ 78B-2-309. Within six years",
                     "An action upon a contract founded upon an instrument in writing.",
                     chapter="Statutes of Limitations"),
                _row("UT2", "Utah Code § 1-1-1", "§ 1-1-1. Old rule", "Repealed text.", status="repealed"),
                _row("UT3", "Utah Code § 2-2-2", "§ 2-2-2. Aggregator copy", "Summary judgment text.", url=""),
            ],
            "us_ut_court_rules.parquet": [
                _row("UTR56", "Utah R. Civ. P. 56", "Rule 56. Summary judgment",
                     "A party may move for summary judgment.", doc_type="court_rule"),
            ],
            "us_federal_court_rules.parquet": [
                _row("FR56", "Fed. R. Civ. P. 56", "Rule 56. Summary Judgment",
                     "A party may move for summary judgment.", doc_type="court_rule"),
            ],
            "us_ca_statutes.parquet": [
                _row("CA1", "Cal. CCP § 437c", "Section 437c", "Summary judgment in California."),
            ],
        }
        paths = []
        for name, rows in files.items():
            path = root / name
            pq.write_table(pa.Table.from_pylist(rows), path)
            paths.append(path)
        cfg = CorpusConfig("vTEST", "http://unused", root, ("ut", "ca", "federal"), ("statutes",))
        build_index(cfg, {"snapshot_date": "2026-08-14"}, paths)
        cls.index = LawIndex(cfg.db_path)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls.tmp.cleanup()

    def test_search_is_limited_to_state_and_federal(self):
        cites = [s.citation for s in self.index.search("summary judgment", "ut")]
        self.assertIn("Utah R. Civ. P. 56", cites)
        self.assertIn("Fed. R. Civ. P. 56", cites)
        self.assertNotIn("Cal. CCP § 437c", cites)

    def test_state_rule_outranks_identical_federal_rule(self):
        cites = [s.citation for s in self.index.search("summary judgment rule", "ut")]
        self.assertLess(cites.index("Utah R. Civ. P. 56"), cites.index("Fed. R. Civ. P. 56"))

    def test_chapter_heading_is_searchable(self):
        cites = [s.citation for s in self.index.search("statutes of limitations", "ut")]
        self.assertEqual(cites[0], "Utah Code § 78B-2-309")

    def test_search_excludes_repealed_unless_asked(self):
        self.assertEqual(self.index.search("repealed", "ut"), [])
        found = self.index.search("repealed", "ut", include_inactive=True)
        self.assertEqual([s.citation for s in found], ["Utah Code § 1-1-1"])

    def test_lookup_ignores_formatting_and_reports_status(self):
        (hit,) = self.index.lookup("utah code §78B-2-309")
        self.assertTrue(hit.in_force)
        (old,) = self.index.lookup("Utah Code § 1-1-1")
        self.assertFalse(old.in_force)
        self.assertEqual(self.index.lookup("Utah Code Ann. § 78-27-102"), [])

    def test_resolve_matches_citation_spellings(self):
        for text, state, expected in [
            ("Utah Code Ann. § 78B-2-309(1)", "ut", ["Utah Code § 78B-2-309"]),
            ("Rule 56 of the Utah Rules of Civil Procedure", "ut", ["Utah R. Civ. P. 56"]),
            ("FRCP 56", "ut", ["Fed. R. Civ. P. 56"]),
            ("Code of Civil Procedure section 437c", "ca", ["Cal. CCP § 437c"]),
            ("Cal. Civ. Proc. Code § 437c", "ca", ["Cal. CCP § 437c"]),
            ("§ 78B-2-309", "ut", ["Utah Code § 78B-2-309"]),
            ("Utah Code § 1-1-1", "ut", ["Utah Code § 1-1-1"]),  # found, though repealed
            ("Utah Code § 9-9-9", "ut", []),
            ("Cal. Civ. Proc. Code § 437c", "ut", []),  # other state's law
        ]:
            with self.subTest(text=text, state=state):
                (ref,) = find_citation_refs(text)
                self.assertEqual([s.citation for s in self.index.resolve(ref, state)], expected)

    def test_missing_source_url_is_not_official(self):
        (hit,) = self.index.lookup("Utah Code § 2-2-2")
        self.assertFalse(hit.official_source)

    def test_meta_records_snapshot(self):
        meta = self.index.meta()
        self.assertEqual((meta["snapshot"], meta["snapshot_date"]), ("vTEST", "2026-08-14"))


class QueryHelpersTest(unittest.TestCase):
    def test_citation_key_keeps_hyphens(self):
        self.assertEqual(citation_key("Utah Code § 78B-2-309"), citation_key("utah code §78b-2-309"))
        self.assertNotEqual(citation_key("78B-2-309"), citation_key("78B-23-09"))

    def test_fts_query_drops_stopwords_and_quotes_words(self):
        self.assertEqual(fts_query("Statute of limitations for the contract"),
                         '"statute" OR "limitations" OR "contract"')
        self.assertEqual(fts_query("of the"), "")

    def test_missing_index_explains_how_to_build(self):
        with self.assertRaisesRegex(FileNotFoundError, "battle_test.corpus build"):
            LawIndex(Path("does/not/exist.sqlite"))


if __name__ == "__main__":
    unittest.main()
