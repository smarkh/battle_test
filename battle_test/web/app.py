"""FastAPI app: sign-in, case form, live progress, results, download.

Plan step 5, parts 1-2. Every page except /login needs a signed-in user,
and each user sees only their own cases. Until the HTTPS deployment step,
it's only ever served on this machine (see __main__.py).
"""

import asyncio
import dataclasses
import hashlib
import hmac
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from battle_test.config import (DEFAULT_CONFIG_PATH, Plan, Plans, load_config, load_corpus_config, load_plans,
                                load_web_config)
from battle_test.law_index import LawIndex
from battle_test.ollama_client import OllamaClient
from battle_test.pipeline import ChatClient, run_case
from battle_test.prompts import SUPPORTED_STATES
from battle_test.report import render_markdown
from battle_test.web import admin, export, render
from battle_test.web.auth import SETUP_LINK_INVALID, AuthError, AuthStore, Session, User
from battle_test.web.jobs import DONE, FAILED, QUEUED, RUNNING, Job, JobStore, QueueFull, QuotaExceeded, Worker

HERE = Path(__file__).resolve().parent
MAX_INPUT_CHARS = 60_000  # ~15k tokens: a long complaint, still inside the models' context
SESSION_COOKIE = "bt_session"
PURGE_INTERVAL_SECONDS = 3600  # how often expired cases are deleted
# Cloudflare closes connections that stay silent for ~100 s, and a run can go
# longer than that between progress events (e.g. while the model thinks
# during research). A comment line this often keeps the stream open.
HEARTBEAT_SECONDS = 15

MODES = {
    "facts": "Complaint and motion drafted",
    "complaint": "Your complaint, motion drafted",
}

# The case information form. Kept as (field, label, hint) so the template
# and the facts text stay in step.
FACT_FIELDS = [
    ("plaintiff", "Your client (the plaintiff)", "Name, and where they live or do business."),
    ("defendant", "The other party (the defendant)", "Name, type of entity, and where it's based."),
    ("agreement", "The agreement", "What was agreed, when, in writing or not, the price, and key terms."),
    ("timeline", "What happened", "In date order: payments, work done or not done, communications."),
    ("damages", "Damages", "What the client lost or had to pay, with amounts."),
    ("evidence", "Evidence available", "Contracts, invoices, bank records, texts, photos, reports."),
    ("wants", "What the client wants", "Money back, costs, interest, fees, anything else."),
]

# Pages may load only this app's own files: no third-party scripts, styles
# or fonts, and no framing by other sites.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                               "form-action 'self'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # same-origin, not no-referrer: under no-referrer, browsers send "Origin: null"
    # on form posts, which the cross-site check has to refuse. No referrer ever
    # goes to other sites either way.
    "Referrer-Policy": "same-origin",
}


def facts_text(fields: dict[str, str]) -> str:
    lines = ["# Case information", ""]
    for key, label, _ in FACT_FIELDS:
        value = fields.get(key, "").strip()
        if value:
            lines.append(f"- **{label}:** {value}")
    return "\n".join(lines) + "\n"


def case_title(form: dict[str, str]) -> str:
    """'Plaintiff v. Defendant' from each party field's first words, e.g.
    "Dana Whitfield, homeowner in Provo" -> "Dana Whitfield"."""
    def name(field: str) -> str:
        return re.split(r"[,;(\n]", form.get(field, "").strip(), maxsplit=1)[0].strip()[:60]

    plaintiff, defendant = name("plaintiff"), name("defendant")
    return f"{plaintiff} v. {defendant}" if defendant else plaintiff


