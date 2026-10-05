import contextlib
import io
import re
import tempfile
import unittest
from dataclasses import astuple
from datetime import datetime
from pathlib import Path
from unittest import mock

from battle_test.config import Plan, load_plans, load_web_config
from battle_test.web import users
from battle_test.web.auth import AuthStore
from battle_test.web.jobs import JobStore

ROOT = Path(__file__).resolve().parent.parent

# Throwaway test value only.
PW = "correct horse battery"

CONFIG = """
[web]
host = "127.0.0.1"
port = 8000
data_dir = "web"
public_url = "https://battle.example/"

[plans]
default = "trial"
[plans.trial]
cases = 1
period = "total"
[plans.solo]
label = "Solo"
cases = 30
[plans.unlimited]
"""


class PlansConfigTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return load_plans(path)

    def test_plans_load_with_defaults(self):
        plans = self.load(CONFIG)
        self.assertEqual(astuple(plans.get("solo")), ("solo", "Solo", 30, "month"))
        self.assertEqual(astuple(plans.get("trial")), ("trial", "Trial", 1, "total"))
        self.assertIsNone(plans.get("unlimited").cases)
        # No plan, or one that's gone from the config, falls back to the default.
        self.assertEqual(plans.get("").name, "trial")
        self.assertEqual(plans.get("gone").name, "trial")

    def test_bad_plans_are_refused(self):
        for text, message in [
            ("[web]\n", "no \\[plans\\] section"),
            ('[plans]\ndefault = "pro"\n[plans.solo]\ncases = 1\n', "plans.default must name"),
            ('[plans]\ndefault = "solo"\n[plans.solo]\ncases = -1\n', "whole number"),
            ('[plans]\ndefault = "solo"\n[plans.solo]\ncases = "30"\n', "whole number"),
            ('[plans]\ndefault = "solo"\n[plans.solo]\ncases = 1\nperiod = "week"\n', "period must be"),
        ]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                self.load(text)

    def test_period_start_and_renewal(self):
        monthly, total = Plan("solo", "Solo", 30, "month"), Plan("trial", "Trial", 5, "total")
        now = datetime(2026, 12, 31, 23, 59, 59)
        self.assertEqual(monthly.period_start(now), "2026-12-01T00:00:00")
        self.assertEqual(monthly.renews_on(now), "2027-01-01")
        self.assertEqual(monthly.renews_on(datetime(2026, 10, 4)), "2026-11-01")
        self.assertEqual((total.period_start(now), total.renews_on(now)), ("", ""))

    def test_both_shipped_configs_have_the_sold_plans(self):
        for name in ("config.toml", "config.server.toml"):
            with self.subTest(config=name):
                plans = load_plans(ROOT / name)
                self.assertEqual(plans.default, "trial")  # an account nobody gave a plan gets the smallest
                self.assertEqual({n: p.cases for n, p in plans.by_name.items()},
                                 {"trial": 5, "solo": 30, "pro": 100, "firm": 60, "unlimited": None})
        self.assertEqual(load_web_config(ROOT / "config.server.toml").base_url, "https://battle.smarkiq.us")
        self.assertEqual(load_web_config(ROOT / "config.toml").base_url, "http://127.0.0.1:8000")
        self.assertEqual(load_web_config(ROOT / "config.toml").max_active_cases, 3)
        self.assertEqual(load_web_config(ROOT / "config.server.toml").max_active_cases, 3)


class UsersCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / "config.toml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.db = Path(self.tmp.name) / "web" / "users.sqlite"
        self.stores = []

    def tearDown(self):
        for store in self.stores:  # before the folder goes: Windows won't delete an open database
            store.close()
        self.tmp.cleanup()

    def run_users(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = users.main(["--config", str(self.config), *args])
        return code, out.getvalue(), err.getvalue()

    def store(self):
        self.stores.append(AuthStore(self.db))
        return self.stores[-1]

    def test_add_prints_a_setup_link_and_never_asks_for_a_password(self):
        with mock.patch("getpass.getpass", side_effect=AssertionError("asked for a password")):
            code, out, _ = self.run_users("add", "Dana", "--plan", "solo")
        self.assertEqual(code, 0)
        self.assertIn("Created dana on the Solo plan (30 cases a month).", out)
        token = re.search(r"^    https://battle\.example/setup/(\S+)$", out, re.M).group(1)
        store = self.store()
        user = store.setup_user(token)
        self.assertEqual((user.username, user.plan, user.needs_setup), ("dana", "solo", True))
        self.assertNotIn(token.encode(), self.db.read_bytes())

        _, listing, _ = self.run_users("list")
        self.assertRegex(listing, r"dana\s+solo\s+created \d{4}-\d{2}-\d{2}  \(no password set yet\)")

    def test_add_uses_the_default_plan_and_refuses_unknown_ones(self):
        code, out, _ = self.run_users("add", "dana")
        self.assertIn("on the Trial plan (1 cases in total)", out)
        code, _, err = self.run_users("add", "erin", "--plan", "platinum")
        self.assertEqual(code, 1)
        self.assertIn("No plan 'platinum'. The config has: trial, solo, unlimited.", err)
        self.assertIsNone(self.store().get_user("erin"))

    def test_add_with_password_prompts_instead(self):
        with mock.patch("getpass.getpass", return_value=PW):
            code, out, _ = self.run_users("add", "boss", "--admin", "--password", "--plan", "unlimited")
        self.assertEqual(code, 0)
        self.assertIn("Created boss (admin) on the Unlimited plan (no limit).", out)
        self.assertNotIn("/setup/", out)
        self.assertTrue(self.store().authenticate("boss", PW).is_admin)

    def test_invite_replaces_the_earlier_link(self):
        _, first, _ = self.run_users("add", "dana")
        code, second, _ = self.run_users("invite", "dana")
        self.assertEqual(code, 0)
        old, new = (re.search(r"/setup/(\S+)", text).group(1) for text in (first, second))
        store = self.store()
        self.assertIsNone(store.setup_user(old))
        self.assertEqual(store.setup_user(new).username, "dana")
        self.assertEqual(self.run_users("invite", "nobody")[0], 1)

    def test_plan_command(self):
        self.run_users("add", "dana")
        code, out, _ = self.run_users("plan", "dana", "solo")
        self.assertEqual(code, 0)
        self.assertIn("dana is now on the Solo plan (30 cases a month).", out)
        self.assertEqual(self.store().get_user("dana").plan, "solo")
        self.assertEqual(self.run_users("plan", "dana", "platinum")[0], 1)
        self.assertEqual(self.store().get_user("dana").plan, "solo")

    def test_firm_commands_and_pooled_usage(self):
        _, out, _ = self.run_users("add", "dana", "--plan", "solo", "--firm", "Whitfield-Law")
        self.assertIn("whitfield-law has 1 seat (dana), sharing 30 cases a month.", out)
        self.run_users("add", "erin", "--plan", "solo")
        code, out, _ = self.run_users("firm", "erin", "whitfield-law")
        self.assertEqual(code, 0)
        self.assertIn("whitfield-law has 2 seats (dana, erin), sharing 60 cases a month.", out)
        self.assertRegex(self.run_users("list")[1], r"erin\s+solo\s+created \S+  \(firm: whitfield-law, no password")
        self.assertEqual(self.run_users("firm", "erin", "two words")[0], 1)

        store = self.store()
        jobs = JobStore(self.db.parent)
        for _ in range(3):
            jobs.create(store.get_user("dana").id, "UT", "facts", "x", 1, "t")
        jobs.create(store.get_user("erin").id, "UT", "facts", "x", 1, "t")
        jobs.close()
        _, out, _ = self.run_users("usage")
        self.assertRegex(out, r"dana\s+solo\s+3\s+30 cases a month\s+0\s+3  \(firm: whitfield-law\)")
        self.assertRegex(out, r"whitfield-law\s+2\s+4\s+60\n")

        # A disabled account isn't a seat, but what it used still counts.
        self.run_users("disable", "erin")
        self.assertRegex(self.run_users("usage")[1], r"whitfield-law\s+1\s+4\s+30\n")

        code, out, _ = self.run_users("firm", "dana")
        self.assertIn("dana is in no firm, and has their own allowance again.", out)
        self.assertIn("whitfield-law has no seats now.", out)
        self.assertEqual(self.store().get_user("dana").firm, "")

    def test_paid_command(self):
        _, out, _ = self.run_users("add", "dana", "--plan", "solo", "--paid-through", "2099-12-31")
        self.assertIn("dana's plan is paid through 2099-12-31.", out)
        self.assertRegex(self.run_users("list")[1], r"dana .*paid through 2099-12-31")

        code, out, _ = self.run_users("paid", "dana", "2026-01-31")
        self.assertEqual(code, 0)
        self.assertIn("dana's plan ended on 2026-01-31.", out)
        self.assertRegex(self.run_users("list")[1], r"dana .*\(expired 2026-01-31, no password")
        self.assertRegex(self.run_users("usage")[1], r"dana .*\(expired 2026-01-31\)")

        code, _, err = self.run_users("paid", "dana", "next month")
        self.assertEqual(code, 1)
        self.assertIn("date must look like", err)
        self.assertEqual(self.store().get_user("dana").paid_through, "2026-01-31")

        _, out, _ = self.run_users("paid", "dana")
        self.assertIn("dana's plan has no end date.", out)
        self.assertEqual(self.run_users("paid", "nobody", "2099-01-01")[0], 1)

    def test_unpaid_seat_leaves_the_firm_allowance(self):
        self.run_users("add", "dana", "--plan", "solo", "--firm", "acme")
        self.run_users("add", "erin", "--plan", "solo", "--firm", "acme")
        _, out, _ = self.run_users("paid", "erin", "2026-01-31")
        self.assertIn("acme has 1 seat (dana), sharing 30 cases a month.", out)
        self.assertRegex(self.run_users("usage")[1], r"acme\s+1\s+0\s+30\n")

    def test_usage_report(self):
        self.run_users("add", "dana", "--plan", "solo")
        self.run_users("add", "erin")
        store = self.store()
        jobs = JobStore(self.db.parent)
        dana, erin = store.get_user("dana").id, store.get_user("erin").id
        for user_id, when, status in [(dana, "2026-09-10T09:00:00", "done"), (dana, "2026-09-11T09:00:00", "failed"),
                                      (dana, "2026-08-01T09:00:00", "done"), (erin, "2026-08-02T09:00:00", "done")]:
            job = jobs.create(user_id, "UT", "facts", "x", 1, "t")
            jobs.update(job.id, status=status)
            with jobs._db:
                jobs._db.execute("UPDATE usage SET created_at = ? WHERE case_id = ?", (when, job.id))
        jobs.close()

        code, out, _ = self.run_users("usage", "--month", "2026-09")
        self.assertEqual(code, 0)
        rows = {line.split()[0]: line.split() for line in out.splitlines()[3:]}
        # account, plan, counted, allowance..., not counted, all time
        self.assertEqual(rows["dana"], ["dana", "solo", "1", "30", "cases", "a", "month", "1", "2"])
        # Erin started nothing in September, but her one trial case is spent.
        self.assertEqual(rows["erin"], ["erin", "trial", "0", "1", "cases", "in", "total", "0", "1", "(used", "up)"])
        self.assertEqual(self.run_users("usage", "--month", "September")[0], 1)


if __name__ == "__main__":
    unittest.main()
