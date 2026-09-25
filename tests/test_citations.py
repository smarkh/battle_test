import unittest

from battle_test.citations import (
    code_name,
    find_citation_refs,
    find_unverified_citations,
    split_corpus_citation,
    strip_echoed_markers,
    tokens_match,
    unwrap_citation_placeholders,
)


class FindCitationRefsTest(unittest.TestCase):
    def refs(self, text):
        return [(r.text, r.section) for r in find_citation_refs(text)]

    def test_statute_forms(self):
        self.assertEqual(
            self.refs("Utah Code Ann. § 78B-2-309(1)(b); 42 U.S.C. § 1983; bare § 437c."),
            [("Utah Code Ann. § 78B-2-309(1)(b)", "78b-2-309"), ("42 U.S.C. § 1983", "1983"),
             ("§ 437c", "437c")],
        )

    def test_rule_forms(self):
        self.assertEqual(
            self.refs("Utah R. Civ. P. 56(a); Rule 56 of the Utah Rules of Civil Procedure; "
                      "URCP 56; Cal. Rules of Court, rule 3.1350."),
            [("Utah R. Civ. P. 56(a)", "56"), ("Rule 56 of the Utah Rules of Civil Procedure", "56"),
             ("URCP 56", "56"), ("Cal. Rules of Court, rule 3.1350", "3.1350")],
        )

    def test_trims_prose_before_citation(self):
        self.assertEqual(self.refs("See U.C.A. § 1-2-3"), [("U.C.A. § 1-2-3", "1-2-3")])
        self.assertEqual(self.refs("The Defendant Violated Utah Code § 1-2-3"),
                         [("Utah Code § 1-2-3", "1-2-3")])

    def test_section_word_needs_a_code_name(self):
        self.assertEqual(self.refs("Civil Code section 1542"), [("Civil Code section 1542", "1542")])
        self.assertEqual(self.refs("Section 4 of the contract"), [])

    def test_lists_of_sections(self):
        self.assertEqual(
            self.refs("Utah Code § 78B-6-2705, 78B-5-826, 70A-2a-214, and 70A-2-712."),
            [("Utah Code § 78B-6-2705", "78b-6-2705"), ("Utah Code § 78B-5-826", "78b-5-826"),
             ("Utah Code § 70A-2a-214", "70a-2a-214"), ("Utah Code § 70A-2-712", "70a-2-712")],
        )
        self.assertEqual(
            self.refs("42 U.S.C. §§ 1983 and 1988"),
            [("42 U.S.C. §§ 1983", "1983"), ("42 U.S.C. § 1988", "1988")],
        )
        self.assertEqual(
            self.refs("Tex. Bus. & Com. Code §§ 2.714, 2.715(b)"),
            [("Tex. Bus. & Com. Code §§ 2.714", "2.714"), ("Tex. Bus. & Com. Code § 2.715(b)", "2.715")],
        )

    def test_list_ignores_numbers_shaped_differently(self):
        for text in ["Utah Code § 78B-2-309, 2026 was the year", "§ 1983, 12.5 percent"]:
            with self.subTest(text=text):
                self.assertEqual(len(find_citation_refs(text)), 1)

    def test_known_limitation_plain_numbers_after_plain_section(self):
        # With plain-number sections, a following plain number is
        # indistinguishable from another section. It errs toward checking
        # too much (an extra flag), never toward missing a citation.
        self.assertEqual(self.refs("§ 12, 3 days later"), [("§ 12", "12"), ("§ 3", "3")])

    def test_ignores_placeholders(self):
        self.assertEqual(self.refs("[CITATION NEEDED: Utah Code § 1-2-3 or similar]"), [])


class UnwrapCitationPlaceholdersTest(unittest.TestCase):
    def test_unwraps_placeholders_holding_only_citations(self):
        for text, expected in [
            ("[CITATION NEEDED: Utah Code § 70A-2-715]", "Utah Code § 70A-2-715"),
            ("[CITATION NEEDED: Utah Code § 1-2-3; Utah R. Civ. P. 56(a)]",
             "Utah Code § 1-2-3; Utah R. Civ. P. 56(a)"),
            ("[CITATION NEEDED: see Utah Code § 1-2-3 and § 4-5-6.]", "see Utah Code § 1-2-3 and § 4-5-6."),
        ]:
            with self.subTest(text=text):
                self.assertEqual(unwrap_citation_placeholders(text), expected)

    def test_keeps_real_placeholders(self):
        for text in [
            "[CITATION NEEDED: Utah statute of limitations for written contracts]",
            "[CITATION NEEDED: a statute like Utah Code § 1-2-3 on venue]",
            "[FACT NEEDED: Utah Code § 1-2-3]",
        ]:
            with self.subTest(text=text):
                self.assertEqual(unwrap_citation_placeholders(text), text)


