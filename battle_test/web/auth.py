"""Accounts and sessions for the web UI.

- Accounts are created by an admin from the command line
  (python -m battle_test.web.users). There's no self-signup, as in smark_iq.
- A new user sets their own password through a one-time setup link, so the
  admin never knows it. Only a SHA-256 of the link's token is stored, and it
  stops working once used, replaced, or after SETUP_LINK_LIFETIME.
- Passwords are stored only as scrypt hashes, with a random salt per user.
- Sessions are random tokens in an HttpOnly cookie. The database keeps only
  a SHA-256 of each token, so a copy of the database can't be used to log in.
- Each session has its own CSRF token, which every form must send back.
- Repeated failed logins lock that username for a while.
- Every change to an account, and every sign-in, is recorded in an activity
  log that says who did it. Nothing here deletes from it.
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
SETUP_LINK_LIFETIME = timedelta(hours=72)
SETUP_LINK_INVALID = "This link has expired or was already used. Ask your admin for a new one."
# Stored instead of a hash until the user sets a password. It never verifies.
NO_PASSWORD = "!"

# scrypt cost: n=2**14, r=8, p=1 (~16 MB, tens of ms per hash).
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,   -- stored lowercase
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT '',   -- a name from [plans] in the config; '' = the default plan
    firm TEXT NOT NULL DEFAULT '',   -- accounts with the same firm share one case allowance; '' = none
    paid_through TEXT NOT NULL DEFAULT ''   -- YYYY-MM-DD, the last day the plan is paid for; '' = no end
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setup_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    expires_at TEXT NOT NULL
);
-- The activity log: sign-ins and every change to an account. Append-only.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,     -- who did it: a username, or "command line"
    kind TEXT NOT NULL,      -- e.g. "plan changed", "signed in"
    subject TEXT NOT NULL,   -- the account it happened to
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_by_subject ON events (subject, id);
"""

# What _user() reads, from "users u". The last column is needs_setup.
_USER_COLUMNS = ("u.id, u.username, u.is_admin, u.disabled, u.created_at, u.plan, u.firm, "
                 f"u.paid_through, u.password_hash = '{NO_PASSWORD}'")


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
    plan: str = ""
    firm: str = ""  # accounts with the same firm pool their case allowances
    paid_through: str = ""  # YYYY-MM-DD, the last day the plan is paid for; "" = no end
    needs_setup: bool = False  # invited, and hasn't set a password yet

    def lapsed(self, today: str) -> bool:
        """The plan wasn't paid past an earlier day. The account can still
        sign in and reach its cases, but not start new ones."""
        return bool(self.paid_through) and self.paid_through < today

    def is_seat(self, today: str) -> bool:
        """Whether the account's plan adds to its firm's shared allowance."""
        return not self.disabled and not self.lapsed(today)


@dataclass(frozen=True)
class Event:
    at: str
    actor: str
    kind: str
    subject: str
    detail: str


@dataclass(frozen=True)
class Session:
    user: User
    csrf_token: str


