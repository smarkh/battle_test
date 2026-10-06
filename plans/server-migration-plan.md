# Plan — Moving battle_test to the smark_iq Server (Public Access)

This plan covers build step 5, part 4 of `plans/plan.md`: move battle_test
from the dev laptop to the smark_iq server, and make the web UI reachable
from anywhere at a public URL.

**Status (2026-10-03):**
- Phases 0–4 are done: **`https://battle.smarkiq.us` is live.**
- Phase 5's anonymous checks passed. The signed-in checks (cookie flags,
  live progress through Cloudflare, a phone on mobile data) are still for
  the user to run.
- Before real users: Cloudflare Access. Real users are a long way off,
  and models may be on Bedrock by then.

**Revised 2026-09-28: battle_test runs fully independently of smark_iq.**
It has its own Docker network and its **own Cloudflare Tunnel** (its own
`cloudflared` connector), not smark_iq's Caddy and tunnel. The earlier
design, which joined smark_iq's network and added a Caddy site block, was
dropped before it was deployed, and the Caddyfile edit was discarded
uncommitted. The only thing the two share is the server's GPU, through the
native Ollama (see Decision 9).

## Goal

A signed-in user can open `https://battle.smarkiq.us` from any browser,
start a case, watch it run, and read the results. Everything runs on the
smark_iq server:
- models on its GPU
- law index and case files on its disk
- the public path through battle_test's own Cloudflare Tunnel, on the
  `smarkiq.us` domain but separate from smark_iq's tunnel

The laptop stays the development machine.

## What's already in place on the server (from smark_iq)

| Piece | State |
|---|---|
| Hardware | Windows 11 Home; RTX 5070 Ti (16 GB VRAM); 24 GB RAM; 1 TB NVMe |
| Ollama | Native install (not a container), GPU verified. `qwen2.5:14b` runs at ~80 tok/s, 100% GPU. Containers reach it at `http://host.docker.internal:11434`. |
| Docker Desktop | Runs smark_iq's compose stack: `open-webui`, `caddy`, `cloudflared` |
| Public path | `smarkiq.us` on Cloudflare. A Cloudflare Tunnel (`cloudflared` container, outbound-only, no router ports) sends `llm.smarkiq.us` to `http://caddy:80`, and Caddy proxies to Open WebUI and adds security headers. |
| Admin access | SSH over Tailscale, key-only (files, docker, Claude Code). Chrome Remote Desktop for signing in to Windows after a reboot. |
| Hardening standard | smark_iq's `guardrails.md`: named volumes (no host bind mounts), no Docker socket, `cap_drop: ALL`, `no-new-privileges`, memory/CPU limits |
| Reboots | No auto-login. After a reboot someone signs in via Chrome Remote Desktop, then Docker and Ollama come back on their own. |

battle_test reuses the hardware, Ollama, Docker Desktop, the domain, admin
access and the hardening standard. It does **not** use smark_iq's
containers, network, Caddy or tunnel.

## Target architecture

```
browser ──https──▶ Cloudflare edge ──battle_test's tunnel──▶ battle-test-cloudflared ──▶ battle-test:8000
                    (TLS, Cloudflare Access                  (battle_test's own network, battle_test_default)
                     before real users)                                                      │
                                                                                             ▼
smark_iq, unchanged:  llm.smarkiq.us ─▶ its own tunnel ─▶ cloudflared ─▶ caddy ─▶ open-webui │
                                                                                             ▼
                                                                 Ollama on the host (one GPU, shared)
```

- **Two containers** in battle_test's own compose project and network
  (`battle_test_default`): the app, `battle-test`, and its tunnel
  connector, `battle-test-cloudflared`. The connector makes an outbound
  connection to Cloudflare, so no router ports are opened, and it forwards
  `battle.smarkiq.us` to `http://battle-test:8000`. There's no reverse
  proxy: the app sets its own security headers, including HSTS in deployed
  mode.
- **Nothing shared with smark_iq's stack.** Either project can be stopped,
  updated, rebuilt or removed without affecting the other. See the
  2026-09-28 incident below for why that matters.
- **No published host port** in normal operation. For private testing,
  the `docker-compose.local-test.yml` override publishes `127.0.0.1:8000`,
  reached over an SSH tunnel.
- **Two named volumes**, following the guardrails rule of no host bind
  mounts:
  - `battle-law`: the law index (`law.sqlite`, ~1.3 GB, plus the
    downloaded Parquet files).
  - `battle-cases`: accounts (`users.sqlite`) and case files
    (`cases.sqlite` plus one folder per case).

## Decisions

Decided 2026-09-25: 1 (`battle.smarkiq.us`), 4 (app login only for now,
with Access **required before real users**), 5 (`qwen2.5:14b`) and 8
(accounts only). The rest are as recommended.

1. **Hostname: decided, `battle.smarkiq.us`.** It's a subdomain of the
   domain smark_iq already uses, so there's no new domain and no DNS
   migration.
2. **Run as a container or natively on Windows?** **Container.** It
   matches smark_iq, and the guardrails hardening applies. It also keeps
   privileged case files inside a volume rather than loose on the Windows
   filesystem, and the image is reproducible from the repo. Ollama stays
   native, as smark_iq already decided (Docker Desktop's GPU handling was
   unreliable).