class CodeNameTest(unittest.TestCase):
    def test_spellings_reduce_to_same_tokens(self):
        for a, b in [
            ("Utah Code Ann.", "Utah Code"),
            ("U.C.A.", "Utah Code"),
            ("Utah Const. art. I,", "Ut. Const. art. I"),
            ("Fed. R. Civ. P.", "Federal Rules of Civil Procedure"),
            ("URCP", "Utah R. Civ. P."),
            ("Tex. Civ. Prac. & Rem. Code Ann.", "Tex. Civil Practice and Remedies Code"),
            ("Tex. Gov't Code", "Tex. Government Code"),
            ("Cal. Rules of Court, rule", "Cal. R. Ct."),
            ("42 U.S.C.A.", "42 U.S.C."),
        ]:
            with self.subTest(a=a, b=b):
                ca, cb = code_name(a), code_name(b)
                self.assertEqual(ca.jurisdiction, cb.jurisdiction)
                self.assertTrue(tokens_match(ca.tokens, cb.tokens))

    def test_different_codes_do_not_match(self):
        for a, b in [
            ("Tex. Bus. & Com. Code", "Tex. Business Organizations Code"),
            ("Utah Const. art. I,", "Ut. Const. art. II"),
            ("42 U.S.C.", "28 U.S.C."),
            ("Utah R. Civ. P.", "Utah R. Evid."),
        ]:
            with self.subTest(a=a, b=b):
                self.assertFalse(tokens_match(code_name(a).tokens, code_name(b).tokens))

    def test_unnamed_jurisdiction(self):
        self.assertEqual(code_name("Code of Civil Procedure"), code_name("Code of Civil Procedure"))
        self.assertIsNone(code_name("Civil Code").jurisdiction)


class SplitCorpusCitationTest(unittest.TestCase):
    def test_splits(self):
        self.assertEqual(split_corpus_citation("42 U.S.C. § 1983 (2024)"), ("42 U.S.C.", "1983"))
        self.assertEqual(split_corpus_citation("Utah R. Civ. P. 56"), ("Utah R. Civ. P.", "56"))
        self.assertEqual(split_corpus_citation("Ut. Const. art. I, § 7"), ("Ut. Const. art. I", "7"))
        self.assertEqual(split_corpus_citation("Utah R. Civ. P. 16 (version superseded)"),
                         ("Utah R. Civ. P.", "16"))


class FindUnverifiedCitationsTest(unittest.TestCase):
    def test_flags_statutes_and_cases(self):
        for line in [
            "jurisdiction under Utah Code Ann. § 78-27-102 et seq.",
            "as required by 42 U.S.C. 1983",
            "see 29 C.F.R. 1910.1200",
            "under Utah R. Civ. P. 56",
            "under Fed. R. Civ. P. 56(a)",
            "Cal. Civ. Proc. Code 437c",
            "Tex. Civ. Prac. & Rem. Code 16.004",
            "Celotex Corp. v. Catrett, 477 U.S. 317 (1986)",
            "Orvis v. Johnson, 2008 UT 2, 177 P.3d 600",
            "45 F. Supp. 2d 789",
            "12 Cal. App. 4th 345",
        ]:
            with self.subTest(line=line):
                self.assertEqual(find_unverified_citations(line), [line])

    def test_ignores_placeholders_and_ordinary_text(self):
        text = "\n".join(
            [
                "[CITATION NEEDED: Utah Code section on contract jurisdiction]",
                "[FACT NEEDED: copy of the contract]",
                "located at 1482 N Canyon View Drive, Provo, Utah.",
                "On April 22, 2026, Brent Kolb responded.",
                "a $12,000 deposit and $18,700 in repairs",
                "DANA WHITFIELD, Plaintiff, v. SUMMIT PEAK ROOFING LLC",
            ]
        )
        self.assertEqual(find_unverified_citations(text), [])

    def test_flags_citation_next_to_placeholder(self):
        line = "pursuant to Utah Code Ann. § 78-27-102 [CITATION NEEDED: jurisdiction statute]."
        self.assertEqual(
            find_unverified_citations(line), ["pursuant to Utah Code Ann. § 78-27-102 ."]
        )

    def test_one_entry_per_line_without_duplicates(self):
        text = "see § 1 and § 2\nsee § 1 and § 2\nsee § 3"
        self.assertEqual(find_unverified_citations(text), ["see § 1 and § 2", "see § 3"])


class StripEchoedMarkersTest(unittest.TestCase):
    def test_removes_marker_lines_only(self):
        text = "Body text.\n=== END REPLY IN SUPPORT OF MOTION ===\n"
        self.assertEqual(strip_echoed_markers(text), "Body text.")

    def test_keeps_inline_equals(self):
        text = "Total === 5 in the middle of a line"
        self.assertEqual(strip_echoed_markers(text), text)


if __name__ == "__main__":
    unittest.main()
