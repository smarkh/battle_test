import tempfile
import textwrap
import unittest
from pathlib import Path

from battle_test import grounding
from battle_test.evaluate import CASES_DIR, load_case, load_cases, render_summary, score, validate
from battle_test.grounding import CitationCheck
from battle_test.law_index import LawSection
from battle_test.pipeline import CaseRun, Document


def section(citation, status="in_force"):
    return LawSection(citation, "ut", "statute", "Title", citation, status, "text", "https://x.gov", 2020)


SJ, LIMITS, INTEREST, UCC, OTHER = (section(c) for c in [
    "Utah R. Civ. P. 56", "Utah Code § 78B-2-309", "Utah Code § 15-1-1",
    "Utah Code § 70A-2-715", "Utah Code § 1-1-1",
])

CASE_TOML = textwrap.dedent("""
    name = "t"
    state = "UT"
    facts = "facts.md"

    [[expected]]
    importance = "core"
    citations = ["Utah R. Civ. P. 56"]
    why = "sj"

    [[expected]]
    importance = "core"
    citations = ["Utah Code § 78B-2-309", "Utah Code § 78B-2-307"]
    why = "limits"

    [[expected]]
    importance = "useful"
    citations = ["Utah Code § 15-1-1"]
    why = "interest"

    [[off_topic]]
    prefix = "Utah Code § 70A-2-"
    why = "goods"
""")


def check(s, status=grounding.VERIFIED):
    return CitationCheck(s.citation, status, s)


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "case.toml").write_text(CASE_TOML, encoding="utf-8")
        (root / "facts.md").write_text("facts", encoding="utf-8")
        self.case = load_case(root / "case.toml")

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, *docs):
        run = CaseRun("UT", 1, "p", "d")
        run.documents.extend(docs)
        return run

    def test_scores_provided_cited_and_off_topic(self):
        run = self.run_with(
            Document("Complaint", "plaintiff", "x [CITATION NEEDED: y]", True,
                     checks=(check(UCC), check(OTHER, grounding.IN_FORCE_NOT_PROVIDED)),
                     authorities=(SJ, LIMITS, UCC)),
            Document("Motion", "plaintiff", "x", True,
                     checks=(check(SJ), check(SJ), CitationCheck("Utah Code § 9-9-9", grounding.NOT_FOUND, None)),
                     unchecked=("550 U.S. 544",), authorities=(SJ,)),
        )
        s = score(run, self.case, seconds=120)

        self.assertEqual(s.core_provided, (2, 2))
        self.assertEqual(s.core_cited, (1, 2))  # SJ cited; limits given but not cited
        self.assertEqual(s.useful_cited, (0, 1))
        self.assertEqual(s.distinct_cited, ["Utah Code § 70A-2-715", "Utah Code § 1-1-1", "Utah R. Civ. P. 56"])
        self.assertEqual(s.on_target, (1, 3))
        self.assertEqual(s.off_topic_cited, ["Utah Code § 70A-2-715"])
        self.assertEqual(s.off_topic_provided, ["Utah Code § 70A-2-715"])
        self.assertEqual(s.problems, 1)
        self.assertEqual((s.unchecked_lines, s.placeholders), (1, 1))

    def test_any_alternative_counts(self):
        run = self.run_with(Document("Motion", "plaintiff", "", True, checks=(check(LIMITS),)))
        s = score(run, self.case)
        self.assertTrue(next(e for e in s.expected if e.why == "limits").cited)

    def test_problem_citations_do_not_count_as_cited(self):
        run = self.run_with(Document("Motion", "plaintiff", "", True,
                                     checks=(CitationCheck("x", grounding.NOT_IN_FORCE, SJ),)))
        self.assertEqual(score(run, self.case).core_cited, (0, 2))

    def test_summary_marks_each_expected_authority(self):
        run = self.run_with(Document("Motion", "plaintiff", "", True, checks=(check(SJ), check(OTHER)),
                                     authorities=(SJ, LIMITS)))
        md = render_summary([score(run, self.case)], "test", {"label": "test"})
        self.assertIn("✅ cited · *core* · `Utah R. Civ. P. 56`", md)
        self.assertIn("➖ given, not cited · *core*", md)
        self.assertIn("❌ missing · *useful* · `Utah Code § 15-1-1`", md)
        self.assertIn("provisional", md)  # not lawyer-reviewed
        self.assertIn("- `Utah Code § 1-1-1`", md)  # listed under "other authorities"

    def test_validate_flags_missing_and_inactive_authorities(self):
        class Law:
            def lookup(self, citation):
                return {"Utah R. Civ. P. 56": [SJ], "Utah Code § 78B-2-309": [LIMITS],
                        "Utah Code § 15-1-1": [section("Utah Code § 15-1-1", "repealed")]}.get(citation, [])

        problems = validate(self.case, Law())
        self.assertEqual(problems, [
            "Utah Code § 78B-2-307: not in the law index",
            "Utah Code § 15-1-1: in the index but not in force (repealed)",
        ])

    def test_bad_importance_is_rejected(self):
        bad = Path(self.tmp.name) / "bad.toml"
        bad.write_text(CASE_TOML.replace('importance = "useful"', 'importance = "nice"'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "core or useful"):
            load_case(bad)


class ShippedCasesTest(unittest.TestCase):
    def test_sample_cases_load_and_have_facts(self):
        cases = load_cases()
        self.assertEqual({c.state for c in cases}, {"UT", "CA", "TX"})
        for c in cases:
            with self.subTest(case=c.name):
                self.assertTrue(c.facts_path.exists())
                self.assertTrue(any(e.importance == "core" for e in c.expected))
        self.assertTrue(CASES_DIR.exists())


if __name__ == "__main__":
    unittest.main()
