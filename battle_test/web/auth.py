"""Accounts and sessions for the web UI.

- Accounts are created by an admin from the command line
  (python -m battle_test.web.users). There's no self-signup, as in smark_iq.
- Passwords are stored only as scrypt hashes, with a random salt per user.
- Sessions are random tokens in an HttpOnly cookie. The database keeps only
  a SHA-256 of each token, so a copy of the database can't be used to log in.
- Each session has its own CSRF token, which every form must send back.
- Repeated failed logins lock that username for a while.
"""

import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

MIN_PASSWORD_LENGTH = 12
SESSION_LIFETIME = timedelta(days=7)
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60

# scrypt cost: n=2**14, r=8, p=1 (~16 MB, tens of ms per hash).
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,   -- stored lowercase
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


class AuthError(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=64, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=len(expected),
                                n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_password_rules(password: str, username: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters.")
    if username.lower() in password.lower():
        raise AuthError("The password can't contain the username.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class User:
    id: int
    username: str
    is_admin: bool
    disabled: bool
    created_at: str


@dataclass(frozen=True)
class Session:
    user: User
    csrf_token: str


class AuthStore:
    def __init__(self, db_path: Path, clock=time.time):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._clock = clock
        self._failures: dict[str, list[float]] = {}  # username -> recent failure times

    def close(self) -> None:
        self._db.close()

    # --- users -------------------------------------------------------------

    def create_user(self, username: str, password: str, *, is_admin: bool = False) -> User:
        username = username.strip().lower()
        if not username or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise AuthError("Usernames may contain only letters, digits, '.', '_' and '-'.")
        check_password_rules(password, username)
        try:
            with self._lock, self._db:
                self._db.execute(
                    "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), int(is_admin), _now()),
                )
        except sqlite3.IntegrityError:
            raise AuthError(f"User {username!r} already exists.") from None
        return self.get_user(username)

    def get_user(self, username: str) -> User | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, username, is_admin, disabled, created_at FROM users WHERE username = ?",
                (username.strip().lower(),),
            ).fetchone()
        return _user(row)

    def list_users(self) -> list[User]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, username, is_admin, disabled, created_at FROM users ORDER BY username"
            ).fetchall()
        return [_user(r) for r in rows]

    def set_password(self, username: str, password: str) -> None:
        user = self._require(username)
        check_password_rules(password, user.username)
        with self._lock, self._db:
            self._db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user.id))
            # A new password signs out every existing session.
            self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user.id,))

    def set_disabled(self, username: str, disabled: bool) -> None:
        user = self._require(username)
        with self._lock, self._db:
            self._db.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user.id))
            if disabled:
                self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user.id,))

    def _require(self, username: str) -> User:
        user = self.get_user(username)
        if user is None:
            raise AuthError(f"No user {username!r}.")
        return user

    # --- logging in ----------------------------------------------------------

    def authenticate(self, username: str, password: str) -> User:
        """The user, if the password is right. The error message is the same
        for a wrong username and a wrong password, so it doesn't reveal which
        usernames exist."""
        key = username.strip().lower()
        if self._locked(key):
            raise AuthError("Too many failed attempts. Try again in a few minutes.")
        with self._lock:
            row = self._db.execute(
                "SELECT id, username, is_admin, disabled, created_at, password_hash FROM users WHERE username = ?",
                (key,),
            ).fetchone()
        # Hash even for unknown users, so response time doesn't reveal them.
        stored = row[5] if row else hash_password(secrets.token_hex(8))
        if not verify_password(password, stored) or row is None or row[3]:
            self._failures.setdefault(key, []).append(self._clock())
            raise AuthError("Wrong username or password.")
        self._failures.pop(key, None)
        return _user(row[:5])

    def _locked(self, key: str) -> bool:
        cutoff = self._clock() - LOCKOUT_SECONDS
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        self._failures[key] = recent
        return len(recent) >= MAX_FAILED_LOGINS

    # --- sessions -----------------------------------------------------------

    def create_session(self, user: User) -> str:
        """A new session token, to be set as the cookie value."""
        token = secrets.token_urlsafe(32)
        now = datetime.fromtimestamp(self._clock())
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (_token_hash(token), user.id, secrets.token_urlsafe(32),
                 now.isoformat(timespec="seconds"), (now + SESSION_LIFETIME).isoformat(timespec="seconds")),
            )
        return token

    def session(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = datetime.fromtimestamp(self._clock()).isoformat(timespec="seconds")
        with self._lock:
            row = self._db.execute(
                "SELECT u.id, u.username, u.is_admin, u.disabled, u.created_at, s.csrf_token "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?",
                (_token_hash(token), now),
            ).fetchone()
        if row is None or row[3]:
            return None
        return Session(_user(row[:5]), row[5])

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock, self._db:
                self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def _user(row) -> User | None:
    if row is None:
        return None
    return User(row[0], row[1], bool(row[2]), bool(row[3]), row[4])


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
