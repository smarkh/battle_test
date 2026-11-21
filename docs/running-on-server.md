# Running battle_test on the server

The deployed copy runs in Docker on the smark_iq home server and is public
at **https://battle.smarkiq.us**.

> **Fictional cases only.** Cloudflare Access must be in front of the site
> before any beta tester signs in (see "Cloudflare Access" below). Real
> case material waits on the lawyer's sign-off
> (`docs/data-handling-for-lawyer.md`).

Commands marked **(laptop)** run in PowerShell on the laptop. Commands
marked **(server)** run on the server, after connecting over SSH.

## Rules that prevent accidents

1. **Use `docker compose stop`, never `down`.** `down` removes the
   containers. This applies to battle_test and to smark_iq.
2. **Check which folder you're in.** The server also runs smark_iq, in
   `C:\Users\smark\Documents\work\smarkiq`. battle_test is in
   `C:\Users\smark\Documents\work\battle_test`. A `.env` copied to the
   wrong one overwrote smark_iq's secrets once.
3. **The server's shell is Windows PowerShell 5.1.** Chain commands with
   `;`, not `&&`.
4. **Set passwords over SSH, not Remote Desktop.** Remote Desktop can map
   keys differently, and the password then won't work from a browser.
5. **A restart interrupts any running case.** It's marked failed. Restart
   between runs.
6. **After any server work, check both stacks** with `docker ps`, and
   that both sites answer (see "Check that it's healthy").

## What runs there

| Container | Project | Purpose |
|---|---|---|
| `battle-test` | battle_test | The app. No port is published on the server. |
| `battle-test-cloudflared` | battle_test | Its Cloudflare Tunnel connector, which makes the site public |
| `open-webui`, `caddy`, `cloudflared` | smark_iq | An unrelated project. Leave these alone. |

- **The drafting and checking models are on Amazon Bedrock** (gpt-oss
  120B, Oregon), from the beta config of 2026-11-20 on. The app calls
  them with a Bedrock API key (see "Bedrock API key"). A case takes about
  6 minutes and costs about $0.05. Two cases run at once.
- Ollama runs natively on the server (not in Docker) and serves
  `nomic-embed-text` on the GPU for semantic search. It also still has
  `qwen2.5:14b`, which the site used until the beta. Going back to it is a
  config change (`config.server.toml`, `[models]`).