def safe_next(target: str | None) -> str:
    """Only same-site paths, so the login page can't be used to redirect
    people to another site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


class LoginRequired(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class Allowance:
    """Where a user stands against their plan's case allowance."""
    plan: Plan
    limit: int | None  # None = no limit. For a firm, its seats' allowances added up.
    used: int          # by this user, or by everyone in their firm
    since: str         # cases started from then count ("" = ever)
    renews_on: str     # "" if it never renews
    pool: tuple[int, ...] = ()  # the firm's user ids, when the allowance is shared
    seats: int = 0              # the firm's enabled, paid-up accounts
    ended: str = ""             # the last day the plan was paid for, once that has passed

    @property
    def left(self) -> int | None:
        """Cases they can still start, or None for no limit."""
        if self.ended:
            return 0
        return None if self.limit is None else max(0, self.limit - self.used)

    @property
    def used_up(self) -> str:
        """What to tell a user who has none left."""
        if self.ended:
            return (f"Your {self.plan.label} plan ended on {self.ended}, so you can't start new cases. Your "
                    "existing cases are still here. Ask your admin to renew it.")
        who = "Your firm has" if self.pool else "You've"
        whose = "its shared" if self.pool else f"your {self.plan.label}"
        if self.renews_on:
            return (f"{who} used all {self.limit} cases in {whose} plan this month. "
                    f"You can start more from {self.renews_on}, or ask your admin about a larger plan.")
        return f"{who} used all {self.limit} cases in {whose} plan. Ask your admin about a plan with more."


async def progress_events(worker: Worker, store: JobStore, job_id: str, poll_seconds: float = 0.5):
    """Server-sent events for a case: stage changes and draft text as they
    happen, queue position while waiting, and a heartbeat comment whenever
    the stream would otherwise go quiet for HEARTBEAT_SECONDS."""
    sent = 0
    clock = asyncio.get_running_loop().time
    last_write = clock()
    while True:
        progress = worker.progress(job_id)
        job = store.get(job_id)
        if job is None:  # deleted while the page was open
            return
        if progress is None:
            # Not tracked in memory (e.g. finished before a restart).
            if job.status in (DONE, FAILED):
                yield f"event: {job.status}\ndata: {{}}\n\n"
                return
        else:
            for event in progress.since(sent):
                sent += 1
                last_write = clock()
                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                if event["type"] in (DONE, FAILED):
                    return
        if job.status == QUEUED:
            last_write = clock()
            yield f"event: queue\ndata: {json.dumps({'position': store.queue_position(job)})}\n\n"
        if clock() - last_write >= HEARTBEAT_SECONDS:
            last_write = clock()
            yield ": keep-alive\n\n"  # an SSE comment line; browsers ignore it
        await asyncio.sleep(poll_seconds)


