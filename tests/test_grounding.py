import json
import unittest

from battle_test import grounding, prompts
from battle_test.law_index import LawSection


def section(citation, jurisdiction="ut", status="in_force"):
    return LawSection(citation, jurisdiction, "statute", f"Title of {citation}", citation, status,
                      "word " * 300, "https://example.gov", 2020)


class FakeLaw:
    def __init__(self, results=None, sections=()):
        self.results = results or {}
        self.sections = list(sections)

    def search(self, text, state, *, limit=10):
        return self.results.get(text, [])[:limit]

    def resolve(self, ref, state):
        return [s for s in self.sections if s.citation.lower().endswith(ref.section)]

    def lookup(self, citation):
        return [s for s in self.sections if s.citation == citation]


class ResearchAndSelectionTest(unittest.TestCase):
    def test_parse_json_list_tolerates_bad_replies(self):
        self.assertEqual(grounding.parse_json_list('{"queries": ["a"]}', "queries"), ["a"])
        self.assertEqual(grounding.parse_json_list("not json", "queries"), [])
        self.assertEqual(grounding.parse_json_list('["a"]', "queries"), [])
        self.assertEqual(grounding.parse_json_list('{"queries": "a"}', "queries"), [])

    def test_research_cleans_dedupes_and_adds_standard_queries(self):
        standard = grounding.STANDARD_QUERIES[0]
        reply = json.dumps({"queries": ["x", " x ", "", 3, "y", standard]})
        queries = grounding.research(lambda task: reply, "task")
        self.assertEqual(queries[:3], ["x", "y", standard])  # model's own first
        self.assertEqual(queries[3:], grounding.STANDARD_QUERIES[1:])  # no duplicate of `standard`
        self.assertEqual(grounding.research(lambda task: "not json", "task"), grounding.STANDARD_QUERIES)

    def test_research_uses_the_states_own_standard_queries(self):
        reply = json.dumps({"queries": ["x"]})
        self.assertEqual(grounding.research(lambda task: reply, "task", "ak"),
                         ["x", *grounding.STATE_STANDARD_QUERIES["ak"]])
        for state in ("ut", "UT", "ca", "tx", None):
            with self.subTest(state=state):
                self.assertEqual(grounding.research(lambda task: reply, "task", state),
                                 ["x", *grounding.STANDARD_QUERIES])

    def test_summary_judgment_rule_per_state(self):
        law = FakeLaw(sections=[section("Utah R. Civ. P. 56"), section("Cal. CCP § 437c", "ca"),
                                section("Tex. R. Civ. P. 166a", "tx", status="repealed")])
        self.assertEqual([s.citation for s in grounding.summary_judgment_rule(law, "ut")],
                         ["Utah R. Civ. P. 56"])
        self.assertEqual([s.citation for s in grounding.summary_judgment_rule(law, "ca")],
                         ["Cal. CCP § 437c"])
        self.assertEqual(grounding.summary_judgment_rule(law, "tx"), [])  # not in force

    def test_candidates_interleave_by_rank(self):
        a1, a2, b1 = section("A1"), section("A2"), section("B1")
        law = FakeLaw({"a": [a1, a2], "b": [b1, a1]})
        self.assertEqual(grounding.gather_candidates(law, ["a", "b"], "ut"), [a1, b1, a2])
        self.assertEqual(grounding.gather_candidates(law, [], "ut"), [])

    def test_candidates_alternate_keyword_and_semantic_results(self):
        k1, k2, s1, s2, both = (section(c) for c in ("K1", "K2", "S1", "S2", "BOTH"))

        class SemanticLaw(FakeLaw):
            def semantic_search(self, queries, state, *, limit=10):
                return [{"a": [s1, both, s2], "b": []}[q][:limit] for q in queries]

        law = SemanticLaw({"a": [k1, both], "b": [k2]})
        # Query a: keyword K1, BOTH; semantic S1, BOTH, S2. Query b: keyword K2 only.
        self.assertEqual(grounding.gather_candidates(law, ["a", "b"], "ut"), [k1, k2, s1, both, s2])

    def test_a_section_stays_with_the_topic_that_ranked_it_highest(self):
        a1, a2, b1, shared = section("A1"), section("A2"), section("B1"), section("SHARED")
        law = FakeLaw({"a": [a1, shared, a2], "b": [shared, b1], "c": [a1]})
        self.assertEqual(grounding.gather_by_topic(law, ["a", "b", "c"], "ut"),
                         [("a", [a1, a2]), ("b", [shared, b1]), ("c", [])])

    def test_candidate_lines_are_numbered_and_cut_short(self):
        text = grounding.candidate_list([section("A"), section("B")])
        self.assertIn("[1] A — Title of A\n", text)
        self.assertIn("[2] B — Title of B\n", text)
        self.assertEqual(text.count("word"), 2 * grounding.CANDIDATE_WORDS)

    def select(self, topics, replies, limit=10):
        """Run select() with the model answering each topic from `replies`. Returns (picks, prompts)."""
        asked = []

        def ask(task):
            asked.append(task)
            return replies[task.split("|")[0]]
        picks = grounding.select(ask, lambda topic, candidates: f"{topic}|{candidates}", topics, limit)
        return picks, asked

    def test_select_asks_once_per_topic_and_takes_one_pick_each(self):
        a1, a2, b1, c1 = section("A1"), section("A2"), section("B1"), section("C1")
        topics = [("a", [a1, a2]), ("b", [b1]), ("empty", []), ("c", [c1])]
        picks, asked = self.select(topics, {"a": '{"selected": [2, 1]}', "b": '{"selected": []}',
                                            "c": '{"selected": [99, "1", true, 1]}'})
        self.assertEqual(picks, [a2, c1])                # one each, none for b, c's first valid number
        self.assertEqual(len(asked), 3)                  # a topic with no candidates isn't asked about
        self.assertIn("[2] A2", asked[0])
        self.assertNotIn("B1", asked[0])                 # each prompt lists only its own topic's candidates

    def test_select_cut_alternates_the_models_topics_and_the_standard_ones(self):
        own = [(f"own {n}", [section(f"OWN{n}")]) for n in range(3)]
        standard = [(q, [section(f"STD{n}")]) for n, q in enumerate(grounding.STANDARD_QUERIES[:3])]
        replies = {q: '{"selected": [1]}' for q, _ in own + standard}
        picks, _ = self.select(own + standard, replies, limit=4)
        self.assertEqual([s.citation for s in picks], ["OWN0", "STD0", "OWN1", "STD1"])

    def test_select_counts_a_states_own_standard_queries_as_standard(self):
        own = [(f"own {n}", [section(f"OWN{n}")]) for n in range(3)]
        standard = [(q, [section(f"STD{n}", "ak")])
                    for n, q in enumerate(grounding.STATE_STANDARD_QUERIES["ak"][:3])]
        replies = {q: '{"selected": [1]}' for q, _ in own + standard}
        picks, _ = self.select(own + standard, replies, limit=4)
        self.assertEqual([s.citation for s in picks], ["OWN0", "STD0", "OWN1", "STD1"])

    def test_select_with_topics_says_which_topic_each_pick_is_for(self):
        a1, a2, b1 = section("A1"), section("A2"), section("B1")
        topics = [("a", [a1, a2]), ("b", [b1])]
        asked = lambda replies: lambda task: replies[task.split("|")[0]]  # noqa: E731
        task_for = lambda topic, candidates: f"{topic}|{candidates}"  # noqa: E731
        self.assertEqual(grounding.select_with_topics(asked({"a": '{"selected": [2]}', "b": '{"selected": [1]}'}),
                                                      task_for, topics, 10), [("a", a2), ("b", b1)])
        # The fallback keeps each candidate's topic too.
        self.assertEqual(grounding.select_with_topics(asked({"a": "x", "b": "x"}), task_for, topics, 2),
                         [("a", a1), ("b", b1)])
        self.assertIn('found the section: "venue"',
                      prompts.fit_task("Utah", "Case", "facts", "C", "T", "text", "venue"))

    def test_select_falls_back_to_the_top_ranked_when_nothing_is_picked(self):
        a1, a2, b1 = section("A1"), section("A2"), section("B1")
        topics = [("a", [a1, a2]), ("b", [b1])]
        picks, _ = self.select(topics, {"a": "garbage", "b": '{"selected": [0]}'}, limit=2)
        self.assertEqual(picks, [a1, b1])

    def test_format_authorities_trims_long_text(self):
        text = grounding.format_authorities([section("Utah Code § 1-1-1")])
        self.assertIn("Utah Code § 1-1-1 — Title of Utah Code § 1-1-1", text)
        self.assertIn("…[excerpt]", text)
        self.assertLess(len(text), grounding.EXCERPT_CHARS + 100)
        self.assertIn("CITATION NEEDED", grounding.format_authorities([]))


