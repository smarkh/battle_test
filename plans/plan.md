# Plan — Legal Adversarial Argument System (battle_test)

Source: `../smark_iq/plans/ideation.md`, "Future Separate Projects" §B, revised
after a conversation with a lawyer about how these lawsuits actually proceed.
This is a standalone project, separate from the smark_iq deployment, but
expected to reuse lessons learned there (self-hosting, local RAG,
guardrails-style network isolation and data handling).

## Status (2026-09-25)

- **Built on the dev laptop:**
  - Steps 1–3: pipeline, local law index (UT, CA, TX, federal),
    grounding, and citation checking.
  - The 3b evaluation set and the first search fixes.
  - Step 5 parts 1–2: a local web UI with accounts, case deletion, and
    3-month retention.

  107 unit tests pass.
- **Evaluation (7B):** the search fixes doubled the core authorities cited
  (3 → 6 of 16) and halved off-topic citations (4 → 2). California and
  Utah improved, Texas didn't: keyword search can't cope with each state's
  different statute wording. See 3b.
- **Next up:**
  - 3b: semantic search (probably on the server), better selection, then
    re-evaluate on 14B / ~30B.
  - Web UI part 3: upload with text extraction, and `.docx`/PDF export.
  - Moving to the smark_iq server (web UI part 4, and larger models). Plan
    written: `plans/server-migration-plan.md`. Waiting on its Phase 0
    decisions.
  - 3a (the "does it say that?" check), which needs a bigger model.
  - Step 4 (case law via CourtListener).
- **Waiting on the lawyer:**
  - a review of the three expected-authority lists
  - the Texas § 27.004 question
  - local vs. Bedrock hosting
  - whether 3-month retention fits any record-keeping duties
- **Waiting on you:** the remaining web UI questions (users, sharing,
  export format) under "Open questions".

## Goal

Battle-test a lawsuit position before it is used for real. One model acts as
the plaintiff's lawyer (drafting the complaint and a summary judgment
motion), a second model acts as the defendant's lawyer (rebutting both), and
optionally the plaintiff's model replies to that rebuttal. The system is
argumentative lawyers, not a judge: it surfaces weaknesses and never
declares a winner.

## Flow

1. **Jurisdiction selection.** The user picks a US state. Arguments must
   apply that state's law in addition to federal law, since laws vary state
   by state.
2. **Complaint.** The user either:
   - inputs a complaint they wrote themselves, or
   - enters the case information and has the plaintiff model generate the
     complaint from it.
3. **Summary judgment motion.** From the complaint (user-written or
   generated), the plaintiff model drafts a summary judgment motion that
   cites **only current, accurate, and valid law** for the selected state
   and federal law.
4. **Rebuttal.** A second model, acting as the defendant's lawyer, takes the
   complaint and the summary judgment motion and responds to them.
5. **Output (round 1).** The system outputs the original complaint, the
   summary judgment motion, and the rebuttal.
6. **Round 2 (if feasible).** The plaintiff model replies to the rebuttal,
   and that reply is added to the output.
7. **No winner.** No winner is declared. At most, the output may include an
   estimated likelihood that the plaintiff's position succeeds, clearly
   labelled as an estimate and not a ruling.

## Components

- **Two model roles:** "plaintiff" (complaint, summary judgment motion,
  round-2 reply) and "defendant" (rebuttal). They could be two instances of
  the same model with different system prompts, or two different models for
  more independent takes.
- **Orchestration layer:** a custom script/pipeline that runs the steps above
  in order and passes each document to the next step. Open WebUI's
  single-conversation chat doesn't do this multi-role turn-taking out of the
  box.
- **State selector:** a US state input that decides which state law applies
  and is passed to both roles. v1 offers Utah, California and Texas.
- **Complaint input:** two modes — paste/upload an existing complaint, or a
  structured form of case information for generation.
- **Legal authority source:** requirement 3 (only current, accurate, valid
  law) means the models can't rely on training data alone, since it may be
  outdated or invented. The project needs a grounded source of federal and
  state law, and every citation is checked against it. See "Law source (v1)"
  below.
- **Web UI:** users won't run this from a terminal. A browser app, hosted on
  the smark_iq server, runs the same pipeline behind a login. See "Web UI"
  below. The command line stays for development and testing.

## Web UI

Users reach the tool in a browser, the way smark_iq's guests reach Open
WebUI. Everything below wraps the existing pipeline (`run_case`); the
drafting, grounding and checking logic doesn't change.

### What users can do

1. **Sign in.** Each user has their own account and sees only their own
   cases.
