import unittest
from datetime import datetime
from pathlib import Path

from battle_test.config import Config
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


class FakeClient:
    """Records each call and replies with a numbered stub document."""

    def __init__(self):
        self.calls = []

    def chat(self, model, system, user, on_token=None):
        self.calls.append({"model": model, "system": system, "user": user})
        return f"DOC-{len(self.calls)}"


class PipelineTest(unittest.TestCase):
    def test_generated_complaint_two_rounds(self):
        client = FakeClient()
        run = run_case(CFG, client, "UT", facts="the facts")

        self.assertEqual(
            [d.title for d in run.documents],
            [
                "Complaint",
                "Motion for Summary Judgment",
                "Opposition to Motion for Summary Judgment",
                "Reply in Support of Motion for Summary Judgment",
            ],
        )
        self.assertEqual([c["model"] for c in client.calls], ["p-model", "p-model", "d-model", "p-model"])
        self.assertTrue(all(d.generated for d in run.documents))
        self.assertIn("the facts", client.calls[0]["user"])

    def test_each_step_sees_prior_documents(self):
        client = FakeClient()
        run_case(CFG, client, "UT", facts="the facts")

        motion, opposition, reply = (c["user"] for c in client.calls[1:])
        self.assertIn("DOC-1", motion)
        self.assertIn("DOC-1", opposition)
        self.assertIn("DOC-2", opposition)
        for doc in ("DOC-1", "DOC-2", "DOC-3"):
            self.assertIn(doc, reply)

    def test_user_complaint_is_used_as_is(self):
        client = FakeClient()
        run = run_case(CFG, client, "CA", complaint="  MY COMPLAINT  ", rounds=1)

        self.assertEqual(len(client.calls), 2)  # motion + opposition only
        first = run.documents[0]
        self.assertEqual((first.text, first.generated), ("MY COMPLAINT", False))
        self.assertIn("MY COMPLAINT", client.calls[0]["user"])

    def test_one_round_skips_reply(self):
        run = run_case(CFG, FakeClient(), "TX", facts="f", rounds=1)
        self.assertEqual(len(run.documents), 3)

    def test_state_reaches_both_roles(self):
        client = FakeClient()
        run_case(CFG, client, "TX", facts="f")
        for call in client.calls:
            self.assertIn("Texas law", call["system"])
        self.assertIn("DEFENDANT", client.calls[2]["system"])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            run_case(CFG, FakeClient(), "NV", facts="f")
        with self.assertRaises(ValueError):
            run_case(CFG, FakeClient(), "UT", facts="f", complaint="c")
        with self.assertRaises(ValueError):
            run_case(CFG, FakeClient(), "UT")

    def test_report_labels_source_and_disclaimer(self):
        run = run_case(CFG, FakeClient(), "UT", complaint="mine", rounds=1)
        md = render_markdown(run, datetime(2026, 9, 24, 12, 0))
        self.assertIn("# Battle test — Utah", md)
        self.assertIn("not legal advice", md)
        self.assertIn("*Plaintiff · user-supplied*", md)
        self.assertIn("*Defendant · LLM-generated*", md)


if __name__ == "__main__":
    unittest.main()
