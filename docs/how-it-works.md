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
   bring a complaint they wrote, and choose 1 or 2 rounds. A complaint can
   be pasted, or uploaded as Word, PDF or text: the server pulls the text
   out and shows it in the form to check before anything runs. The file
   itself isn't kept.
2. The case joins a queue. A progress page shows each stage and streams
   the draft text as it's written.
3. The results page shows the documents in tabs, a citation-check table,
   possible references (sections the research picked that no draft
   cited), the authorities quoted to the models, and a download menu
   (Word, PDF or Markdown).
4. Every case page, and the new-case form, lists the user's cases down
   the left, newest first, so they can switch from one case to another.

The same pipeline also runs from the command line
(`python -m battle_test`), which writes one Markdown file.

## The pipeline, step by step

All of this is `run_case()` in `battle_test/pipeline.py`. A 2-round case
that starts from case information makes **6 long model calls** (two for
research, four drafts) and about **25 short ones** for selection.

| # | Stage | Who | What happens |
|---|---|---|---|
| 1 | Legal research | Plaintiff | The model lists its legal issues as short search queries, in statute wording, as JSON. Six standard procedural queries (limitations, venue, jurisdiction, attorney fees, interest, damages) are always added, in the state's own wording where the default wording misses (Alaska). |
| | Search | Code | Each query is run against the law index, 8 results each. A section found by several queries stays with the one that ranked it highest. |
| 2 | Selecting authorities | Plaintiff | One short call per query: the model sees that query's candidates (citation, title, first 40 words) and picks the one that states the rule for this case, or none, as JSON. Up to 10 picks are kept. |
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
  Utah R. Civ. P. 56, Cal. CCP § 437c, Tex. R. Civ. P. 166a, Alaska R. Civ.
  P. 56.
- **The standard queries are worded for Utah and Texas** ("county",
  "district court", "instrument in writing"). For Alaska, which has no
  counties and whose trial court is the superior court, they found federal
  statutes and the wrong court's rules, so Alaska has its own wording
  (`grounding.STATE_STANDARD_QUERIES`). In search alone that found 5 of 6
  expected Alaska authorities, against 2. The other states still use the
  default, which their evaluation results were measured with.
- **A drafting prompt holds at most 12 sections,** each cut to about 700
  characters, so the longest prompt (the reply) fits the model's context.
