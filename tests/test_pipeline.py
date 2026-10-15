import dataclasses
import json
import unittest
from datetime import datetime
from pathlib import Path

from battle_test import grounding
from battle_test.config import Config
from battle_test.law_index import LawSection
from battle_test.models import Usage
from battle_test.pipeline import run_case
from battle_test.report import possible_references, render_markdown

CFG = Config(
    ollama_url="http://unused",
    timeout_seconds=1,
    plaintiff_model="p-model",
    defendant_model="d-model",
    num_ctx=8192,
    temperature=0.0,
    rounds=2,
    output_dir=Path("unused"),
)


def section(citation, title="Title", jurisdiction="ut", status="in_force"):
    return LawSection(citation, jurisdiction, "statute", title, citation, status,
                      f"Text of {citation}.", "https://example.gov", 2020)


SJ_RULE = section("Utah R. Civ. P. 56", "Rule 56. Summary judgment")
LIMITS = section("Utah Code § 78B-2-309", "Within six years")


class FakeLaw:
    """Search returns fixed sections; resolve knows only those two."""

    def meta(self):
        return {"snapshot": "vTEST", "snapshot_date": "2026-08-14", "attribution": "Test Law"}

    def search(self, text, state, *, limit=10):
        return [SJ_RULE, LIMITS][:limit]

    def resolve(self, ref, state):
        return [s for s in (SJ_RULE, LIMITS) if s.citation.lower().endswith(ref.section)]

    def lookup(self, citation):
        return [s for s in (SJ_RULE, LIMITS) if s.citation == citation]


class FakeClient:
    """Records each call. JSON calls get research/selection answers; drafts
    get a numbered stub, or whatever `drafts` supplies."""

    def __init__(self, drafts=None, usage=None, fit="applies"):
        self.usage = usage  # (input tokens, output tokens) reported for every call
        # The checker's verdict on every selected section, or a list of
        # verdicts, one per fit call in order.
        self.fit = fit
        self.calls = []
        self.drafts = list(drafts or [])

    def chat(self, model, system, user, on_token=None, json_mode=False, on_usage=None, temperature=None):
        if on_usage and self.usage:
            on_usage(Usage(model, *self.usage))
        self.calls.append({"model": model, "system": system, "user": user, "json": json_mode,
                           "temperature": temperature})
        if json_mode:
            if '"applies"' in user:  # the fit check, before drafting
                verdict = self.fit.pop(0) if isinstance(self.fit, list) else self.fit
                return json.dumps({"verdict": verdict, "reason": "Because."})
            if '"verdict"' in user:  # asked only when the config has a checker model
                return json.dumps({"verdict": "does_not_support", "reason": "It is about something else."})
            if '"queries"' in user:
                return json.dumps({"queries": ["action upon instrument in writing"]})
            return json.dumps({"selected": [2]})  # LIMITS; the summary judgment rule is supplied anyway
        n = sum(not c["json"] for c in self.calls)
        return self.drafts.pop(0) if self.drafts else f"DOC-{n}"

    def drafting_calls(self):
        return [c for c in self.calls if not c["json"]]


