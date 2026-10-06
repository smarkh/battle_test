# Code guide: what each file does

Read [how-it-works.md](how-it-works.md) first. This is the map.

## Top level

| Path | What it is |
|---|---|
| `battle_test/` | The Python package: the pipeline and the web app |
| `tests/` | Unit tests (`python -m unittest`) |
| `examples/` | Three fictional sample cases, and their expected-authority lists in `examples/eval/` |
| `docs/` | These documents |
| `plans/` | Design, decisions, results and future plans |
| `config.toml` | Laptop settings |
| `config.server.toml` | Server settings, copied into the Docker image |
| `requirements.txt` | Python packages for the corpus build, the web UI and its tests |
| `Dockerfile`, `.dockerignore` | The server image: code only, non-root user |
| `docker-compose.yml` | The server's two containers and two volumes |
| `.env.example` | Template for the server's `.env`, which holds the tunnel token |
| `data/` (gitignored) | The downloaded law files and `law.sqlite` |
| `cases/` (gitignored) | The web UI's accounts and case files, and any real case material |
| `output/` (gitignored) | Command-line and evaluation output |

## The pipeline (`battle_test/`)

The pipeline uses the standard library only. Third-party packages are
needed only to build the law index and to run the web UI.

| File | What it does | Depends on |
|---|---|---|
| `cli.py` | The command line: `python -m battle_test --state UT --facts FILE`. Parses arguments, runs one case, writes the Markdown file. `__main__.py` just calls it. | pipeline, report |
| `pipeline.py` | `run_case()`: the whole flow, from research to the reply. Defines `CaseRun` (one run's results) and `Document` (one brief with its citation checks). Also defines `ChatClient`, the interface a model client must offer. | grounding, prompts |
| `prompts.py` | Every prompt: the system prompts for each role, and the task prompts for research, selection and each brief. Also the supported states and each state's trial court. | none |
| `grounding.py` | Research (model queries plus the standard ones), gathering search candidates, selection, formatting sections for a prompt, and `check_citations()`, which classifies every citation in a draft. | citations, law_index |
| `citations.py` | Pure text handling: finds citations in a draft, parses them, and normalises code names so "Utah Code Ann." and "U.C.A." match. No database access. | none |
| `law_index.py` | `LawIndex`: reads `law.sqlite`. `search()` is the keyword search, `lookup()` finds an exact citation, and `resolve()` matches a parsed citation from a draft. | citations |
| `corpus.py` | `python -m battle_test.corpus build / info / search`. Downloads Open US Law, verifies checksums, builds the index. Needs `pyarrow`. | law_index, citations |
| `report.py` | Turns a `CaseRun` into the Markdown document set: disclaimer, citation-check table, documents, appendix. | grounding |
| `evaluate.py` | `python -m battle_test.evaluate`. Runs the sample cases and scores them against the expected authorities. | pipeline, report |
| `ollama_client.py` | A minimal Ollama client: one `chat()` method, streaming, with a JSON mode. | none |
| `config.py` | Loads the TOML config into typed settings: `Config`, `CorpusConfig`, `WebConfig`, `Plans`. | none |

### How the pieces call each other

```
cli.py / evaluate.py / web/app.py
        │
        ▼
   pipeline.run_case()
        ├── prompts.py        builds every prompt
        ├── client.chat()     ollama_client.py, or web/demo.py
        └── grounding.py
              ├── law_index.py   search, lookup, resolve
              └── citations.py   find and parse citations
        │
        ▼
   report.render_markdown()
```

`run_case()` takes its model client and its law index as arguments. That
is what lets the tests pass in fakes, and the web UI's demo mode pass in
canned drafts.

## The web app (`battle_test/web/`)

| File | What it does |
|---|---|
| `__main__.py` | `python -m battle_test.web`. Checks the config is allowed to serve on its host, then starts uvicorn. Flags: `--demo-model`, `--port`, `--config`. |
| `app.py` | `create_app()`: every user-facing route (sign-in, setup link, account, case list, new case, progress stream, results, delete, download), the security headers, the CSRF and same-origin checks, and the hourly retention purge. |
| `jobs.py` | `JobStore`: cases and the usage record in `cases.sqlite`, plus each case's folder. `Worker`: the background thread that runs one case at a time. `Progress`: the in-memory event log the progress page reads. |
| `auth.py` | `AuthStore`: accounts, password hashing, one-time setup links, sessions, login lockout, and the activity log, all in `users.sqlite`. |
| `admin.py` | The `/admin` routes: accounts, plans, setup links, disable and enable, the activity log. |
| `users.py` | `python -m battle_test.web.users`: the command-line account tool (`add`, `invite`, `passwd`, `plan`, `firm`, `paid`, `disable`, `enable`, `list`, `usage`). |
| `render.py` | Turns a saved result into HTML: links ✅ citations to their source, highlights problems and placeholders. |
| `export.py` | Builds the Word and PDF downloads. Both come from one list of blocks (`blocks()`), and are built in memory when asked for, so nothing extra is stored with a case. Needs `python-docx` and `reportlab`. |
| `demo.py` | `DemoClient`: canned drafts for working on the UI without a GPU. Research, selection and checking still run for real. |
| `templates/` | Jinja pages. `base.html` is the layout. `_case_form.html`, `_case_nav.html` (the case list down the left of a case page and the new-case form) and `_events.html` are shared fragments. |
| `static/app.css`, `static/app.js` | The stylesheet, and the one script: the form mode switch, document tabs, and live progress. |

### The life of a web case

1. `POST /cases` (`app.py`) validates the form and checks the plan
   allowance and the queue cap.
2. `JobStore.create()` writes `input.md`, the `cases` row and the `usage`
   row, then `Worker.submit()` queues the case.
3. The worker thread calls `run_case()`, passing callbacks that feed
   `Progress`.
4. The browser's progress page reads `GET /cases/{id}/events`, a
   server-sent event stream.
5. On success, `JobStore.save_result()` writes `result.json` and
   `result.md`. On failure, the case is marked failed and its usage row
   stops counting.
6. `GET /cases/{id}` renders the results through `render.py`.

## Tests (`tests/`)

No test needs a GPU, a network connection or the real law index. They use
fake model clients and small temporary databases.

| File | Covers |
|---|---|
| `test_citations.py` | Finding and parsing citations in all their written forms |
| `test_law_index.py` | Search ranking, lookup, resolving citations |
| `test_grounding.py` | Research, selection, citation classification and inline marks |
| `test_pipeline.py` | The order of model calls, what each prompt receives, both input modes |
| `test_evaluate.py` | Scoring against expected authorities |
| `test_jobs.py` | Case storage, allowances, the queue cap, retention |
| `test_auth.py` | Passwords, sessions, setup links, lockout, the activity log |
| `test_users.py` | The account command-line tool |
| `test_web.py` | The routes end to end: sign-in, cases, privacy, CSRF, admin pages |
| `test_deploy.py` | The serving rule, the server config, the Docker files' hardening, `/healthz`, the heartbeat |

## Where to make common changes

| To change… | Look in |
|---|---|
| What a model is told to write | `prompts.py` |
| How law is searched or ranked | `law_index.py` (`search`, `STATE_BOOST`), `grounding.py` (`STANDARD_QUERIES`, `RESULTS_PER_QUERY`) |
| How many sections a model gets, or how much of each | `grounding.py` (`MAX_AUTHORITIES`, `EXCERPT_CHARS`), `pipeline.py` (`MAX_COMBINED_AUTHORITIES`) |
| A citation form the checker doesn't recognise | `citations.py` (`_PATTERNS`), with a test in `test_citations.py` |
| The order of documents, or adding a step | `pipeline.py` |
| Adding a state | `prompts.py` (`SUPPORTED_STATES`, `TRIAL_COURTS`), `grounding.py` (`SUMMARY_JUDGMENT_RULES`), `[corpus] jurisdictions` in both configs, then rebuild the index |
| The model or its settings | `[models]` and `[generation]` in the config |
| A different model provider | A new client with the same `chat()` method as `ollama_client.py` |
| A page's layout or wording | `web/templates/`, `web/static/` |
| Plans and allowances | `[plans]` in both configs |
| The Markdown output | `report.py` |
| The Word or PDF download | `web/export.py` |