- The two projects share only the GPU.
- Data is in two Docker volumes: `battle_test_battle-law` (the law index,
  its section vectors, and the law refresh's `staging/` and `archive/`)
  and `battle_test_battle-cases` (accounts and case files).
- Files copied into a volume with `docker cp` are owned by root, and the
  container can't change that. Have the app make its own copy:
  `docker exec battle-test sh -c 'cp F F.tmp && mv -f F.tmp F'`.
- The server uses `config.server.toml`, which is built into the image.
- The tunnel token is in `.env` in the battle_test folder on the server.
  It's gitignored and exists nowhere else.
- The Bedrock API key is in `secrets\bedrock_api_key` in the same folder.
  Also gitignored, kept out of the image, and nowhere else.

## Connect

The server is reached over SSH on Tailscale, with a key.

**(laptop)** Load the key into Windows' agent. Windows remembers it across
reboots, so repeat this only if SSH asks for a passphrase or is refused:
```powershell
& "$env:WINDIR\System32\OpenSSH\ssh-add.exe" "$env:USERPROFILE\.ssh\id_ed25519"
```

**(laptop)** Connect, then go to the project folder:
```powershell
ssh smark@smark-iq
cd C:\Users\smark\Documents\work\battle_test
```

Use `ssh -t smark@smark-iq` when the command you'll run asks for a
password.

## Check that it's healthy

**(server)**
```powershell
docker ps
```
Expect `battle-test` (healthy) and `battle-test-cloudflared`, plus
smark_iq's `open-webui`, `caddy` and `cloudflared`.

**(laptop)**
```powershell
curl.exe -4 https://battle.smarkiq.us/healthz
```
Expect `{"status":"ok"}`. Also check `https://llm.smarkiq.us` still loads,
so you know smark_iq wasn't disturbed.

## Start

**(server)** Start the app and make it public:
```powershell
docker compose up -d
```

- This starts both containers. The connector waits until the app reports
  healthy.
- If `docker-compose.local-test.yml` exists in the folder, rename it to
  `docker-compose.local-test.yml.disabled` first. See "Private mode".
- Both containers restart on their own after a Docker restart
  (`restart: unless-stopped`), unless you stopped them yourself.

## Stop

**(server)**

| To… | Command |
|---|---|
| Take the site off the internet, keep the app running | `docker compose stop cloudflared` |
| Stop everything | `docker compose stop` |
| Restart the app | `docker compose restart battle-test` |

**Without the server:** in the Cloudflare Zero Trust dashboard, remove the
`battle.smarkiq.us` route from the `battle-test` tunnel. The site is
offline at once.

## Private mode (reach it from the laptop only)

For testing on the server's model without the public site.

**(server)** Rename the override back if it's disabled, then start only
the app with a private port:
```powershell
docker compose -f docker-compose.yml -f docker-compose.local-test.yml up -d battle-test
```

**(laptop)** Open a tunnel and leave the window open:
```powershell
ssh -N -L 8000:127.0.0.1:8000 smark@smark-iq
```

Then browse to **http://localhost:8000** and sign in with a server
account. Press **Ctrl+C** in the tunnel window to disconnect.

- The override file isn't in git. It exists only on the server and
  publishes `127.0.0.1:8000` there.
- Starting with the override recreates the app container, which
  interrupts a running case.
- To go back to normal, rename the override to `.disabled` and run
  `docker compose up -d`.

## Update the code

1. **(laptop)** Commit and push. The server pulls from GitHub.
2. **(server)** Pull:
   ```powershell
   git pull
   ```
3. **(server)** If the update changes the accounts database, back it up
   first. Delete the copy once the update is checked:
   ```powershell
   docker cp battle-test:/data/cases/users.sqlite $HOME\users-backup.sqlite
   ```
4. **(server)** Build, then check the image is a few minutes old. This
   often fails over SSH, even for a code-only change. If it does, see
   below:
   ```powershell
   docker compose build
   docker images battle-test
   ```
5. **(server)** Restart on the new image:
   ```powershell
   docker compose up -d
   ```
6. Check it's healthy (above).

**If the build fails with a credentials error** ("A specified logon
session does not exist"): the build has to contact the image
registry, which doesn't work over SSH on this server. It happened on
2026-10-05 for a code-only change. Use any of these:

- **Classic builder, over SSH (easiest; worked 2026-10-15).** It uses the
  `python:3.12-slim` image already on the server instead of asking the
  registry, and a throwaway Docker config with no credential helper. It
  tags the image `battle-test`, as `docker compose build` would.
  ```powershell
  $env:DOCKER_CONFIG = Join-Path $env:TEMP "docker-nocreds"
  New-Item -ItemType Directory -Force $env:DOCKER_CONFIG | Out-Null
  [IO.File]::WriteAllText("$env:DOCKER_CONFIG\config.json", "{}")
  $env:DOCKER_BUILDKIT = "0"
  docker build -t battle-test .
  Remove-Item Env:DOCKER_CONFIG, Env:DOCKER_BUILDKIT
  ```
  Then carry on from step 5. A credential-free config doesn't help
  `docker compose build` itself: BuildKit still asks for credentials.
- **Remote Desktop:** sign in with Chrome Remote Desktop and run `docker
  compose build` there.
- **Build on the laptop and copy the image.** The laptop must be on the
  same commit as the server.
  1. **(laptop)** `docker compose build`
  2. **(laptop)** `docker save -o battle-test-image.tar battle-test:latest`
  3. **(laptop)** `scp battle-test-image.tar smark@smark-iq:battle-test-image.tar`
     (a plain home-relative destination)
  4. **(server)** `docker load -i $HOME\battle-test-image.tar`, then
     `docker compose up -d`
  5. Delete the `.tar` on both machines.

## Accounts

**(server, over SSH from the laptop)** Run the account tool inside the
container:
```powershell
docker compose run --rm battle-test python -m battle_test.web.users add NAME --plan solo
```

That prints a one-time setup link. Send it to the user privately. They
open it and choose their own password. The link works once and expires
after 72 hours.

**Beta testers** (one or several at once):
```powershell
docker compose run --rm battle-test python -m battle_test.web.users beta jdoe asmith --days 60
```
Each gets the Beta plan (15 cases in total), a paid-through date 60 days
away, and their own setup link. Nothing is created if any name is taken or
invalid. Usernames can't be email addresses. Each tester's email must also
be allowed in Cloudflare Access. The whole routine, with an email to send,
is in `docs/beta.md`.

| To… | Replace `add NAME --plan solo` with |
|---|---|
| Issue a new setup link (forgotten password, expired link) | `invite NAME` |
| Change the plan | `plan NAME PLAN` |
| Put the account in a firm, or take it out | `firm NAME FIRM`, or `firm NAME` |
| Set the last paid day, or remove it | `paid NAME 2026-11-30`, or `paid NAME` |
| Add beta testers | `beta NAME [NAME...] --days 60` |
| Block or restore sign-in | `disable NAME`, `enable NAME` |
| List accounts | `list` |
| Report cases per account | `usage` or `usage --month 2026-10` |
| Set a password by hand (needs `ssh -t`) | `passwd NAME` |
| Create an admin (needs `ssh -t`) | `add NAME --admin --password --plan unlimited` |

Most of this can also be done by an admin in the browser at `/admin`.
Creating or removing admins is command-line only.

## Bedrock API key

The live site calls Bedrock with a **long-term Bedrock API key**. It can
call only Bedrock, it expires on a date chosen when it's made, and it's
revoked in one click. An `aws sso login` sign-in doesn't work here: it
lasts at most a few hours, and the site runs all the time.

The key lives in one file on the server, `secrets\bedrock_api_key` next to
`docker-compose.yml`. Compose mounts it read-only into the container at
`/run/secrets/bedrock_api_key`, which `config.server.toml` names under
`[bedrock]`. It's never in git, the image, an environment variable,
`docker inspect`, or a command line.

If the file is missing, compose only warns and starts the site anyway.
Sign-in and old cases work, but every new case fails, and the app log
starts with "WARNING: every case will fail".

Console menus move, so treat the click paths as approximate.

### Make the key (you, in the AWS console)

1. Sign in through the portal as `smarkh`, account 548108386131,
   `AdministratorAccess`. Set the region to **US West (Oregon)**.
2. **Amazon Bedrock** → **API keys** → **Long-term API keys** →
   **Generate long-term API key**.
   - **Expiry: 90 days.** That covers a 60-day beta with room to spare.
     Write the expiry date in `plans/beta-plan.md`.
   - AWS makes an IAM user for the key (named like `BedrockAPIKey-…`) with
     the managed policy `AmazonBedrockLimitedAccess`.
3. **Copy the key now.** It's shown once. Don't paste it into chat, email
   or a file on the laptop: it goes straight to the server (next section).
4. **Tighten it** to the one model the site uses. IAM → **Users** → the
   new `BedrockAPIKey-…` user → **Permissions**:
   - Remove `AmazonBedrockLimitedAccess`.
   - **Add permissions → Create inline policy → JSON**, name it
     `battle-test-live-invoke`:
     ```json
     {
       "Version": "2012-10-17",
       "Statement": [
         {
           "Effect": "Allow",
           "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
           "Resource": "arn:aws:bedrock:us-west-2::foundation-model/openai.gpt-oss-120b-1:0"
         },
         {
           "Effect": "Allow",
           "Action": "bedrock:CallWithBearerToken",
           "Resource": "*"
         }
       ]
     }
     ```
     `CallWithBearerToken` is what lets an API key be used at all. A
     model added later (`config.server.toml`) needs its ARN added here, or
     its calls are refused with `AccessDeniedException`.

### Put it on the server (you, over SSH)

**(laptop)** `ssh -t smark@smark-iq` (`-t`, so the hidden prompt works).
Then **(server)**:
```powershell
cd C:\Users\smark\Documents\work\battle_test
New-Item -ItemType Directory -Force secrets | Out-Null
$k = Read-Host "Bedrock API key" -AsSecureString
[IO.File]::WriteAllText("$PWD\secrets\bedrock_api_key", [Net.NetworkCredential]::new("", $k).Password)
Remove-Variable k
(Get-Item secrets\bedrock_api_key).Length   # more than 0; never print the key itself
```
Then deploy as in "Update the code", or, if the code is already deployed,
`docker compose up -d --force-recreate battle-test` (between runs).

**Check:** `docker logs --tail 20 battle-test` has no "WARNING: every case
will fail". Then run one fictional sample case on the site (about $0.05)
and check its report shows `gpt-oss-120b` and a cost.

### Replace, revoke, expire

- **Before it expires,** make a new key as above, write it over the old
  file, and recreate the container between runs. Then delete the old
  key's IAM user. The app reads the key when it makes its first Bedrock
  call after starting, so it needs the restart.
- **If the key may have leaked:** Bedrock → API keys → revoke it, or
  delete the `BedrockAPIKey-…` user in IAM. Calls stop at once. Then make
  a new one.
- **When it expires or is revoked,** cases fail with "AWS didn't accept
  the credentials". Sign-in and old cases keep working.
- **The budget alerts** ($10 and $25) still apply. The beta should cost
  about $5 to $11 in all (`plans/beta-plan.md`).

## Cloudflare Access

Access puts a Cloudflare sign-in in front of the whole site, so only
approved emails reach even the sign-in page. **Required before any beta
tester gets a link.** It's set in the dashboard, with no code change and no
deploy. Free up to 50 users.

Testers then sign in twice: first Cloudflare (their email, then a 6-digit
code it emails them), then Battle Test (the username and password they
chose). `docs/beta.md` has an email that explains this to them.

### Set it up (you, in the Cloudflare dashboard)

1. **Zero Trust** (one.dash.cloudflare.com). The first time, it asks for a
   team name and a plan: choose **Free**. It may ask for a card even for
   the free plan.
2. **Settings → Authentication → Login methods:** check **One-time PIN**
   is there (it's on by default). Nothing else is needed.
3. **Access → Access groups → Add a group:** name `battle-beta`. Include →
   **Emails** → your own email. Testers' emails are added here later, one
   at a time.
4. **Access → Applications → Add an application → Self-hosted:**
   - Name `Battle Test`. Session duration **24 hours**.
   - Domain `battle.smarkiq.us`, path **empty** (the whole site, so
     `/admin` and `/setup/…` are covered too).
   - Policy: name `Beta testers and admin`, action **Allow**, include →
     **Access groups** → `battle-beta`.
   - Login method: One-time PIN. Turn on **instant auth** if offered, so
     testers go straight to the email box.
5. **A bypass for the health check,** so the outside uptime check (D5)
   still works. Add a second self-hosted application, `Battle Test
   health`, domain `battle.smarkiq.us`, path `healthz`, with one policy:
   action **Bypass**, include **Everyone**. `/healthz` shows only `ok`.

### Check it

- From a private window: `https://battle.smarkiq.us` shows Cloudflare's
  page. Your email gets a code, and then the Battle Test sign-in appears.
  Sign in, open a case, watch live progress on a running one, and download
  a Word file.
- An email not in `battle-beta` is refused (try a second address of
  yours).
- **(laptop)** `curl -s https://battle.smarkiq.us/healthz` still prints
  `ok`, and `curl -sI https://battle.smarkiq.us/login` now redirects to
  `cloudflareaccess.com`.
- A phone on mobile data (D3 in `plans/plan.md`).

### Add or remove a tester

Access → Access groups → `battle-beta` → edit the emails. Removing an email
stops new sign-ins at once. A session already open lasts until its 24
hours run out, so also `disable` their Battle Test account if it must stop
now.

## Evaluation and logs

**(server)**

| What | Command |
|---|---|
| Run the four-case evaluation (~8 min) | `docker compose run --rm -T battle-test python -m battle_test.evaluate --label server-14b` |
| Read an evaluation summary | `docker exec battle-test cat /data/cases/output/eval/<folder>/summary.md` |
| App log | `docker logs --tail 100 battle-test` |
| Tunnel log | `docker logs --tail 50 battle-test-cloudflared` |
| Law index details | `docker compose run --rm battle-test python -m battle_test.corpus info` |
| Is the model on the GPU? | `ollama ps` (expect 100% GPU during a run) |

### Long evaluations on Ollama Cloud models (set up 2026-10-31)

Run long evaluations here rather than on the laptop. The server has the
memory, and a run doesn't depend on the laptop staying awake. The server's
Ollama is signed in to ollama.com (device `smark_iq`), so `<name>:cloud`
models (e.g. `gpt-oss:120b-cloud`, `nemotron-3-super:cloud`) are passed on
to Ollama Cloud. They use the account's free monthly usage, and no card is
on file. Fictional evaluation cases only.

- The run uses the **built image**, so deploy first if the code changed
  (see "Update the code").
- It reads `config.server.toml` (semantic search on). `--model` and
  `--checker` choose the models.
- Cloud models barely touch the GPU, so a run can go alongside the live
  site. A run on the server's 14B competes with real cases, so do that
  when the site is quiet.

**(server)** Start it in the background, then follow it:
```powershell
docker compose run -d --no-deps --name battle-eval battle-test python -m battle_test.evaluate --model gpt-oss:120b-cloud --checker gpt-oss:120b-cloud --repeats 3 --label <label>
docker logs --tail 5 battle-eval
```
Add `--case NAME` (repeatable) for some cases only. When `docker ps -a`
shows `battle-eval` as Exited, remove it with `docker rm battle-eval`.

**Copy the results to the laptop, then delete them from the server.** They
sit in the case-data volume. Take `<folder>` from
`docker exec battle-test ls /data/cases/output/eval`.
```powershell
# (server)
docker cp battle-test:/data/cases/output/eval/<folder> $HOME\eval-<folder>
# (laptop)
scp -r smark@smark-iq:eval-<folder> output\eval\<folder>
# (server)
docker exec battle-test rm -rf /data/cases/output/eval/<folder>
Remove-Item -Recurse -Force $HOME\eval-<folder>
```

## Routine maintenance

- **After a reboot.** There's no auto-login. Sign in to Windows through
  Chrome Remote Desktop. Docker Desktop, the containers and Ollama then
  come back on their own.
- **Law updates:** see "Law updates" below.
- **Rebuild the law index after any change to how it's built** (such as
  `corpus.py` or `ak_headings.json`), with the same two commands. The app
  opens the index fresh for each case, so no restart is needed after it.
- **Backups.** Only accounts (`users.sqlite`) are backed up. Case files
  are deliberately not: they're privileged and expire after 90 days.
- **Retention** runs itself: an hourly purge of cases older than 90 days.

## Law updates

Open US Law publishes a new snapshot about every quarter, and only the
latest can be downloaded. A weekly check builds a new one beside the live
law. **Nothing changes for users until you approve it.**

**Every week (scheduled):** `corpus refresh` checks for a new snapshot.
- Nothing new: the activity log (Admin → Activity log) shows "law
  checked · Up to date".
- A new snapshot: it's downloaded and built in `/data/law/staging/`, with
  section vectors if semantic search is on (only new and changed sections
  are embedded). It's then checked against the live law, and the log shows
  "law update ready" with a summary and any warnings.
- A failure: "law update failed", with the error. The live law is
  untouched.
- No entry for over a week means the task didn't run. Usually the server
  rebooted and nobody has signed in, so Docker isn't running.

**To approve a new snapshot (server):**
```powershell
docker compose run --rm battle-test cat /data/law/staging/report.md
docker compose run --rm battle-test python -m battle_test.corpus promote
```
- Read the report first. It lists what changed per state, section counts
  that moved more than 5%, expected-authority lists that no longer
  validate, and a search check comparing the two laws on the evaluation
  cases.
- `promote` swaps the index and its vectors in together, in
  milliseconds, with no restart needed. The old law moves to
  `/data/law/archive/`, because it can't be downloaded again (about 1.4 GB
  each, so clear out old ones now and then).
- Afterwards set `corpus.snapshot` in `config.server.toml` and `config.toml`
  at the next deploy, so `corpus build` matches.
- If any expected-authority lists no longer validate, fix them in
  `examples/eval/`.

**To undo it:** `docker compose run --rm battle-test python -m battle_test.corpus rollback`
puts the previous law back (and archives the one it replaces).

**Set up the weekly task (once, server):** in an Administrator PowerShell:
```powershell
$action = New-ScheduledTaskAction -Execute "docker" -WorkingDirectory "C:\Users\smark\Documents\work\battle_test" `
  -Argument "compose run --rm -T battle-test python -m battle_test.corpus refresh"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am
Register-ScheduledTask -TaskName "battle_test law refresh" -Action $action -Trigger $trigger `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable)
```
`-StartWhenAvailable` runs a missed check once the server is back. It runs
at night so it doesn't compete with Open WebUI for the GPU. To run it now:
`Start-ScheduledTask -TaskName "battle_test law refresh"`.

## If the site can't be reached

Work through these in order:

1. **Check your own internet connection.** It was the cause once.
2. **Clear stale DNS (laptop):** run `ipconfig /flushdns`, and in Chrome
   open `chrome://net-internals/#dns` and clear the host cache. If `curl.exe
   -4 https://battle.smarkiq.us/healthz` returns `{"status":"ok"}`, the
   site is up and the problem is local.
3. **Check the server:** `docker ps` should show `battle-test` (healthy)
   and `battle-test-cloudflared`. The tunnel log should show "Registered
   tunnel connection".
4. **If the server doesn't answer over SSH,** it may have rebooted. Sign
   in through Chrome Remote Desktop.
   - Windows Update restarts it on its own, often around midnight. Until
     someone signs in, Docker Desktop doesn't start and Tailscale stays
     at "starting", so both sites show Cloudflare error 530 and SSH to
     `smark-iq` times out.
   - From home, SSH still works on the local network, which is enough to
     confirm it:
     `ssh -o HostKeyAlias=smark-iq smark@10.0.0.219` (the router assigns
     the address, so it may change). Event ID 1074 in the System log
     says who restarted it and why.

| Symptom | Likely cause |
|---|---|
| `battle-test-cloudflared` keeps restarting, "Provided Tunnel token is not valid" | `.env` is missing or holds more than the bare token |
| A case fails with "Could not reach Ollama" | Ollama isn't running on the server |
| Cases are slow, and Open WebUI chats are too | The two are sharing the GPU. It clears 30 seconds after a run. |
| Cloudflare error 530 or 1033 | The connector isn't running or isn't registered |
| Every case fails with "can't read the API key file", or the app log has "WARNING: every case will fail" | `secrets\bedrock_api_key` is missing or empty on the server ("Bedrock API key") |
| Cases fail with "AWS didn't accept the credentials" | The Bedrock API key expired or was revoked. Make a new one. |
| Cases fail with `AccessDeniedException` | The key's inline policy doesn't name the model in `config.server.toml` |
| A tester sees Cloudflare's "That account does not have access" | Their email isn't in the `battle-beta` Access group |

## What's not done yet

- **Cloudflare Access**, required before beta testers. The steps are
  above ("Cloudflare Access"). Not set up as of 2026-11-20.
- Plain `http://` requests aren't redirected to HTTPS. Sign-in can't work
  over HTTP, so nothing sensitive is exposed.
- A check of how sharing the GPU with Open WebUI behaves under load.

`plans/server-migration-plan.md` has the full history, including what
went wrong during setup.
