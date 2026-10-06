# Working on battle_test

How to make a change safely. Set up first with
[running-locally.md](running-locally.md).

## The workflow

1. Make the change on the laptop.
2. Run the unit tests.
3. If it affects what the models are given or write, run the evaluation.
4. If it affects a page, check it in a browser.
5. Update the docs and `plans/plan.md`.
6. Commit and push, then update the server
   ([running-on-server.md](running-on-server.md)).

## Rules

- **Case documents are sensitive.** Keep real case material in `cases/` or
  `output/`, both gitignored. Never put it in git or a Docker image, and
  never send case content to an external service.
- **Only fictional cases** go on the public site or to any hosted model,
  until the lawyer advising the project agrees otherwise.
- **No credentials in files, commands or chat.** Passwords are typed at
  hidden prompts. The tunnel token lives only in the server's `.env`. AWS
  access comes from `aws sso login`, never from keys in a config file.
- **Don't restart or change the server** without agreeing it first.
- **Report results honestly,** including what got worse.

## Unit tests

```powershell
.venv\Scripts\python -m unittest
.venv\Scripts\python -m unittest tests.test_grounding
.venv\Scripts\python -m unittest tests.test_grounding.CheckCitationsTest
```

- The full run takes about 35 seconds and needs no GPU, network or law
  index.
- Tests use fake model clients and temporary databases. Follow that
  pattern: nothing in a test should call a real model or touch
  `cases/web/`.
- Add a test with every behaviour change. A bug fix gets a test that
  fails without the fix.

## Evaluation: measuring quality

Unit tests show the code works. They don't show the briefs are any good.
The evaluation does that.

Three fictional sample cases are in `examples/`, one per state, all
residential construction disputes. Each has an expected-authority list in
`examples/eval/*.toml`:

- **core:** a competent brief on these facts should cite it
- **useful:** welcome if cited
- **off-topic:** code areas that don't apply, such as sales-of-goods law
  for a construction contract

```powershell
.venv\Scripts\python -m battle_test.evaluate --label my-change
.venv\Scripts\python -m battle_test.evaluate --case utah_roofing --rounds 1
.venv\Scripts\python -m battle_test.evaluate --research-only
.venv\Scripts\python -m battle_test.evaluate --validate
```

| Mode | Time | Use it for |
|---|---|---|
| Full run | ~40 min on the laptop, ~6 min on the server | Any change to prompts, search, selection or the model |
| `--research-only` | ~1 min a case | A fast check of the search step alone |
| `--validate` | Seconds | Checking the lists against the index, after a new law snapshot |

Useful options:

- `--repeats 3` runs each case three times and adds a total row.
- `--model NAME` uses one model for both roles.
- `--config config.bedrock.toml` runs the models on Amazon Bedrock. This
  costs money (cents per case) and sends the case text to AWS, so use the
  fictional sample cases only. See [running-on-bedrock.md](running-on-bedrock.md).

Results go to `output/eval/<timestamp>-<label>/`:

- `summary.md`: per case, which expected authorities search found, which
  were given to the models, and which were cited, plus off-topic citations,
  the citation-check counts, token usage and estimated cost
- `results.json`: for comparing setups
- each case's full document set

**Reading the scores:**

- The three stages are separate on purpose. "Found by search", "given to
  the models" and "cited" show where an authority was lost.
- One run per case at temperature 0.4 is noisy. A change of one authority
  either way means nothing.
- The expected lists are drafts and haven't been reviewed by a lawyer
  (`lawyer_reviewed = false`).
- The lists and the search tuning were written by the same person, so
  watch for tuning the search to the test.

Past scores, and what each change did, are in `plans/plan.md` under build
step 3.

## Checking pages in a browser

Use the demo model and a throwaway account, never your real accounts
database.

1. Make a scratch config that points `[web] data_dir` at a temporary
   folder (see "Trying a change without touching your real accounts" in
   [running-locally.md](running-locally.md)).
2. Create a test account with that config:
   `python -m battle_test.web.users --config SCRATCH add tester --password --plan trial`
3. Start the server: `python -m battle_test.web --config SCRATCH --demo-model`
4. Walk through the pages you changed, and check the browser console for
   errors.

The demo model's drafts each contain one ✅ citation, one ❌ citation and
one placeholder, so every kind of highlight appears.

## Conventions in the code

- **The pipeline stays on the standard library.** Third-party packages
  belong to the corpus build and the web UI only.
- **Dependencies are passed in.** `run_case()` takes its model client and
  law index as arguments. Don't reach for a global.
- **Constants carry their reasons.** Where a number was tuned (for
  example `STATE_BOOST` or `RESULTS_PER_QUERY`), the comment says what it
  was tuned on. Keep that up when you change one.
- **Fail safe.** An unusable model reply falls back to something
  sensible. A missing plan gets the most limited one. A non-local host is
  refused unless the config explicitly allows it.
- **Model output is never trusted as HTML.** It's escaped first, and tags
  are added only around text the code matched.
- **Settings go in the config,** in both `config.toml` and
  `config.server.toml` when they apply to both. `test_deploy.py` checks
  the server config.

## Keeping the documents accurate

| When you… | Update |
|---|---|
| Add, rename or repurpose a file | [code-guide.md](code-guide.md) and the root `README.md` |
| Change how something is started, stopped or deployed | [running-locally.md](running-locally.md), [running-on-server.md](running-on-server.md) or [running-on-bedrock.md](running-on-bedrock.md) |
| Change what the pipeline does | [how-it-works.md](how-it-works.md) |
| Make a decision or get an evaluation result | `plans/plan.md` |
| Do something on the server | `plans/server-migration-plan.md` |

## What's next

The current priorities are in the "Status" section of `plans/plan.md`. In
short:

1. Hosted models on Amazon Bedrock (`plans/aws-bedrock-plan.md`).
2. Better search (semantic search alongside keyword search) and better
   selection of authorities.
3. A check that each cited section says what the brief claims.
4. Case law, through CourtListener.
5. File upload and Word/PDF export in the web UI.