class AuthStore:
    def __init__(self, db_path: Path, clock=time.time):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(users)")}
        for column in ("plan", "firm", "paid_through"):  # created before these existed
            if column not in columns:
                self._db.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        self._lock = threading.Lock()
        self._clock = clock
        self._failures: dict[str, list[float]] = {}  # username -> recent failure times

    def close(self) -> None:
        self._db.close()

    # --- activity log --------------------------------------------------------
    # Methods that change an account take `by` (who is doing it) and record
    # the change in the same transaction as the change itself.

    def log(self, kind: str, subject: str, *, by: str = "", detail: str = "") -> None:
        with self._lock, self._db:
            self._log(kind, subject, by, detail)

    def _log(self, kind: str, subject: str, by: str, detail: str = "") -> None:
        """Call inside a transaction."""
        at = datetime.fromtimestamp(self._clock()).isoformat(timespec="seconds")
        self._db.execute("INSERT INTO events (at, actor, kind, subject, detail) VALUES (?, ?, ?, ?, ?)",
                         (at, by, kind, subject, detail))

    def events(self, subject: str | None = None, limit: int = 200) -> list[Event]:
        """The newest events first, for one account or for all of them."""
        where, args = ("WHERE subject = ?", (subject,)) if subject is not None else ("", ())
        with self._lock:
            rows = self._db.execute(
                f"SELECT at, actor, kind, subject, detail FROM events {where} ORDER BY id DESC LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [Event(*r) for r in rows]

    def last_events(self, kind: str) -> dict[str, str]:
        """Per account, when an event of this kind last happened."""
        with self._lock:
            return dict(self._db.execute("SELECT subject, MAX(at) FROM events WHERE kind = ? GROUP BY subject",
                                         (kind,)).fetchall())

    # --- users -------------------------------------------------------------

    def create_user(self, username: str, password: str | None, *, is_admin: bool = False,
                    plan: str = "", firm: str = "", by: str = "") -> User:
        """With password None the account can't sign in until its user sets
        one through a setup link (create_setup_token)."""
        username = username.strip().lower()
        if not username or not username.replace("_", "").replace("-", "").replace(".", "").isalnum():
            raise AuthError("Usernames may contain only letters, digits, '.', '_' and '-'.")
        if password is not None:
            check_password_rules(password, username)
        firm = _firm_name(firm)
        try:
            with self._lock, self._db:
                self._db.execute(
                    "INSERT INTO users (username, password_hash, is_admin, created_at, plan, firm) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (username, NO_PASSWORD if password is None else hash_password(password), int(is_admin),
                     _now(), plan, firm),
                )
                self._log("account created", username, by, ", ".join(
                    part for part in (f"plan {plan}" if plan else "", f"firm {firm}" if firm else "",
                                      "admin" if is_admin else "") if part))
        except sqlite3.IntegrityError:
            raise AuthError(f"User {username!r} already exists.") from None
        return self.get_user(username)

    def get_user(self, username: str) -> User | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM users u WHERE u.username = ?",
                (username.strip().lower(),),
            ).fetchone()
        return _user(row)

    def list_users(self) -> list[User]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM users u ORDER BY u.username"
            ).fetchall()
        return [_user(r) for r in rows]

    def set_password(self, username: str, password: str, *, by: str = "") -> None:
        user = self._require(username)
        check_password_rules(password, user.username)
        with self._lock, self._db:
            self._store_password(user.id, hash_password(password))
            self._log("password changed", user.username, by)

    def _store_password(self, user_id: int, password_hash: str) -> None:
        """Call inside a transaction. A new password signs out every existing
        session and cancels any setup link."""
        self._db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self._db.execute("DELETE FROM setup_tokens WHERE user_id = ?", (user_id,))

    def set_plan(self, username: str, plan: str, *, by: str = "") -> None:
        user = self._require(username)
        with self._lock, self._db:
            self._db.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user.id))
            self._log("plan changed", user.username, by, f"{user.plan or 'none'} to {plan or 'none'}")

    def set_firm(self, username: str, firm: str, *, by: str = "") -> None:
        """Put the account in a firm, or take it out of one with ""."""
        user, firm = self._require(username), _firm_name(firm)
        with self._lock, self._db:
            self._db.execute("UPDATE users SET firm = ? WHERE id = ?", (firm, user.id))
            self._log("firm changed", user.username, by, f"{user.firm or 'none'} to {firm or 'none'}")

    def set_paid_through(self, username: str, day: str, *, by: str = "") -> None:
        """The last day the account's plan is paid for (YYYY-MM-DD), or ""
        for no end."""
        user = self._require(username)
        if day:
            try:
                day = datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                raise AuthError(f"The date must look like 2026-11-30, got {day!r}.") from None
        with self._lock, self._db:
            self._db.execute("UPDATE users SET paid_through = ? WHERE id = ?", (day, user.id))
            self._log("paid-through changed", user.username, by,
                      f"{user.paid_through or 'no end'} to {day or 'no end'}")

    def firm_members(self, firm: str) -> list[User]:
        """Every account in the firm, disabled ones included."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM users u WHERE u.firm = ? AND u.firm != '' ORDER BY u.username",
                (_firm_name(firm),),
            ).fetchall()
        return [_user(r) for r in rows]

    def set_disabled(self, username: str, disabled: bool, *, by: str = "") -> None:
        user = self._require(username)
        with self._lock, self._db:
            self._db.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(disabled), user.id))
            self._log("disabled" if disabled else "enabled", user.username, by)
            if disabled:
                self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user.id,))
                self._db.execute("DELETE FROM setup_tokens WHERE user_id = ?", (user.id,))

    def _require(self, username: str) -> User:
        user = self.get_user(username)
        if user is None:
            raise AuthError(f"No user {username!r}.")
        return user

    # --- setup links ---------------------------------------------------------

    def create_setup_token(self, username: str, *, by: str = "") -> str:
        """A one-time token that lets its holder set this account's password
        (the last part of the setup link). It replaces any earlier one. The
        current password, if there is one, keeps working until it's used."""
        user = self._require(username)
        if user.disabled:
            raise AuthError(f"{user.username} is disabled. Enable the account first.")
        token = secrets.token_urlsafe(32)
        expires = datetime.fromtimestamp(self._clock()) + SETUP_LINK_LIFETIME
        with self._lock, self._db:
            self._db.execute("DELETE FROM setup_tokens WHERE user_id = ?", (user.id,))
            self._db.execute("INSERT INTO setup_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                             (_token_hash(token), user.id, expires.isoformat(timespec="seconds")))
            self._log("setup link issued", user.username, by)
        return token

    def setup_user(self, token: str) -> User | None:
        """Whose password this token may set, or None if it's no longer valid."""
        now = datetime.fromtimestamp(self._clock()).isoformat(timespec="seconds")
        with self._lock:
            row = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM setup_tokens t JOIN users u ON u.id = t.user_id "
                "WHERE t.token_hash = ? AND t.expires_at > ? AND u.disabled = 0",
                (_token_hash(token), now),
            ).fetchone()
        return _user(row)

    def finish_setup(self, token: str, password: str) -> User:
        user = self.setup_user(token)
        if user is None:
            raise AuthError(SETUP_LINK_INVALID)
        check_password_rules(password, user.username)
        password_hash = hash_password(password)
        with self._lock, self._db:
            # Deleting the token is what claims it, so a link works only once
            # even if two requests arrive together.
            claimed = self._db.execute("DELETE FROM setup_tokens WHERE token_hash = ?", (_token_hash(token),))
            if not claimed.rowcount:
                raise AuthError(SETUP_LINK_INVALID)
            self._store_password(user.id, password_hash)
            self._log("password set with setup link", user.username, user.username)
        self._failures.pop(user.username, None)
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
                f"SELECT {_USER_COLUMNS}, u.password_hash FROM users u WHERE u.username = ?",
                (key,),
            ).fetchone()
        # Hash even for unknown users, and ones with no password yet, so
        # response time doesn't reveal them.
        known = row is not None and row[-1] != NO_PASSWORD
        stored = row[-1] if known else hash_password(secrets.token_hex(8))
        if not verify_password(password, stored) or not known or row[3]:
            self._failures.setdefault(key, []).append(self._clock())
            raise AuthError("Wrong username or password.")
        self._failures.pop(key, None)
        return _user(row[:-1])

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
                f"SELECT {_USER_COLUMNS}, s.csrf_token "
                "FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?",
                (_token_hash(token), now),
            ).fetchone()
        if row is None or row[3]:
            return None
        return Session(_user(row[:-1]), row[-1])

    def end_session(self, token: str | None) -> None:
        if token:
            with self._lock, self._db:
                self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


def _user(row) -> User | None:
    if row is None:
        return None
    return User(row[0], row[1], bool(row[2]), bool(row[3]), row[4], row[5], row[6], row[7], bool(row[8]))


def _firm_name(firm: str) -> str:
    firm = firm.strip().lower()
    if firm and not firm.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise AuthError("Firm names may contain only letters, digits, '.', '_' and '-'.")
    return firm


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