FILLER = [f"({n}) Filler paragraph number {n} about unrelated matters of fees, forms and filing offices "
          "that go on for a while to make this section long." for n in range(1, 41)]
LONG_TEXT = "\n\n".join(["(a) This section governs deposits held by landlords."] + FILLER
                         + ["(z) The landlord shall return the deposit within 21 days, or forfeit twice its amount."])


class RelevantExcerptTest(unittest.TestCase):
    def test_short_text_or_no_query_is_the_plain_excerpt(self):
        self.assertEqual(grounding.relevant_excerpt("Short text.", "deposit"), "Short text.")
        self.assertEqual(grounding.relevant_excerpt(LONG_TEXT, None), grounding.excerpt(LONG_TEXT))
        self.assertEqual(grounding.relevant_excerpt(LONG_TEXT, "nothing matches xyzzy"),
                         grounding.excerpt(LONG_TEXT))

    def test_long_text_shows_its_opening_and_the_matching_part(self):
        text = grounding.relevant_excerpt(LONG_TEXT, "deposit returned within 21 days", 700)
        self.assertTrue(text.startswith("(a) This section governs deposits held by landlords."))
        self.assertIn("[…] (z) The landlord shall return the deposit within 21 days", text)
        self.assertNotIn("Filler paragraph number 20", text)
        self.assertLessEqual(len(text), 700)
        self.assertFalse(text.endswith("[excerpt]"))  # the last paragraph is shown whole

    def test_words_in_every_paragraph_count_for_nothing(self):
        # "filler" and "paragraph" are in 40 of 42 paragraphs; "forfeit" in one.
        text = grounding.relevant_excerpt(LONG_TEXT, "filler paragraph forfeit", 400)
        self.assertIn("forfeit twice its amount", text)

    def test_parts_shown_keep_the_section_order_and_mark_what_was_left_out(self):
        # Single digits don't count as matches, so not "number 5".
        text = grounding.relevant_excerpt(LONG_TEXT, "number 30 number 15", 3000)
        self.assertLess(text.index("number 15 "), text.index("number 30 "))
        self.assertEqual(text.count("[…]"), 2)
        self.assertTrue(text.endswith("…[excerpt]"))

    def test_a_long_paragraph_is_split_at_its_subdivisions(self):
        one_line = " ".join(f"({n}) Rule {n} about general matters of procedure and form." for n in range(1, 60))
        text = grounding.relevant_excerpt(one_line + " (60) Interest accrues at ten percent.", "interest rate", 500)
        self.assertIn("(60) Interest accrues at ten percent.", text)

    def test_drafting_authorities_use_each_sections_query(self):
        law = LawSection("Cal. CIV § 1", "ca", "statute", "T", "p", "in_force", LONG_TEXT, "u", None)
        self.assertIn("within 21 days", grounding.format_authorities([law], {"Cal. CIV § 1": "21 days"}))
        self.assertNotIn("within 21 days", grounding.format_authorities([law]))


