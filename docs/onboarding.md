# Onboarding: setting up to work on battle_test

For a developer joining the project. It covers the software to install,
the access you need, setting up the project, the day-to-day way of
working (trunk-based, straight to `main`), and updating the server.

Commands are for PowerShell on Windows, run from the project folder,
as in the rest of these docs. On macOS or Linux, use `.venv/bin/python`
in place of `.venv\Scripts\python`.

Read [how-it-works.md](how-it-works.md) first. It explains what the system
does, and the rest of this makes more sense after it.

## 1. Ask for access

Mark (the project owner) has to set these up. Ask for all of them at once:

| Access | What it's for | What you send |
|---|---|---|
| GitHub: write access to `smarkh/battle_test` | Cloning and pushing | Your GitHub username |
| Tailscale: an invite to the tailnet, with the server `smark-iq` shared to you | Reaching the server. It has no public SSH | Your email |
| SSH: your public key on the server | Model runs through the tunnel, and deploys | Your public key (step 2) |
| A web account on `battle.smarkiq.us` (optional) | Trying the live site | A username. You get a one-time setup link to choose your password |

The models run on the server's GPU, and nothing that needs a real model
works without the tunnel. Until you have Tailscale and SSH access, you
can still run the unit tests, the law search, and the web UI with
`--demo-model`.

## 2. Install the software

| Software | Why | Get it |
|---|---|---|
| **Git** | Source control | https://git-scm.com/download/win, or `winget install Git.Git` |
| **Python 3.12** | The project runs on 3.11 or newer, and the server's image uses 3.12 | https://www.python.org/downloads/, or `winget install Python.Python.3.12`. Tick "Add python.exe to PATH" |
| **Ollama** | Only the embedding model for semantic search runs locally. The drafting models are on the server | https://ollama.com/download, or `winget install Ollama.Ollama` |
| **Tailscale** | The private network the server is on | https://tailscale.com/download, or `winget install Tailscale.Tailscale`. Sign in with the account you were invited with |
| **OpenSSH client** | The tunnel and deploys | Built into Windows 10 and 11. Check with `ssh -V` |
| **An editor** | PyCharm or VS Code both work | Point its interpreter at `.venv` (step 3) |

Optional:
- **GitHub CLI** (`winget install GitHub.cli`), for `gh auth login`
  instead of a personal access token.
- **Docker Desktop**, only for building the server's image on your own
  machine. That's a fallback, not the normal deploy
  ([running-on-server.md](running-on-server.md)).
- **AWS CLI**, only for Bedrock, which is blocked for now
  ([running-on-bedrock.md](running-on-bedrock.md)).

You need about **8 GB of free disk**: the law index is about 1.4 GB, the
section vectors about 0.6 GB, and the downloads and the virtual
environment take the rest.

**Make an SSH key** if you don't have one, and send Mark the `.pub` file.
Never send the other file, which is your private key:
```powershell
ssh-keygen -t ed25519 -C "your-email@example.com"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```
Load the key into Windows' SSH agent once. Windows remembers it across
reboots:
```powershell
Get-Service ssh-agent | Set-Service -StartupType Automatic; Start-Service ssh-agent
ssh-add $env:USERPROFILE\.ssh\id_ed25519
```
Use Windows' own `ssh` (from PowerShell), not Git Bash's. Git Bash's
`ssh` can't see the Windows agent, and the server refuses it with
"Permission denied (publickey)".

## 3. Set up the project

1. **Set Git up.** Use your own name and email. `autocrlf` matches the
   repo's line endings to Windows:
   ```powershell
   git config --global user.name "Your Name"
   git config --global user.email "your-email@example.com"
   git config --global core.autocrlf true
   git config --global pull.rebase true
   ```
2. **Clone and create the virtual environment:**
   ```powershell
   git clone https://github.com/smarkh/battle_test.git
   cd battle_test
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```
3. **Run the unit tests.** They need no model, network or law index, and
   should all pass:
   ```powershell
   .venv\Scripts\python -m unittest
   ```
4. **Build the law index** (a ~200 MB download, about a minute):
   ```powershell
   .venv\Scripts\python -m battle_test.corpus build
   .venv\Scripts\python -m battle_test.corpus info
   ```
   `info` should show snapshot v2026.09.1 and about 397,000 sections.
   If `build` says the latest published snapshot is newer than
   `config.toml`'s, see "When the law snapshot changes" below.
5. **Get the section vectors** for semantic search. Building them from
   scratch takes about 1.5 hours on a laptop GPU. Copying Mark's takes
   minutes, and is just as good: each vector is matched to its section by
   a hash of the text, so they fit your index even though you built it
   yourself.
   ```powershell
   ollama pull nomic-embed-text
   # Copy Mark's data\law-vectors.npy, law-vectors-ids.npy,
   # law-vectors-hashes.npy and law-vectors.json into your data\ folder, then:
   .venv\Scripts\python -m battle_test.corpus --config config.tunnel.toml embed
   ```
   `embed` should report almost every section "kept from the last build".
   Without the copy it builds them all, which is slow but fine (it can be
   stopped and resumed).
