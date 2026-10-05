import sqlite3
import tempfile
import unittest
from pathlib import Path

from battle_test.web import auth
from battle_test.web.auth import AuthError, AuthStore

# Throwaway test values only.
PW = "correct horse battery"
PW2 = "another long passphrase"


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class PasswordHashTest(unittest.TestCase):
    def test_hash_verifies_and_is_salted(self):
        h1, h2 = auth.hash_password(PW), auth.hash_password(PW)
        self.assertNotEqual(h1, h2)
        self.assertTrue(h1.startswith("scrypt$16384$8$1$"))
        self.assertTrue(auth.verify_password(PW, h1))
        self.assertFalse(auth.verify_password(PW + "x", h1))

    def test_bad_stored_values_never_verify(self):
        for stored in ["", "plaintext", "md5$x$y$z$a$b", "scrypt$x$8$1$AAAA$AAAA"]:
            with self.subTest(stored=stored):
                self.assertFalse(auth.verify_password(PW, stored))

    def test_password_rules(self):
        with self.assertRaisesRegex(AuthError, "at least 12"):
            auth.check_password_rules("short", "dana")
        with self.assertRaisesRegex(AuthError, "username"):
            auth.check_password_rules("dana-is-my-password", "Dana")
        auth.check_password_rules(PW, "dana")


class AuthStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.store = AuthStore(Path(self.tmp.name) / "users.sqlite", clock=self.clock)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_create_and_authenticate(self):
        self.store.create_user("Dana", PW, is_admin=True)
        user = self.store.authenticate("dana", PW)  # usernames are case-insensitive
        self.assertEqual((user.username, user.is_admin), ("dana", True))

    def test_passwords_are_not_stored_in_plain_text(self):
        self.store.create_user("dana", PW)
        raw = (Path(self.tmp.name) / "users.sqlite").read_bytes()
        self.assertNotIn(PW.encode(), raw)

    def test_duplicate_and_invalid_usernames(self):
        self.store.create_user("dana", PW)
        with self.assertRaisesRegex(AuthError, "already exists"):
            self.store.create_user("DANA", PW)
        with self.assertRaisesRegex(AuthError, "letters, digits"):
            self.store.create_user("dana smith", PW)

    def test_same_message_for_unknown_user_and_wrong_password(self):
        self.store.create_user("dana", PW)
        messages = set()
        for username, password in (("dana", "wrong password!!"), ("nobody", PW)):
            with self.assertRaises(AuthError) as cm:
                self.store.authenticate(username, password)
            messages.add(str(cm.exception))
        self.assertEqual(messages, {"Wrong username or password."})

    def test_lockout_after_repeated_failures_then_expiry(self):
        self.store.create_user("dana", PW)
        for _ in range(auth.MAX_FAILED_LOGINS):
            with self.assertRaises(AuthError):
                self.store.authenticate("dana", "wrong password!!")
        with self.assertRaisesRegex(AuthError, "Too many"):
            self.store.authenticate("dana", PW)  # even the right password
        self.clock.t += auth.LOCKOUT_SECONDS + 1
        self.assertEqual(self.store.authenticate("dana", PW).username, "dana")

    def test_sessions_expire_and_store_only_a_hash(self):
        user = self.store.create_user("dana", PW)
        token = self.store.create_session(user)
        session = self.store.session(token)
        self.assertEqual(session.user.username, "dana")
        self.assertTrue(session.csrf_token)
        raw = (Path(self.tmp.name) / "users.sqlite").read_bytes()
        self.assertNotIn(token.encode(), raw)
        self.assertIsNone(self.store.session("not-a-token"))
        self.clock.t += auth.SESSION_LIFETIME.total_seconds() + 1
        self.assertIsNone(self.store.session(token))

    def test_new_password_or_disabling_ends_sessions(self):
        user = self.store.create_user("dana", PW)
        token = self.store.create_session(user)
        self.store.set_password("dana", PW2)
        self.assertIsNone(self.store.session(token))
        with self.assertRaises(AuthError):
            self.store.authenticate("dana", PW)
        token = self.store.create_session(self.store.authenticate("dana", PW2))
        self.store.set_disabled("dana", True)
        self.assertIsNone(self.store.session(token))
        with self.assertRaises(AuthError):
            self.store.authenticate("dana", PW2)
        self.store.set_disabled("dana", False)
        self.store.authenticate("dana", PW2)

    def test_logout_ends_only_that_session(self):
        user = self.store.create_user("dana", PW)
        a, b = self.store.create_session(user), self.store.create_session(user)
        self.store.end_session(a)
        self.assertIsNone(self.store.session(a))
        self.assertIsNotNone(self.store.session(b))

    # --- setup links and plans ------------------------------------------------

    def test_invited_account_cannot_sign_in_until_its_password_is_set(self):
        user = self.store.create_user("dana", None, plan="solo")
        self.assertTrue(user.needs_setup)
        self.assertEqual(user.plan, "solo")
        for attempt in ("", auth.NO_PASSWORD, PW):
            with self.assertRaisesRegex(AuthError, "Wrong username or password"):
                self.store.authenticate("dana", attempt)

        token = self.store.create_setup_token("dana")
        raw = (Path(self.tmp.name) / "users.sqlite").read_bytes()
        self.assertNotIn(token.encode(), raw)  # only its hash is stored
        self.assertEqual(self.store.setup_user(token).username, "dana")
        self.store.finish_setup(token, PW)
        user = self.store.authenticate("dana", PW)
        self.assertFalse(user.needs_setup)

    def test_setup_link_works_once(self):
        self.store.create_user("dana", None)
        token = self.store.create_setup_token("dana")
        self.store.finish_setup(token, PW)
        self.assertIsNone(self.store.setup_user(token))
        with self.assertRaisesRegex(AuthError, "expired or was already used"):
            self.store.finish_setup(token, PW2)
        self.store.authenticate("dana", PW)  # the second attempt changed nothing

    def test_setup_link_expires_and_is_replaced_by_a_newer_one(self):
        self.store.create_user("dana", None)
        first = self.store.create_setup_token("dana")
        second = self.store.create_setup_token("dana")
        self.assertIsNone(self.store.setup_user(first))
        self.assertIsNotNone(self.store.setup_user(second))
        self.assertIsNone(self.store.setup_user("not-a-token"))
        self.clock.t += auth.SETUP_LINK_LIFETIME.total_seconds() + 1
        self.assertIsNone(self.store.setup_user(second))
        with self.assertRaises(AuthError):
            self.store.finish_setup(second, PW)

    def test_setup_link_keeps_the_password_rules(self):
        self.store.create_user("dana", None)
        token = self.store.create_setup_token("dana")
        with self.assertRaisesRegex(AuthError, "at least 12"):
            self.store.finish_setup(token, "short")
        self.assertIsNotNone(self.store.setup_user(token))  # a refused password doesn't use the link up

    def test_setup_link_for_an_existing_account(self):
        # A reset: the old password works until the link is used, and using
        # it signs out every session.
        user = self.store.create_user("dana", PW)
        session = self.store.create_session(user)
        token = self.store.create_setup_token("dana")
        self.store.authenticate("dana", PW)
        self.assertIsNotNone(self.store.session(session))
        self.store.finish_setup(token, PW2)
        self.assertIsNone(self.store.session(session))
        with self.assertRaises(AuthError):
            self.store.authenticate("dana", PW)
        self.store.authenticate("dana", PW2)

    def test_disabling_or_a_new_password_cancels_the_setup_link(self):
        self.store.create_user("dana", None)
        token = self.store.create_setup_token("dana")
        self.store.set_password("dana", PW)
        self.assertIsNone(self.store.setup_user(token))

        token = self.store.create_setup_token("dana")
        self.store.set_disabled("dana", True)
        self.assertIsNone(self.store.setup_user(token))
        with self.assertRaisesRegex(AuthError, "disabled"):
            self.store.create_setup_token("dana")
        with self.assertRaisesRegex(AuthError, "No user"):
            self.store.create_setup_token("nobody")

    def test_set_plan(self):
        self.store.create_user("dana", PW)
        self.assertEqual(self.store.get_user("dana").plan, "")
        self.store.set_plan("dana", "pro")
        self.assertEqual(self.store.get_user("dana").plan, "pro")
        self.assertEqual(self.store.session(self.store.create_session(self.store.get_user("dana"))).user.plan, "pro")

    def test_firms(self):
        self.store.create_user("dana", PW, firm="Whitfield-Law")
        self.store.create_user("erin", PW)
        self.store.create_user("solo", PW)
        self.store.set_firm("erin", " whitfield-law ")
        self.store.set_disabled("erin", True)
        members = self.store.firm_members("WHITFIELD-LAW")
        self.assertEqual([(m.username, m.firm, m.disabled) for m in members],
                         [("dana", "whitfield-law", False), ("erin", "whitfield-law", True)])
        self.assertEqual(self.store.firm_members(""), [])  # accounts with no firm aren't one firm
        self.store.set_firm("erin", "")
        self.assertEqual(len(self.store.firm_members("whitfield-law")), 1)
        with self.assertRaisesRegex(AuthError, "Firm names"):
            self.store.set_firm("dana", "two words")

    def test_paid_through(self):
        self.store.create_user("dana", PW)
        user = self.store.get_user("dana")
        self.assertEqual(user.paid_through, "")
        self.assertFalse(user.lapsed("2099-01-01"))  # no date: never ends
        self.store.set_paid_through("dana", "2026-10-31")
        user = self.store.authenticate("dana", PW)   # an unpaid account can still sign in
        self.assertFalse(user.lapsed("2026-10-31"))  # the last paid day is still paid
        self.assertTrue(user.lapsed("2026-11-01"))
        self.assertFalse(user.is_seat("2026-11-01"))
        for bad in ("31/10/2026", "2026-02-30", "soon"):
            with self.assertRaisesRegex(AuthError, "date must look like"):
                self.store.set_paid_through("dana", bad)
        self.store.set_paid_through("dana", "")
        self.assertEqual(self.store.get_user("dana").paid_through, "")

    def test_database_from_before_plans_is_upgraded(self):
        path = Path(self.tmp.name) / "old.sqlite"
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, "
                    "password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, "
                    "disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
        old.execute("INSERT INTO users (username, password_hash, created_at) VALUES ('dana', ?, '2026-09-25T10:00:00')",
                    (auth.hash_password(PW),))
        old.commit()
        old.close()
        store = AuthStore(path)
        try:
            user = store.authenticate("dana", PW)
        finally:
            store.close()
        self.assertEqual((user.plan, user.firm, user.paid_through, user.needs_setup), ("", "", "", False))


if __name__ == "__main__":
    unittest.main()