class CheckCitationsTest(unittest.TestCase):
    LAW = FakeLaw(sections=[
        section("Utah Code § 78B-2-309"),
        section("Utah Code § 1-1-1", status="repealed"),
        section("Utah Code § 5-5-5"),
    ])

    def check(self, text, provided=frozenset({"Utah Code § 78B-2-309"})):
        return grounding.check_citations(text, self.LAW, "ut", "Utah", provided)

    def test_statuses(self):
        text = ("Utah Code Ann. § 78B-2-309(1); Utah Code § 5-5-5; Utah Code § 1-1-1; "
                "Utah Code § 9-9-9; Cal. Civ. Code § 1542.")
        statuses = [c.status for c in self.check(text).checks]
        self.assertEqual(statuses, [
            grounding.VERIFIED, grounding.IN_FORCE_NOT_PROVIDED, grounding.NOT_IN_FORCE,
            grounding.NOT_FOUND, grounding.WRONG_JURISDICTION,
        ])

    def test_problems_are_marked_inline(self):
        text = "See Utah Code § 1-1-1 and Utah Code § 9-9-9, and Cal. Civ. Code § 1542."
        marked = self.check(text).text
        self.assertIn("§ 1-1-1 [⚠ NOT IN FORCE: repealed]", marked)
        self.assertIn("§ 9-9-9 [⚠ NOT FOUND: no such section in the law index]", marked)
        self.assertIn("§ 1542 [⚠ NOT UTAH OR FEDERAL LAW]", marked)

    def test_every_section_in_a_list_is_checked(self):
        checked = self.check("Jurisdiction under Utah Code § 78B-2-309, 9-9-9, and 5-5-5.")
        self.assertEqual([c.status for c in checked.checks],
                         [grounding.VERIFIED, grounding.NOT_FOUND, grounding.IN_FORCE_NOT_PROVIDED])
        self.assertEqual(checked.checks[1].text, "Utah Code § 9-9-9")
        self.assertIn("9-9-9 [⚠ NOT FOUND: no such section in the law index], and 5-5-5.", checked.text)

    def test_verified_text_is_unchanged(self):
        text = "Under Utah Code § 78B-2-309, the claim is timely."
        self.assertEqual(self.check(text).text, text)

    def test_user_text_has_no_provided_list(self):
        (check,) = self.check("Utah Code § 5-5-5", provided=None).checks
        self.assertEqual(check.status, grounding.VERIFIED)

    def test_case_citations_are_left_unchecked(self):
        checked = self.check("Utah Code § 78B-2-309; Celotex Corp. v. Catrett, 477 U.S. 317 (1986).")
        self.assertEqual(len(checked.checks), 1)
        self.assertEqual(len(checked.unchecked), 1)

    def test_cited_sections_skips_problems(self):
        checked = self.check("Utah Code § 78B-2-309; Utah Code § 5-5-5; Utah Code § 9-9-9")
        self.assertEqual([s.citation for s in grounding.cited_sections(checked)],
                         ["Utah Code § 78B-2-309", "Utah Code § 5-5-5"])


