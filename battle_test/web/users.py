"""Manage web UI accounts (admin only, from the command line).

    python -m battle_test.web.users add NAME --plan solo   # prints a one-time setup link
    python -m battle_test.web.users add NAME --admin --password   # prompts for a password
    python -m battle_test.web.users invite NAME          # a new setup link (forgotten password)
    python -m battle_test.web.users passwd NAME          # set a new password
    python -m battle_test.web.users plan NAME PLAN       # change the account's plan
    python -m battle_test.web.users firm NAME [FIRM]     # put the account in a firm, or take it out
    python -m battle_test.web.users paid NAME [DATE]     # the plan is paid through DATE (YYYY-MM-DD)
    python -m battle_test.web.users disable NAME         # block sign-in, end sessions
    python -m battle_test.web.users enable NAME
    python -m battle_test.web.users list
    python -m battle_test.web.users usage [--month YYYY-MM]

Accounts in the same firm share one case allowance: their plans' allowances
added together, used by whichever of them needs it.

Once an account's paid-through date has passed it can still sign in and
reach its cases, but can't start new ones until `paid` moves the date on.
`paid NAME` with no date removes the date: the plan never ends.

A new user sets their own password through the setup link, so nobody else
ever knows it. Passwords typed here go to a hidden prompt, never arguments,
so they don't end up in shell history.
"""

import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path

from battle_test.config import DEFAULT_CONFIG_PATH, Plan, Plans, load_plans, load_web_config
from battle_test.web.auth import MIN_PASSWORD_LENGTH, SETUP_LINK_LIFETIME, AuthError, AuthStore, User
from battle_test.web.jobs import JobStore

# Who the activity log says made a change from here.
BY = "command line"


def users_db(config_path: Path) -> Path:
    return load_web_config(config_path).data_dir / "users.sqlite"


def _ask_password() -> str:
    first = getpass.getpass(f"Password (at least {MIN_PASSWORD_LENGTH} characters): ")
    if first != getpass.getpass("Repeat password: "):
        raise AuthError("The passwords didn't match.")
    return first


