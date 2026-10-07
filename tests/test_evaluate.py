import tempfile
import textwrap
import unittest
from pathlib import Path

from battle_test import grounding
from battle_test.evaluate import CASES_DIR, load_case, load_cases, render_research, render_summary, score, validate
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

    def test_summary_shows_cost_repeats_and_a_total(self):
        def run(cost):
            r = self.run_with(Document("Motion", "plaintiff", "x", True, checks=(check(SJ),), authorities=(SJ,)))
            r.usage = {"m": {"calls": 8, "input_tokens": 39_000, "output_tokens": 7_000}}
            r.cost_usd = cost
            return r

        scores = [score(run(0.04), self.case, repeat=1), score(run(0.05), self.case, repeat=2)]
        md = render_summary(scores, "test", {"label": "test"})
        self.assertIn("| Minutes | Cost |", md)
        self.assertIn("| t | 0/2 | 1/2 | 1/2 |", md)
        self.assertIn("| t #2 | 0/2 | 1/2 | 1/2 |", md)
        self.assertRegex(md, r"\| \*\*Total\*\* \| 0/4 \| 2/4 \| \*\*2/4\*\* \|.*\| \$0\.090 \|")
        self.assertIn("**`m` usage, all runs:** 16 calls, 78,000 tokens in, 14,000 out.", md)
        self.assertIn("## t #2 (UT)", md)

        # A model with no price gives "?", never a total that leaves it out.
        unpriced = [scores[0], score(run(None), self.case, repeat=2)]
        self.assertRegex(render_summary(unpriced, "test", {}), r"\| \*\*Total\*\* \|.*\| \? \|")
        # Nothing reported at all (the old behaviour): a dash, and no total row for one run.
        plain = render_summary([score(self.run_with(), self.case)], "test", {})
        self.assertIn("| 0 | – |", plain)
        self.assertNotIn("**Total**", plain)

    def test_found_by_search_counts_candidates_from_either_side(self):
        run = self.run_with(Document("Motion", "plaintiff", "", True))
        run.candidates = {"plaintiff": ["Utah Code § 15-1-1"], "defendant": ["Utah Code § 78B-2-307"]}
        s = score(run, self.case)
        self.assertEqual(s.core_found, (1, 2))  # the limits entry, via its alternative citation
        md = render_summary([s], "test", {"label": "test"})
        self.assertIn("🔍 found by search, not selected · *useful* · `Utah Code § 15-1-1`", md)

    def test_research_report_totals(self):
        md = render_research([(self.case, ["limitation of actions"], ["Utah R. Civ. P. 56"])], "test")
        self.assertIn("**Total: 1/3 expected authorities found by search.**", md)
        self.assertIn("🔍 found · *core* · `Utah R. Civ. P. 56`", md)
        self.assertIn("- limitation of actions", md)
        self.assertNotIn("Model usage", md)

    def test_research_report_shows_usage_and_cost(self):
        usage = {"m": {"calls": 3, "input_tokens": 7_500, "output_tokens": 300}}
        md = render_research([(self.case, ["q"], [])], "test", usage, 0.002)
        self.assertIn("**Model usage:** 3 calls, 7,500 tokens in, 300 out, estimated cost $0.002", md)
        self.assertLess(md.index("**Total:"), md.index("**Model usage:"))
        self.assertLess(md.index("**Model usage:"), md.index("## t:"))

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
        self.assertIn("❌ never found by search · *useful* · `Utah Code § 15-1-1`", md)
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
