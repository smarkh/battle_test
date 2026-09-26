# Plan — Moving battle_test to the smark_iq Server (Public Access)

This is a plan, not a change log. Nothing here has been done yet. It covers
build step 5, part 4 of `plans/plan.md`: move battle_test from the dev
laptop to the smark_iq server, and make the web UI reachable from anywhere
at a public URL, the same way `llm.smarkiq.us` is.

## Goal

A signed-in user can open `https://battle.smarkiq.us` from any browser,
start a case, watch it run, and read the results. Everything runs on the
smark_iq server:
- models on its GPU
- law index and case files on its disk
- the public path through the Cloudflare Tunnel that smark_iq already has

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

battle_test reuses all of this. The only public-facing changes are one new
hostname on the existing tunnel and one new site block in the existing
Caddyfile.

## Target architecture

```
browser ──https──▶ Cloudflare edge ──tunnel──▶ cloudflared ──▶ caddy ──▶ battle-test:8000
                    (TLS, optional                 (smark_iq containers)   (new container)
                     Cloudflare Access)                                         │
                                                                               ▼
                                                            Ollama on the host (GPU)
                                                            via host.docker.internal:11434
```

- **New `battle-test` container**, defined in battle_test's own
  `docker-compose.yml`, not added to smark_iq's. It joins smark_iq's Docker
  network as an external network, so the existing `caddy` can reach it by
  name. The two projects stay separate repos with separate lifecycles.
- **No published host port** in normal operation. Traffic reaches it only
  through Caddy on the Docker network. For testing before going public, a
  `127.0.0.1:8000` port is published temporarily and reached over SSH.
- **Two named volumes**, following the guardrails rule of no host bind
  mounts:
  - `battle-law`: the law index (`law.sqlite`, ~1.3 GB, plus the
    downloaded Parquet files).
  - `battle-cases`: accounts (`users.sqlite`) and case files
    (`cases.sqlite` plus one folder per case).

## Decisions (recommended answer in bold)

1. **Hostname.** **`battle.smarkiq.us`** is a subdomain of the domain
   smark_iq already uses, so there's no new domain and no DNS migration.
   Any unused subdomain works.
2. **Run as a container or natively on Windows?** **Container.** It
   matches smark_iq, and the guardrails hardening applies. It also keeps
   privileged case files inside a volume rather than loose on the Windows
   filesystem, and the image is reproducible from the repo. Ollama stays
   native, as smark_iq already decided (Docker Desktop's GPU handling was
   unreliable).
3. **Separate compose project, or add to smark_iq's?** **Separate**, joined
   to smark_iq's network. Rebuilding or stopping battle_test never touches
   Open WebUI, and the reverse is also true.
4. **Second sign-in layer: Cloudflare Access in front?** **Recommended for
   this app,** unlike smark_iq, where it was deferred. The cases are
   privileged legal material, and Access adds an email one-time-code check
   at Cloudflare's edge before a request ever reaches the server. It's free
   for up to 50 users, and it protects against a bug in the app's own login.
   The app's accounts stay as they are (Access decides who reaches the
   site; the app's accounts decide whose cases you see). **Your call:** it
   adds a step for users.
5. **Which model on the server?** **`qwen2.5:14b` first,** since it's
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
8. **Backups.**
   - **Back up `users.sqlite`** (accounts) on a schedule, e.g. a weekly
     copy.
   - **Don't back up case files by default.** They're privileged, they
     expire after 90 days anyway, and a backup would outlive the retention
     promise.
   - **Your call,** ideally with the lawyer's input on record-keeping.

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
   - Caddy passes server-sent events through without buffering, but that
     should be checked.
6. **`/healthz`:** returns 200 without signing in and reveals nothing. It's
   for the compose healthcheck and for Caddy.
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

### Phase 0 — Decisions
Confirm (or change) the recommended answers above: the hostname,
Cloudflare Access, backups, and the model. Tell the lawyer about the
Cloudflare TLS point.

### Phase 1 — Code changes on the laptop
Make the changes in "Code changes needed first", get the tests passing, and
build the image locally. Run it with the demo model and a local config to
check that the container starts, `/healthz` answers, and the non-root user
can write to its volumes. Commit and push.

### Phase 2 — Prepare the server (over SSH)
1. Confirm the smark_iq stack is healthy (`docker compose ps` in smark_iq),
   and note its network name (`docker network ls`, probably
   `smark_iq_default`).
2. Add a read-only deploy key for the GitHub repo, then clone battle_test
   next to smark_iq.
3. `ollama pull qwen2.5:14b` if it's not there. Check `ollama ps` shows
   `100% GPU` during a test prompt. (smark_iq hit a silent CPU fallback
   once; the fix is in its hardware-migration plan.)
4. Build the image, and create the volumes with `docker compose up
   --no-start`.
5. **Build the law index** into `battle-law`: `docker compose run --rm
   battle-test python -m battle_test.corpus build`. Check it with `...
   corpus info`, which should show snapshot v2026.08 and 369,896 sections.
6. **Create the first admin account** (interactive, so run it over SSH with
   a TTY): `docker compose run --rm battle-test python -m
   battle_test.web.users add NAME --admin`.