3. **Separate compose project, or add to smark_iq's?** **Separate and
   fully independent** (revised 2026-09-28): its own network and its own
   Cloudflare Tunnel. It originally joined smark_iq's network behind
   smark_iq's Caddy, but that coupling was dropped before going public.
4. **Second sign-in layer: decided, the app's own login only for now.**
   ⚠ **Cloudflare Access must be added before any real users test this,**
   i.e. before anyone enters a real case. Until then, only the admin and
   test accounts with fictional cases may use the public site. Why it
   matters:
   - The cases are privileged legal material.
   - Access adds an email one-time-code check at Cloudflare's edge before a
     request ever reaches the server, which protects against a bug in the
     app's own login.
   - It's free for up to 50 users, and needs no code change (it's a
     Cloudflare dashboard setting, see Phase 4 step 3).
   - The app's accounts stay as they are: Access decides who reaches the
     site, and the app's accounts decide whose cases you see.
5. **Model: decided, `qwen2.5:14b` first,** since it's
   already pulled and fits fully on the GPU with the 12k context (~9 GB of
   weights plus ~2.4 GB of context cache). That's step 1 of the plan's
   "Model size estimate" path. Then try Qwen3 30B-A3B and Mistral Small 24B
   using the evaluation. It's one line in the server config.
6. **Where the law index comes from.** **Build it on the server** with
   `python -m battle_test.corpus build` inside the container (a ~176 MB
   download, about a minute). Copying the laptop's 1.3 GB file would work
   too, but rebuilding is simpler, SHA256-checked, and is the same routine
   used for the quarterly refresh.
7. **How code gets to the server.** **git**, as smark_iq's remote-access
   plan already says. Clone the private GitHub repo on the server using a
   **read-only deploy key** (not a personal token), then `git pull` and
   `docker compose up -d --build` for each update.
8. **Backups: decided, accounts only.**
   - Back up `users.sqlite` (accounts) on a schedule, e.g. a weekly copy.
   - Don't back up case files. They're privileged, they expire after 90
     days anyway, and a backup would outlive the retention promise.
     Revisit if the lawyer's record-keeping advice says otherwise.
9. **GPU sharing with Open WebUI: decided 2026-09-28, "option 1" for
   now.** One 16 GB card can't hold battle_test's 14B (11 GB with its
   context) and a typical Open WebUI chat model at once, so they share it:
   - battle_test sets Ollama's `keep_alive` to **30 s**
     (`config.server.toml`). The model stays loaded through a run, whose
     calls come back to back, and is unloaded 30 s after the last call, so
     the card is free for Open WebUI soon after.
   - Chats may slow down only while a battle_test run is going (~90 s per
     case on 14B).
   - **Later: hosted models via Bedrock** (see `plans/plan.md`), which
     would take battle_test off the GPU entirely. The other options were
     a second GPU (the 750 W PSU and the case may not allow it) or a
     separate machine.

## Privacy note to resolve with the lawyer

