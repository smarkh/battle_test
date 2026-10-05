"""The admin pages: accounts, their plans, and the activity log.

For signed-in admins only; everyone else gets a 404. The limits are
deliberate, because an admin sign-in on a public site is worth stealing:

- Every change asks for the admin's own password again, so a session left
  open, or a stolen cookie, isn't enough to change anything.
- Admins see accounts, usage counts and the activity log, never a case's
  title, text or results.
- Making or removing admins is left to the command line, and so are setup
  links for, and disabling of, admin accounts: one admin sign-in can't be
  used to take over another or to create more.
- Every change is recorded in the activity log with who made it, and nothing
  here can remove an entry.
"""

from datetime import datetime
from typing import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from battle_test.config import Plans, WebConfig
from battle_test.web.auth import SETUP_LINK_LIFETIME, AuthError, AuthStore, Session, User
from battle_test.web.jobs import JobStore

COMMAND_LINE_ONLY = "That's done from the command line for admin accounts."


def add_routes(app: FastAPI, *, auth: AuthStore, store: JobStore, plans: Plans, web: WebConfig,
               current: Callable[[Request], Session],
               checked_form: Callable[[Request, Session], Awaitable[dict[str, str]]],
               page: Callable[..., HTMLResponse], allowance_for: Callable[[User], object]) -> None:

    def admin(request: Request) -> Session:
        session = current(request)
        if not session.user.is_admin:
            raise HTTPException(404, "Not Found")  # as if the page didn't exist
        return session

    def account(username: str) -> User:
        user = auth.get_user(username)
        if user is None:
            raise HTTPException(404, "No such account")
        return user

    async def confirmed(request: Request) -> tuple[Session, dict[str, str], str]:
        """The admin, the form, and an error unless the form carried the
        admin's own password."""
        session = admin(request)
        form = await checked_form(request, session)
        me = session.user.username
        try:
            auth.authenticate(me, form.get("admin_password", ""))
        except AuthError as e:
            auth.log("admin password refused", me, by=me)
            wrong = "Your password is wrong, so nothing was changed."
            return session, form, str(e) if str(e).startswith("Too many") else wrong
        return session, form, ""

    def accounts_page(request: Request, session: Session, *, error: str = "", form: dict | None = None,
                      status: int = 200) -> HTMLResponse:
        today = datetime.now().strftime("%Y-%m-%d")
        last_seen = auth.last_events("signed in")
        rows = [{"user": u, "allowance": allowance_for(u), "lapsed": u.lapsed(today),
                 "last_seen": last_seen.get(u.username, "")} for u in auth.list_users()]
        response = page(request, "admin.html", session, rows=rows, plans=plans, error=error, form=form or {})
        response.status_code = status
        return response

    def account_page(request: Request, session: Session, user: User, *, error: str = "", message: str = "",
                     token: str = "", status: int = 200) -> HTMLResponse:
        user = account(user.username)  # as it is now, after any change
        response = page(
            request, "admin_user.html", session, user=user, plans=plans, error=error, message=message,
            their=allowance_for(user), lapsed=user.lapsed(datetime.now().strftime("%Y-%m-%d")),
            events=auth.events(user.username, 100), cases=store.usage_rows(user.id, 100),
            link=f"{web.base_url}/setup/{token}" if token else "",
            link_hours=int(SETUP_LINK_LIFETIME.total_seconds() // 3600),
            # Admin accounts are managed from the command line.
            locked=user.is_admin,
        )
        response.status_code = status
        return response

    @app.get("/admin", response_class=HTMLResponse)
    def accounts(request: Request):
        return accounts_page(request, admin(request))

    @app.get("/admin/activity", response_class=HTMLResponse)
    def activity(request: Request):
        return page(request, "admin_activity.html", admin(request), events=auth.events(limit=300))

    @app.post("/admin/users")
    async def add_account(request: Request):
        session, form, error = await confirmed(request)
        if error:
            return accounts_page(request, session, error=error, form=form, status=403)
        by, username = session.user.username, form.get("username", "")
        plan = form.get("plan", "")
        try:
            if plan not in plans.by_name:
                raise AuthError("Choose a plan.")
            user = auth.create_user(username, None, plan=plan, firm=form.get("firm", ""), by=by)
        except AuthError as e:
            return accounts_page(request, session, error=str(e), form=form, status=400)
        try:
            if form.get("paid_through", ""):
                auth.set_paid_through(user.username, form["paid_through"], by=by)
        except AuthError as e:
            error = f"The account was created, but: {e}"
        return account_page(request, session, user, error=error, token=auth.create_setup_token(user.username, by=by),
                            message=f"Created {user.username}.")

    @app.get("/admin/users/{username}", response_class=HTMLResponse)
    def view_account(request: Request, username: str):
        session = admin(request)
        return account_page(request, session, account(username))

    @app.post("/admin/users/{username}/edit")
    async def edit_account(request: Request, username: str):
        session, form, error = await confirmed(request)
        user = account(username)
        if error:
            return account_page(request, session, user, error=error, status=403)
        by = session.user.username
        plan, firm, paid = form.get("plan", ""), form.get("firm", "").strip().lower(), form.get("paid_through", "")
        try:
            if plan not in plans.by_name:
                raise AuthError("Choose a plan.")
            # Only what changed, so the activity log shows just that.
            if paid != user.paid_through:
                auth.set_paid_through(user.username, paid, by=by)
            if firm != user.firm:
                auth.set_firm(user.username, firm, by=by)
            if plan != user.plan:
                auth.set_plan(user.username, plan, by=by)
        except AuthError as e:
            return account_page(request, session, user, error=str(e), status=400)
        return account_page(request, session, user, message="Saved.")

    @app.post("/admin/users/{username}/invite")
    async def invite(request: Request, username: str):
        session, _, error = await confirmed(request)
        user = account(username)
        if error or user.is_admin:
            return account_page(request, session, user, error=error or COMMAND_LINE_ONLY, status=403)
        try:
            token = auth.create_setup_token(user.username, by=session.user.username)
        except AuthError as e:
            return account_page(request, session, user, error=str(e), status=400)
        return account_page(request, session, user, token=token)

    async def set_disabled(request: Request, username: str, disabled: bool):
        session, _, error = await confirmed(request)
        user = account(username)
        if error or user.is_admin:
            return account_page(request, session, user, error=error or COMMAND_LINE_ONLY, status=403)
        auth.set_disabled(user.username, disabled, by=session.user.username)
        return account_page(request, session, user,
                            message="Disabled, and signed out everywhere." if disabled else "Enabled.")

    @app.post("/admin/users/{username}/disable")
    async def disable(request: Request, username: str):
        return await set_disabled(request, username, True)

    @app.post("/admin/users/{username}/enable")
    async def enable(request: Request, username: str):
        return await set_disabled(request, username, False)