class SupportCheckTest(unittest.TestCase):
    """The "does it say that?" check: a judge's verdict on each in-force citation."""

    LAW = CheckCitationsTest.LAW

    def check(self, text, verdicts):
        """Check `text`, with the judge answering from `verdicts` by section. Returns (result, calls)."""
        calls = []

        def judge(passage, section):
            calls.append((passage, section.citation))
            return verdicts.get(section.citation)
        return grounding.check_citations(text, self.LAW, "ut", "Utah", {"Utah Code § 78B-2-309"}, judge), calls

    def test_parse_verdict(self):
        self.assertEqual(grounding.parse_verdict('{"verdict": "partly", "reason": " Overstates\\n it. "}'),
                         (grounding.PARTLY, "Overstates it."))
        self.assertEqual(grounding.parse_verdict('Sure: {"verdict": "Does Not Support"}'),
                         (grounding.DOES_NOT_SUPPORT, ""))
        for bad in ('{"verdict": "maybe"}', '{"verdict": 1}', "not json", '["supports"]'):
            self.assertIsNone(grounding.parse_verdict(bad))

    def test_passage_is_the_citing_paragraph_cut_around_the_citation(self):
        text = "Heading\n" + "before " * 100 + "CITE" + " after" * 100 + "\nNext paragraph."
        start = text.index("CITE")
        passage = grounding.passage_around(text, start, start + 4)
        self.assertTrue(passage.startswith("…before"))
        self.assertTrue(passage.endswith("after…"))
        self.assertIn(" CITE ", passage)
        self.assertLess(len(passage), grounding.PASSAGE_CHARS + 10)
        self.assertEqual(grounding.passage_around("One.\nThe CITE here.\nThree.", 9, 13), "The CITE here.")

    def test_doubted_citations_are_marked_and_carry_the_reason(self):
        text = ("1. The claim is timely under Utah Code § 78B-2-309.\n"
                "2. This court has jurisdiction under Utah Code § 5-5-5.")
        checked, calls = self.check(text, {"Utah Code § 78B-2-309": (grounding.SUPPORTS, "It says so."),
                                           "Utah Code § 5-5-5": (grounding.DOES_NOT_SUPPORT, "About fees.")})
        self.assertEqual([(c.support, c.support_reason) for c in checked.checks],
                         [(grounding.SUPPORTS, "It says so."), (grounding.DOES_NOT_SUPPORT, "About fees.")])
        self.assertEqual(calls, [("1. The claim is timely under Utah Code § 78B-2-309.", "Utah Code § 78B-2-309"),
                                 ("2. This court has jurisdiction under Utah Code § 5-5-5.", "Utah Code § 5-5-5")])
        self.assertIn("§ 78B-2-309.\n", checked.text)  # supported: no mark
        self.assertIn("§ 5-5-5 [⚠ MAY NOT SUPPORT THIS].", checked.text)

        partly, _ = self.check(text, {"Utah Code § 5-5-5": (grounding.PARTLY, "Overstated.")})
        self.assertEqual(partly.text, text)  # "partly" is recorded for the citation check, not marked
        self.assertEqual(partly.checks[1].support, grounding.PARTLY)
        self.assertIsNone(partly.checks[0].support)  # an unusable reply leaves the citation unjudged

    def test_only_in_force_citations_with_something_to_judge_are_asked_about(self):
        text = ("The claim fails under Utah Code § 9-9-9 and Utah Code § 1-1-1.\n"  # not found, repealed
                "Utah Code § 78B-2-309\n"                                           # a bare citation
                "It is timely under Utah Code § 78B-2-309, see Utah Code § 78B-2-309.")
        checked, calls = self.check(text, {"Utah Code § 78B-2-309": (grounding.PARTLY, "r")})
        self.assertEqual(len(calls), 1)  # the last line, once for both of its citations
        self.assertEqual([c.support for c in checked.checks], [None, None, None, grounding.PARTLY, grounding.PARTLY])

    def test_fit_filter_keeps_what_applies_and_what_got_no_usable_verdict(self):
        a, b, c, d = (section(x) for x in "ABCD")
        verdicts = {"A": (grounding.APPLIES, "fits"), "B": (grounding.UNSURE, "depends"),
                    "C": (grounding.DOES_NOT_APPLY, "probate"), "D": None}
        kept, checks = grounding.fit_filter(lambda s: verdicts[s.citation], [a, b, c, d])
        self.assertEqual(kept, [a, d])
        self.assertEqual([(x.verdict, x.reason, x.kept) for x in checks],
                         [("applies", "fits", True), ("unsure", "depends", False),
                          ("does_not_apply", "probate", False), (None, "", True)])
        self.assertEqual(grounding.parse_verdict('{"verdict": "Does not apply"}', grounding.FIT_VERDICTS),
                         (grounding.DOES_NOT_APPLY, ""))
        self.assertIsNone(grounding.parse_verdict('{"verdict": "supports"}', grounding.FIT_VERDICTS))

    def test_no_judge_means_no_verdicts(self):
        checked = grounding.check_citations("Timely under Utah Code § 78B-2-309.", self.LAW, "ut", "Utah", None)
        self.assertIsNone(checked.checks[0].support)


if __name__ == "__main__":
    unittest.main()