The Cloudflare Tunnel **ends TLS at Cloudflare's edge**. Case text travels
encrypted from browser to Cloudflare and encrypted again from Cloudflare to
the server, but Cloudflare's systems handle it in plain text in between.
That's how every Cloudflare-proxied site works, and it's the same for
`llm.smarkiq.us`. It's much less exposure than sending case text to a hosted
model (Cloudflare passes it through; it doesn't store or process it), but
for privileged material the lawyer should know about it.

This sits alongside the Bedrock question in `plans/plan.md`. The
alternative with no third party in the path is Tailscale-only access (no
public URL), which is what smark_iq moved away from.

## Code changes needed first (on the laptop, before touching the server)

The app currently refuses to run anywhere but `127.0.0.1`, which was right
for a login-less preview. These changes make it deployable without
weakening that default:

1. **`Dockerfile` + `.dockerignore`.**
   - Base image: `python:3.12-slim`, installing `requirements.txt`
     (including `pyarrow`, so the corpus build can run in the container).
   - Runs as a **non-root user**. We control this image, unlike Open
     WebUI's, so the guardrails' open "run as non-root" item can be met
     here.
   - The build context excludes `data/`, `cases/`, `output/`, `.venv/` and
     `.git/`, so no case data or law index is ever baked into an image.
2. **`docker-compose.yml`:**
   - one `battle-test` service with the guardrails baseline: `cap_drop:
     ALL`, `no-new-privileges`, a read-only root filesystem plus `tmpfs` for
     `/tmp`, and memory/CPU limits (e.g. 3 GB / 4 CPUs)
   - the two named volumes
   - `extra_hosts: host.docker.internal:host-gateway`, as Open WebUI has
   - smark_iq's network, declared external
   - `restart: unless-stopped`
   - a healthcheck on a new `/healthz`
3. **A server config file, `config.server.toml`:**

   | Setting | Value |
   |---|---|
   | Ollama URL | `http://host.docker.internal:11434` |
   | Models | 14B |
   | Law index / case data | `/data/law`, `/data/cases` (the volumes) |
   | `web.host` | `0.0.0.0` |
   | `secure_cookies` | `true` |
   | New `behind_proxy` | `true` |

   The app picks the file up from a `BATTLE_TEST_CONFIG` environment
   variable, so the laptop keeps `config.toml` unchanged.
4. **Replace the localhost-only guard with an explicit deployed mode.**
   Serving on a non-local address is allowed **only** when `behind_proxy =
   true` **and** `secure_cookies = true`. Otherwise the server refuses to
   start, so a laptop config can't accidentally go public. When
   `behind_proxy` is on, uvicorn trusts `X-Forwarded-*` headers from the
   Docker network only.
5. **Keep live progress alive through Cloudflare.**
   - Cloudflare drops connections that go ~100 seconds with no data. A run
     can go longer than that between events, e.g. while the model thinks
     during research or selection. The progress stream needs a heartbeat
     comment every ~15 seconds.
   - (There's no Caddy since the 2026-09-28 revision: `cloudflared` goes
     straight to the app. Check in Phase 5 that the stream isn't buffered.)
6. **`/healthz`:** returns 200 without signing in and reveals nothing. It's
   for the compose healthcheck, which `cloudflared` waits on before
   starting.
7. **Tests** for: the deployed-mode guard (refuses a public host without
   secure cookies and proxy mode), the heartbeat, `/healthz`, and the
   `BATTLE_TEST_CONFIG` override.

Everything above can be built and tested on the laptop. Docker Desktop is
needed there only to try the image before the server.

## Local development stays the same

The laptop stays the place where changes are made and tested. None of the
changes above take that away:
- **Separate configs.** The laptop keeps `config.toml` (local Ollama, 7B,
  `data/` and `cases/web/`, `127.0.0.1`, non-secure cookies). The server
  uses `config.server.toml`, selected by `BATTLE_TEST_CONFIG`. With the
  variable unset, everything behaves exactly as today.
- **Same commands.** `python -m battle_test`, `battle_test.evaluate`,
  `battle_test.corpus`, `battle_test.web` (including `--demo-model`) and
  `unittest` all keep working from `.venv`.
- **Localhost-only stays the default.** A non-local host is only allowed
  when both `behind_proxy` and `secure_cookies` are true, and the laptop
  config sets neither.
- **Docker is optional locally.** It's only needed to try the container
  image before deploying (Phase 1).
- **Workflow:** change and test on the laptop, commit and push, then `git
  pull` and rebuild on the server. Only the config differs between the two.

## Step-by-step plan

### Phase 0 — Decisions — DONE (2026-09-25)
Hostname, sign-in layer, model and backups are decided (see Decisions).
Still to do: tell the lawyer about the Cloudflare TLS point.

### Phase 1 — Code changes on the laptop — DONE (2026-09-25)
Make the changes in "Code changes needed first", get the tests passing, and
build the image locally. Run it with the demo model and a local config to
check that the container starts, `/healthz` answers, and the non-root user
can write to its volumes. Commit and push.

**Done:**
- **New files:**
  - `Dockerfile`: `python:3.12-slim`, non-root user `battle` (uid 10001),
    `BATTLE_TEST_CONFIG=/app/config.server.toml`.
  - `.dockerignore`: keeps `data/`, `cases/`, `output/`, `.venv/`, `.git/`
    and `.env` out of the image.
  - `docker-compose.yml`: the `battle-test` service, the `battle-law` and
    `battle-cases` volumes, and smark_iq's network as external. The
    network name defaults to `smarkiq_default` (confirmed on the server),
    and can be overridden with `SMARK_IQ_NETWORK`.
  - `config.server.toml`: host Ollama, `qwen2.5:14b`, `/data/...` volume
    paths, `0.0.0.0` with `secure_cookies` and `behind_proxy` on.
- **The container's hardening:** `read_only` root filesystem plus `tmpfs
  /tmp`, `cap_drop: ALL`, `no-new-privileges`, 3 GB / 4 CPUs,
  `restart: unless-stopped`, and a healthcheck. No host port is published.
- **Code:**
  - `BATTLE_TEST_CONFIG` selects the config file.
  - The new `[web] behind_proxy` setting: a non-local host is refused
    unless it and `secure_cookies` are both true.
  - uvicorn trusts proxy headers only in that mode.
  - `/healthz`.
  - A progress-stream heartbeat every 15 s.
  - A case deleted while its progress page is open now ends the stream
    cleanly (a latent bug found while refactoring).
  - Paths starting with `/` count as absolute on Windows too.
  - `corpus info` with no index prints a clean error.
- **Verified:**
  - 123 tests pass, including 16 new deployment tests: the serving rule,
    the server config, the config override, the Docker context and compose
    hardening, `/healthz`, and the heartbeat.
  - The image built on the laptop (420 MB).
  - Run with the compose hardening flags and the demo model, it:
    - answered `/healthz` with 200, and redirected signed-out requests to
      `/login`
    - ran as `uid=10001(battle)`, with a read-only root filesystem and a
      writable cases volume
    - created `cases.sqlite` and `users.sqlite` on first start
    - ran `users list` and `corpus info` inside the container
  - The laptop config still refuses `0.0.0.0` without `behind_proxy` and
    `secure_cookies`.
- **Tip for Git Bash on Windows:** set `MSYS_NO_PATHCONV=1` when passing
  container paths like `/data/cases` to `docker`, or Git Bash rewrites them
  into Windows paths.
- **Not tested on the laptop:** the compose file as a whole. It needs
  smark_iq's network, which only exists on the server (Phase 2).

### Phase 2 — Prepare the server (over SSH)
1. Confirm the smark_iq stack is healthy (`docker compose ps` in smark_iq),
   and note its network name (`docker network ls`). **Done 2026-09-28:**
   - SSH from the laptop works once its key is loaded into Windows'
     `ssh-agent`. The server's shell is Windows PowerShell 5.1 (no `&&`).
   - The stack is healthy: the compose project is `smarkiq` at
     `C:\Users\smark\Documents\work\smarkiq`, and all three containers
     have been up 4 days.
   - The network is **`smarkiq_default`**, not `smark_iq_default`.
     `docker-compose.yml`'s default is corrected to match.
   - `qwen2.5:14b` is already pulled, and there's 752 GB free.
2. Add a read-only deploy key for the GitHub repo, then clone battle_test
   next to smark_iq. **Done 2026-09-28:**
   - The key `~/.ssh/battle_test_deploy` (no passphrase, server only) was
     created on the server, and added to GitHub as a read-only deploy key.
   - The server's SSH config has a `Host github-battle-test` entry using
     only that key.
   - GitHub's host key in `known_hosts` was checked against the
     fingerprint GitHub's API publishes
     (`SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU`). `ssh-keyscan`
     hangs on Windows, so the API was used instead.
   - Cloned to `C:\Users\smark\Documents\work\battle_test` from
     `git@github-battle-test:smarkh/battle_test.git`.
3. `ollama pull qwen2.5:14b` if it's not there. Check `ollama ps` shows
   `100% GPU` during a test prompt. (smark_iq hit a silent CPU fallback
   once; the fix is in its hardware-migration plan.) **Done:** it was
   already pulled, and runs **100% GPU** at 83 tok/s, using 11 GB with the
   12k context.
4. Build the image, and create the volumes with `docker compose up
   --no-start`. **Done, from Chrome Remote Desktop,** because it can't be
   done over SSH (see below):
   - Image `battle-test` is 420 MB.
   - The container is on `smarkiq_default`, running as `battle`, with a
     read-only root filesystem, `cap_drop ALL`, `no-new-privileges`, a
     3 GB limit, no published ports, and `restart: unless-stopped`.
   - Volumes: `battle_test_battle-law` and `battle_test_battle-cases`.
5. **Build the law index** into `battle-law`: `docker compose run --rm
   battle-test python -m battle_test.corpus build`. Check it with `...
   corpus info`, which should show snapshot v2026.08 and 369,896 sections.
   **Done over SSH, in 36 s:** 369,896 sections, snapshot v2026.08
   (2026-08-14). `evaluate --validate` passes for all three cases.
6. **Create the first admin account** (interactive, so run it over SSH with
   a TTY): `docker compose run --rm battle-test python -m
   battle_test.web.users add NAME --admin`. **Done, with one lesson:**
   - The account was first created from Chrome Remote Desktop, and the
     password then didn't work from the laptop's browser. Remote Desktop
     can translate some keys differently when the two machines' keyboard
     settings differ. Both password prompts went through the same
     translation, so they matched each other but not what the laptop
     sends.
   - **Set passwords over SSH from the machine whose keyboard will be used
     to sign in** (`ssh -t smark@smark-iq`, then the `users` command), or
     at the server itself. Never through Remote Desktop.
   - Reset over SSH with `users passwd`, and confirmed with a one-line
     `AuthStore.authenticate` check in the container before trying the
     browser.
   - Heads-up: the account is currently named `yourname`, because the
     example command's placeholder was used. Add a real-username admin and
     `disable yourname` when convenient.

**Found in Phase 2: Docker image downloads don't work over SSH on this
server.**
- Any pull or build that fetches an image fails with `error getting
  credentials … A specified logon session does not exist`. That happens
  even for public images, and even with the client's credential helper
  removed.
- Docker Desktop handles registry access through the signed-in desktop
  session's Windows credential store, which an SSH (network) logon can't
  use.
- Everything that uses images already on the server works fine over SSH:
  `docker compose run`, `up`, `ps`, `logs`, and so on.
- So for updates: `git pull` works over SSH, but **`docker compose build`
  must be run from Chrome Remote Desktop**. The alternatives, if updates
  become frequent: build on the laptop and `docker save` / `scp` /
  `docker load`, or trigger the build as a scheduled task that runs inside
  the desktop session.
- **Revised 2026-10-04: a rebuild after a code change worked over SSH.**
  `docker compose build` over SSH produced a good image for the accounts
  and admin update, which changed only the app's code and config.
  - **Likely reason (not confirmed):** the base image and the installed
    packages were already on the server from the first build, so nothing
    had to be downloaded, and the download is the part SSH can't do.
  - **So:** try the build over SSH first. Expect it to fail, with the
    credentials error above, when it needs a download: a new base image
    version, or (probably) a changed `requirements.txt`. Then use Remote
    Desktop or the laptop build.
  - Check the result before restarting: `docker images battle-test` should
    show an image a few minutes old.
- **Revised again 2026-10-05: a code-only rebuild failed over SSH.**
  `docker compose build` for the case-list update (commit `8d23708`,
  code and templates only) stopped at "load metadata for
  docker.io/library/python:3.12-slim" with the credentials error.
  - The build asks the registry about the base image even when nothing
    needs downloading, and that lookup is what SSH can't do. Why it got
    through on 2026-10-04 is unknown.
  - **So: don't count on an SSH build.** Build on the laptop and copy
    the image (`docker save` → `scp` → `docker load`), which worked
    again this time: 105 MB, and the same image ID on both machines.
  - Untried: Docker's older builder (`DOCKER_BUILDKIT=0`), which uses
    the local base image without asking the registry.
- Other Windows-over-SSH notes:
  - The server's shell is Windows PowerShell 5.1, so there's no `&&`.
  - Send scripts with `powershell -EncodedCommand` so quoting survives.
  - Force TLS 1.2 for `Invoke-RestMethod`.
  - Filter out the `#< CLIXML` progress noise.

### Phase 3 — Run privately and test on the server
1. Start the container with a temporary `127.0.0.1:8000` port published.
   Reach it from the laptop through an SSH tunnel over Tailscale (`ssh -L
   8000:127.0.0.1:8000 server`). It still isn't on the internet or the LAN.
   **Done 2026-09-28.**
   - The port was added with an untracked override file,
     `docker-compose.local-test.yml` (`docker compose -f docker-compose.yml
     -f docker-compose.local-test.yml up -d`).
   - Container `healthy`, port `127.0.0.1:8000` only, `/healthz` ok, and
     signed-out requests redirect to `/login`.
   - The tunnel is `ssh -N -L 8000:127.0.0.1:8000 smark@smark-iq`, then
     browse to `http://localhost:8000`. Chrome accepts the Secure cookie
     on `localhost` over plain HTTP.
2. Test with the real 14B model: sign in, then run the Utah sample case
   end to end. Check that live progress streams, results render, citations
   link, the download works, and deletion works. **Done 2026-09-28. All
   worked:**
   - **Sign-in:** needed the password reset over SSH (Phase 2 step 6).
   - **Speed:** the Utah case ran in **90 s** end to end (8 model calls,
     2 rounds), against ~13–16 min on the laptop. The documents were
     complete, at 600–800 words each, with signature blocks.
   - **Live progress:** stages ticked off, and the page switched to the
     results on its own.
   - **Results:** 10 ✅ citations linking to official sources, 0 ❌, 0
     placeholders, and the auto-delete date shown (2026-12-27).
   - **Download:** `result.md` confirmed on the server. Reading it in the
     browser was blocked by the automation tool, but the route is covered
     by the tests.
   - **Delete:** the confirmation page, then "Case deleted.", and the
     folder and database row were both gone.
   - **Resources during the run:**
     - GPU 95% busy, 11.8 of 16 GB, model 100% GPU.
     - Container 1.0 of 3 GB.
     - ⚠ Server RAM only 2.7 GB free of 23.4 GB, down from 5.1 GB idle.
       See Risks.
   - **Quality on 14B:** still noisy selection. It cited Utah Code
     § 78A-5-102 (expected), § 13-11-19 (consumer sales, plausible),
     § 63G-7-403 (Governmental Immunity Act, off-topic) and § 70A-2-701
     (UCC sales, off-topic). The authorities quoted to it included federal
     consumer-finance and trust-law sections unrelated to the case. The
     evaluation (step 3) measures this.
3. Run the evaluation on the server (`python -m battle_test.evaluate
   --label server-14b`) for the first real comparison against the laptop's
   7B. This is also the start of 3b's "re-evaluate on 14B". **Done
   2026-09-28:**
   - The three cases took ~6 min in total (2 min each), against ~40 min on
     the laptop.
   - Run with `docker compose run --rm -T battle-test python -m
     battle_test.evaluate --label server-14b`, over SSH.
   - Results are in the cases volume at
     `/data/cases/output/eval/20260928-201608-server-14b/`.
   - Core cited: 4/16 (laptop 7B 6/16), off-topic: 3 (7B 2). There was no
     measurable gain in legal choice from the bigger model. See
     `plans/plan.md` 3b.
4. Check GPU sharing with Open WebUI: start a battle_test run while chatting
   in Open WebUI. Expect slower responses while models swap in and out of
   VRAM. Decide whether that's acceptable, or whether to set Ollama's
   `OLLAMA_MAX_LOADED_MODELS` / keep-alive differently (see Risks).
   **Skipped for now (2026-09-28).** Do it before real users. The low free
   RAM seen during the Utah run (2.7 GB of 23.4 GB) makes it worth
   checking.
5. Remove the temporary port. **Done 2026-09-28:**
   - The container was restarted with only `docker-compose.yml`. It's
     healthy, with no published ports, and `127.0.0.1:8000` on the server
     refuses connections.
   - The override is kept as `docker-compose.local-test.yml.disabled`
     (gitignored) for future private tests. Rename it back and use `-f
     docker-compose.yml -f docker-compose.local-test.yml up -d`.

**Phase 3 is done, except the GPU-sharing check.** The app is running on
the server, unreachable except from inside Docker's network, waiting for
Phase 4.

### Incident, 2026-09-28: smark_iq's containers disappeared

Found at the start of Phase 4.
- **What happened:** `open-webui`, `caddy` and `cloudflared` no longer
  existed (not just stopped), and `llm.smarkiq.us` returned Cloudflare 530.
- **What was left:** the `open-webui` data volume, all the images, and the
  `smarkiq_default` network. The server hadn't rebooted since 2026-09-23.
- **Timing:** they were present at this morning's Phase 2 survey (up 4
  days), and already gone at the resource snapshots around 19:45. Docker's
  event history (in memory, and filled by health-check events) was too
  short to show the cause.
- **Cause: unknown.** The pattern (containers gone, network kept because
  `battle-test` was attached to it) fits `docker compose down` in the
  smark_iq folder, or removing the containers in Docker Desktop. None of
  battle_test's commands target smark_iq: they're a different compose
  project, run in a different folder, with no `--remove-orphans`.
- **Fix:** `docker compose up -d` in smark_iq's folder, over SSH. That
  used the existing images and `.env`, so no downloads were needed.
  `open-webui` was healthy and `llm.smarkiq.us` back to 200 within a
  minute, with the data volume reattached intact.
- **Lessons:**
  - **To stop either stack, use `docker compose stop` or `restart`, never
    `down`,** unless removing it on purpose.
  - **battle_test depends on smark_iq for public access, not the other way
    round.** smark_iq runs fine without battle_test. battle_test keeps
    running without smark_iq but isn't reachable, and can't start if the
    shared network is gone.
  - **Possible improvement:** a shared network owned by neither project
    (`docker network create edge`, joined as external by both compose
    files), so neither stack's up or down affects the other's networking.
    Not done yet. It needs a small change in smark_iq's compose file too.
  - Before Phase 4, and after any server work, check both stacks with
    `docker ps` and that `llm.smarkiq.us` answers.

### Phase 4 — Go public
Revised 2026-09-28: battle_test uses its own tunnel, and smark_iq isn't
touched.

**Done 2026-09-28: `https://battle.smarkiq.us` is live.** Everything
below the log is in place: the code, battle_test's own tunnel and route,
the token in the server's `.env`, the rebuilt image, and both services
running on `battle_test_default`. What happened along the way:
- **Token and image on the laptop by mistake:** the `.env` and the image
  build were first done on the laptop, whose folder names match the
  server's.
- **`scp` overwrote smark_iq's `.env`:** copying the laptop `.env` to the
  server first landed on **smark_iq's** `.env`, replacing its
  `CLOUDFLARE_TUNNEL_TOKEN` and `WEBUI_SECRET_KEY`. The user restored
  them, and they were confirmed identical to the running containers'
  values by comparing hashes. smark_iq was never restarted in between, so
  it didn't go down. **Lesson:** use a plain home-relative `scp`
  destination, and check which project's file you're writing.
- **Image copied instead of rebuilt:** the fresh laptop image was moved
  to the server with `docker save` → `scp` → `docker load`, which
  downloads nothing, so it works over SSH. It was 97 MB compressed, and
  took 7 s to copy. It had the same image ID on both machines. **This is
  a working alternative to building in Remote Desktop.**
- **Invalid token:** `battle-test-cloudflared` first crash-looped with
  "Provided Tunnel token is not valid". The whole install command
  (`cloudflared.exe service install <token>`) had been pasted into
  `.env`. The prefix was stripped in place without the token being shown,
  and the connector then registered 4 connections (slc01, den03).
- **Tunnel ID:** the tunnel in use is **`04dcccfb-1dc1-461c-be03-a54748c20c86`**.
  The token refresh left a new tunnel, not the earlier `a7df488c-…` one.
  If that old tunnel still exists with no replicas, it can be deleted.
- **Route and DNS:** the tunnel showed "Healthy" but `Routes: 0`, and
  `battle.smarkiq.us` had no DNS record. Adding a **Published
  application** route (`battle.smarkiq.us` → `http://battle-test:8000`)
  created the DNS record.

Original steps, for reference:
1. **Code (done on the laptop, 2026-09-28):**
   - `docker-compose.yml` now has battle_test's own `cloudflared` service
     (`battle-test-cloudflared`), hardened like the app: read-only,
     `cap_drop ALL`, `no-new-privileges`, 256 MB.
   - Both services use battle_test's own default network. The external
     smark_iq network is gone.
   - The tunnel token comes from `BATTLE_TUNNEL_TOKEN` in `.env`
     (gitignored; see `.env.example`).
   - The app sends HSTS itself when `behind_proxy` is on.
   - `keep_alive = "30s"` is set in `config.server.toml` (Decision 9).
2. **You, in the Cloudflare Zero Trust dashboard:**
   1. **Create a new tunnel** (Networks → Tunnels → Create a tunnel →
      Cloudflared), named e.g. `battle-test`. It's separate from
      smark_iq's.
   2. **Copy its token** (the long string in the "install connector"
      command), without pasting it anywhere else.
   3. **Add its public hostname:** `battle.smarkiq.us`, service type
      `HTTP`, URL `battle-test:8000`.
3. **You, on the server:** create
   `C:\Users\smark\Documents\work\battle_test\.env` containing
   `BATTLE_TUNNEL_TOKEN=<token>`, from Remote Desktop (Notepad) or over
   SSH. Never commit it or share it.
4. **Server update:**
   - `git pull` over SSH (the deploy key works).
   - **Rebuild the image in Remote Desktop,** since the code changed and
     builds can't run over SSH: `docker compose build`. (Since found,
     2026-10-04: a code-only rebuild does work over SSH. See "Found in
     Phase 2".)
   - Then `docker compose up -d` (over SSH is fine). That recreates
     `battle-test` on its own network and starts `battle-test-cloudflared`.
     The `cloudflared` image is already on the server, so nothing is
     downloaded.
5. **Cloudflare Access: deferred, but a gate before real users** (Decision
   4). Going public with the app's login alone is fine for the admin and
   test accounts with fictional cases. **Before any real user or real case,**
   create an Access application for `battle.smarkiq.us` with an email
   one-time-code policy listing the allowed users' addresses, then recheck
   the Phase 5 list. This is a Cloudflare dashboard change; the app needs no
   code change.
6. **Optional:** a Cloudflare rate-limiting rule on `POST /login`, on top of
   the app's own per-username lockout.

### Phase 5 — Verify the public path
All of these checks go through the real path (DNS → Cloudflare →
battle_test's tunnel → `battle-test-cloudflared` → app):
- The site loads over HTTPS. Signed-out requests to `/`, `/cases/…` and
  `/cases/…/download.md` redirect to `/login` (or to the Access prompt, if
  enabled).
- The sign-in cookie has `Secure; HttpOnly; SameSite=Lax`.
- Security headers are present: CSP, `X-Frame-Options: DENY`, and HSTS,
  all from the app.
- A wrong password is refused, and 5 failures lock the username.
- A form post with a foreign `Origin` gets 403. A normal sign-in from a
  browser works (the `Origin: null` bug found on the laptop must not come
  back).
- Live progress streams for a whole run without disconnecting (the
  heartbeat check).
- Another account can't open the first account's case (404).
- `llm.smarkiq.us` is unaffected.
- Last, the manual check: from a phone on mobile data (not home Wi-Fi, and
  Tailscale off), sign in and start a case.

**Results, 2026-09-28** (from the laptop, through the public path):

| Check | Result |
|---|---|
| DNS, `/healthz` over HTTPS | ✅ Resolves to Cloudflare, 200 |
| Signed-out `/`, `/cases/x/download.md` | ✅ 303 → `https://battle.smarkiq.us/login?next=…` |
| Security headers | ✅ HSTS `max-age=31536000; includeSubDomains`, CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, `Server: cloudflare` |
| Wrong password (nonexistent username) | ✅ 401 "Wrong username or password." |
| Cross-site sign-in (`Origin: evil.example`, `Origin: null`) | ✅ 403, 403 |
| `llm.smarkiq.us` | ✅ 200, unaffected |
| **Plain `http://`** | ⚠ **Not redirected to HTTPS:** it reaches the app over HTTP. HSTS covers repeat visits, and sign-in can't work over HTTP (Secure cookie), so nothing sensitive is exposed that way. **Decision 2026-09-28: leave Cloudflare's "Always Use HTTPS" off for now.** Note that it's a zone-wide setting, so it would affect `llm.smarkiq.us` too. If wanted later, an app-only alternative is for battle_test to redirect `http://` to `https://` itself, when `behind_proxy` is on and Cloudflare reports the request came in over HTTP. That's a small code change and doesn't touch smark_iq. |
| Cookie flags, live progress through Cloudflare, phone on mobile data | ⏳ **For the user** (they need signing in): DevTools → Application → Cookies (`bt_session`: Secure, HttpOnly, SameSite Lax); run the Utah sample and watch it stream to the end; sign in from a phone with Wi-Fi and Tailscale off |
| Another account can't see a case | Covered by the web tests. Optional to repeat live with a second test account |

**Still required before real users:** Cloudflare Access (Decision 4), and
the GPU-sharing check (Phase 3 step 4).
- **Timing (2026-09-28):** real users are a long way off, and battle_test
  may be on Bedrock by then. That would make the GPU-sharing check moot,
  since battle_test would no longer use the server's GPU.
- **Cloudflare Access stays required either way:** it protects the site,
  wherever the models run.

**Tidying done 2026-09-28:**
- The laptop's `.env` (which held the tunnel token) is deleted, so the only
  copy is the server's.
- The laptop's `.env.example` is restored.
- No stale tunnel was left in Cloudflare: the account has exactly two
  tunnels, `battle-test` and `smark-iq`, both healthy with 1 route each.

### Phase 6 — Operations and hand-off
The day-to-day commands now live in `plans/plan.md`, under "Start and stop:
how to run it". That replaces the separate `deploy.md` planned here.
- **Adding users:** over SSH **from the laptop** (not Remote Desktop, whose
  keyboard mapping can garble passwords): `docker compose run --rm
  battle-test python -m battle_test.web.users add NAME`. With Access, also
  add their email to the Access policy.
- **Updating the app:**
  - `git pull` over SSH (the deploy key works).
  - Back up the accounts first when the update changes the accounts
    database: `docker cp battle-test:/data/cases/users.sqlite
    $HOME\users-backup.sqlite`. Delete the copy once the update is checked.
  - Then `docker compose build`. **Try it over SSH first:** it worked on
    2026-10-04 for a code-only change. If it fails with the credentials
    error, run it **in Remote Desktop**, or build on the laptop and copy
    the image (`docker save` → `scp` → `docker load`).
  - Then `docker compose up -d`.
  - **Last done 2026-10-04:** setup links, plans, firm pooling, the queue
    cap, plan expiry and the admin pages (commit `ef1475d`), all over SSH.
    The accounts database upgraded itself on first start.
  - **Last done 2026-10-05:** the case list on case pages and the
    new-case form (commit `8d23708`). The SSH build failed, so the image
    was built on the laptop and copied. No database change.
  - Running jobs are interrupted by a restart, and the app marks them
    failed with a message, so update between runs.
  - (`&&` doesn't work in the server's PowerShell 5.1, so run the steps
    separately or chain them with `;`.)
- **Quarterly law refresh:** bump `corpus.snapshot` in
  `config.server.toml`, then rerun `corpus build`. The index swap is atomic,
  and `evaluate --validate` confirms the expected-authority lists still
  hold.
- **After a reboot:** sign in via Chrome Remote Desktop. Docker Desktop,
  then the containers (`restart: unless-stopped`), come back on their own.
- **Retention** runs itself (hourly purge, 90 days).
- **"Site can't be reached":**
  - First check this machine's internet connection. It was the cause on
    2026-10-03.
  - Then DNS caching: run `ipconfig /flushdns`, and clear Chrome's cache
    at `chrome://net-internals/#dns`.
  - Then the server: over SSH, check `docker ps` shows `battle-test`
    (healthy) and `battle-test-cloudflared`, and look at `docker logs
    battle-test-cloudflared` for "Registered tunnel connection".
  - `curl -4 https://battle.smarkiq.us/healthz` from the laptop separates
    a local DNS problem from a real outage.

## Risks and how they're handled

| Risk | Handling |
|---|---|
| **GPU contention with Open WebUI.** One 16 GB card; a 14B battle_test model plus an Open WebUI chat model may not both fit. | Decision 9: battle_test unloads its model 30 s after a run (`keep_alive`), so chats slow down only during a run (~90 s per case). Runs are one at a time. Later: Bedrock would take battle_test off the GPU entirely. The GPU-sharing check (Phase 3 step 4) is still to do before real users. |
| **RAM (24 GB total).** Open WebUI 4 GB limit, Ollama CPU-side buffers, the 1.3 GB law index in page cache, Windows itself. | Memory limit on the battle-test container (~3 GB). Watch Task Manager in Phase 3. The board takes up to 256 GB if needed. |
| **Cloudflare idle timeout breaks live progress.** | Heartbeat every ~15 s (code change 5), verified in Phase 5. |
| **Docker Desktop instability** (smark_iq hit an image-pull error once). | Nothing new here: battle_test uses Docker the same way Open WebUI already does. The GPU stays with native Ollama. |
| **App login bug exposes cases.** | Cloudflare Access in front (decision 4), plus the app's own sign-in, CSRF and per-user checks, which are already tested. |
| **Privileged data in transit through Cloudflare.** | Raise with the lawyer (see Privacy note). Tailscale-only access is the fallback. |
| **Case data leaking into backups or git.** | Case files live only in the `battle-cases` volume. The build context excludes data. Backups cover accounts only, unless decided otherwise. |
| **A deploy breaks smark_iq.** | Fully independent since 2026-09-28: own network, own tunnel, no shared files, so battle_test's up, down or rebuild can't touch smark_iq. On 2026-09-28 smark_iq's containers disappeared anyway, for an unknown cause (see Incident). Use `stop`/`restart`, never `down`, and check both stacks after server work. |

## Rollback

Each step can be undone on its own. smark_iq is never involved:
1. In the Cloudflare dashboard, remove the `battle.smarkiq.us` public
   hostname, or stop or delete battle_test's tunnel. The site is off the
   internet immediately.
2. `docker compose stop` in battle_test stops the app and its connector.
   Use `stop`, not `down`, unless removing it on purpose; `down` removes
   the containers. The volumes (accounts, cases, law index) survive either
   way, unless they're deliberately removed with `docker volume rm`.

## Open questions

- **Before real users:** add Cloudflare Access (Decision 4). This is a
  requirement, not an option.
- The lawyer's view on Cloudflare handling case text in transit.
- Whether GPU sharing with Open WebUI is acceptable during working hours
  (measured in Phase 3).
