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


if __name__ == "__main__":
    unittest.main()