6. **Create your local web account.** This prompts for a password, so run
   it yourself in a terminal:
   ```powershell
   .venv\Scripts\python -m battle_test.web.users add YOURNAME --admin --password --plan unlimited
   ```
   Local accounts are in `cases\web\users.sqlite`, separate from the
   live site's.
7. **Check the web UI** without a model:
   ```powershell
   .venv\Scripts\python -m battle_test.web --demo-model
   ```
   Open http://127.0.0.1:8000 and sign in.

## 4. Running with the real models

The drafting and checking models (`qwen2.5:14b`) are on the server. Open
the tunnel in its own window and leave it open:
```powershell
ssh -N -L 11435:127.0.0.1:11434 smark@smark-iq
```
Then use `--config config.tunnel.toml`. The law search stays on your
machine, and only the model calls go through the tunnel:
```powershell
.venv\Scripts\python -m battle_test --config config.tunnel.toml --state UT --facts examples\utah_roofing_facts.md
.venv\Scripts\python -m battle_test.web --config config.tunnel.toml      # http://127.0.0.1:8001
.venv\Scripts\python -m battle_test.evaluate --config config.tunnel.toml --model qwen2.5:14b --repeats 3 --label my-change
```

- The server's GPU is shared with another project (Open WebUI), and runs
  one case at a time. **Tell Mark before a long evaluation**, so two
  aren't running at once.
- The default `config.toml` expects a local `qwen2.5:7b`. You only need
  that for working offline, and it's slow and poor on a small GPU.

## 5. The rules

