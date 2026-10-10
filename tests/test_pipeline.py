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
from battle_test.report import render_markdown

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

    def __init__(self, drafts=None, usage=None):
        self.usage = usage  # (input tokens, output tokens) reported for every call
        self.calls = []
        self.drafts = list(drafts or [])

    def chat(self, model, system, user, on_token=None, json_mode=False, on_usage=None):
        if on_usage and self.usage:
            on_usage(Usage(model, *self.usage))
        self.calls.append({"model": model, "system": system, "user": user, "json": json_mode})
        if json_mode:
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


if __name__ == "__main__":
    unittest.main()