### Phase 3 — Run privately and test on the server
1. Start the container with a temporary `127.0.0.1:8000` port published.
   Reach it from the laptop through an SSH tunnel over Tailscale (`ssh -L
   8000:127.0.0.1:8000 server`). It still isn't on the internet or the LAN.
2. Test with the real 14B model: sign in, then run the Utah sample case
   end to end. Check that live progress streams, results render, citations
   link, the download works, and deletion works.
3. Run the evaluation on the server (`python -m battle_test.evaluate
   --label server-14b`) for the first real comparison against the laptop's
   7B. This is also the start of 3b's "re-evaluate on 14B".
4. Check GPU sharing with Open WebUI: start a battle_test run while chatting
   in Open WebUI. Expect slower responses while models swap in and out of
   VRAM. Decide whether that's acceptable, or whether to set Ollama's
   `OLLAMA_MAX_LOADED_MODELS` / keep-alive differently (see Risks).
5. Remove the temporary port.

### Phase 4 — Go public
1. **Caddy (in the smark_iq repo):** add a site block `http://battle.smarkiq.us`
   that proxies to `battle-test:8000`, with the same HSTS and security
   headers as the `llm` block. Reload Caddy.
2. **Cloudflare dashboard:** add the public hostname `battle.smarkiq.us` to
   the existing tunnel, pointing at `http://caddy:80`. There's no new tunnel
   or token, and `cloudflared` picks it up live.
3. **Cloudflare Access (if chosen in Phase 0):** create an Access
   application for `battle.smarkiq.us` with an email one-time-code policy
   listing the allowed users' addresses. Do this before (or in the same
   sitting as) step 2, so there's no window where the site is public
   without it.
4. **Optional:** a Cloudflare rate-limiting rule on `POST /login`, on top of
   the app's own per-username lockout.

### Phase 5 — Verify the public path
All of these checks go through the real path (DNS → Cloudflare → tunnel →
Caddy → app):
- The site loads over HTTPS. Signed-out requests to `/`, `/cases/…` and
  `/cases/…/download.md` redirect to `/login` (or to the Access prompt, if
  enabled).
- The sign-in cookie has `Secure; HttpOnly; SameSite=Lax`.
- Security headers are present (CSP, `X-Frame-Options: DENY`, HSTS from
  Caddy).
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

### Phase 6 — Operations and hand-off
- **Adding users:** `docker compose exec battle-test python -m
  battle_test.web.users add NAME` over SSH. With Access, also add their
  email to the Access policy.
- **Updating the app:** `git pull && docker compose up -d --build`. Running
  jobs are interrupted by a restart, and the app marks them failed with a
  message, so update between runs.
- **Quarterly law refresh:** bump `corpus.snapshot` in
  `config.server.toml`, then rerun `corpus build`. The index swap is atomic,
  and `evaluate --validate` confirms the expected-authority lists still
  hold.
- **After a reboot:** sign in via Chrome Remote Desktop. Docker Desktop,
  then the containers (`restart: unless-stopped`), come back on their own.
- **Retention** runs itself (hourly purge, 90 days).
- Write a short `deploy.md` in this repo with these commands, like
  smark_iq's `remote-access.md`.

## Risks and how they're handled

| Risk | Handling |
|---|---|
| **GPU contention with Open WebUI.** One 16 GB card; a 14B battle_test model plus an Open WebUI chat model may not both fit. | Runs are one at a time. Ollama swaps models (slower, but it works). Measure it in Phase 3. If it's a problem, schedule runs off-hours, use the same model for both, or tune Ollama's keep-alive. |
| **RAM (24 GB total).** Open WebUI 4 GB limit, Ollama CPU-side buffers, the 1.3 GB law index in page cache, Windows itself. | Memory limit on the battle-test container (~3 GB). Watch Task Manager in Phase 3. The board takes up to 256 GB if needed. |
| **Cloudflare idle timeout breaks live progress.** | Heartbeat every ~15 s (code change 5), verified in Phase 5. |
| **Docker Desktop instability** (smark_iq hit an image-pull error once). | Nothing new here: battle_test uses Docker the same way Open WebUI already does. The GPU stays with native Ollama. |
| **App login bug exposes cases.** | Cloudflare Access in front (decision 4), plus the app's own sign-in, CSRF and per-user checks, which are already tested. |
| **Privileged data in transit through Cloudflare.** | Raise with the lawyer (see Privacy note). Tailscale-only access is the fallback. |
| **Case data leaking into backups or git.** | Case files live only in the `battle-cases` volume. The build context excludes data. Backups cover accounts only, unless decided otherwise. |
| **A deploy breaks smark_iq.** | Separate compose project, and the only shared-file edit is one new Caddy site block. Rollback below. |

## Rollback

Each step can be undone on its own, and smark_iq keeps running:
1. Remove the `battle.smarkiq.us` public hostname in the Cloudflare
   dashboard. The site is off the internet immediately.
2. Remove the Caddy site block and reload Caddy.
3. `docker compose down` in battle_test. The volumes (accounts, cases, law
   index) survive, unless they're deliberately removed with `docker volume
   rm`.

## Open questions

- Hostname (`battle.smarkiq.us`?), Cloudflare Access (yes/no), and the
  backup policy. See Decisions 1, 4 and 8.
- The lawyer's view on Cloudflare handling case text in transit.
- Whether GPU sharing with Open WebUI is acceptable during working hours
  (measured in Phase 3).