- **Long sections show the part that matters, not just their start**
  (`grounding.relevant_excerpt`, since 2026-10-22). A section longer than
  the space for it shows its opening (a quarter of the space, to say what
  it's about), then the paragraphs that best match what it's wanted for,
  in the section's order, with `[…]` where text is left out. "What it's
  wanted for" is the research topic that found it, the other side's
  passage citing it, or, for the summary judgment rule, the standard
  itself. Words that appear all through the section count for nothing.
  The same goes for the fit check and the "does it say that?" check
  below. Cal. Civ. Code § 1950.5 is 26,182 characters, and its 21-day
  deposit rule starts past character 20,000.
- **Selection asks about one query at a time** because small models pick
  close to at random from a long list. Asked to choose 10 from about 100
  in one call, `qwen2.5:7b` kept 6 of the 24 expected authorities the
  search had found on the sample cases. Asked per query, it kept 21.
- **When there are more than 10 picks,** the model's own queries and the
  standard ones take turns, so the cut doesn't fall on one kind alone.
- **If a model's JSON reply is unusable,** research falls back to the
  standard queries alone, and a selection call counts as no pick. If
  nothing is picked at all, the top-ranked candidates are used. A run
  doesn't fail over it.
- **Case law isn't supported yet.** The models are told not to cite cases,
  and anything that looks like a case citation is listed as unchecked.

## Where the models run

The pipeline talks to a model through one method, `chat()`, and two
clients offer it:

- **Ollama** runs open models on the machine itself: the laptop's GPU or
  the server's. Nothing leaves the machine. This is the default.
- **Amazon Bedrock** runs larger hosted models. The case text in each
  prompt is sent to AWS, so only fictional cases may use it until the
  lawyer advising the project agrees otherwise.

`provider` in `[models]` chooses. Either way, the law index, the search
and the citation check run locally.

Both clients report the tokens each call used. A run records the totals
per model, and when the config has a price for the model, an estimated
cost. Bedrock has no JSON mode, so research and selection replies are read
leniently: text around the JSON is ignored, and an unusable reply falls
back as described above.

## The law index

The law comes from [Open US Law](https://www.vaquill.ai/open-us-law) (by
Vaquill AI, CC BY 4.0): statutes, constitutions and court rules for Utah,
California, Texas, Alaska and federal law.

- `python -m battle_test.corpus build` downloads 15 files (~200 MB),
  checks their SHA256 checksums, and builds `data/law.sqlite` (~1.4 GB,
  about 397,000 sections) in about a minute.
- **Keeping it current:** a weekly `corpus refresh` checks for a new
  snapshot and builds it beside the live law, with a report of what
  changed. It goes live only when an admin runs `corpus promote`
  ([running-on-server.md](running-on-server.md), "Law updates").
- It's a SQLite database with a full-text (FTS5) index. Searches run on
  the machine, so no case facts leave it.
- Search is **keyword search**. It returns only law that's in force, from
  the chosen state and federal law, with the state's law ranked slightly
  higher.
- **Semantic search** runs alongside it on the live site (`semantic =
  true` under `[search]` in `config.server.toml`, since 2026-10-16). It's
  off in the laptop's `config.toml` and on in `config.tunnel.toml`. Keyword search only matches shared words,
  so it misses a statute that words a topic differently from the query,
  and each state words its statutes its own way. Semantic search matches
  on meaning:
  - `python -m battle_test.corpus embed` turns every in-force section
    into a vector with an embedding model on Ollama, once per snapshot.
    The vectors are saved next to the index (`data/law-vectors.npy`).
    A hash of each section's text is saved with them, so after a new
    snapshot only new and changed sections are embedded again.
  - At run time each search query is turned into a vector the same way,
    and the closest sections are returned.
  - Each query's keyword and semantic results are merged in alternation,
    and the model then selects from that query's combined list, as before.
  - The vectors are built on the laptop and on the server. In
    evaluations it finds about twice as much of the expected law, but
    the briefs cite little more of it yet (`plans/plan.md`, "Broader
    evaluation set").
  - The same model must embed the sections and the queries. Vectors made
    by another model, or before the index was rebuilt, are refused: an
    evaluation stops, and a user's case falls back to keyword search,
    with a warning in the log.
  - **Known limit:** only each section's first 1,500 characters are
    embedded, and keyword ranking penalises long sections. California's
    statutes also have no titles or named headings in the data. Together
    these hide long California statutes such as Civil Code § 1950.5
    (security deposits) from both searches. Indexing passages instead of
    whole sections is proposed in `plans/plan.md`.
- The data is a quarterly snapshot. Every output states the date the law
  is current as of.
- **Two fixes to the Alaska statutes** are applied while building, both
  found on 2026-10-16 (snapshot v2026.09.1):
  - **Status.** The dataset marks 861 Alaska statutes "reserved" and 6
    "repealed". A sample of 36 checked on akleg.gov were all current law:
    "reserved" marks a section with one repealed subsection, "repealed" one
    that mentions repeal. That hid the Unfair Trade Practices Act from
    search. They're stored as in force, unless the text is only a repeal
    note. Alaska's constitution and court rule statuses are right, and are
    kept.
  - **Headings.** The dataset gives every Alaska statute the heading "AK
    Code", while other states' headings name the title and chapter, which
    search weights highly. The names come from akleg.gov's table of
    contents, saved in `battle_test/ak_headings.json` (47 titles, 942
    chapters) so a build doesn't depend on the site. `python -m
    battle_test.corpus ak-headings` refreshes them. Alaska law's share of
    search candidates rose from 73% to 81%, and "consumer protection"
    queries now reach chapter 45.50. They still don't rank its key sections
    (AS 45.50.471, .531) in the top 8: the chapter holds about 100 sections,
    and the more precise article name isn't in the table of contents.

## The citation check

`grounding.check_citations()` runs on every draft. It finds statute, rule
and constitution citations in many written forms ("Utah Code Ann. §",
"U.C.A.", "Rule 56 of the Utah Rules of Civil Procedure", "Code of Civil
Procedure section 437c", Alaska's "AS 09.10.053") and resolves each against
the index.

| Mark | Meaning |
|---|---|
| ✅ | In force, and its text was shown to the model |
| ☑ | In force, but the model was never shown its text |
| ❌ | Not found, not in force, or another state's law. Also marked inline in the draft |
| ⚠ | Ambiguous: it matches more than one section |
| unchecked | Looks like a citation, but the parser can't read it (case law, for example) |

Problem citations are marked, not removed, so the reviewer sees exactly
what the model wrote.

That check confirms a section exists and is in force. It doesn't confirm
the section says what the brief claims.

### Does the section fit the case? (optional)

When `[models] checker` names a model, it's also asked a question
*before* drafting: would a court apply this section in this kind of case?
It's asked once for each section the selection step picked, with the case,
the section's text, and the research topic that found the section in
front of it. A section applies if any part of it covers the case.

| Verdict | What happens to the section |
|---|---|
| *applies* | It goes to the drafting model, as before |
| *unsure* | It's left out of the drafts, and listed under "Possible references" with the checker's note |
| *does not apply* | It's left out, and listed in the appendix as set aside, with the note |

- The summary judgment rule, and the sections the other side cited, are
  never filtered.
- **One verdict for both sides.** A section the plaintiff's check left
  out is left out for the defendant too, without asking again. A section
  the plaintiff kept is asked about again for the defendant, and if it's
  left out then, it's kept out of the reply as well. The complaint and
  motion are already written by that point.
- The checker runs at `[generation] checker_temperature` (0 unless set),
  for both checks, so the same question gets the same answer. The
  drafts keep `temperature`.
- An unusable reply keeps the section, so a failing checker can't empty
  every prompt.
- `[pipeline] fit_check = false` turns this off and leaves the check
  below on.

### Does the section say that? (optional)

When `[models] checker` names a model, a second check runs on each draft
("3a" in `plans/plan.md`). For every ✅ or ☑ citation, the checker model
reads the paragraph citing it beside the section's text (up to 3,000
characters: for a long section, its opening and the parts that best match
the paragraph) and answers *supports*, *partly* or *does not support*, with
one sentence of reasoning.

- *Does not support* is marked inline, `[⚠ MAY NOT SUPPORT THIS]`. The
  next model to read the draft sees the mark too.
- *Partly* isn't marked in the draft. Both are listed in the citation
  check with the checker's note.
- A citation alone on its line isn't judged: it's cited for nothing in
  particular. At most 40 are judged per document.
- With no checker set, the check doesn't run, and the report says so.
- It's a warning, not a guarantee. On the server's 14B (2026-10-12) a
  *does not support* was right every time, by our own reading. *Partly*
  caught most of the wrong law, and also flagged about two in three
  citations that were fine. See `plans/plan.md`, 3a.

## Known weaknesses

Be clear on these before judging any output:

- **The wrong law gets chosen.** Keyword search misses statutes worded
  differently from the query, and each state words things differently. On
  the four sample cases, the briefs cite 8 of the 20 core authorities a
  competent brief should (server, 14B, 2026-10-16). Venue authorities are
  never cited in any of them. A bigger model (14B instead of 7B) didn't help,
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
- **The queue.** Background worker threads take cases from a queue, oldest
  first. On Ollama there's one thread, so one case runs at a time, because
  there's one GPU. On Bedrock, `workers` in `[web]` sets how many run at
  once. Each run has its own state, so cases running together don't
  affect each other. Queued cases survive a restart. A case that was
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
| `config.bedrock.toml` | Nothing, unless passed with `--config` | Hosted models on Amazon Bedrock, with each model's ID and price |
| `config.tunnel.toml` | Nothing, unless passed with `--config` | The laptop's law index and search, with the model calls sent to the server's Ollama through an SSH tunnel (`127.0.0.1:11435`) |
| `config.tunnel-bedrock.toml` | Nothing, unless passed with `--config` | As `config.tunnel.toml`, with the server's 14B drafting and a Bedrock model as the checker. `[models] checker_provider` splits the two, and `models.make_client()` then returns a `SplitClient` that sends each call by model name |

The `BATTLE_TEST_CONFIG` environment variable picks the file. The Docker
image sets it to the server config. Most commands also take `--config`.

| Section | What it sets |
|---|---|
| `[ollama]` | The Ollama address, timeout, and how long the model stays loaded |
| `[models]` | The provider (`ollama` or `bedrock`) and the model for each role (plaintiff, defendant) |
| `[bedrock]` | The AWS region, the output limit, and each hosted model's ID and price |
| `[generation]` | Context size and temperature |
| `[pipeline]` | Default rounds and the output folder |
| `[corpus]` | The law snapshot, which jurisdictions and document types to index, and the data folder |
| `[search]` | Whether semantic search is on, and the embedding model it uses |
| `[web]` | Host, port, data folder, cookie and proxy settings, retention, the queue cap, how many cases run at once (`workers`), the public address |
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