def create_app(config_path: Path = DEFAULT_CONFIG_PATH, *, client: ChatClient | None = None,
               model_label: str | None = None, data_dir: Path | None = None,
               law_db: Path | None = None, plans: Plans | None = None) -> FastAPI:
    """`client`, `data_dir`, `law_db` and `plans` override the config (for the
    demo model and for tests). `model_label` replaces the model names recorded on
    each run, so demo output isn't labelled as a real model's."""
    cfg = load_config(config_path)
    if model_label:
        cfg = dataclasses.replace(cfg, plaintiff_model=model_label, defendant_model=model_label)
    corpus = load_corpus_config(config_path)
    web = load_web_config(config_path)
    plans = plans or load_plans(config_path)
    client = client or OllamaClient.from_config(cfg)
    root = data_dir or web.data_dir
    store = JobStore(root)
    auth = AuthStore(root / "users.sqlite")

    def runner(job: Job, text: str, on_stage, on_token):
        # Opened per run, on the worker thread: SQLite connections stay on
        # the thread that made them.
        with LawIndex(law_db or corpus.db_path) as law:
            run = run_case(
                cfg, client, law, job.state_code,
                facts=text if job.input_mode == "facts" else None,
                complaint=text if job.input_mode == "complaint" else None,
                rounds=job.rounds, on_stage=on_stage, on_token=on_token,
            )
        return run, render_markdown(run, datetime.now())

    worker = Worker(store, runner)

    def purge() -> None:
        store.purge_expired(web.retention_days)

    async def purge_periodically() -> None:
        while True:
            await asyncio.to_thread(purge)
            await asyncio.sleep(PURGE_INTERVAL_SECONDS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start()
        purger = asyncio.create_task(purge_periodically())  # also runs once at startup
        yield
        purger.cancel()
        store.close()
        auth.close()

    app = FastAPI(title="battle_test", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.worker, app.state.auth = store, worker, auth
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    # Pages link to the stylesheet and script as ...?v=<this>. Cloudflare and
    # browsers cache them for hours, so without it an update shows new pages
    # with the old styles (seen on the 2026-10-05 deploy).
    static_version = hashlib.sha256(b"".join(
        f.read_bytes() for f in sorted((HERE / "static").iterdir()))).hexdigest()[:12]
    templates.env.globals["static_version"] = static_version
    templates.env.globals.update(states=SUPPORTED_STATES, fact_fields=FACT_FIELDS, modes=MODES,
                                 retention_days=web.retention_days)

    def deletes_on(job: Job) -> str:
        """The date a case will be deleted automatically, or "" if never."""
        if web.retention_days <= 0:
            return ""
        return (datetime.fromisoformat(job.created_at) + timedelta(days=web.retention_days)).strftime("%Y-%m-%d")

    templates.env.globals["deletes_on"] = deletes_on

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if web.behind_proxy:
            # Deployed behind its own Cloudflare Tunnel with no reverse proxy
            # in front, so the app sends HSTS itself: browsers then refuse
            # plain HTTP for the site. Never on the laptop's http://localhost.
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path != "/login" and not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"  # case pages shouldn't linger in caches
        return response

    @app.exception_handler(LoginRequired)
    async def to_login(request: Request, exc: LoginRequired):
        return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)

    # --- helpers ------------------------------------------------------------

    def current(request: Request) -> Session:
        session = auth.session(request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise LoginRequired()
        return session

    def same_origin(request: Request) -> None:
        """Reject cross-site form posts (defence in depth next to CSRF tokens
        and SameSite cookies)."""
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            if urlsplit(origin).netloc != request.url.netloc:
                raise HTTPException(403, "Cross-site request refused.")

    async def checked_form(request: Request, session: Session) -> dict[str, str]:
        same_origin(request)
        form = {k: str(v) for k, v in (await request.form()).items()}
        if not hmac.compare_digest(form.get("csrf", ""), session.csrf_token):
            raise HTTPException(403, "The form expired. Go back, reload the page and try again.")
        return form

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=web.secure_cookies,
                            max_age=7 * 24 * 3600, path="/")

    def allowance_for(user: User) -> Allowance:
        now = datetime.now()
        plan = plans.get(user.plan)
        since = plan.period_start(now)
        today = now.strftime("%Y-%m-%d")
        ended = user.paid_through if user.lapsed(today) else ""
        if not user.firm:
            return Allowance(plan, plan.cases, store.used([user.id], since), since, plan.renews_on(now),
                             ended=ended)
        # A firm's seats share one allowance. Disabled and unpaid accounts
        # aren't seats, but what they used this period still counts.
        members = auth.firm_members(user.firm)
        seats = [m for m in members if m.is_seat(today)]
        pool = tuple(m.id for m in members)
        return Allowance(plan, plans.pooled_cases([m.plan for m in seats]), store.used(pool, since), since,
                         plan.renews_on(now), pool, len(seats), ended)

    def page(request: Request, name: str, session: Session | None, **context) -> HTMLResponse:
        if session:
            context["allowance"] = allowance_for(session.user)
            if name == "new.html":  # however the form is shown, the user's cases go down the left
                context["nav_jobs"] = store.list(session.user.id)
        return templates.TemplateResponse(request, name, {"session": session, **context})

    def own_job(job_id: str, session: Session) -> Job:
        job = store.get(job_id)
        # Someone else's case looks exactly like a missing one.
        if job is None or job.user_id != session.user.id:
            raise HTTPException(404, "No such case")
        return job

    @app.get("/healthz")
    def healthz():
        """For Docker's healthcheck and Caddy. Needs no sign-in, reveals nothing."""
        return {"status": "ok"}

    # --- signing in ----------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/"):
        if auth.session(request.cookies.get(SESSION_COOKIE)):
            return RedirectResponse(safe_next(next), status_code=303)
        return page(request, "login.html", None, next=safe_next(next), error="", username="",
                    no_accounts=not auth.list_users(), password_set=request.query_params.get("set") == "1")

    @app.post("/login")
    async def login(request: Request):
        same_origin(request)
        form = await request.form()
        username, password = str(form.get("username", "")), str(form.get("password", ""))
        target = safe_next(str(form.get("next", "/")))
        # For the activity log. Behind the tunnel, uvicorn takes it from the
        # forwarded headers (see __main__.py).
        origin = f"from {request.client.host}" if request.client else ""
        try:
            user = auth.authenticate(username, password)
        except AuthError as e:
            known = auth.get_user(username)
            if known:  # never a mistyped name: people type passwords into that box
                auth.log("sign-in failed", known.username, detail=origin)
            response = page(request, "login.html", None, next=target, error=str(e), username=username,
                            no_accounts=not auth.list_users())
            response.status_code = 401
            return response
        auth.log("signed in", user.username, by=user.username, detail=origin)
        response = RedirectResponse(target, status_code=303)
        set_session_cookie(response, auth.create_session(user))
        return response

    # A new user's one-time link (python -m battle_test.web.users add / invite).
    # The token in the address is the only credential, so these need no session.

    def setup_page(request: Request, token: str, error: str = "") -> HTMLResponse:
        user = auth.setup_user(token)
        response = page(request, "setup.html", None, user=user, error=error if user else SETUP_LINK_INVALID)
        response.status_code = 404 if user is None else 400 if error else 200
        return response

    @app.get("/setup/{token}", response_class=HTMLResponse)
    def setup(request: Request, token: str):
        return setup_page(request, token)

    @app.post("/setup/{token}")
    async def finish_setup(request: Request, token: str):
        same_origin(request)
        form = await request.form()
        new = str(form.get("new", ""))
        if new != str(form.get("repeat", "")):
            return setup_page(request, token, "The passwords didn't match.")
        try:
            auth.finish_setup(token, new)
        except AuthError as e:
            return setup_page(request, token, str(e))
        # Sign out whoever this browser was signed in as, so the sign-in page
        # is shown rather than their case list.
        auth.end_session(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/login?set=1", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.post("/logout")
    async def logout(request: Request):
        session = current(request)
        await checked_form(request, session)
        auth.end_session(request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request):
        return page(request, "account.html", current(request), error="", message="")

    @app.post("/account")
    async def change_password(request: Request):
        session = current(request)
        form = await checked_form(request, session)
        error = ""
        try:
            auth.authenticate(session.user.username, form.get("current", ""))
        except AuthError:
            error = "Your current password is wrong."
        if not error and form.get("new", "") != form.get("repeat", ""):
            error = "The new passwords didn't match."
        if not error:
            try:
                auth.set_password(session.user.username, form.get("new", ""), by=session.user.username)
            except AuthError as e:
                error = str(e)
        if error:
            response = page(request, "account.html", session, error=error, message="")
            response.status_code = 400
            return response
        # Changing the password signed out every session; keep this browser signed in.
        new_session = auth.session(token := auth.create_session(session.user))
        response = page(request, "account.html", new_session, error="",
                        message="Password changed. Any other signed-in browsers were signed out.")
        set_session_cookie(response, token)
        return response

    # --- cases ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        session = current(request)
        # With no cases yet, the page shows the new-case form itself.
        return page(request, "index.html", session, jobs=store.list(session.user.id),
                    deleted=request.query_params.get("deleted") == "1", errors=[], form={})

    @app.get("/cases/new", response_class=HTMLResponse)
    def new_case(request: Request):
        return page(request, "new.html", current(request), errors=[], form={})

    @app.post("/cases")
    async def create_case(request: Request):
        session = current(request)
        form = await checked_form(request, session)
        errors = []
        state = form.get("state", "")
        mode = form.get("mode", "")
        rounds = form.get("rounds", "2")
        if state not in SUPPORTED_STATES:
            errors.append("Choose a state.")
        if mode not in MODES:
            errors.append("Choose what to draft.")
        if rounds not in ("1", "2"):
            errors.append("Choose 1 or 2 rounds.")
        if mode == "complaint":
            text = form.get("complaint", "").strip()
            if not text:
                errors.append("Paste your complaint.")
            title = text.splitlines()[0][:80] if text else ""
        else:
            text = facts_text(form)
            if not form.get("plaintiff", "").strip() or not form.get("timeline", "").strip():
                errors.append("Fill in at least the plaintiff and what happened.")
            title = case_title(form)
        if len(text) > MAX_INPUT_CHARS:
            errors.append(f"The input is too long ({len(text):,} characters; the limit is {MAX_INPUT_CHARS:,}).")
        if errors:
            return page(request, "new.html", session, errors=errors, form=form)

        allowed = allowance_for(session.user)
        if allowed.ended:
            # As for a used-up allowance, the form itself says why.
            response = page(request, "new.html", session, errors=[], form=form)
            response.status_code = 403
            return response
        try:
            job = store.create(session.user.id, state, mode, text, int(rounds), title.strip() or "Untitled case",
                               limit=allowed.limit, since=allowed.since, pool=allowed.pool,
                               max_active=web.max_active_cases)
        except QueueFull:
            response = page(request, "new.html", session, form=form, errors=[
                f"You already have {web.max_active_cases} cases waiting or running, which is the most allowed at "
                "once. Start this one when one of them has finished."])
            response.status_code = 429
            return response
        except QuotaExceeded:
            # The form itself says the allowance is used up, and keeps what was typed.
            response = page(request, "new.html", session, errors=[], form=form)
            response.status_code = 403
            return response
        worker.submit(job.id)
        return RedirectResponse(f"/cases/{job.id}", status_code=303)

    @app.get("/cases/{job_id}", response_class=HTMLResponse)
    def case(request: Request, job_id: str):
        session = current(request)
        job = own_job(job_id, session)
        # The user's cases, newest first, to switch between down the left.
        nav_jobs = store.list(session.user.id)
        if job.status in (QUEUED, RUNNING):
            position = store.queue_position(job) if job.status == QUEUED else 0
            return page(request, "progress.html", session, job=job, position=position, nav_jobs=nav_jobs)
        if job.status == FAILED:
            return page(request, "failed.html", session, job=job, nav_jobs=nav_jobs)
        result = store.result(job_id)
        documents = [
            {**doc, "html": render.document_html(doc), "summary": render.check_summary(doc),
             "notable": render.notable_checks(doc)}
            for doc in result["documents"]
        ]
        return page(request, "result.html", session, job=job, result=result, documents=documents,
                    authorities=render.authorities(result), nav_jobs=nav_jobs)

    @app.get("/cases/{job_id}/events")
    async def events(request: Request, job_id: str):
        """Server-sent events: stage changes and draft text as they happen."""
        own_job(job_id, current(request))
        return StreamingResponse(progress_events(worker, store, job_id), media_type="text/event-stream")

    @app.get("/cases/{job_id}/delete", response_class=HTMLResponse)
    def confirm_delete(request: Request, job_id: str):
        session = current(request)
        return page(request, "delete.html", session, job=own_job(job_id, session))

    @app.post("/cases/{job_id}/delete")
    async def delete_case(request: Request, job_id: str):
        session = current(request)
        job = own_job(job_id, session)
        await checked_form(request, session)
        if job.status == RUNNING:
            raise HTTPException(409, "This case is running. Delete it once it has finished.")
        store.delete(job_id)
        worker.forget(job_id)
        return RedirectResponse("/?deleted=1", status_code=303)

    @app.get("/cases/{job_id}/download.{fmt}")
    def download(request: Request, job_id: str, fmt: str):
        job = own_job(job_id, current(request))
        path = store.result_markdown_path(job_id)
        if job.status != DONE or not path.exists() or fmt not in ("md", *export.FORMATS):
            raise HTTPException(404, "No result yet")
        filename = f"battle-test-{job.state_code.lower()}-{job.created_at[:10]}.{fmt}"
        if fmt == "md":
            return FileResponse(path, media_type="text/markdown", filename=filename)
        # Word and PDF are built from the saved result each time, so no
        # extra copies of a case sit on disk.
        generated = (job.finished_at or job.created_at).replace("T", " ")[:16]
        content = export.BUILDERS[fmt](store.result(job_id), job.title, generated)
        return Response(content, media_type=export.FORMATS[fmt],
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    admin.add_routes(app, auth=auth, store=store, plans=plans, web=web, current=current,
                     checked_form=checked_form, page=page, allowance_for=allowance_for)
    return app
