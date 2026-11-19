# Plan — Hosting the battle_test website on AWS instead of the smark_iq server

**Status: not decided (written 2026-11-05).** Nothing here has been done.
This plan sets out what moving the site to AWS would involve, and the
advantages and disadvantages, so the decision can be made. It builds on
`plans/aws-bedrock-plan.md` (costs, the Bedrock client, and an earlier
outline of Phases 3–5) and `plans/server-migration-plan.md` (how the site
runs today).

## The question

The site `https://battle.smarkiq.us` runs today on the smark_iq server at
home: a Windows 11 PC with Docker Desktop, the models on its GPU through
Ollama, and a Cloudflare Tunnel for public access.

The **models** are already moving to Amazon Bedrock (unblocked
2026-11-05). That is decided separately. The open question here is where
the **app** runs: the web pages, accounts, case files, the law index and
the search.

| | A. Stay on the smark_iq server | B. Move the app to AWS |
|---|---|---|
| Web app, accounts, case files | smark_iq server, at home | AWS server (EC2) in `us-west-2` (Oregon) |
| Law index and search | smark_iq server | AWS server |
| Drafting and checking models | Bedrock (planned) | Bedrock |
| Search embedding model | Server GPU, as now | See "The embedding model" below |
| Public address | `battle.smarkiq.us` through Cloudflare | Same address, same tunnel |

Moving the app to AWS only makes sense with the models on Bedrock. Keeping
models on the home GPU while the app runs on AWS would send every model
call back to the house, with the downsides of both.

## Advantages of moving to AWS

1. **The site no longer depends on the home server, power or internet.**
   - On 2026-10-14 a Windows Update restart took the site down for about
     13 hours without anyone noticing. Docker Desktop doesn't start until
     someone signs in to Windows, and Windows 11 Home can't turn automatic
     restarts off.
   - On 2026-09-28 the smark_iq containers disappeared from the shared
     Docker install, cause unknown.
   - A Linux server on AWS starts Docker at boot with nobody signed in, and
     AWS can restart the server automatically if its hardware fails.
2. **One provider for everything that handles case text** (apart from
   Cloudflare in transit). Processing (Bedrock) and storage would both be
   in one AWS account, in one region, under AWS's published security
   certifications and data-processing terms. That's easier to explain to
   the lawyer, and later to clients, than a home PC.
3. **Better physical and account security than a home PC.** There's no
   machine anyone can sit down at, no shared desktop, and nothing on the
   server shared with Open WebUI. Admin access is through AWS's Session
   Manager, with no SSH port and no Tailscale.
4. **The GPU goes back to Open WebUI alone.** No more sharing it with
   `keep_alive` timing, so chats aren't slowed by a running case.
5. **Several cases can run at once** (`[web] workers = 4`, as in
   `config.bedrock.toml`), since there's no single GPU to wait for.
6. **Simpler builds and deploys.** It's Linux, so the Windows
   credential-manager workaround (an empty Docker config folder and
   `DOCKER_BUILDKIT=0`) isn't needed.
7. **Room to grow.** A bigger server, a second region or a managed
   database later is a setting change, not new hardware.

## Disadvantages of moving to AWS

1. **A fixed monthly cost of about $40–60**, where the home server costs
   nothing extra (see "Cost"). That cost is paid whether or not anyone uses
   the site.
2. **Case files leave your premises.** They'd sit on an encrypted AWS disk
   in your account instead of your own hardware. This is decision 3 in
   `docs/data-handling-for-lawyer.md`, and the lawyer has to approve it.
3. **All the eggs in one AWS account.** In October, AWS held this account's
   Bedrock access for about a month for a fraud-prevention review, with
   little warning and no timeline. If a similar hold or billing problem
   hit an account that also runs the website, the whole site could go down,
   not just the models. Mitigation: keep the home server able to take the
   site back (see "Rollback"), and keep an account backup outside AWS.
4. **The search embedding model needs a new home.** Today it runs on the
   server's GPU. On AWS it either runs on the server's CPU (which needs a
   bigger server) or moves to a Bedrock embedding model, which needs code
   changes, all the vectors rebuilt, and the search results re-checked. See
   "The embedding model".
5. **More to learn and look after:** IAM roles, security groups, Session
   Manager, OS updates and AWS billing. The home server needs looking
   after too, but you already know how.
6. **Migration work and some risk** in the move itself. The tunnel cutover
   keeps downtime near zero, but it's a day or two of work and checks.
7. **Harder to leave later.** Moving away means doing a migration like
   this one again. The app itself stays portable (Docker, plain files, no
   AWS-only services), which limits this.