2. **Start a case:**
   - Pick the state (Utah, California or Texas in v1).
   - Choose how the complaint comes in:
     - **Paste or upload** an existing complaint (`.docx`, `.pdf` or `.txt`;
       the text is extracted on the server), or
     - **Fill in a case information form** and have the complaint generated.
       Suggested fields: parties, the agreement and its date, what happened
       (timeline), damages and amounts, evidence available, and what the
       client wants.
   - Choose 1 or 2 rounds (round 2 adds the plaintiff's reply).
3. **Watch progress.** A run takes many minutes, so it runs as a background
   job. The page shows each stage as it happens (research, selecting
   authorities, complaint, motion, opposition, reply), with the draft text
   streaming in. The user can close the tab and come back.
4. **Read the results:**
   - Each document in its own tab: complaint, summary judgment motion,
     opposition, reply.
   - The citation check up front. ⚠/❌ marks are highlighted in the text,
     and ✅ citations link to their official source.
   - The authorities appendix, the "current as of" law date, and the
     disclaimer, shown on every result.
5. **Download** the document set (Word `.docx` and/or PDF, plus the
   Markdown file the CLI already produces).
6. **Case history:** a list of past runs, reopenable. Users can **delete** a
   case, which removes its inputs and outputs from the server for good.

### Recommended architecture

- **Standalone app, not an Open WebUI plugin.** Open WebUI's chat layout
  doesn't fit a form → background job → multi-document result. A separate
  app also keeps privileged case documents out of the general chat
  instance's database.
- **Python web backend** (e.g. FastAPI) that imports the existing
  `battle_test` package directly, so everything stays in one language.
  Server-rendered pages with light interactivity (e.g. HTMX) are enough; no
  separate JavaScript front-end build is needed.
- **Job queue with one worker.** There's one GPU, so runs execute one at a
  time and extra runs wait in line. The UI shows their place in the queue.
  The pipeline's existing `on_stage` / `on_token` callbacks feed live
  progress to the browser (e.g. server-sent events).
- **Storage:** SQLite for users, cases and job status. Case inputs and
  outputs live on the server's disk, never in git, and can be encrypted at
  rest.
- **Hosting:** its own Docker container on the smark_iq server, reached
  through the existing Caddy + Cloudflare Tunnel on a new subdomain (e.g.
  `battle.smarkiq.us`). It talks to the server's local Ollama, or to
  Bedrock if that option is adopted.
- **Guardrails:** apply smark_iq's `guardrails.md` container baseline. The
  only outbound traffic should be CourtListener citation checks (step 4)
  and the quarterly law-data download.

## Law source (v1) — free sources only

Decided: v1 uses free sources only. Paid sources are revisited after v1.

### Statutes, regulations, constitutions, court rules — Open US Law

- [Open US Law](https://www.vaquill.ai/open-us-law) (Vaquill AI): the U.S.
  Code, federal and state regulations, and the codes, constitutions and court
  rules of all 50 states, DC and Puerto Rico. About 3M sections in one
  schema.
- Downloaded (only the needed files, ~176 MB) and indexed **locally**, so
  statute lookups never send case facts off the machine.
- Licence: statute text is public domain; the compilation is CC BY 4.0.
  Credit line required: "Open US Law by Vaquill AI, CC BY 4.0."
- Refreshed quarterly (current snapshot v2026.08, dated 2026-08-14). Updating
  the local copy each quarter is a maintenance task, and every output states
  the snapshot date its statutes are current as of.
- A minority of state codes come from commercial aggregators, not official
  publishers, and have no official source URL. Citations resting on those
  records are flagged in the output.
- Statute citations produced by the models are checked against this local
  corpus. A section that isn't found, isn't in force, or belongs to another
  state is flagged ❌ inline and in the citation check. It's marked, not
  deleted, so the reviewer sees exactly what the model wrote.

### Case law — CourtListener (Free Law Project)

- [CourtListener REST API](https://www.courtlistener.com/help/api/rest/):
  search for relevant cases. Free, requires an API token.
- [Citation Lookup API](https://wiki.free.law/c/courtlistener/help/api/rest/v4/citation-lookup):
  every case citation the models produce is checked against ~18M real
  citations. Citations that don't exist, or are ambiguous, are flagged ❌
  the same way statute problems are (planned for step 4).
  - Limits: 60 valid citations per minute, 250 per request.
  - Case law only: it does not look up statutes. Statutes are checked
    against Open US Law instead.
- Privacy: citations are extracted locally with Eyecite (the open-source
  parser CourtListener itself uses) and only the citation strings are sent,
  never the complaint or case facts.

### Known gap — "still good law" for cases

- Confirming a case exists doesn't confirm it hasn't been overruled. That
  needs a citator (KeyCite, Shepard's), and no free one exists yet. Free Law
  Project is [building an open-source one](https://free.law/2025/05/01/citator/),
  still in progress and focused on Supreme Court cases. Revisit when it ships.
- v1 mitigation:
  1. Use CourtListener's "cited by" data to pull later opinions citing each
     case, and have a model scan them for negative treatment (overruled,
     reversed, criticised). This is a **warning signal only**, not a
     guarantee.
  2. Every case citation in the output is marked **"verify with
     KeyCite/Shepard's before filing."**
- What v1 can honestly claim: every citation exists, and statutes are
  current as of a stated snapshot date. It can't claim that every case is
  still good law.

### Deferred paid options (post-v1)

- [OpenLaws](https://openlaws.us/): same statute coverage with weekly federal
  and monthly state updates. Pricing not published; access by application.
- vLex/Fastcase (Cert citator), Westlaw (KeyCite), Lexis (Shepard's): would
  close the good-law gap, but APIs are sales-led or quote-only.

## Build steps

1. **Pipeline, no law data.** Done on the dev laptop. A command-line flow
   runs state → complaint (user-supplied or generated) → summary judgment
   motion → opposition → optional reply, and saves the documents as one
   Markdown file.
   - The models write `[CITATION NEEDED: …]` and `[FACT NEEDED: …]`
     placeholders instead of citing from memory or inventing facts.
   - The first Utah test showed prompts alone don't enforce this. The model
     still cited a renumbered Utah statute from memory. So any citation-like
     text outside a placeholder is now flagged in the output as unverified.
   - Also fixed from that test: the complaint names each state's correct
     trial court, captions keep the plaintiff first, and dates and signature
     blocks are placeholders.
   - Left for the server's larger models: the defendant inventing facts,
     such as a force majeure clause that isn't in the case facts, and weak
     damages analysis (a double-counted claim that the defence missed).
     Prompt rules can't fix these on a 7B model.
2. **Local statute data.** Done. `python -m battle_test.corpus build`
   downloads the statutes, constitutions and court rules for UT, CA, TX and
   federal law from snapshot v2026.08 (12 files, ~176 MB, SHA256-checked).
   It indexes them into a local SQLite full-text index (`data/law.sqlite`,
   ~1.3 GB, 369,896 sections) in about a minute.
   - Federal regulations (~2.8 GB) are left out for now. Ordinary civil
     suits rarely rely on them, and `[corpus] document_types` in
     `config.toml` can add them.
   - Every section in these four jurisdictions has an official source URL,
     so the "aggregator-sourced, flag it" case doesn't come up yet.
   - Section status is recorded (`in_force`, `repealed`, `superseded`,
     `renumbered`, …) and search returns only in-force law. The Utah files
     contain only in-force statutes, so a repealed Utah section shows up as
     "not found" instead of "repealed". It's flagged ❌ either way.
   - Citation lookup ignores spacing, case, `§` and periods, but not
     hyphens. The first run's invented `Utah Code Ann. § 78-27-102` returns
     nothing.
   - Ranking weights section titles and title/chapter names, and gives a
     small boost (1.15×) to the selected state over federal law. That puts
     each state's own summary judgment rule in the top 3 without burying
     federal law on federal topics.
   - Known limits, for step 3:
     - **Vocabulary gap.** Keyword search misses statutes worded
       differently from the query. "Written contract" vs. the statute's
       "instrument in writing" ranks Utah's § 78B-2-309 only #3. The
       Texas contract limitations search doesn't find the general
       provision. *Step 3 addresses this with model-written research
       queries. Whether that's enough is part of 3b.*
     - **Citation aliases.** Models write "Utah Code Ann. §" or "U.C.A."
       while the corpus says "Utah Code §", so lookup needs alias handling
       before it can verify model citations. *Resolved in step 3: citations
       are matched by meaning, not exact text.*
3. **Grounding.** Built.
   - **Research:** before drafting, each side lists its legal issues as
     statute-style search queries (JSON). This addresses the vocabulary
     gap: the model supplies the statute's wording, not the user's.
   - **Selection:** the index returns candidates (4 per query, interleaved
     by rank), and the model picks up to 10.
   - **Always included:** each state's summary judgment rule (Utah R. Civ.
     P. 56, Cal. CCP § 437c, Tex. R. Civ. P. 166a) is looked up directly and
     given to every brief. The defendant also gets the text of what the
     plaintiff cited, and the reply gets what the defendant cited.
   - **Drafting:** the models may cite only the sections quoted to them,
     exactly as written. Anything else gets a `[CITATION NEEDED]`
     placeholder, and case law isn't allowed yet.
   - **Checking, in code:** every statute, rule and constitution citation
     is parsed (Bluebook, spelled-out and initialism forms, e.g. "Utah Code
     Ann.", "U.C.A.", "Rule 56 of the Utah Rules of Civil Procedure",
     "Tex. Civ. Prac. & Rem. Code", "Code of Civil Procedure section 437c")
     and resolved against the index:
     - ✅ in force
     - ☑ in force but not shown to the model
     - ❌ not found, not in force, or another state's law, also marked
       inline in the draft
     - ⚠ ambiguous
     Citations the model wraps in a placeholder are unwrapped and checked
     too, and every section in a list ("Utah Code § 1-2-3, 4-5-6, and
     7-8-9", "§§ 1983 and 1988") is checked separately. Anything citation-like the parser can't read (e.g. case
     citations) is listed as unchecked.
   - **Report:** a citation-check table, the law's "current as of" date,
     the research queries, and every authority quoted to the models, with
     its official source link.
   - **Result of the first grounded Utah run:** 0 invented or outdated
     citations, where the ungrounded run cited a long-renumbered statute.
     But it exposed two quality problems the checker can't catch: wrong law
     chosen, and real law misstated. They're covered by 3a and 3b below.
   - **3a. "Does it say that?" check (to build).** Existence and in-force
     checks don't catch a real statute cited for something it doesn't say.
     In the first grounded run the complaint cited Utah Code § 78B-5-825
     (attorney fees for frivolous or bad-faith claims) as the source of the
     court's jurisdiction, and § 13-8-3 (which voids clauses sending
     construction disputes out of state) for venue where the work was done.
     Plan:
     - After each draft, pull the sentence around every ✅/☑ citation.
     - Ask a model: "Does the text of this section support this sentence?
       Answer supports / partly / does not support, with one line of
       reasoning."
     - Flag *partly* and *does not support* in the citation check and inline
       (e.g. `[⚠ MAY NOT SUPPORT THIS PROPOSITION]`).
     - Use a stronger model than the drafting one if possible. A 7B model
       judging its own citations isn't reliable, so build and evaluate
       this on the server.
     - The text comes from the local index, so this stays fully local.
   - **3b. Better selection of authorities (to evaluate).** In the first
     grounded Utah run, the model chose mostly UCC Article 2 (sales of
     goods) for a roof replacement, which is a construction/services
     contract. It left out the six-year written-contract limitations period
     (§ 78B-2-309). That's a legal-judgement error by the 7B model, not a
     search failure. Plan:
     - Re-run the Utah, California and Texas sample cases on the server's
       larger models first, before adding machinery.
     - If selection is still poor, try, in order:
       1. Show more of each candidate's text (not just its title) when
          selecting.
       2. Have the model first classify the case (e.g. "construction
          services contract, residential") and pass that to selection.
       3. Semantic (embedding) search alongside keyword search.
     - Keep a small set of expected authorities per sample case, so
       selection quality can be measured instead of eyeballed.
   - **Evaluation set (built 2026-09-25).** Three fictional sample cases,
     all residential construction disputes so they're comparable:
     - **Utah:** roofing contract abandoned mid-job, then storm damage.
     - **California:** kitchen remodel. The contractor's license had
       expired, it took a $15,000 down payment, and it abandoned the job
       after demolition.
     - **Texas:** foundation repair with piers short of the contract depth,
       a "guaranteed for life" sales promise, and a certified demand letter.

     Each has an expected-authority list in `examples/eval/*.toml`: *core*
     (a competent brief should cite it), *useful* (welcome if cited), and
     *off-topic* code areas (e.g. UCC sales law). Every listed section was
     checked against its text in the index. `python -m battle_test.evaluate`
     runs the cases and scores each run:
     - core authorities given to the models / cited
     - useful authorities cited
     - how many of the cited authorities are on the list ("on-target")
     - off-topic authorities cited
     - ❌ problem citations and remaining placeholders
     - runtime

     Results go to `output/eval/`, with a JSON file for comparing setups.
     `--validate` re-checks the lists against the index, e.g. after a new
     quarterly law snapshot.
   - **Baseline (2026-09-25, laptop `qwen2.5:7b`, 2 rounds, ~16 min/case):**

     | Case | Core given to models | Core cited | Useful cited | Cited on-target | Off-topic cited |
     |---|---|---|---|---|---|
     | California | 1/6 | 1/6 | 0/4 | 1/4 | 0 |
     | Texas | 1/6 | 1/6 | 0/2 | 1/6 | 1 |
     | Utah | 1/4 | 1/4 | 0/3 | 1/5 | 3 |

     The only core authority that ever reached a drafting prompt was the
     hard-coded summary judgment rule.
   - **Diagnosis: search is the bottleneck, not selection.** The research
     queries were replayed offline against the index:

     | Queries | Expected authorities found by search |
     |---|---|
     | The model's own research queries | 3 / 25 |
     | Same, with party names and question words stripped | 4 / 25 |
     | Short topic queries written lawyer-style (an upper bound: written knowing the targets) | 13 / 25 |

     - Without an example, the 7B writes long questions about the facts,
       e.g. "Did Golden Oak Remodeling Inc. breach the contract with Priya
       Natarajan by failing to complete the kitchen remodel…". Keyword
       search matches the names and details, not statute topics.
     - Even good topic queries miss ~40%, e.g. the attorney-fee, venue and
       jurisdiction sections and DTPA § 17.50. Keyword search alone won't be
       enough.
   - **Search fixes (built 2026-09-25):**
     1. **Topic-style research queries.** The prompt asks for 3–8 words in
        statute vocabulary, with no names, dates, amounts or questions, and
        gives a format template (`"<legal topic in statute words>"`) rather
        than a copyable example. The queries came out clean but too generic
        for keyword search ("venue proper", "jurisdiction exists"): alone,
        they still found only ~1 of 22 expected authorities.
     2. **Standard procedural queries** (`grounding.STANDARD_QUERIES`) are
        always searched too: limitations on written contracts, venue,
        trial-court jurisdiction, attorney fees, prejudgment interest, and
        contract damages. In the offline replay, topic queries plus these
        found 8 of 22 (the summary judgment rules excluded, since they're
        supplied directly). They're procedural topics every contract suit
        needs, not case-specific law.
     3. **8 results per query** (up from 4).
     4. **Tried and dropped:** requiring every query word to match (AND,
        falling back to OR). It helped hand-written queries (13 → 15 / 25)
        but not the model's.
     5. **Diagnostics:** runs record their search candidates, the
        evaluation scores "found by search" separately, and
        `--research-only` checks the search step in ~1 minute per case.
   - **Result (2026-09-25, laptop `qwen2.5:7b`, 2 rounds):**

     | Case | Core cited (baseline → now) | Useful cited | Cited on-target | Off-topic | Minutes |
     |---|---|---|---|---|---|
     | California | 1/6 → 3/6 | 0/4 → 1/4 | 1/4 → 4/6 | 0 → 0 | 16 → 13 |
     | Texas | 1/6 → 1/6 | 0/2 → 0/2 | 1/6 → 1/5 | 1 → 1 | 17 → 14 |
     | Utah | 1/4 → 2/4 | 0/3 → 0/3 | 1/5 → 2/5 | 3 → 1 | 16 → 12 |
     | **Total** | **3 → 6 / 16** | 0 → 1 / 9 | 3/15 → 7/16 | **4 → 2** | |

     - **Improved:**
       - California now cites CCP § 337, Civ. Code § 3300 and § 3287/3289.
       - Utah cites § 78B-2-309, the six-year limitations statute that
         every earlier run missed.
       - UCC sales citations in Utah dropped from 3 to 1.
     - **Unchanged: Texas.** The standard queries use Utah/California
       wording ("instrument in writing"), but Texas words these topics
       differently ("four-year limitations period… debt", "reasonable
       attorney's fees… oral or written contract"). Keyword search depends
       on each state's phrasing.
     - **Still never found:** the case-specific law. That's Cal. B&P §§ 7031
       and 7159.5 (unlicensed contractor, down payment), and the Texas RCLA
       and DTPA. The model's case-specific queries ("contractor license
       required", "consumer protection") are too vague.
     - **Selection is now a visible second bottleneck:** 3 core
       authorities were found but not picked (Cal. CCP § 395, Tex. Gov't
       Code § 24.007, Utah § 15-1-1), and Utah § 78A-5-102 was given to the
       model but not cited.
   - **Next for 3b, in order:**
     1. **Semantic (embedding) search** alongside keyword search. Texas
        shows keyword search can't cope with state-by-state wording. This
        is the biggest remaining gain. Embedding ~150k sections is slow on
        the laptop, so it's probably built on the server.
     2. **Selection:** show more of each candidate's text, and consider
        more than 10 picks.
     3. **Re-evaluate on the server's 14B / ~30B,** which should write
        sharper case-specific queries and select better.
     - **Watch for overfitting:** the expected lists and the standard
       queries were written by the same person tuning the search. The
       lawyer's review of the lists protects against that.
   - **Needs the lawyer:**
     - **The lists are drafts** (`lawyer_reviewed = false`). Choosing the
       right law is the judgement being measured, so the lists should be
       reviewed before the scores are trusted.
     - **Texas RCLA notice.** The index marks Tex. Property Code § 27.004
       (the Residential Construction Liability Act's pre-suit notice and
       offer provision) as **repealed in 2023**, while §§ 27.001–27.003 are
       in force. The lawyer should confirm this. If the data is wrong,
       that's a data-quality problem worth knowing about beyond this case.
     - **Conditional authorities.** Attorney-fee statutes that depend on a
       fee clause (Utah § 78B-5-826, Cal. Civ. Code § 1717) are "useful",
       not "core", because the sample facts don't say whether the contract
       has one.
4. **Case law.** CourtListener search and citation checks, plus the
   "verify with KeyCite/Shepard's before filing" flags.
5. **Web UI.** Build the browser app described in "Web UI" above, then move
   it to the smark_iq server with the rest of the project. Suggested order:
   1. Backend and job queue running the pipeline, with the case form,
      progress and result pages, on this laptop.
   2. Accounts and login, case history and deletion.
   3. Upload with text extraction, and `.docx`/PDF export.
   4. Containerise and deploy on the server behind Caddy + Cloudflare
      Tunnel. Detailed plan: `plans/server-migration-plan.md`.

   This can start before step 4 is finished. The UI only depends on
   `run_case` and its results, and step 4 adds to those without changing
   their shape.

   **Part 1: done (2026-09-25),** as `python -m battle_test.web`.
   - **Stack:** FastAPI with server-rendered Jinja templates and one small
     script (form mode switch, document tabs, live progress over
     server-sent events). No front-end build step, and no CDN, so pages
     load nothing from third parties.
   - **Queue:** jobs are stored in SQLite and files under `cases/web/`
     (gitignored). A single worker thread runs one job at a time.
     - Queued jobs survive a restart.
     - A job that was mid-run is marked failed, with a message saying the
       server restarted.
   - **Pages:**
     - Case list.
     - New case: state, generate from a case information form or paste a
       complaint, 1 or 2 rounds.
     - Live progress, with queue position, completed stages and the draft
       streaming.
     - Results, showing:
       - the disclaimer with the law date, and the citation-check table
         with a "to look at" list
       - the documents in tabs, with ✅ citations linked to their official
         source and ❌/⚠ marks and placeholders highlighted
       - the authorities appendix, and the research queries
     - Failure page.
     - Markdown download.
   - **Safety for now:**
     - No login yet, so the server refuses any host but `127.0.0.1` /
       `localhost`.
     - Model output is HTML-escaped before any highlighting is added.
     - Outbound source links use `rel="noopener noreferrer"`, so case URLs
       aren't leaked to le.utah.gov and similar sites.
     - Input is capped at 60,000 characters.
   - **`--demo-model`:** canned drafts for UI work without a GPU. Research,
     selection and checking still run against the real index, and the
     output is labelled `demo`.
   - **Verified:** 9 web tests (full runs from facts and from a pasted
     complaint, the event stream, validation, escaping, link matching),
     plus a manual pass in Chrome with the demo model: form, live
     progress, results, tabs, and no console errors.
   - **Not yet tested with the real model** in the UI. The GPU was busy
     with the evaluation run. That's the next check.
   - **Part 2, accounts: done (2026-09-25).**
     - **Accounts:** the app's own. An admin creates them with `python -m
       battle_test.web.users add NAME [--admin]`, plus `passwd`,
       `disable`, `enable` and `list`. There's no self-signup.
     - **Passwords:** typed at a hidden prompt, never as arguments. Stored
       only as salted scrypt hashes, and must be 12+ characters without the
       username. Five failed logins lock that username for 15 minutes, and
       the same error is shown for a wrong username or a wrong password.
     - **Sessions:** random tokens in an HttpOnly, SameSite=Lax cookie. The
       database stores only a SHA-256 of each token. Sessions last 7 days.
       A password change or disabling an account ends that user's sessions.
     - **Forms:** every form carries a per-session CSRF token, and posts
       from another origin (including `Origin: null`) are refused.
     - **Headers:** a Content-Security-Policy allowing only the app's own
       files, no framing, `no-store` caching on case pages, and a
       same-origin referrer policy.
     - **Privacy:** each case belongs to the user who created it. Anyone
       else gets a 404, the same as for a missing case.
     - **Account page:** users can change their own password.
     - **Cookies:** a `secure_cookies` setting in `[web]` must be turned on
       once served over HTTPS.
     - **Found in the browser check:** a `no-referrer` policy makes Chrome
       send `Origin: null` on form posts, which broke sign-in. The fix is
       the same-origin policy, and a regression test covers it.
     - **Tested:** 29 web and auth tests, plus a manual sign-in in Chrome.
     - Accounts live in `cases/web/users.sqlite` (gitignored). Cases
       created before accounts existed have no owner and aren't shown to
       anyone.
   - **Part 2, case deletion and retention: done (2026-09-25).**
     - **Default retention: 3 months.** Finished and failed cases are deleted
       automatically 90 days after they're started (`retention_days` in
       `[web]`, where 0 keeps them forever). The server purges at startup
       and hourly, removing the database row and the case's folder
       (inputs, results and download). Queued and running cases never
       expire.
     - **Manual delete:** a "Delete case" button on results and failure
       pages leads to a confirmation page. It's a real page rather than a
       browser pop-up, and says the deletion can't be undone. Only the
       owner can delete (anyone else gets a 404), the form needs its CSRF
       token, and a running case can't be deleted.
     - **Visible deadline:** the case list shows each case's deletion date
       and states the retention period. The results page repeats the
       date.
     - **Tested:** 4 store tests (file removal, expiry rules, 0 = keep,
       per-user lists) and 3 web tests (the delete flow with permission and
       CSRF checks, running-case refusal, dates shown).
   - **Empty case list shows the form (2026-09-25).** A user with no cases
     lands straight on the new-case form ("Start your first case") instead
     of a "Start one" link. The form is a shared template
     (`_case_form.html`), used by this page and the New case page.
   - **Part 2 is now complete.** Next is part 3: upload with text
     extraction, and `.docx`/PDF export.
   - **New-case options renamed** to say what gets drafted: "Draft the
     complaint and the motion" (from case information) or "Use my
     complaint, draft only the motion" (paste). The case list shows which
     was used.

## Model size estimate

**Estimate:** about **30B parameters is the minimum for the pipeline to be
reliably useful**, and **about 70B for the "does it say that?" check (3a)
and consistently sound legal judgement**. 14B should be a clear step up
from today's 7B, but not enough on its own.

These are estimates from how model families generally behave at each size,
not measurements on this task. They should be confirmed with the sample-case
evaluation planned in 3b: the same UT/CA/TX cases, scored against their
expected authorities.

### What the system needs from the model

| Capability | Where it's used | 7B today |
|---|---|---|
| Follow strict rules: cite only the list, invent no facts, keep caption and placeholders | Every draft | Partly. Invented a force majeure clause and misstated statutes, but the list-only rule mostly held. |
| Valid JSON on request | Research, selection | Worked (with Ollama's JSON mode) |
| Legal judgement: which law governs this kind of case | Selection | Poor. Chose sales-of-goods law for a construction contract. |
| Read a statute and state it accurately | Drafting, 3a check | Poor. Cited an attorney-fee statute as the source of jurisdiction. |
| Long context: 3 prior documents plus quoted statutes, ~10–16k tokens | Opposition, reply | Works at 12k, but quality drops with length. |
| Judge whether a citation supports a sentence | 3a check | Not attempted. A 7B model judging this isn't trustworthy. |

### Size tiers

| Size | Example models (or current equivalents) | Expected result |
|---|---|---|
| **7B** (today) | `qwen2.5:7b` | Fine for building and testing the pipeline. Not for judging output quality. |
| **14B** | Qwen2.5 14B, Qwen3 14B, Phi-4 14B | Noticeably better at following rules and at JSON, with fewer invented facts. Legal judgement and statute reading are still unreliable. The best size for fast iteration on the server. |
| **~30B** | Qwen2.5/Qwen3 32B, Qwen3 30B-A3B (MoE), Gemma 3 27B, Mistral Small 24B | **The realistic minimum for trustworthy drafts:** rules held consistently, sensible choice of law in most cases, and statutes described accurately most of the time. |
| **~70B** | Llama 3.3 70B, Qwen2.5 72B | Better legal reasoning and a credible 3a checker. Also the right size for the "judge" side of 3a if drafting stays at ~30B. |

### What fits on the smark_iq server

RTX 5070 Ti with **16 GB VRAM** and **24 GB system RAM**. VRAM is the limit:
the model's weights plus its context cache (the memory used to hold the
prompt, which grows with context length) must fit on the card to run at
full speed.

| Model (Q4 quantization) | Weights | + context cache at 16k | Fits in 16 GB? | Expected speed |
|---|---|---|---|---|
| 14B dense | ~9 GB | ~3 GB | ✅ fully on GPU | Fast. `qwen2.5:14b` runs at ~80 tok/s on this server. |
| 24B dense (Mistral Small) | ~14 GB | ~2.5 GB | ⚠ only with an 8-bit context cache and ≤12k context | Fast if it fits |
| 30B-A3B MoE (Qwen3) | ~18–19 GB | ~1.5 GB | ❌ ~4 GB spills to CPU | Still usable: only ~3B parameters are active per token, so spilling costs less |
| 32B dense | ~19–20 GB | ~4 GB | ❌ ~8 GB spills to CPU | Slow (roughly 5–15 tok/s), and 24 GB of RAM is tight alongside Windows and Open WebUI |
| 70B dense | ~40–43 GB | ~5 GB | ❌ | Needs new hardware |

A full run is 8 model calls: research and selection for each side, then
four drafts. That's roughly 10k tokens written and 40k+ read. At 80 tok/s
that's a few minutes. At 5–15 tok/s it's 15–40 minutes, which the UI's
background job queue can absorb.

### Recommended path

1. **Start with 14B on the server** (fully on the GPU, fast) to re-run
   the sample cases and set a quality baseline.
2. **Try the ~30B tier next.** Qwen3 30B-A3B is the best fit for 16 GB,
   since it's a MoE model that tolerates partial CPU offload. Mistral Small
   24B is the dense option that nearly fits. Compare both against 14B on the
   sample-case evaluation.
3. **If ~30B dense or 70B turns out to be needed,** hardware options:
   - **More system RAM** (the board supports up to 256 GB). A cheap
     upgrade that makes 32B partial offload comfortable, but slow.
   - **A 24–32 GB GPU** (e.g. RTX 3090/4090/5090) to run 32B fully on the
     GPU.
   - **48 GB+ of VRAM** for 70B. This means a workstation card, or two
     cards, which the current 750 W PSU and case may not support.
4. **Mixing sizes is an option.** Draft with ~30B and run the 3a check with
   a larger model, since the check is short and can tolerate a slower model.
5. **The GPU is shared with smark_iq's Open WebUI.** A large battle_test
   model will push the chat models out of memory while it runs. Plan for
   that, e.g. run battle_test jobs off-hours, or accept slower chat while a
   job runs.

## Option: hosted models on Amazon Bedrock (not decided)

**Status: an option under consideration, not a decision.** The current
decision is still local models via Ollama (see "Decisions made"). This
section records what Bedrock would offer and cost, so it can be decided
later with the lawyer.

### What it would change

- **Only the model calls move.** The law index, citation checking, reports,
  web UI and case storage stay on the smark_iq server. The pipeline gets a
  Bedrock client with the same `chat()` interface as the Ollama client,
  plus a config switch (`provider = "ollama" | "bedrock"`), so the two can
  be compared on the same sample cases.
- **Model size stops being the limit.** Bedrock offers models well above
  the ~30B / ~70B thresholds in "Model size estimate": Llama 3.3 70B,
  Qwen3 235B, DeepSeek v3.2, Mistral Large 3, gpt-oss-120b and Claude
  models. That makes 3a (the "does it say that?" check) and 3b (choosing
  the right law) realistic without new hardware.
- **Speed and GPU sharing.** A run takes minutes instead of 25+ on the
  laptop, and the server's GPU stays free for Open WebUI.

### The deciding question: case data leaves the machine

Prompts contain the case facts, complaint and briefs, so privileged
material would be processed by AWS. This reverses the "Models" decision
that case documents stay on the machine.

- AWS says Bedrock doesn't store or log prompts, doesn't use them to train
  models, and doesn't share them with model providers. Data is encrypted in
  transit and at rest, and Bedrock is HIPAA-eligible under a BAA.
- **The lawyer should confirm this is acceptable** under their
  confidentiality duties (see also ABA Formal Opinion 512 on generative AI
  tools).
- If adopted:
  - Use a **regional** endpoint (e.g. us-west-2) rather than the global
    one, so data stays in a known region (~10% price premium).
  - Keep AWS credentials out of git, per `guardrails.md`.
  - Set an AWS Budgets alert.

### Estimated cost

Per run: ~50k input and ~12k output tokens (8 calls plus the planned 3a
check, with headroom). Prices are on-demand, US regions, as of 2026-09.
Check them before relying on these, especially the Claude prices, which
came from third-party summaries.

| Model | Price per 1M tokens (in / out) | ≈ per run | ≈ 100 runs/month |
|---|---|---|---|
| gpt-oss-120b | $0.15 / $0.60 | $0.015 | $1.50 |
| Qwen3 32B | $0.15 / $1.20 | $0.02 | $2 |
| Mistral Large 3 | $0.50 / $1.50 | $0.04 | $4 |
| Llama 3.3 70B | $0.72 / $0.72 | $0.045 | $4.50 |
| DeepSeek v3.2 | $0.62 / $1.85 | $0.05 | $5 |
| Qwen3 235B | $0.53 / $2.66 | $0.06 | $6 |
| Claude Haiku 4.5 | $1 / $5 | $0.11 | $11 |
| Claude Sonnet 5 | $2 / $10 | $0.22 | $22 |
| Claude Opus (current) | $5 / $25 | $0.55 | $55 |

- Other AWS costs: negligible if the app stays on the smark_iq server.
  Moving the whole app to AWS would add ~$15–40/month for no real benefit.
- Batch inference is 50% cheaper but asynchronous, which doesn't suit the
  UI's live progress.
- Compared with hardware: a 24–32 GB GPU (~$800–2,500+) costs as much as
  roughly 9,000+ Claude Sonnet runs or 40,000+ Llama 70B runs, and still
  only reaches ~32B locally.

### If it's adopted, a likely setup

- Drafting with Llama 3.3 70B or Qwen3 235B (~$0.05/run).
- The 3a check with Claude Sonnet, where accuracy matters most. It's a
  short call, so only a few cents per run.
- Keep Ollama as a fallback provider, e.g. for cases the lawyer decides
  must not leave the machine.

### Before deciding

1. The lawyer's view on sending case material to Bedrock.
2. Build the Bedrock client behind the config switch (cheap either way).
3. Run the UT/CA/TX sample cases on local 14B/~30B and on Bedrock models,
   and compare using the 3b scoring.
4. Decide per model role: drafting, 3a check, or both.

## Decisions made

- **Web UI sign-in:** the app's own accounts, created by an admin from the
  command line, with no self-signup. Each user sees only their own cases.
  See step 5, part 2.
- **Case retention (default, for now):** 3 months, then automatic deletion.
  Users can delete sooner. See step 5, part 2.
- **End result:** no declared winner and no judge role. The output is the
  document set (complaint, motion, rebuttal, optional reply), optionally
  with a likelihood-of-success estimate.
- **Grounding:** arguments must be grounded in verified current law, not
  just the uploaded text.
- **Law source (v1):** free sources only. Open US Law (local) for statutes
  and regulations, CourtListener for case law and citation checks. Details
  in "Law source (v1)" above.
- **State coverage (v1):** Utah, California and Texas, plus federal law.
  Utah is required. California and Texas are large, heavily litigated, and
  have civil procedure that differs from Utah's, which tests that switching
  states really changes the law applied. The state selector offers only
  these three in v1. Open US Law covers every state, so adding more later
  means testing output quality for that state, not sourcing new data.
- **Models:** local models via Ollama, so case documents stay on the
  machine. The only outbound calls are the citation-string checks to
  CourtListener. Early development happens on this dev laptop (RTX 3050 Ti,
  4 GB VRAM, `qwen2.5:7b` already pulled). That's enough to build and test the
  pipeline end to end, but not to judge drafting quality. Once there's real
  progress, the project migrates to the smark_iq server, where larger models
  and round-2 feasibility get evaluated. Code should keep the Ollama URL and
  model names in config so the move is a config change. Hosting the models
  on Amazon Bedrock is being considered as an alternative but isn't decided.
  See "Option: hosted models on Amazon Bedrock (not decided)".

## Open questions

- **Likelihood estimate:** include it or not, and if so, how is it worded
  and justified so it doesn't read as a verdict?
- **Round 2:** it runs end to end on the laptop at `num_ctx` 12288 (every
  Utah test so far ran both rounds). Still to confirm on the server: that
  quality holds as the reply's input grows, and the runtime with larger
  models.
- **Which local models:** which models on the server are strong enough for
  legal drafting, and whether the plaintiff and defendant roles should use
  different models. Evaluated after migration. See "Model size estimate"
  for the starting point: 14B baseline, then the ~30B tier.
- **Local vs. Bedrock:** stay fully local, or move model calls to Amazon
  Bedrock? This hinges on the lawyer's view of sending case material to
  AWS. See "Option: hosted models on Amazon Bedrock (not decided)".
- **Data sensitivity:** complaints and case information are likely
  sensitive/privileged, so the same data-handling considerations as the tax
  idea (and smark_iq's `guardrails.md`) apply. The output also likely needs
  a disclaimer that it assists a licensed attorney's review rather than
  replacing it.
- **UI: who the users are.** Lawyers only, or also clients preparing a case
  for their lawyer? How many accounts to start with? This affects the
  wording of the case form, how prominent the disclaimers are, and whether
  there should be an admin role.
- **UI: sign-in.** Decided (2026-09-25): the app's own accounts, created by
  an admin, with no self-signup. Still open: whether to add Cloudflare
  Access in front as a second layer when it's deployed.
- **UI: sharing a case.** Can a user share a case with another account
  (e.g. a client with their lawyer), or is every case private to its
  creator?
- **UI: export formats.** `.docx` (editable, what lawyers usually work in),
  PDF, or both?
- **UI: data retention.** Decided for now (2026-09-25): 3 months, then
  automatic deletion, and users can delete sooner. Worth confirming with the
  lawyer against any record-keeping duties. It's one setting to change.

## Restart prompt

Paste this into a new Claude Code session, opened in the `battle_test`
folder, to pick up where the 2026-09-24 session left off:

```
We're continuing work on battle_test, the legal adversarial argument system.
Start by reading plans/plan.md (especially "Status", "Build steps", "Model
size estimate", "Option: hosted models on Amazon Bedrock (not decided)" and
"Open questions") and README.md, then skim the code in battle_test/ and
tests/.

Where things stand:
- Steps 1-3 are built and run from the command line on this dev laptop
  (RTX 3050 Ti, 4 GB VRAM, Ollama with qwen2.5:7b): pipeline, local Open US
  Law index for UT/CA/TX/federal (data/law.sqlite), grounded research ->
  selection -> drafting, and code-level citation checking with inline flags.
- The latest Utah sample run had 0 invented/outdated citations, but the 7B
  model often chose the wrong law (UCC sales/lease sections for a
  construction contract) and misstated statutes. That's tracked as 3a
  ("does it say that?" check) and 3b (better selection).
- Local vs. Bedrock hosting is NOT decided. It's waiting on the lawyer's
  view of sending case material to AWS.

Before doing anything else:
1. Run `python -m unittest` (use .venv/Scripts/python) and confirm all
   tests pass.
2. Check that data/law.sqlite exists. If it doesn't, rebuild it with
   `python -m battle_test.corpus build`.
3. Tell me what you think the next step should be, given the plan's Status
   section, and wait for me to confirm before starting it.

Working rules for this project:
- Don't git commit or push. I handle all commits and pushes myself. When
  you finish something, list the changed files and suggest a commit message.
- Record decisions and findings in plans/plan.md as we go, and keep
  README.md accurate.
- Test changes against the real model with the Utah sample case
  (examples/utah_roofing_facts.md), not just unit tests, and report the
  results honestly, including what got worse.
- Case documents are sensitive. Keep them in cases/ or output/ (both
  gitignored), and don't send case content to any external service.
```
