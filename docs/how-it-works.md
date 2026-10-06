# How battle_test works

## The idea

A lawyer wants to know where a lawsuit is weak before filing it.
battle_test has two language models argue it out:

- The **plaintiff** model drafts the complaint (or takes one the user
  wrote) and a motion for summary judgment.
- The **defendant** model writes the opposition to that motion.
- Optionally, the plaintiff model writes a reply (round 2).

The system plays two argumentative lawyers, never a judge. It surfaces
weaknesses and doesn't say who wins.

The hard requirement is that the briefs cite only real, current law.
Models invent and misremember citations, so battle_test doesn't trust
them:

1. Before drafting, it **finds** the relevant statutes and rules in a local
   copy of the law and quotes them to the model.
2. The model may cite **only** what it was shown. For anything else it
   writes a `[CITATION NEEDED: …]` placeholder.
3. After drafting, code **checks** every citation against the local copy.

## What a user sees

1. They sign in to the web UI and start a case: pick a state (Utah,
   California or Texas), then either fill in a case information form or
   paste a complaint they wrote, and choose 1 or 2 rounds.
2. The case joins a queue. A progress page shows each stage and streams
   the draft text as it's written.
3. The results page shows the documents in tabs, a citation-check table,
   the authorities quoted to the models, and a download menu (Word, PDF
   or Markdown).
4. Every case page, and the new-case form, lists the user's cases down
   the left, newest first, so they can switch from one case to another.

The same pipeline also runs from the command line
(`python -m battle_test`), which writes one Markdown file.

## The pipeline, step by step

All of this is `run_case()` in `battle_test/pipeline.py`. A 2-round case
that starts from case information makes **8 model calls**.

| # | Stage | Who | What happens |
|---|---|---|---|
| 1 | Legal research | Plaintiff | The model lists its legal issues as short search queries, in statute wording, as JSON. Six standard procedural queries (limitations, venue, jurisdiction, attorney fees, interest, damages) are always added. |
| | Search | Code | Each query is run against the law index, 8 results each. The results are interleaved so every query's best hit comes first. |
| 2 | Selecting authorities | Plaintiff | The model sees the candidates (citation, title, first 25 words) and picks up to 10, as JSON. |
| 3 | Complaint | Plaintiff | Drafted from the case information and the selected sections. Skipped if the user supplied a complaint, which is used as written but still checked. |
| 4 | Motion for summary judgment | Plaintiff | Drafted from the complaint and the selected sections, plus the state's summary judgment rule. |
| 5 | Legal research | Defendant | The same as step 1, from the complaint and the motion. |
| 6 | Selecting authorities | Defendant | The same as step 2. |
| 7 | Opposition | Defendant | Drafted from the complaint, the motion, its own sections, and the text of what the plaintiff cited, so it can check what those sections really say. |
| 8 | Reply (round 2 only) | Plaintiff | Drafted from all three documents, its sections, and the text of what the defendant cited. |

After each draft, code checks its citations (below) and marks problems
inline in the text.

Details worth knowing:

- **The summary judgment rule is looked up directly,** not left to search:
  Utah R. Civ. P. 56, Cal. CCP § 437c, Tex. R. Civ. P. 166a.
- **A drafting prompt holds at most 12 sections,** each cut to about 700
  characters, so the longest prompt (the reply) fits the model's context.
- **If a model's JSON reply is unusable,** research falls back to the
  standard queries alone, and selection falls back to the top-ranked
  candidates. A run doesn't fail over it.
- **Case law isn't supported yet.** The models are told not to cite cases,
  and anything that looks like a case citation is listed as unchecked.

## The law index

The law comes from [Open US Law](https://www.vaquill.ai/open-us-law) (by
Vaquill AI, CC BY 4.0): statutes, constitutions and court rules for Utah,
California, Texas and federal law.

- `python -m battle_test.corpus build` downloads 12 files (~176 MB),
  checks their SHA256 checksums, and builds `data/law.sqlite` (~1.3 GB,
  about 370,000 sections) in about a minute.
- It's a SQLite database with a full-text (FTS5) index. Searches run on
  the machine, so no case facts leave it.
- Search is **keyword search**. It returns only law that's in force, from
  the chosen state and federal law, with the state's law ranked slightly
  higher.
- The data is a quarterly snapshot. Every output states the date the law
  is current as of.

## The citation check

