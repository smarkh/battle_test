# Running battle_test on the server

The deployed copy runs in Docker on the smark_iq home server and is public
at **https://battle.smarkiq.us**.

> **Fictional cases and test accounts only.** Cloudflare Access must be
> added in front of the site before any real user or real case.

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

- Ollama runs natively on the server (not in Docker) and serves
  `qwen2.5:14b` on the GPU. A case takes about 90 seconds.
- The two projects share only the GPU.
- Data is in two Docker volumes: `battle_test_battle-law` (the law index)
  and `battle_test_battle-cases` (accounts and case files).
- The server uses `config.server.toml`, which is built into the image.
- The tunnel token is in `.env` in the battle_test folder on the server.
  It's gitignored and exists nowhere else.

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
4. **(server)** Build, then check the image is a few minutes old:
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
session does not exist"): the build needs to download something, and
Docker downloads don't work over SSH on this server. That happens when
`requirements.txt` or the base image changes. Use either of these:

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

| To… | Replace `add NAME --plan solo` with |
|---|---|
| Issue a new setup link (forgotten password, expired link) | `invite NAME` |
| Change the plan | `plan NAME PLAN` |
| Put the account in a firm, or take it out | `firm NAME FIRM`, or `firm NAME` |
| Set the last paid day, or remove it | `paid NAME 2026-11-30`, or `paid NAME` |
| Block or restore sign-in | `disable NAME`, `enable NAME` |
| List accounts | `list` |
| Report cases per account | `usage` or `usage --month 2026-10` |
| Set a password by hand (needs `ssh -t`) | `passwd NAME` |
| Create an admin (needs `ssh -t`) | `add NAME --admin --password --plan unlimited` |

Most of this can also be done by an admin in the browser at `/admin`.
Creating or removing admins is command-line only.

## Evaluation and logs

**(server)**

| What | Command |
|---|---|
| Run the three-case evaluation (~6 min) | `docker compose run --rm -T battle-test python -m battle_test.evaluate --label server-14b` |
| Read an evaluation summary | `docker exec battle-test cat /data/cases/output/eval/<folder>/summary.md` |
| App log | `docker logs --tail 100 battle-test` |
| Tunnel log | `docker logs --tail 50 battle-test-cloudflared` |
| Law index details | `docker compose run --rm battle-test python -m battle_test.corpus info` |
| Is the model on the GPU? | `ollama ps` (expect 100% GPU during a run) |

## Routine maintenance

- **After a reboot.** There's no auto-login. Sign in to Windows through
  Chrome Remote Desktop. Docker Desktop, the containers and Ollama then
  come back on their own.
- **Quarterly law refresh.** Change `corpus.snapshot` in
  `config.server.toml` (and `config.toml`), deploy the update, then:
  ```powershell
  docker compose run --rm battle-test python -m battle_test.corpus build
  docker compose run --rm -T battle-test python -m battle_test.evaluate --validate
  ```
- **Backups.** Only accounts (`users.sqlite`) are backed up. Case files
  are deliberately not: they're privileged and expire after 90 days.
- **Retention** runs itself: an hourly purge of cases older than 90 days.

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

| Symptom | Likely cause |
|---|---|
| `battle-test-cloudflared` keeps restarting, "Provided Tunnel token is not valid" | `.env` is missing or holds more than the bare token |
| A case fails with "Could not reach Ollama" | Ollama isn't running on the server |
| Cases are slow, and Open WebUI chats are too | The two are sharing the GPU. It clears 30 seconds after a run. |
| Cloudflare error 530 or 1033 | The connector isn't running or isn't registered |

## What's not done yet

- **Cloudflare Access**, required before real users. It's a Cloudflare
  dashboard setting and needs no code change.
- Plain `http://` requests aren't redirected to HTTPS. Sign-in can't work
  over HTTP, so nothing sensitive is exposed.
- A check of how sharing the GPU with Open WebUI behaves under load.

`plans/server-migration-plan.md` has the full history, including what
went wrong during setup.