class PipelineTest(unittest.TestCase):
    def run_case(self, client, state="UT", **kwargs):
        return run_case(CFG, client, FakeLaw(), state, **kwargs)

    def test_generated_complaint_two_rounds(self):
        client = FakeClient()
        run = self.run_case(client, facts="the facts")

        self.assertEqual(
            [d.title for d in run.documents],
            [
                "Complaint",
                "Motion for Summary Judgment",
                "Opposition to Motion for Summary Judgment",
                "Reply in Support of Motion for Summary Judgment",
            ],
        )
        self.assertEqual([c["model"] for c in client.drafting_calls()],
                         ["p-model", "p-model", "d-model", "p-model"])
        self.assertTrue(all(d.generated for d in run.documents))
        self.assertIn("the facts", client.drafting_calls()[0]["user"])

    def test_token_usage_is_totalled_per_model_and_costed(self):
        from battle_test.config import BedrockModel
        cfg = dataclasses.replace(CFG, bedrock_models={"p-model": BedrockModel("p", 1.0, 2.0),
                                                       "d-model": BedrockModel("d", 10.0, 20.0)})
        run = run_case(cfg, FakeClient(usage=(1000, 100)), FakeLaw(), "UT", facts="f")
        # Plaintiff: research, selection, complaint, motion, reply. Defendant: research, selection, opposition.
        self.assertEqual(run.usage, {
            "p-model": {"calls": 5, "input_tokens": 5000, "output_tokens": 500},
            "d-model": {"calls": 3, "input_tokens": 3000, "output_tokens": 300},
        })
        self.assertAlmostEqual(run.cost_usd, (5000 * 1 + 500 * 2 + 3000 * 10 + 300 * 20) / 1e6)
        self.assertIn("**Model usage:** 8 calls, 8,000 tokens in, 800 out, estimated cost $0.042",
                      render_markdown(run, datetime(2026, 10, 5)))

    def test_no_usage_reported_means_no_usage_line(self):
        run = self.run_case(FakeClient(), facts="f")  # like the demo model
        self.assertEqual((run.usage, run.cost_usd), ({}, None))
        self.assertNotIn("Model usage", render_markdown(run, datetime(2026, 10, 5)))

    def test_usage_without_a_price_says_so(self):
        run = self.run_case(FakeClient(usage=(10, 5)), facts="f", rounds=1)  # CFG has no prices, like Ollama
        self.assertIsNone(run.cost_usd)
        self.assertIn("no price set for this model", render_markdown(run, datetime(2026, 10, 5)))

    def test_research_and_selection_run_for_each_side(self):
        client = FakeClient()
        run = self.run_case(client, facts="f")
        json_models = [c["model"] for c in client.calls if c["json"]]
        self.assertEqual(json_models, ["p-model", "p-model", "d-model", "d-model"])
        self.assertEqual(run.research["plaintiff"][0], "action upon instrument in writing")
        self.assertEqual(run.research["plaintiff"][1:], grounding.STANDARD_QUERIES)
        self.assertEqual(run.candidates["plaintiff"], ["Utah R. Civ. P. 56", "Utah Code § 78B-2-309"])
        self.assertIn("action upon instrument in writing", run.research["defendant"])

    def test_authorities_are_quoted_in_every_draft(self):
        client = FakeClient()
        run = self.run_case(client, facts="f")
        for call in client.drafting_calls():
            self.assertIn("=== AUTHORITIES ===", call["user"])
            self.assertIn("Text of Utah Code § 78B-2-309.", call["user"])  # the one the model selected
        for call in client.drafting_calls()[1:]:  # the motion and after also get the summary judgment rule
            self.assertIn("Text of Utah R. Civ. P. 56.", call["user"])
        self.assertEqual(run.documents[1].authorities, (SJ_RULE, LIMITS))

    def test_each_step_sees_prior_documents(self):
        client = FakeClient()
        self.run_case(client, facts="the facts")

        motion, opposition, reply = (c["user"] for c in client.drafting_calls()[1:])
        self.assertIn("DOC-1", motion)
        self.assertIn("DOC-1", opposition)
        self.assertIn("DOC-2", opposition)
        for doc in ("DOC-1", "DOC-2", "DOC-3"):
            self.assertIn(doc, reply)

    def test_user_complaint_is_used_as_is(self):
        client = FakeClient()
        run = self.run_case(client, "CA", complaint="  MY COMPLAINT  ", rounds=1)

        self.assertEqual(len(client.drafting_calls()), 2)  # motion + opposition only
        first = run.documents[0]
        self.assertEqual((first.text, first.generated), ("MY COMPLAINT", False))
        self.assertIn("MY COMPLAINT", client.drafting_calls()[0]["user"])
        self.assertIn("MY COMPLAINT", client.calls[0]["user"])  # plaintiff research reads it

    def test_one_round_skips_reply(self):
        run = self.run_case(FakeClient(), "TX", facts="f", rounds=1)
        self.assertEqual(len(run.documents), 3)

    def test_state_reaches_both_roles(self):
        client = FakeClient()
        self.run_case(client, "TX", facts="f")
        for call in client.calls:
            self.assertIn("Texas law", call["system"])
        self.assertIn("DEFENDANT", client.drafting_calls()[2]["system"])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            self.run_case(FakeClient(), "NV", facts="f")
        with self.assertRaises(ValueError):
            self.run_case(FakeClient(), facts="f", complaint="c")
        with self.assertRaises(ValueError):
            self.run_case(FakeClient())

    def test_citations_are_checked_and_marked(self):
        drafts = [
            "Under Utah Code § 78B-2-309 and Utah Code Ann. § 78-27-102.\n=== END COMPLAINT ===",
            "See Utah R. Civ. P. 56(a).",
        ]
        run = self.run_case(FakeClient(drafts), facts="f", rounds=1)
        complaint, motion = run.documents[:2]
        self.assertEqual([c.status for c in complaint.checks], [grounding.VERIFIED, grounding.NOT_FOUND])
        self.assertIn("78-27-102 [⚠ NOT FOUND", complaint.text)
        self.assertNotIn("===", complaint.text)
        self.assertEqual([c.status for c in motion.checks], [grounding.VERIFIED])

    def test_summary_judgment_rule_reaches_every_brief(self):
        class NoSearchLaw(FakeLaw):
            def search(self, text, state, *, limit=10):
                return []

        run = run_case(CFG, FakeClient(), NoSearchLaw(), "UT", facts="f")
        complaint, *briefs = run.documents
        self.assertEqual(complaint.authorities, ())
        for doc in briefs:
            self.assertEqual(doc.authorities, (SJ_RULE,))

    def test_citation_wrapped_in_placeholder_is_checked(self):
        drafts = ["c", "m", "Defense. [CITATION NEEDED: Utah Code § 78B-2-309]"]
        run = self.run_case(FakeClient(drafts), facts="f", rounds=1)
        opposition = run.documents[2]
        self.assertEqual([c.status for c in opposition.checks], [grounding.VERIFIED])
        self.assertEqual(opposition.text, "Defense. Utah Code § 78B-2-309")

    def test_defendant_sees_text_of_what_plaintiff_cited(self):
        client = FakeClient(["complaint", "See Utah Code § 78B-2-309."])
        run = self.run_case(client, facts="f", rounds=1)
        self.assertIn(LIMITS, run.documents[2].authorities)

    def test_user_complaint_citations_are_checked_without_provided_list(self):
        run = self.run_case(FakeClient(), complaint="Under Utah Code § 78B-2-309.", rounds=1)
        (check,) = run.documents[0].checks
        self.assertEqual(check.status, grounding.VERIFIED)

    def test_complaint_names_state_trial_court(self):
        client = FakeClient()
        self.run_case(client, facts="f", rounds=1)
        self.assertIn("Utah District Court", client.drafting_calls()[0]["user"])

    def test_report_summarises_checks_and_sources(self):
        drafts = ["Utah Code Ann. § 78-27-102; 550 U.S. 544.", "motion"]
        run = self.run_case(FakeClient(drafts), facts="f", rounds=1)
        md = render_markdown(run, datetime(2026, 9, 24, 12, 0))
        self.assertIn("# Battle test — Utah", md)
        self.assertIn("not legal advice", md)
        self.assertIn("current as of 2026-08-14", md)
        self.assertIn("## Citation check", md)
        self.assertIn("❌ not found: `Utah Code Ann. § 78-27-102`", md)
        self.assertIn("⚠ not checked", md)  # the case citation
        self.assertIn("[source](https://example.gov)", md)
        self.assertIn("*Defendant · LLM-generated*", md)

    def test_report_lists_uncited_authorities_as_possible_references(self):
        drafts = ["complaint", "See Utah R. Civ. P. 56."]  # the limitations statute is given and never cited
        run = self.run_case(FakeClient(drafts), facts="f", rounds=1)
        self.assertEqual(possible_references(run), [LIMITS])
        md = render_markdown(run, datetime(2026, 10, 12))
        section = md.split("## Possible references")[1].split("## Appendix")[0]
        self.assertIn("`Utah Code § 78B-2-309` Within six years — [source](https://example.gov)", section)
        self.assertIn("Text of Utah Code § 78B-2-309.", section)
        self.assertIn("has not been checked", section)
        self.assertNotIn("Utah R. Civ. P. 56", section)

    def test_checker_model_judges_what_each_citation_is_cited_for(self):
        cfg = dataclasses.replace(CFG, checker_model="c-model", fit_check=False)
        drafts = ["Venue is proper under Utah Code § 78B-2-309.", "no citations here"]
        client = FakeClient(drafts)
        stages = []
        run = run_case(cfg, client, FakeLaw(), "UT", facts="f", rounds=1,
                       on_stage=lambda title, role: stages.append(title))

        (asked,) = [c for c in client.calls if c["model"] == "c-model"]
        self.assertTrue(asked["json"])
        self.assertIn("Text of Utah Code § 78B-2-309.", asked["user"])
        self.assertIn("Venue is proper under Utah Code § 78B-2-309.", asked["user"])
        self.assertIn("take no side", asked["system"])
        self.assertEqual(stages[2:4], ["Complaint", "Checking citations: Complaint"])

        complaint = run.documents[0]
        self.assertEqual(complaint.checks[0].support, grounding.DOES_NOT_SUPPORT)
        self.assertEqual(complaint.text, "Venue is proper under Utah Code § 78B-2-309 [⚠ MAY NOT SUPPORT THIS].")
        self.assertIn("78B-2-309 [⚠ MAY NOT SUPPORT THIS]", client.drafting_calls()[1]["user"])  # the motion sees it

        md = render_markdown(run, datetime(2026, 10, 12))
        self.assertIn("- **Citation checker model:** `c-model`", md)
        self.assertIn("A model also compared each one with the passage citing it", md)
        self.assertIn("| Complaint | 1 | 0 | 0 | 1 |", md)
        self.assertIn("- ⚠ may not support what it's cited for: `Utah Code § 78B-2-309`. "
                      "Checker's note: It is about something else.", md)

    def fit_run(self, verdict):
        client = FakeClient(fit=verdict)
        stages = []
        run = run_case(dataclasses.replace(CFG, checker_model="c-model"), client, FakeLaw(), "UT",
                       facts="the facts", rounds=1, on_stage=lambda title, role: stages.append(title))
        return run, client, stages

    def test_fit_check_keeps_sections_that_apply(self):
        run, client, stages = self.fit_run("applies")
        self.assertEqual(stages[:4], ["Legal research", "Selecting authorities",
                                      "Checking the authorities fit the case", "Complaint"])
        asked = next(c for c in client.calls if '"applies"' in c["user"])
        self.assertEqual(asked["model"], "c-model")
        self.assertIn("the facts", asked["user"])
        # It's told the research topic that found the section.
        self.assertIn('found the section: "action upon instrument in writing"', asked["user"])
        self.assertIn("Text of Utah Code § 78B-2-309.", asked["user"])
        self.assertEqual(run.documents[0].authorities, (LIMITS,))
        self.assertEqual([(c.section, c.verdict) for c in run.fit["plaintiff"]], [(LIMITS, "applies")])
        self.assertNotIn("Set aside before drafting", render_markdown(run, datetime(2026, 10, 12)))

    def test_fit_check_sets_aside_sections_that_do_not_apply(self):
        run, client, _ = self.fit_run("does_not_apply")
        self.assertEqual(run.documents[0].authorities, ())           # the complaint drafts with none
        self.assertEqual(run.documents[1].authorities, (SJ_RULE,))   # the rule is supplied, never judged
        self.assertNotIn("Text of Utah Code § 78B-2-309.", client.drafting_calls()[0]["user"])
        self.assertNotIn(LIMITS, possible_references(run))
        appendix = render_markdown(run, datetime(2026, 10, 12)).split("## Appendix")[1]
        self.assertIn("Set aside before drafting", appendix)
        self.assertIn("- `Utah Code § 78B-2-309` Within six years — [source](https://example.gov)  \n"
                      "  *Checker's note: Because.*", appendix)

    def test_fit_check_holds_back_sections_it_is_unsure_of_as_possible_references(self):
        run, _, _ = self.fit_run("unsure")
        self.assertEqual(run.documents[0].authorities, ())
        self.assertIn(LIMITS, possible_references(run))
        md = render_markdown(run, datetime(2026, 10, 12))
        section = md.split("## Possible references")[1].split("## Appendix")[0]
        self.assertIn("`Utah Code § 78B-2-309`", section)
        self.assertIn("Not given to the drafting models: the checker wasn't sure it applies to this case. "
                      "Checker's note: Because.", section)
        # The note above the list says the fit check ran, not that nothing was checked.
        self.assertIn("a model judged whether each fits this kind of case", section)
        self.assertNotIn("has not been checked", section)
        self.assertNotIn("Set aside before drafting", md)

    def test_fit_check_keeps_a_section_when_the_reply_is_unusable_or_it_is_switched_off(self):
        run, _, _ = self.fit_run("maybe")
        self.assertEqual(run.documents[0].authorities, (LIMITS,))
        self.assertIsNone(run.fit["plaintiff"][0].verdict)

        client = FakeClient(fit="does_not_apply")
        off = run_case(dataclasses.replace(CFG, checker_model="c-model", fit_check=False), client, FakeLaw(),
                       "UT", facts="f", rounds=1)
        self.assertEqual((off.documents[0].authorities, off.fit), ((LIMITS,), {}))

    def two_round_fit_run(self, verdicts):
        client = FakeClient(fit=list(verdicts))
        run = run_case(dataclasses.replace(CFG, checker_model="c-model", temperature=0.4), client, FakeLaw(),
                       "UT", facts="the facts", rounds=2)
        return run, [c for c in client.calls if '"applies"' in c["user"]]

    def test_checker_calls_run_at_the_checker_temperature(self):
        client = FakeClient(["Venue is proper under Utah Code § 78B-2-309."])
        run_case(dataclasses.replace(CFG, checker_model="c-model", temperature=0.4), client, FakeLaw(), "UT",
                 facts="f", rounds=1)
        checker = [c for c in client.calls if c["model"] == "c-model"]
        self.assertTrue(any('"applies"' in c["user"] for c in checker))           # the fit check
        self.assertTrue(any('"supports"' in c["user"] for c in checker))          # "does it say that?"
        self.assertEqual({c["temperature"] for c in checker}, {0.0})
        self.assertEqual({c["temperature"] for c in client.calls if c["model"] != "c-model"}, {None})

    def test_a_section_set_aside_for_the_plaintiff_is_set_aside_for_the_defendant_without_asking(self):
        run, asked = self.two_round_fit_run(["does_not_apply", "applies"])
        self.assertEqual(len(asked), 1)  # only the plaintiff's check asked
        self.assertEqual([(c.verdict, c.reason) for c in run.fit["defendant"]], [("does_not_apply", "Because.")])
        self.assertNotIn(LIMITS, run.documents[2].authorities)  # the opposition

    def test_a_section_the_defendant_sets_aside_is_kept_out_of_the_reply(self):
        run, asked = self.two_round_fit_run(["applies", "does_not_apply"])
        self.assertEqual(len(asked), 2)  # a kept section is asked about again, for the other side
        self.assertIn(LIMITS, run.documents[1].authorities)     # the motion came before the defendant's check
        self.assertNotIn(LIMITS, run.documents[3].authorities)  # the reply
        appendix = render_markdown(run, datetime(2026, 10, 15)).split("## Appendix")[1]
        self.assertIn("Set aside before drafting", appendix)

    def test_without_a_checker_model_nothing_is_judged(self):
        client = FakeClient(["Venue is proper under Utah Code § 78B-2-309."])
        run = self.run_case(client, facts="f", rounds=1)
        self.assertFalse(any('"verdict"' in c["user"] for c in client.calls))
        self.assertIsNone(run.documents[0].checks[0].support)
        md = render_markdown(run, datetime(2026, 10, 12))
        self.assertIn("| Complaint | 1 | 0 | 0 | – |", md)
        self.assertIn("not for whether they say what the brief claims", md)

    def test_no_possible_references_when_everything_given_is_cited(self):
        drafts = ["Under Utah Code § 78B-2-309.", "See Utah R. Civ. P. 56."]
        run = self.run_case(FakeClient(drafts), facts="f", rounds=1)
        self.assertEqual(possible_references(run), [])
        self.assertNotIn("Possible references", render_markdown(run, datetime(2026, 10, 12)))


if __name__ == "__main__":
    unittest.main()
