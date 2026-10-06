# battle_test

Stress-tests a lawsuit before it's used for real. A plaintiff model drafts
the complaint (or takes yours) and a motion for summary judgment. A defendant
model writes the opposition, and optionally the plaintiff model replies. No
winner is declared. See `plans/plan.md` for the full design.

**New here? Start with [`docs/`](docs/README.md):** how the system works,
what each file does, how to start and stop it on the laptop and on the
server, and how to make a change.

**Status (2026-10-03):** plan steps 1–3 are built. The web UI (step 5,
parts 1–2) runs locally, and is deployed on the smark_iq server at
**https://battle.smarkiq.us**, for testing with fictional cases only until
Cloudflare Access is added. Drafts are grounded in a local copy of the law:
1. Before drafting, each side searches the law index for the statutes and
   rules it needs.
2. It may cite only the sections it's shown, and everything else gets a
   `[CITATION NEEDED: …]` placeholder.
3. Every statute, rule and constitution citation in the result is then
   checked in code: ✅ in force, ❌ not found / not in force / another
   state's law (also marked inline in the draft), or ⚠ ambiguous.

Case law isn't checked yet (plan step 4), so the models are told not to cite
cases, and any case citation that appears is flagged.

The checking works, but the models often pick the wrong law or misstate it.
That's true on the laptop's 7B and on the server's 14B, which scored no
better. Search and selection are the bottleneck. `plans/plan.md` covers the
model size estimate, the planned fixes (3a/3b), the web UI (step 5), and
the Bedrock hosting option. AWS hosting costs are in
`plans/aws-bedrock-plan.md`, and pricing and profit in
`plans/profitability-plan.md`. **Day-to-day start/stop instructions** are in
`plans/plan.md`, under "Start and stop: how to run it".

## Requirements

- Python 3.11+. The pipeline uses the standard library only. `pip install -r
  requirements.txt` adds what's needed to build the law corpus, run the web
  UI, and run its tests.
