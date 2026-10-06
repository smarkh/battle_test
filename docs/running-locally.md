# Running battle_test locally

For development and testing on your own machine. Commands are for
PowerShell on Windows, run from the project folder. On macOS or Linux, use
`.venv/bin/python` in place of `.venv\Scripts\python`.

## First-time setup

You need Python 3.11 or newer, [Ollama](https://ollama.com), and about
6 GB of free disk space.

1. **Create the virtual environment and install the packages:**
   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
2. **Pull the model** named in `config.toml`:
   ```powershell
   ollama pull qwen2.5:7b
   ```
3. **Build the law index** (a ~176 MB download, a ~1.3 GB index, about a
   minute):
   ```powershell
   .venv\Scripts\python -m battle_test.corpus build
   ```
4. **Check it all works:**
   ```powershell
   .venv\Scripts\python -m unittest
   .venv\Scripts\python -m battle_test.corpus info
   ```
   The tests should all pass. `corpus info` should show snapshot v2026.08
   and about 370,000 sections.
5. **Create your web account.** This asks for a password at a hidden
   prompt, so run it yourself in a terminal:
   ```powershell
   .venv\Scripts\python -m battle_test.web.users add NAME --admin --password --plan unlimited
   ```
   The password must be 12 or more characters. Local accounts live in
   `cases\web\users.sqlite` and are separate from the server's.

## Before each session

- **Ollama is running.** It starts with Windows. `ollama list` should show
  `qwen2.5:7b`.
- **The law index exists:** `data\law.sqlite`. If it's missing, repeat
  step 3 above.

## Start the web UI

```powershell
.venv\Scripts\python -m battle_test.web                # real model
.venv\Scripts\python -m battle_test.web --demo-model   # canned drafts, no GPU needed
```

Then open **http://127.0.0.1:8000** and sign in.

- **`--demo-model`** is for working on the pages. Research, selection and
  citation checking still run against the real law index. Only the drafts
  are canned, and the output is labelled `demo`.
- **`--port 8001`** uses another port. You need it if port 8000 is taken,
  for example by an SSH tunnel to the server.
- A real run takes roughly 12 to 16 minutes on a 4 GB laptop GPU with the
  7B model.
- The local server listens on `127.0.0.1` only. It refuses to start on
  any other address.

## Stop the web UI

Press **Ctrl+C** in the window running it.

- A case that was running is marked failed ("interrupted") the next time
  the server starts.
- Queued cases resume on the next start.

## Run one case from the command line

```powershell
.venv\Scripts\python -m battle_test --state UT --facts examples\utah_roofing_facts.md
.venv\Scripts\python -m battle_test --state CA --complaint path\to\complaint.md --rounds 1
```

| Option | Meaning |
|---|---|
| `--state` | `UT`, `CA` or `TX`. Federal law always applies. |
| `--facts FILE` | Case information. The complaint is generated from it. |
| `--complaint FILE` | A complaint you wrote, used as written. Give this or `--facts`. |
| `--rounds 1` or `2` | Round 2 adds the plaintiff's reply. The default is in `config.toml`. |
| `--out FILE` | Where to save. The default is a timestamped file in `output\`. |
| `--quiet` | Don't stream the drafts to the terminal. |
| `--config FILE` | Use another config file. |

Stop a run with **Ctrl+C**. Nothing is saved for an interrupted run.

## Other commands

| What | Command |
|---|---|
| Unit tests | `.venv\Scripts\python -m unittest` |
| Search the law index | `.venv\Scripts\python -m battle_test.corpus search --state UT "summary judgment"` |
| Law snapshot and section counts | `.venv\Scripts\python -m battle_test.corpus info` |
| Evaluate all three sample cases (~40 min) | `.venv\Scripts\python -m battle_test.evaluate --label laptop-7b` |
| Evaluate the search step only (~1 min a case) | `.venv\Scripts\python -m battle_test.evaluate --research-only` |
| Check the expected-authority lists against the index | `.venv\Scripts\python -m battle_test.evaluate --validate` |
| List accounts | `.venv\Scripts\python -m battle_test.web.users list` |
| Add a user (prints a one-time setup link) | `.venv\Scripts\python -m battle_test.web.users add NAME --plan solo` |

The account tool has more commands (`invite`, `passwd`, `plan`, `firm`,
`paid`, `disable`, `enable`, `usage`). Run it with `--help`, or see the
root `README.md`.

## Where your local data is

| Folder | Holds |
|---|---|
| `data\` | The downloaded law files and `law.sqlite`. Safe to delete and rebuild. |
| `cases\web\` | `users.sqlite`, `cases.sqlite`, and one folder per case |
| `output\` | Command-line results, and evaluation results under `output\eval\` |

All three are gitignored. Keep any real case material in `cases\`.

## Trying a change without touching your real accounts

Copy `config.toml` to a scratch file outside the repo, point its `[web]
data_dir` at a temporary folder, and pass `--config` to both the account
tool and the web server. Paths in a config file are relative to that
file's folder, so use absolute paths for `data_dir` in `[corpus]` and
`[web]`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Could not reach Ollama at http://localhost:11434` | Ollama isn't running. Start it, then check `ollama list`. |
| `No law index at ...\law.sqlite` | Build it: `python -m battle_test.corpus build`. |
| A warning that the model "used N of 12288 context tokens" | The input was too long and may have been cut. Shorten it, or raise `num_ctx` in `[generation]` if the GPU has room. |
| The port is in use | Start with `--port 8001`. |
| Sign-in refused after several tries | Five failures lock the username for 15 minutes. |
| "The server restarted during this run" on a case | The server was stopped mid-run. Start the case again. |
| Runs are very slow | Check `ollama ps`. If it doesn't show 100% GPU, the model has spilled to the CPU. |