**Unchanged either way:** Cloudflare still handles traffic in transit, and
Cloudflare Access is still required before real users. The 90-day case
deletion and the accounts-only backups also stay as they are.

## The embedding model

Semantic search (on since 2026-10-16) turns each search query into a vector
with `nomic-embed-text`, and compares it with about 397,000 saved section
vectors (594 MB, read from disk as needed). The weekly law refresh also
embeds new and changed sections. Today both run on the server's Ollama.
The app finds the embedding model through `[search] embedding_url`, so it
can point anywhere.

| Option | How | Cost | Work |
|---|---|---|---|
| **1. Ollama on the AWS server's CPU (recommended to start)** | Run Ollama with `nomic-embed-text` (274 MB) next to the app. A query takes a fraction of a second on CPU. A full re-embed is slow on CPU, but the refresh only embeds what changed. | A bigger server (8 GB) | Config only; the same vectors and model, so search results don't change |
| 2. Build vectors elsewhere, embed queries on AWS | As option 1, but large re-embeds run on the laptop or the home GPU and are copied up | As option 1 | A copy step after big law updates |
| 3. A Bedrock embedding model (e.g. Amazon Titan Text Embeddings) | A new embedding client; rebuild all vectors | About $3 to embed everything once (~150M tokens at about $0.02 per million; check the price), then pennies | Code, a full rebuild, and a new evaluation, since search results will change |

Option 1 keeps search exactly as evaluated. Option 3 could follow later if
it searches better.

## Cost

Prices for `us-west-2`, on demand, from the 2026-10 notes in
`plans/aws-bedrock-plan.md`. **Check them again before deciding.**

| Item | $/month | Notes |
|---|---|---|
| EC2 `t4g.large` (2 vCPU, 8 GB, arm64) | ~49 | 8 GB for the law index (1.4 GB), the vectors (0.6 GB), the app and the CPU embedding model. A `t4g.medium` (4 GB, ~$24.50) is too tight with Ollama on it |
| EBS gp3 disk, 40 GB, encrypted | ~3.20 | The law, plus the refresh's staging and archive copies, the image and cases |
| Public IPv4 address | ~3.65 | Needed for outbound traffic. No inbound ports open |
| S3 for account backups | <0.10 | `users.sqlite` is a few KB |
| CloudWatch alarm, Session Manager, Budgets | 0 | Within free allowances |
| Data out to Cloudflare | ~0 | First 100 GB a month is free |
| **Fixed total** | **~$56** | **~$32** with a 1-year Savings Plan or reserved instance on the server (about 35–40% off), once settled |

**Model costs are the same either way**, since Bedrock is used in both
options. Measured 2026-11-05: gpt-oss 120B for drafting and both checks
costs about **$0.05 a case** (2 rounds). At 20 cases per user per month,
that's about $1 per user per month.

**Home server, for comparison:** $0 extra. It's already on for Open WebUI.

## Steps, if the move is chosen

### Phase 0 — Decisions
1. **The lawyer:** decisions 1–3 in `docs/data-handling-for-lawyer.md`.
   Real case material on Bedrock, and case files stored on AWS. Without
   the second, stay on the home server (option A) with models on Bedrock.
2. **Embedding model:** option 1 (CPU on the server) unless there's a
   reason to change search now.
3. **Server size:** `t4g.large`, reviewed after a month of real use.
4. **Budget alerts** at production levels (e.g. $75 and $125 a month),
   replacing the $10 / $25 testing alerts.
5. **Separate AWS account for the website or not.** A second account in
   the same organization would keep the site apart from experiments. It
   also brings its own review risk (the `battle-test-bedrock` member
   account got the same Bedrock block). Keeping one account is simpler.

### Phase 1 — Code and config (on the laptop, no AWS cost)
1. **`config.aws.toml`:**
   - Bedrock provider and the chosen models, with `profile` left empty so
     boto3 uses the server's IAM role.
   - `[search] embedding_url` pointing at the local Ollama.
   - `workers = 4`, and the deployed-mode settings from
     `config.server.toml`.
2. **Compose override for AWS** (`docker-compose.aws.yml`):
   - add an `ollama` container (CPU), in place of `host.docker.internal`
   - raise the app's `mem_limit` to fit the law index and vectors
3. **Scheduled jobs on Linux:** systemd timers or cron for the weekly
   `corpus refresh` and a weekly `users.sqlite` copy to S3. These replace
   the Windows Task Scheduler entry.
4. **Check the image builds and runs on arm64.** `python:3.12-slim`,
   `pyarrow` and `numpy` all have arm64 builds.
5. Tests and an end-to-end run with `config.aws.toml` from the laptop
   (fictional case, a few cents on Bedrock).

