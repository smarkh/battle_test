# battle_test

Stress-tests a lawsuit before it's used for real. A plaintiff model drafts
the complaint (or takes yours) and a motion for summary judgment. A defendant
model writes the opposition, and optionally the plaintiff model replies. No
winner is declared. See `plans/plan.md` for the full design.

**Status: plan step 3.** Drafts are grounded in a local copy of the law:
1. Before drafting, each side searches the law index for the statutes and
   rules it needs.
2. It may cite only the sections it's shown, and everything else gets a
   `[CITATION NEEDED: …]` placeholder.
3. Every statute, rule and constitution citation in the result is then
   checked in code: ✅ in force, ❌ not found / not in force / another
   state's law (also marked inline in the draft), or ⚠ ambiguous.

Case law isn't checked yet (plan step 4), so the models are told not to cite
cases, and any case citation that appears is flagged.

On the dev laptop's 7B model the checking works, but the model often picks
the wrong law or misstates it. `plans/plan.md` covers the model size needed
("Model size estimate"), the planned fixes (3a/3b), the web UI (step 5),
and the Bedrock hosting option.

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
| `battle_test/web/jobs.py` | Case storage, the one-at-a-time job queue, live progress events |
| `battle_test/web/auth.py` | Accounts, password hashing, sessions, login lockout |
| `battle_test/web/users.py` | Admin command for accounts (`python -m battle_test.web.users`) |
| `battle_test/web/render.py` | Turning a finished run into HTML (links, highlights, summaries) |
| `battle_test/web/demo.py` | The demo model for working on the UI without a GPU |
| `battle_test/web/templates/`, `static/` | Pages, CSS, and the small progress/tabs script |
| `examples/` | Fictional sample cases (UT, CA, TX) for testing |
| `examples/eval/` | Expected authorities for each sample case |
| `plans/plan.md` | Design, decisions, build steps, and open questions |

## Web UI (local preview)

A browser front end for the same pipeline (plan step 5, parts 1–2). First
create an account. You'll be asked for the password at a hidden prompt, so
run it yourself in a terminal:

```
python -m battle_test.web.users add NAME --admin
```

Then start the server and open http://127.0.0.1:8000:

```
python -m battle_test.web                 # real model via Ollama
python -m battle_test.web --demo-model    # canned drafts, no GPU needed
```

- **Sign in** with that account. There's no self-signup: an admin adds
  people with `python -m battle_test.web.users add NAME`, and can also
  `passwd`, `disable`, `enable` or `list` accounts. Users can change their
  own password under their name in the header. Each user sees only their
  own cases.
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
  - A Markdown download.
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
- Until the HTTPS deployment step, the server refuses to listen on anything
  but `127.0.0.1` / `localhost`. Set `secure_cookies = true` in `[web]` once
  it's served over HTTPS.
- Cases and accounts are stored in `cases/web/` (gitignored).
`--demo-model` still runs research, selection and citation checking against
the real law index. Only the drafting is canned, and its output is labelled
`demo`.

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