- [Ollama](https://ollama.com) running, with the models named in
  `config.toml` pulled (default `qwen2.5:7b`)
- The law index, built once (see **Law corpus** below)

## Usage

```
python -m battle_test --state UT --facts examples/utah_roofing_facts.md
python -m battle_test --state CA --complaint path/to/my_complaint.md --rounds 1
```

- `--state`: `UT`, `CA` or `TX` (v1 coverage). Federal law always applies.
- `--facts` or `--complaint`: generate a complaint from case information, or
  use one you wrote.
- `--rounds 1|2`: round 2 adds the plaintiff's reply. The default is set in
  `config.toml`.

The documents are saved as one Markdown file in `output/`, which is
gitignored because it may hold case facts. Keep real case files in `cases/`,
which is also gitignored.

Each output file contains:
- The disclaimer, and the date the law is current as of.
- **Citation check:** a table per document of ✅ / ☑ / ❌ / ⚠ counts, plus
  each citation that needs attention.
- The documents themselves (complaint, motion, opposition, reply), with
  problem citations marked inline.
- **Appendix:** each side's research queries, and every authority quoted to
  the models, with a link to its official source.

## Code layout

| File | What it does |
|---|---|
| `battle_test/cli.py` | Command-line entry point (`python -m battle_test`) |
| `battle_test/pipeline.py` | Runs the flow: research → complaint → motion → opposition → reply |
| `battle_test/prompts.py` | System and task prompts, state list, trial courts |
| `battle_test/grounding.py` | Research, authority selection, and checking each draft's citations |
| `battle_test/citations.py` | Finding and parsing citations in text (no database) |
| `battle_test/law_index.py` | Searching the local law index and resolving citations |
| `battle_test/corpus.py` | Downloading Open US Law and building the index (`python -m battle_test.corpus`) |
| `battle_test/report.py` | Writing the Markdown output |
| `battle_test/evaluate.py` | Scoring runs against the sample cases (`python -m battle_test.evaluate`) |
| `battle_test/ollama_client.py` | Minimal Ollama client (streaming, JSON mode) |
| `battle_test/config.py` | Loads `config.toml` |
| `battle_test/web/app.py` | Web UI routes: case form, live progress, results, download |
| `battle_test/web/jobs.py` | Case storage, the usage record and plan limits, the one-at-a-time job queue, live progress events |
| `battle_test/web/auth.py` | Accounts, password hashing, one-time setup links, sessions, login lockout |
| `battle_test/web/admin.py` | Admin pages: accounts, plans, setup links, the activity log |
| `battle_test/web/users.py` | Admin command for accounts (`python -m battle_test.web.users`) |
| `battle_test/web/export.py` | Building the Word and PDF downloads from a finished run |
| `battle_test/web/render.py` | Turning a finished run into HTML (links, highlights, summaries) |
| `battle_test/web/demo.py` | The demo model for working on the UI without a GPU |
| `battle_test/web/templates/`, `static/` | Pages, CSS, and the small progress/tabs script |
| `examples/` | Fictional sample cases (UT, CA, TX) for testing |
| `examples/eval/` | Expected authorities for each sample case |
| `docs/` | How it works, a guide to the code, running it locally and on the server, and how to work on it |
| `plans/plan.md` | Design, decisions, build steps, and open questions |

## Web UI

A browser front end for the same pipeline (plan step 5, parts 1–2). It's
live on the server at https://battle.smarkiq.us (see "Deploying to the
server"). To run it on the laptop instead, first create a local account.
Laptop and server accounts are separate. You'll be asked for the password at
a hidden prompt, so run it yourself in a terminal:

```
python -m battle_test.web.users add NAME --admin --password --plan unlimited
```

Then start the server and open http://127.0.0.1:8000:

```
python -m battle_test.web                 # real model via Ollama
python -m battle_test.web --demo-model    # canned drafts, no GPU needed
```

- **Sign in** with that account. There's no self-signup, and each user sees
  only their own cases. Users can change their own password under their
  name in the header.
- **Adding a user:** `python -m battle_test.web.users add NAME --plan solo`
  creates the account and prints a **one-time setup link**. Send it to the
  user privately. They open it and choose their own password, so nobody
  else ever knows it.
  - The link works once and expires after 72 hours. Only a hash of it is
    stored.
  - `invite NAME` prints a new link (a forgotten password, or an expired
    link), and cancels the earlier one. So does `passwd` or `disable`.
  - Other commands: `passwd`, `disable`, `enable`, `list`.
- **Admin pages:** an admin sees an "Admin" link in the header (`/admin`).
  Everything below can be done there or with the `users` command.
  - **Accounts:** each account's plan, firm, cases used, paid-through date,
    last sign-in and status. Add an account (you get its one-time setup
    link, shown once), change its plan, firm or paid-through date, issue a
    new setup link, or disable and enable it.
  - **Activity log** (`/admin/activity`, and per account): sign-ins, failed
    sign-ins with the address they came from, and every account change with
    who made it, from the pages or the command line. Entries can't be
    edited or removed.
  - **Limits, on purpose:**
    - Every change asks for the admin's own password again.
    - Admins never see a case's title, text or results, only when cases
      were started, the state, mode and rounds.
    - Making or removing an admin, and setup links for or disabling of an
      admin account, are command-line only (`add NAME --admin --password`).
    - Anyone who isn't an admin gets a 404.
- **Plans and usage:** each account is on a plan from `[plans]` in the
  config (Trial 5 cases in total; Solo 30, Pro 100, Firm 60 a month;
  Unlimited). `plan NAME PLAN` changes it, effective at once.
  - A case counts when it's started. Monthly allowances renew on the 1st.
  - A run that fails, or a case deleted before it ran, isn't counted.
    Deleting a finished case doesn't give the use back.
  - At the limit, the new-case form says so and refuses more. The account
    page shows the plan and what's been used.
  - `usage [--month YYYY-MM]` reports cases per account. The usage record
    keeps only the date, state, mode and rounds of each case, and outlives
    the case itself.
  - **Firms:** accounts in the same firm share one allowance, the sum of
    their plans (three Firm seats share 180 a month), used by whichever of
    them needs it. `add NAME --plan firm --firm FIRM` or `firm NAME FIRM`
    joins one, and `firm NAME` leaves it. Cases stay private to each user.
    A disabled account stops adding to the allowance, but what it already
    used still counts. `usage` adds a line per firm.
  - **Non-payment:** `paid NAME 2026-11-30` records the last day a plan is
    paid for, and `paid NAME` removes the date (no end). After that day the
    user can still sign in, read, download and delete their cases, but
    can't start new ones until you move the date on. An unpaid firm seat
    stops adding to the firm's allowance. `list` and `usage` mark expired
    accounts. To shut someone out completely, use `disable`.
  - **Queue cap:** a user may have at most 3 cases waiting or running at
    once (`max_active_cases` in `[web]`, 0 for no cap), so one person can't
    fill the one-at-a-time queue. A refused case uses none of the allowance.
  - An account with no plan gets `plans.default` (Trial). **Accounts made
    before plans existed have none,** so give your own one:
    `plan NAME unlimited`.
- **New case** (shown straight away if you have no cases yet): pick the
  state, choose what to draft, and choose 1 or 2 rounds:
  - **"Draft the complaint and the motion"**: fill in the case information.
  - **"Use my complaint, draft only the motion"**: paste a complaint you
    wrote. It's used as written, and its citations are still checked.
- **Progress:** runs go into a queue and execute one at a time. The page
  shows each stage live and streams the draft text. You can close it and
  come back.
- **Results:** the disclaimer and law date, the citation check, and each
  document in a tab:
  - ✅ citations link to their official source.
  - ❌/⚠ marks and `[CITATION NEEDED]` placeholders are highlighted.
  - Below the documents: the authorities appendix and the research queries.
  - A **Download** menu: Word (`.docx`), PDF, or Markdown. Each holds the
    whole set: the disclaimer, the citation check, every document, and
    the authorities. The PDF uses plain fonts, so its warning marks read
    `[! NOT FOUND …]` and unusual characters lose their accents.
- **Switching cases:** every case page (progress, results, or a failed
  run) and the new-case form list your cases down the left, newest first, with each one's
  status. The one you're on is highlighted.
- **Deleting and retention:** "Delete case" (after a confirmation page)
  removes a case and all its files for good. Finished cases are also deleted
  automatically after `retention_days` (default 90, set in `[web]`). The
  case list shows each case's deletion date.

**Security:**
- Passwords are stored only as salted scrypt hashes.
- Sessions are HttpOnly cookies, and only a hash of each session token is
  stored.
- Every form has a CSRF token, and cross-site posts are refused.
- Pages load nothing from third parties.
- **On the laptop,** the server refuses to listen on anything but
  `127.0.0.1` / `localhost`. Serving on any other address (the deployed
  container) needs both `behind_proxy = true` and `secure_cookies = true`
  in `[web]`, which only `config.server.toml` sets. In that mode the app
  also sends HSTS.
- On the laptop, cases and accounts are stored in `cases/web/`
  (gitignored). On the server they're in the `battle-cases` Docker volume.

`--demo-model` still runs research, selection and citation checking against
the real law index. Only the drafting is canned, and its output is labelled
`demo`.

## Deploying to the server

**Day-to-day start and stop instructions** are in `plans/plan.md`, under
"Start and stop: how to run it". That covers running on the laptop, on the
server through an SSH tunnel, and publicly.

The server runs the same code in Docker, at `https://battle.smarkiq.us`,
fully independent of smark_iq: its own compose project and network, and
its own Cloudflare Tunnel connector (`battle-test-cloudflared`). Only the
server's GPU, through Ollama, is shared. See `plans/server-migration-plan.md`
for the full steps.
- The tunnel token goes in `.env` on the server (gitignored). See
  `.env.example`.
- `Dockerfile`, `docker-compose.yml` and `config.server.toml` are only for
  the server. The container sets `BATTLE_TEST_CONFIG` so every command reads
  the server config.
- Local development is unchanged. Without `BATTLE_TEST_CONFIG`, everything
  uses `config.toml`, and the web UI stays on `127.0.0.1`. Serving on any
  other address needs `behind_proxy = true` and `secure_cookies = true`,
  which only the server config sets.

## Law corpus

Statutes, constitutions and court rules for Utah, California, Texas and
federal law come from [Open US Law](https://www.vaquill.ai/open-us-law)
(Open US Law by Vaquill AI, CC BY 4.0). They're downloaded and indexed
locally in SQLite, so searches never leave the machine.

```
pip install -r requirements.txt           # pyarrow, needed only to build
python -m battle_test.corpus build        # ~176 MB download, ~1.3 GB index, ~1 min
python -m battle_test.corpus info         # snapshot date and section counts
python -m battle_test.corpus search --state UT "summary judgment"
```

Files are checked against the dataset's published SHA256 checksums. Which
jurisdictions and document types are included, and which quarterly snapshot,
is set under `[corpus]` in `config.toml`. Everything lives in `data/`, which
is gitignored.

## Evaluation

Three fictional sample cases in `examples/`, one per state, each have an
expected-authority list in `examples/eval/*.toml`. Each list names:
- **core** authorities a competent brief should cite
- **useful** ones that are welcome if cited
- **off-topic** code areas (e.g. UCC sales law for a construction contract)

The lists are drafts until a lawyer has reviewed them (`lawyer_reviewed`
in each file).

```
python -m battle_test.evaluate --validate          # check the case files against the law index
python -m battle_test.evaluate --label laptop-7b   # run and score all three cases
python -m battle_test.evaluate --case texas_foundation --rounds 1
python -m battle_test.evaluate --research-only     # fast (~1 min/case): score the search step alone
```

Results go to `output/eval/<timestamp>-<label>/`:
- `summary.md`: per case, which core/useful authorities search found, which
  were given to the models and which were cited, plus anything off-topic
  and the citation-check counts
- `results.json`: for comparing setups
- each case's full document set

Use a different `--config` file to compare models, e.g. 14B vs. ~30B, or
local vs. Bedrock.

## Tests

```
python -m unittest
```
