"""Manage web UI accounts (admin only, from the command line).

    python -m battle_test.web.users add NAME [--admin]   # prompts for a password
    python -m battle_test.web.users passwd NAME          # set a new password
    python -m battle_test.web.users disable NAME         # block sign-in, end sessions
    python -m battle_test.web.users enable NAME
    python -m battle_test.web.users list

Passwords are typed at a hidden prompt, never passed as arguments, so they
don't end up in shell history.
"""

import argparse
import getpass
import sys
from pathlib import Path

from battle_test.config import DEFAULT_CONFIG_PATH, load_web_config
from battle_test.web.auth import MIN_PASSWORD_LENGTH, AuthError, AuthStore


def users_db(config_path: Path) -> Path:
    return load_web_config(config_path).data_dir / "users.sqlite"


def _ask_password() -> str:
    first = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters): ")
    if first != getpass.getpass("Repeat password: "):
        raise AuthError("The passwords didn't match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="battle_test.web.users", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Create an account.")
    add.add_argument("username")
    add.add_argument("--admin", action="store_true", help="Mark the account as an admin.")
    for name, help_text in (("passwd", "Set a new password."), ("disable", "Block sign-in."),
                            ("enable", "Allow sign-in again.")):
        sub.add_parser(name, help=help_text).add_argument("username")
    sub.add_parser("list", help="List accounts.")
    args = parser.parse_args(argv)

    store = AuthStore(users_db(args.config))
    try:
        if args.command == "add":
            user = store.create_user(args.username, _ask_password(), is_admin=args.admin)
            print(f"Created {user.username}{' (admin)' if user.is_admin else ''}.")
        elif args.command == "passwd":
            store.set_password(args.username, _ask_password())
            print(f"Password changed for {args.username}. Their existing sessions were signed out.")
        elif args.command in ("disable", "enable"):
            store.set_disabled(args.username, args.command == "disable")
            print(f"{args.username} {args.command}d.")
        else:
            users = store.list_users()
            for u in users:
                flags = ", ".join(f for f, on in (("admin", u.is_admin), ("disabled", u.disabled)) if on)
                print(f"{u.username:24} created {u.created_at[:10]}{f'  ({flags})' if flags else ''}")
            if not users:
                print("No accounts yet. Create one with: python -m battle_test.web.users add NAME --admin")
    except AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