`grounding.check_citations()` runs on every draft. It finds statute, rule
and constitution citations in many written forms ("Utah Code Ann. §",
"U.C.A.", "Rule 56 of the Utah Rules of Civil Procedure", "Code of Civil
Procedure section 437c") and resolves each against the index.

| Mark | Meaning |
|---|---|
| ✅ | In force, and its text was shown to the model |
| ☑ | In force, but the model was never shown its text |
| ❌ | Not found, not in force, or another state's law. Also marked inline in the draft |
| ⚠ | Ambiguous: it matches more than one section |
| unchecked | Looks like a citation, but the parser can't read it (case law, for example) |

Problem citations are marked, not removed, so the reviewer sees exactly
what the model wrote.

**What the check doesn't do:** it confirms a section exists and is in
force. It doesn't confirm the section says what the brief claims. That
check is planned ("3a" in `plans/plan.md`).

## Known weaknesses

Be clear on these before judging any output:

- **The wrong law gets chosen.** Keyword search misses statutes worded
  differently from the query, and each state words things differently. On
  the three sample cases, the briefs cite about 4 to 6 of the 16 authorities a
  competent brief should. A bigger model (14B instead of 7B) didn't help,
  because a model only sees what search returns.
- **Real law gets misstated.** A model may cite a real, in-force section
  for something it doesn't say. Nothing catches this yet.
- **Models invent facts** that aren't in the case information, despite
  the prompts.

The evaluation set (see [development.md](development.md)) exists to
measure the first of these.

## The web application

`battle_test/web/` wraps the pipeline. It's FastAPI with server-rendered
pages and one small script. There's no front-end build step, and pages
load nothing from third parties.

- **Accounts.** There's no self-signup. An admin creates an account and
  gets a one-time setup link, and the user sets their own password there.
  Each user sees only their own cases. Anyone else gets a 404.
- **The queue.** One background worker thread runs one case at a time,
  because there's one GPU. Queued cases survive a restart. A case that was
  mid-run is marked failed with an "interrupted" message.
- **Live progress.** The pipeline reports stages and text through
  callbacks, and the browser receives them as server-sent events. A
  heartbeat every 15 seconds keeps the connection open through
  Cloudflare.
- **Plans and limits.** Each account has a plan with a case allowance
  (Trial 5 in total; Solo 30, Pro 100, Firm 60 a month; Unlimited).
  Accounts in the same firm share the sum of their plans. A user may have
  3 cases queued or running at once. A paid-through date can be set by
  hand, and after it the user keeps their cases but can't start new ones.
- **Admin pages** (`/admin`). Admins manage accounts and plans and read an
  append-only activity log. Every change asks for the admin's password
  again, and admins never see case content.
- **Retention.** Finished and failed cases are deleted automatically
  after 90 days. Users can delete sooner.

### Where data lives

| Data | Laptop | Server |
|---|---|---|
| Law index | `data/law.sqlite` | `battle-law` Docker volume (`/data/law`) |
| Accounts, sessions, setup links, activity log | `cases/web/users.sqlite` | `battle-cases` volume (`/data/cases/users.sqlite`) |
| Case list and usage record | `cases/web/cases.sqlite` | `/data/cases/cases.sqlite` |
| Each case's files: `input.md`, `result.json`, `result.md` | `cases/web/<case id>/` | `/data/cases/<case id>/` |
| Command-line and evaluation output | `output/` | `/data/cases/output/` |

`data/`, `cases/` and `output/` are gitignored and excluded from the
Docker image. Case material never goes into git or an image.

### Security measures

- Passwords are stored only as salted scrypt hashes. They must be 12 or
  more characters.
- Sessions are HttpOnly, SameSite=Lax cookies lasting 7 days. Only a
  SHA-256 of each session token is stored.
- Five failed sign-ins lock that username for 15 minutes.
- Every form carries a CSRF token, and posts from another origin are
  refused.
- A Content-Security-Policy allows only the app's own files.
- Model output is HTML-escaped before any links or highlights are added.
- The app refuses to listen on anything but localhost unless the config
  sets both `behind_proxy` and `secure_cookies`. Only the server config
  does.

## Configuration

Settings live in a TOML file. `battle_test/config.py` loads it.

| File | Used by | Notable settings |
|---|---|---|
| `config.toml` | The laptop (the default) | Local Ollama, `qwen2.5:7b`, `127.0.0.1` |
| `config.server.toml` | The server's container | Host Ollama, `qwen2.5:14b`, `keep_alive = "30s"`, `/data/...` paths, `0.0.0.0` with `behind_proxy` and `secure_cookies` |

The `BATTLE_TEST_CONFIG` environment variable picks the file. The Docker
image sets it to the server config. Most commands also take `--config`.

| Section | What it sets |
|---|---|
| `[ollama]` | The Ollama address, timeout, and how long the model stays loaded |
| `[models]` | The model for each role (plaintiff, defendant) |
| `[generation]` | Context size and temperature |
| `[pipeline]` | Default rounds and the output folder |
| `[corpus]` | The law snapshot, which jurisdictions and document types to index, and the data folder |
| `[web]` | Host, port, data folder, cookie and proxy settings, retention, the queue cap, the public address |
| `[plans]` | The plans and their case allowances |

## How it's deployed

```
browser ──https──▶ Cloudflare ──tunnel──▶ battle-test-cloudflared ──▶ battle-test:8000
                                                                           │
                                                                           ▼
                                                        Ollama on the server (the GPU)
```

- Two Docker containers on a home server: the app (`battle-test`) and its
  own Cloudflare Tunnel connector (`battle-test-cloudflared`).
- The connector dials out to Cloudflare, so no ports are open on the
  router, and the app publishes no port on the server.
- Ollama runs natively on the server, not in a container. The app reaches
  it at `host.docker.internal:11434`.
- The same server runs an unrelated project, smark_iq (containers
  `open-webui`, `caddy` and `cloudflared`). The two share nothing but the
  GPU. battle_test unloads its model 30 seconds after a run to free it.

See [running-on-server.md](running-on-server.md) for operating it.