These are the project's non-negotiables. The full list is in
[development.md](development.md#rules).

- **Fictional cases only.** No real case material in git, in a Docker
  image, on the public site or in any hosted model. Keep anything real in
  `cases/` or `output/`, which are both gitignored.
- **No credentials in files, commands, commits or chat.** Passwords go in
  at hidden prompts. The tunnel token exists only in the server's `.env`.
- **Don't restart or change the server without agreeing it with Mark
  first.** A restart interrupts any case that's running.
- **Report results honestly,** including what got worse.

## 6. Day-to-day: trunk-based development

There's one long-lived branch, `main`, and it's always deployable. Work
goes into it in small, complete steps, several times a day if need be,
rather than in long-running branches.

**Start of each session:**
```powershell
git pull
.venv\Scripts\python -m unittest
```
With `pull.rebase` set, `git pull` replays any local commits on top of
the latest `main` instead of making a merge commit.

**For each change:**
1. **Keep it small.** One idea per commit: a fix and its test, or one
   step of a feature. If a feature takes days, land it in steps that each
   leave `main` working.
2. **Hide unfinished behaviour behind config,** the way the project
   already does: `semantic = false`, no `checker`, `fit_check = false`.
   Code can be on `main` and deployed while it's still switched off.
3. **Test it.**
   - Add a unit test with every behaviour change. A bug fix gets a test
     that fails without the fix.
   - Run the full suite (about 40 seconds).
   - If the change affects what the models are given or write (prompts,
     search, selection, checks), run the evaluation and compare it with
     the last result in `plans/plan.md`. See
     [development.md](development.md#evaluation-measuring-quality).
   - If it changes a page, look at it with `--demo-model`.
4. **Update the docs in the same commit**
   ([development.md](development.md#keeping-the-documents-accurate)).
   Record decisions and evaluation results in `plans/plan.md`.
5. **Commit with a message that says what changed and why,** in one line
   if it fits ("Fit check: one verdict per section for both sides"), not
   "fixes" or "wip".
6. **Pull, test again, push:**
   ```powershell
   git pull
   .venv\Scripts\python -m unittest
   git push
   ```
   If the push is refused because `main` moved, `git pull` again, rerun
   the tests, and push. If the rebase reports a conflict, fix the files,
   `git add` them, then `git rebase --continue`.

**Never** force-push `main`, push with failing tests, or rewrite commits
that are already pushed.

**A short-lived branch and a pull request are fine** when you want Mark
to look before it lands: a risky change, or the server config. Keep it to
a day or two, and rebase it on `main` before merging. Don't let branches
pile up.

**Coordinate with Mark on who deploys,** so two people aren't updating
the server at once. Pushing to `main` doesn't deploy anything by itself.

## 7. Updating the server

The live site runs in Docker on the server and builds from its own
clone of the repo. The full procedure, and what to do when a step fails,
is in [running-on-server.md](running-on-server.md) ("Update the code").
In short, after agreeing it with Mark:

1. **Push your change to `main`** (section 6).
2. **Connect:**
   ```powershell
   ssh smark@smark-iq
   cd C:\Users\smark\Documents\work\battle_test
   ```
   The server's shell is Windows PowerShell 5.1: chain commands with `;`,
   not `&&`.
3. **Check that no case is running.** A restart fails any case in
   progress. Save this on your machine as `active_cases.py`:
   ```python
   import sqlite3
   db = sqlite3.connect("file:/data/cases/cases.sqlite?mode=ro", uri=True)
   print(db.execute("SELECT status, COUNT(*) FROM cases "
                    "WHERE status IN ('queued', 'running') GROUP BY status").fetchall())
   ```
   Then, from your machine (not inside the SSH session):
   ```powershell
   Get-Content active_cases.py | ssh smark@smark-iq "docker exec -i battle-test python -"
   ```
   `[]` means nothing is queued or running.
4. **Back up the accounts database** if the change touches accounts or
   the activity log:
   ```powershell
   docker cp battle-test:/data/cases/users.sqlite $HOME\users-backup.sqlite
   ```
5. **Pull and build:**
   ```powershell
   git pull
   docker compose build
   docker images battle-test
   ```
   The image should be a few minutes old. Over SSH, `docker compose build`
   often fails with a credentials error. The classic-builder fallback is
   in running-on-server.md.
6. **Restart on the new image:**
   ```powershell
   docker compose up -d
   ```
7. **Check it's healthy.** `docker ps` should show `battle-test` as
   healthy. From your machine:
   ```powershell
   curl.exe -4 https://battle.smarkiq.us/healthz
   ```
   It should return `{"status":"ok"}`. Also check that
   https://llm.smarkiq.us still loads: it's the other project on the same
   server.
8. **Delete the backup** once you've checked the site works, and note
   anything you did on the server in `plans/server-migration-plan.md`.

**Use `docker compose stop`, never `down`.** `down` removes the
containers. **Check which folder you're in** before any Docker or `.env`
change: the server also runs another project, smark_iq, in
`C:\Users\smark\Documents\work\smarkiq`, and a `.env` copied to the wrong
folder once overwrote its secrets. The other rules are at the top of
running-on-server.md.

**Running long or quoted commands over SSH:** a one-line `ssh smark@smark-iq "..."`
breaks as soon as the command has its own quotes. Write a `.ps1` script,
copy it with `scp script.ps1 smark@smark-iq:`, and run it with
`ssh smark@smark-iq "powershell -NoProfile -ExecutionPolicy Bypass -File script.ps1"`.

## 8. When the law snapshot changes

The live site checks for new law every Sunday and builds it beside the
live law. An admin approves it with `corpus promote`
([running-on-server.md](running-on-server.md), "Law updates"). Once that's
done, `corpus.snapshot` in `config.toml` and `config.server.toml` has to
be updated to match. On your machine, rebuild the index with
`corpus build`, then run `corpus --config config.tunnel.toml embed`. Only
new and changed sections are embedded, so it takes minutes.

## 9. For Mark: granting access

- **GitHub:** the repo's Settings → Collaborators → Add people, with the
  Write role.
- **Tailscale:** invite them to the tailnet (admin console → Users →
  Invite), or share just the `smark-iq` machine with them (Machines →
  `smark-iq` → Share).
- **SSH:** the `smark` account on the server is an administrator, so
  Windows' OpenSSH reads its keys from
  `C:\ProgramData\ssh\administrators_authorized_keys`, not
  `~\.ssh\authorized_keys`. On the server, in an administrator
  PowerShell, add their public key as one new line:
  ```powershell
  Add-Content C:\ProgramData\ssh\administrators_authorized_keys "ssh-ed25519 AAAA... their-email"
  ```
  Then they check with `ssh smark@smark-iq hostname`. To remove their
  access later, delete that line.
  - Their key logs in as `smark`, an administrator, so they can do
    anything on the server, including touching smark_iq. If that's more
    than they need, give them their own non-administrator Windows
    account instead. Its keys then go in that account's
    `~\.ssh\authorized_keys`, and it needs Docker access, so add it to
    the `docker-users` group.
- **The live site:** `docker compose run --rm battle-test python -m battle_test.web.users add NAME --plan unlimited`
  prints a one-time setup link to send them privately. Add `--admin` if
  they should see the admin pages.
- **The vectors:** copy your `data\law-vectors*` files (four of them, about
  600 MB) to them, so they don't have to spend 1.5 hours building them
  (section 3, step 5).

## 10. Where to go next

| To… | Read |
|---|---|
| Find the file that does something | [code-guide.md](code-guide.md) |
| Run things locally: every command and option | [running-locally.md](running-locally.md) |
| Make a change: tests, evaluation, conventions | [development.md](development.md) |
| Understand why it is the way it is, and what's next | `plans/plan.md` ("Status" and "Decisions made") |
