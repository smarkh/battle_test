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
            # Open US Law's Alaska statute statuses are wrong; see corpus._act_status.
            "us_ak_statutes.parquet": [
                _row("AK1", "Alaska Stat. § 45.50.471", "Unlawful acts and practices.",
                     "(a) Unfair or deceptive acts are unlawful. (b) [Repealed, § 1 ch 2 SLA 1990.]",
                     status="reserved"),
                _row("AK2", "Alaska Stat. § 06.05.910", "Construction with subsequent legislation.",
                     "No part of this chapter shall be considered impliedly repealed.", status="repealed"),
                _row("AK3", "Alaska Stat. § 1.01.010", "Old section.", "[Repealed, § 3 ch 4 SLA 1999.]",
                     status="repealed"),
            ],
            "us_ak_constitutions.parquet": [
                _row("AKC1", "Alaska Const. art. VI, § 5", "Section 5", "[Repealed 1998]",
                     status="repealed", doc_type="constitution"),
            ],
        }
        paths = []
        for name, rows in files.items():
            path = root / name
            pq.write_table(pa.Table.from_pylist(rows), path)
            paths.append(path)
        cfg = CorpusConfig("vTEST", "http://unused", root, ("ut", "ca", "federal"), ("statutes",))
        ak_headings = {"titles": {"45": "Trade and Commerce"},
                       "chapters": {"45.50": "Competitive Practices, Regulation of Competition, Consumer Protection"}}
        build_index(cfg, {"snapshot_date": "2026-08-14"}, paths, ak_headings)
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

    def test_alaska_statute_statuses_are_corrected(self):
        status = {c: self.index.lookup(c)[0].act_status for c in [
            "Alaska Stat. § 45.50.471", "Alaska Stat. § 06.05.910", "Alaska Stat. § 1.01.010",
            "Alaska Const. art. VI, § 5"]}
        self.assertEqual(status, {
            "Alaska Stat. § 45.50.471": "in_force",  # "reserved": one subsection repealed
            "Alaska Stat. § 06.05.910": "in_force",  # "repealed": only mentions repeal
            "Alaska Stat. § 1.01.010": "repealed",  # really is just a repeal note
            "Alaska Const. art. VI, § 5": "repealed",  # constitution statuses are trusted
        })
        self.assertEqual([s.citation for s in self.index.search("unfair deceptive", "ak")],
                         ["Alaska Stat. § 45.50.471"])

    def test_alaska_statutes_get_akleg_chapter_names(self):
        # The section's own title ("Unlawful acts and practices.") doesn't say it.
        self.assertEqual([s.citation for s in self.index.search("consumer protection", "ak")],
                         ["Alaska Stat. § 45.50.471"])
        heading = self.index._db.execute(
            "SELECT heading FROM sections WHERE citation = 'Alaska Stat. § 06.05.910'").fetchone()[0]
        self.assertEqual(heading, "Code")  # no name known: the dataset's heading stays

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


class AlaskaHeadingsTest(unittest.TestCase):
    # Shaped like akleg.gov's table of contents: the main page lists titles
    # (with a space inside the call), a title lists chapters or parts.
    PAGES = {
        "": '<a onclick="loadTOC( 9);" href="#">Title 9. Code of Civil Procedure. <BR> </a>'
            '<a onclick="loadTOC(45);" href="#">Title 45. Trade and Commerce. <BR> </a>',
        "9": '<a onclick=loadTOC("09"); href=x >Title 9. Code of Civil Procedure. <BR> </a>'
             '<li><a onclick=loadTOC("09.10"); href=x ><b>Chapter 10. Limitations of Actions.<BR></h6> </a></li>',
        "45": '<a onclick=loadTOC("45"); href=x >Title 45. Trade and Commerce. <BR> </a>'
              '<li><a onclick=loadTOC("45p2"); href=x ><b>Part 2. Trade Practices.<BR></h6> </a></li>',
        "45p2": '<a onclick=loadTOC("45p2"); href=x >Part 2. Trade Practices. </a>'
                '<li><a onclick=loadTOC("45.50"); href=x ><b>Chapter 50. Competitive Practices, '
                'Regulation of Competition, Consumer Protection.<BR></h6> </a></li>',
    }

    def test_fetch_reads_titles_chapters_and_parts(self):
        from battle_test.corpus import fetch_ak_headings
        names = fetch_ak_headings(lambda title="": self.PAGES[title], pause=0)
        self.assertEqual(names["titles"], {"09": "Code of Civil Procedure", "45": "Trade and Commerce"})
        self.assertEqual(names["chapters"], {
            "09.10": "Limitations of Actions",
            "45.50": "Competitive Practices, Regulation of Competition, Consumer Protection"})

    def test_heading_for_a_section(self):
        from battle_test.corpus import _ak_heading
        names = {"titles": {"09": "Code of Civil Procedure"}, "chapters": {"09.10": "Limitations of Actions"}}
        self.assertEqual(_ak_heading("09.10.053", names), "Code of Civil Procedure / Limitations of Actions")
        self.assertEqual(_ak_heading("09.99.010", names), "Code of Civil Procedure")
        self.assertIsNone(_ak_heading("12.01.010", names))
        self.assertIsNone(_ak_heading("56", names))  # a court rule number


if __name__ == "__main__":
    unittest.main()