def _print_setup_link(store: AuthStore, username: str, base_url: str) -> None:
    token = store.create_setup_token(username, by=BY)
    hours = int(SETUP_LINK_LIFETIME.total_seconds() // 3600)
    print(f"Setup link for {username.lower()} (works once, for {hours} hours):\n\n"
          f"    {base_url}/setup/{token}\n\n"
          "Send it to them privately. Until it's used, anyone who has it can set this account's password.\n"
          "Any earlier link for this account no longer works.")


def _allowance(plan: Plan) -> str:
    if plan.cases is None:
        return "no limit"
    return f"{plan.cases} cases{' a month' if plan.period == 'month' else ' in total'}"


def _paid_summary(user: User, today: str) -> str:
    if not user.paid_through:
        return f"{user.username}'s plan has no end date."
    if user.lapsed(today):
        return (f"{user.username}'s plan ended on {user.paid_through}. They can sign in and reach their cases, "
                "but can't start new ones.")
    return f"{user.username}'s plan is paid through {user.paid_through}."


def _firm_summary(store: AuthStore, plans: Plans, firm: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    seats = [m for m in store.firm_members(firm) if m.is_seat(today)]
    if not seats:
        return f"{firm} has no seats now."
    limit = plans.pooled_cases([m.plan for m in seats])
    period = " a month" if plans.get(seats[0].plan).period == "month" else " in total"
    return (f"{firm} has {len(seats)} seat{'s' if len(seats) != 1 else ''} ({', '.join(m.username for m in seats)}), "
            f"sharing {'cases with no limit' if limit is None else f'{limit} cases{period}'}.")


def _month_bounds(month: str) -> tuple[str, str]:
    try:
        start = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise AuthError(f"--month must look like 2026-10, got {month!r}.") from None
    end = datetime(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="battle_test.web.users", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Create an account and print its one-time setup link.")
    add.add_argument("username")
    add.add_argument("--admin", action="store_true", help="Mark the account as an admin.")
    add.add_argument("--plan", help="The account's plan, from [plans] in the config. Default: plans.default.")
    add.add_argument("--firm", default="", help="The firm whose shared case allowance the account joins.")
    add.add_argument("--paid-through", default="", metavar="DATE",
                     help="The last day the plan is paid for (YYYY-MM-DD). Default: no end.")
    add.add_argument("--password", action="store_true",
                     help="Type the password here instead of printing a setup link (for your own account).")
    for name, help_text in (("invite", "Print a new one-time setup link."), ("passwd", "Set a new password."),
                            ("disable", "Block sign-in."), ("enable", "Allow sign-in again.")):
        sub.add_parser(name, help=help_text).add_argument("username")
    plan_cmd = sub.add_parser("plan", help="Change an account's plan.")
    plan_cmd.add_argument("username")
    plan_cmd.add_argument("plan")
    firm_cmd = sub.add_parser("firm", help="Put an account in a firm (a shared case allowance), or take it out.")
    firm_cmd.add_argument("username")
    firm_cmd.add_argument("firm", nargs="?", default="", help="Leave out to take the account out of its firm.")
    paid = sub.add_parser("paid", help="Set the last day an account's plan is paid for.")
    paid.add_argument("username")
    paid.add_argument("date", nargs="?", default="", help="YYYY-MM-DD. Leave out for a plan that never ends.")
    sub.add_parser("list", help="List accounts.")
    usage = sub.add_parser("usage", help="Cases started per account in a month.")
    usage.add_argument("--month", default=datetime.now().strftime("%Y-%m"), help="YYYY-MM. Default: this month.")
    args = parser.parse_args(argv)

    web = load_web_config(args.config)
    plans = load_plans(args.config)
    store = AuthStore(web.data_dir / "users.sqlite")
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        plan = getattr(args, "plan", None)
        if plan is not None and plan not in plans.by_name:
            raise AuthError(f"No plan {plan!r}. The config has: {', '.join(plans.by_name)}.")
        if args.command == "add":
            password = _ask_password() if args.password else None
            user = store.create_user(args.username, password, is_admin=args.admin, plan=plan or plans.default,
                                     firm=args.firm, by=BY)
            print(f"Created {user.username}{' (admin)' if user.is_admin else ''} on the "
                  f"{plans.get(user.plan).label} plan ({_allowance(plans.get(user.plan))}).")
            if args.paid_through:
                store.set_paid_through(user.username, args.paid_through, by=BY)
                print(_paid_summary(store.get_user(user.username), today))
            if user.firm:
                print(_firm_summary(store, plans, user.firm))
            if password is None:
                _print_setup_link(store, user.username, web.base_url)
        elif args.command == "invite":
            _print_setup_link(store, args.username, web.base_url)
        elif args.command == "passwd":
            store.set_password(args.username, _ask_password(), by=BY)
            print(f"Password changed for {args.username}. Their existing sessions were signed out.")
        elif args.command == "plan":
            store.set_plan(args.username, plan, by=BY)
            print(f"{args.username} is now on the {plans.get(plan).label} plan ({_allowance(plans.get(plan))}).")
        elif args.command == "firm":
            before = store.get_user(args.username)
            store.set_firm(args.username, args.firm, by=BY)
            after = store.get_user(args.username)
            if after.firm:
                print(f"{after.username} is now in {after.firm}.\n{_firm_summary(store, plans, after.firm)}")
            else:
                print(f"{after.username} is in no firm, and has their own allowance again.")
            if before.firm and before.firm != after.firm:
                print(_firm_summary(store, plans, before.firm))
        elif args.command == "paid":
            store.set_paid_through(args.username, args.date, by=BY)
            user = store.get_user(args.username)
            print(_paid_summary(user, today))
            if user.firm:
                print(_firm_summary(store, plans, user.firm))
        elif args.command in ("disable", "enable"):
            store.set_disabled(args.username, args.command == "disable", by=BY)
            print(f"{args.username} {args.command}d.")
        elif args.command == "usage":
            since, until = _month_bounds(args.month)
            jobs = JobStore(web.data_dir)
            try:
                month, ever = jobs.usage_by_user(since, until), jobs.usage_by_user()
            finally:
                jobs.close()
            print(f"Cases started in {args.month}. Failed runs, and cases deleted before they ran, aren't counted.\n")
            print(f"{'account':24} {'plan':10} {'counted':>7} {'allowance':>18} {'not counted':>11} {'all time':>8}")
            for u in store.list_users():
                p = plans.get(u.plan)
                counted, uncounted = month.get(u.id, (0, 0))
                all_time = ever.get(u.id, (0, 0))[0]
                # A plan that never renews is measured against every case ever, not the month's.
                over = p.cases is not None and (counted if p.period == "month" else all_time) >= p.cases
                note = (f"  (expired {u.paid_through})" if u.lapsed(today) else f"  (firm: {u.firm})" if u.firm
                        else "  (used up)" if over else "")
                print(f"{u.username:24} {p.name:10} {counted:>7} {_allowance(p):>18} {uncounted:>11} "
                      f"{all_time:>8}{note}")
            firms = sorted({u.firm for u in store.list_users() if u.firm})
            if firms:
                print("\nFirms share their seats' allowances.\n")
                print(f"{'firm':24} {'seats':>5} {'counted':>7} {'allowance':>10}")
            for firm in firms:
                members = store.firm_members(firm)
                seats = [m for m in members if m.is_seat(today)]
                limit = plans.pooled_cases([m.plan for m in seats])
                # As on the site: measured over the period of the seats' plan.
                source = month if not seats or plans.get(seats[0].plan).period == "month" else ever
                counted = sum(source.get(m.id, (0, 0))[0] for m in members)
                print(f"{firm:24} {len(seats):>5} {counted:>7} {'no limit' if limit is None else limit:>10}"
                      f"{'  (used up)' if limit is not None and counted >= limit else ''}")
        else:
            users = store.list_users()
            for u in users:
                flags = ", ".join(f for f, on in ((f"firm: {u.firm}", u.firm), ("admin", u.is_admin),
                                                  ("disabled", u.disabled),
                                                  (f"expired {u.paid_through}", u.lapsed(today)),
                                                  (f"paid through {u.paid_through}",
                                                   u.paid_through and not u.lapsed(today)),
                                                  ("no password set yet", u.needs_setup)) if on)
                print(f"{u.username:24} {plans.get(u.plan).name:10} created {u.created_at[:10]}"
                      f"{f'  ({flags})' if flags else ''}")
            if not users:
                print("No accounts yet. Create one with: python -m battle_test.web.users add NAME --admin --password")
    except AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