### Phase 2 — Set up AWS (in the console, or the CLI with `battle-test`)
1. **IAM role for the server**, allowed only to:
   - call the chosen Bedrock models (`bedrock:InvokeModel*`, `Converse*`,
     on those model ARNs)
   - use Session Manager (`AmazonSSMManagedInstanceCore`)
   - write to the backup bucket
2. **S3 bucket for account backups:** private, encrypted, versioned, with
   a lifecycle rule that keeps about 8 weeks.
3. **Security group with no inbound rules.** The tunnel is outbound only,
   and admin goes through Session Manager.
4. **EC2 `t4g.large`:**
   - Amazon Linux 2023 (arm64), with the 40 GB encrypted gp3 disk
   - **IMDSv2 required**
   - automatic recovery on a failed system status check
5. **CloudWatch alarm** on the status checks, emailing you.
6. **No EBS snapshots of the case disk.** They'd keep deleted cases past
   the 90-day promise. Only accounts are backed up, as now.

### Phase 3 — Build and test privately
1. Over Session Manager: install Docker and git, then clone the repo with
   a **new read-only deploy key**.
2. Build the image, then `corpus build` (about a minute) and the section
   vectors.
   - Build the vectors on the server's CPU if it's fast enough, or copy
     `law-vectors*` from the home server (option 2).
3. `evaluate --validate`, then one fictional case through the
   `docker-compose.local-test.yml` override, reached with Session Manager
   port forwarding (no public access yet).
4. Copy `users.sqlite` from the home server so existing accounts carry
   over. Don't copy cases: they're fictional and expire anyway.

### Phase 4 — Cut over (no DNS change)
1. Start the tunnel connector on AWS with the **existing** `battle-test`
   tunnel token. Cloudflare treats it as a second copy of the same
   tunnel, so both servers serve the site for a moment.
2. Check `/healthz`, sign-in, and one fictional case end to end on AWS.
3. **Stop the connector on the home server** (`docker compose stop
   cloudflared`). All traffic now goes to AWS.
4. Re-run the public-path checks from `plans/server-migration-plan.md`
   Phase 5: headers, cookie flags, live progress, a phone on mobile data.
5. Add an outside uptime check on `/healthz` that emails on failure. It
   was suggested after the 13-hour outage and helps in either option.

### Phase 5 — Operations
- **OS updates:** automatic security updates (`dnf-automatic`), or
  Session Manager Patch Manager, with restarts in a quiet window. Docker
  comes back on its own after a restart.
- **Deploys:** `git pull`, build, `docker compose up -d` over Session
  Manager, as on the home server but without the Windows workarounds.
  Update `docs/running-on-server.md`, or add `docs/running-on-aws.md`.
- **Law updates:** the weekly refresh runs on its timer, and you approve a
  new snapshot with `corpus promote` as now.
- **Watch the bill** for the first two months against the estimate.

### Phase 6 — Retire the home copy
1. After about two weeks of AWS running cleanly, `docker compose stop` the
   battle_test stack on the home server. Leave it installed, stopped.
   That's the fallback for an AWS problem (Disadvantage 3).
2. Remove `keep_alive` settings that existed only for GPU sharing.
3. Update `plans/plan.md`, the README and the docs for the new home.

## Rollback

- **During cutover:** start the connector on the home server again and
  stop it on AWS. It takes seconds, with no DNS change.
- **Later, for example during an AWS account hold:** start the stopped
  home stack, restore `users.sqlite` from the latest backup, and switch
  the connector back. If Bedrock is the thing that's blocked, point the
  home stack at the local 14B (`config.server.toml`) until it's back.
  Cases from the AWS period stay on AWS until that's resolved.

## Recommendation

**Not decided.** On the evidence so far:
- **If the lawyer approves storing case files on AWS,** moving is worth
  it, mainly for reliability (Advantage 1) and a single provider to
  explain (Advantage 2). The extra ~$56 a month (~$32 once committed) is
  small next to the proposed prices in `plans/profitability-plan.md`.
- **If not, or until then:** stay on the home server with the models on
  Bedrock. Do the cheap reliability fixes from the 2026-10-14 incident
  either way: Tailscale "Run unattended", Windows Update active hours, and
  an outside uptime check.

## Open questions

- The lawyer's answers to decisions 1–3 and 9 in
  `docs/data-handling-for-lawyer.md`.
- Whether a CPU embedding model keeps search fast enough under several
  cases at once. Measure in Phase 3.
- Whether a full section re-embed on CPU (after a large law update)
  finishes in a reasonable time, or needs the copy-up in option 2.
- Expected users and usage in the first year, to choose between on-demand
  and a Savings Plan.
- Current EC2, EBS and Titan embedding prices in `us-west-2`.
