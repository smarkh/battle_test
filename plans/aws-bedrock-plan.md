# Plan — Hosting battle_test on AWS (Bedrock models, AWS-hosted app)

This is a plan, not a change log. Nothing here has been done yet.

**Goal:** move battle_test off the smark_iq server entirely.
- The **models** run on **Amazon Bedrock**.
- The **app** (web UI, law index, accounts and cases) runs on a small
  **AWS server (EC2)**.
- The public address stays `https://battle.smarkiq.us`, through the same
  Cloudflare Tunnel.

The smark_iq server then gets its GPU back for Open WebUI alone. This
replaces "option 1" (sharing the GPU with `keep_alive`) and makes the
Bedrock option in `plans/plan.md` concrete.

For revenue, pricing and profit, built on these costs, see
`plans/profitability-plan.md`.

## Summary

- **Cost per case** (2 rounds, all 8 model calls): about **$0.01**
  (budget model) to **$0.28** (Claude Sonnet for everything). The
  recommended mix is **~$0.10**.
- **Fixed cost:** about **$30/month** for the AWS server, whatever the use.
- **Monthly totals,** recommended mix:
  - 1 user: **~$32**
  - 5 users: **~$40**
  - 25 users: **~$80**
  - 50 users: **~$135**

  That assumes 20 cases per user per month.
- **How costs grow:** model cost grows **linearly with total cases** (users
  × cases per user). The server cost stays flat up to at least 50–100
  users.
- **The one step change:** above **50 users**, Cloudflare Access (required
  before real users) goes from free to **$7 per user per month for every
  user**. At 100 users that's $700/month, more than everything else
  combined. See "Scaling past 50 users".
- **The deciding question** is still the lawyer's view on case material
  being processed and stored on AWS, not cost.

## Where the numbers come from

### Tokens per case: measured, not guessed

The real pipeline (real law index, real research and selection prompts,
all three sample cases, 2 rounds) was run on 2026-10-03 with a stand-in
model that returns realistic-length drafts. That measures exactly what
Bedrock would receive:

| Step | Calls | Input tokens |
|---|---|---|
| Research (each side) | 2 | ~5k |
| Selection (each side) | 2 | ~14k |
| Drafts: complaint, motion, opposition, reply | 4 | ~20k |
| **Total per case** | **8** | **~39k in, ~7k out** |

- All three states came within 1% of each other.
- Output assumes ~1,000-word drafts. The server's 14B wrote 600–800
  words, and larger models usually write more.
- **The planned 3a check** ("does the cited section say that?") would add
  roughly **15k in / 1.5k out**.
- Token counts are estimated as characters ÷ 4. Bedrock reports exact
  usage on every call, so the Bedrock client should record it, and that
  will replace this estimate (see Code changes).

### Prices (Bedrock on-demand, US regions, checked 2026-10-03)

| Model | Input $/1M | Output $/1M | Notes |
|---|---|---|---|
| gpt-oss-120b | 0.15 | 0.60 | |
| Qwen3 32B | 0.15 | 1.20 | |
| Mistral Large 3 | 0.50 | 1.50 | |
| Qwen3 235B | 0.53 | 2.66 | |
| DeepSeek v3.2 | 0.62 | 1.85 | |
| Llama 3.3 70B | 0.99 | 1.32 | Up from 0.72 / 0.72 in the earlier estimate |
| Claude Haiku 4.5 | 1.00 | 5.00 | +10% on a regional endpoint |
| Claude Sonnet 5 | 2.00 | 10.00 | Now the standard price. +10% regional. Newer tokenizer counts ~30% more tokens |
| Claude Opus 5.5 | 4.00 | 20.00 | Same notes as Sonnet 5 |

- **Regional endpoints** (data stays in a chosen region) add 10% for
  Claude 4.5 and later. The estimates below assume regional endpoints
  for Claude.
- **Batch** pricing is 50% off, but it's asynchronous, so it doesn't fit
  a live progress page.
- **Prompt caching** (Claude) could cut repeated input, e.g. the system
  prompt and quoted authorities reused across the motion, opposition and
  reply. That's a later optimisation and isn't counted here.

### Cost per case

| Model | Per case (current pipeline) | With the 3a check on the same model |
|---|---|---|
| gpt-oss-120b | $0.010 | $0.013 |
| Qwen3 32B | $0.014 | $0.019 |
| Mistral Large 3 | $0.031 | $0.040 |
| DeepSeek v3.2 | $0.038 | $0.050 |
| Qwen3 235B | $0.040 | $0.052 |
| Llama 3.3 70B | $0.049 | $0.066 |
| Claude Haiku 4.5 | $0.083 | $0.107 |
| Claude Sonnet 5 | $0.215 | $0.279 |
| Claude Opus 5.5 | $0.429 | $0.558 |

**Three setups worth comparing:**
- **Budget:** Qwen3 32B for everything, no 3a check. **$0.014/case.**
- **Recommended:** a large open model drafts (Qwen3 235B), and **Claude
  Sonnet 5 runs the 3a check**, where accuracy matters most. **$0.104/case.**
- **Premium:** Claude Sonnet 5 for everything, including 3a. **$0.279/case.**

Which model drafts best is an open question. The evaluation set will
answer it (step 2 below), and the costs above let the choice be made on
value, not just quality.

### Fixed monthly costs (AWS server and services)

| Item | $/month | Notes |
|---|---|---|
| EC2 `t4g.medium` (2 vCPU, 4 GB, arm64), on-demand | ~24.50 | 4 GB, because the law index is 1.3 GB and benefits from being cached in memory |
| EBS gp3 disk, 30 GB, encrypted | ~2.40 | Law index (~1.5 GB with downloads), image, cases |
| Public IPv4 address | ~3.65 | Needed for outbound traffic. No inbound ports open |
| AWS Budgets alerts, SSM Session Manager, CloudTrail (default) | 0 | |
| Cloudflare Tunnel | 0 | |
| Cloudflare Access, up to 50 users | 0 | **$7/user/month for every user** once over 50 |
| Backups of accounts only (`users.sqlite`, KB-sized) | ~0 | A small snapshot or an S3 copy |
| **Total** | **~$30** | |

A 1-year Savings Plan or reserved instance would cut the EC2 line by
~30–40%, once the setup has settled.

## How costs scale

**Monthly cost = $30 fixed + (users × cases per user per month × cost per
case) + Cloudflare Access ($0 up to 50 users, then $7 × users).**

- **More cases per user:** cost grows in a straight line. Doubling usage
  doubles the model cost and nothing else.
- **More users:** the same straight line through the model cost. The
  server stays the same size: battle_test only waits on Bedrock during a
  run, so a 4 GB server handles dozens of simultaneous users. The two
  real thresholds are:
  - **50 users:** Cloudflare Access starts charging for everyone.
  - **Hundreds of runs at once:** Bedrock's per-account token quotas may
    need raising. That's a free quota request, not a cost.
- **The model choice** is the biggest lever. It's a 20× spread per case
  (budget to premium).

Monthly totals, including the $30 fixed cost and Cloudflare Access (model
cost alone in brackets):

**Budget: Qwen3 32B for everything, no 3a check** ($0.014 per case)

| Users | Light (5 cases/user/mo) | Regular (20) | Heavy (60) |
|---|---|---|---|
| 1 | $30 (models $0) | $30 (models $0) | $31 (models $1) |
| 5 | $30 (models $0) | $31 (models $1) | $34 (models $4) |
| 25 | $32 (models $2) | $37 (models $7) | $52 (models $22) |
| 50 | $34 (models $4) | $44 (models $14) | $73 (models $43) |
| 100 | $737 (models $7) | $759 (models $29) | $816 (models $86) |

**Recommended: Qwen3 235B drafts + Claude Sonnet 5 for the 3a check** ($0.104 per case)

| Users | Light (5 cases/user/mo) | Regular (20) | Heavy (60) |
|---|---|---|---|
| 1 | $31 (models $1) | $32 (models $2) | $36 (models $6) |
| 5 | $33 (models $3) | $40 (models $10) | $61 (models $31) |
| 25 | $43 (models $13) | $82 (models $52) | $186 (models $156) |
| 50 | $56 (models $26) | $134 (models $104) | $343 (models $313) |
| 100 | $782 (models $52) | $938 (models $208) | $1,355 (models $625) |

**Premium: Claude Sonnet 5 for everything, including 3a** ($0.279 per case)

| Users | Light (5 cases/user/mo) | Regular (20) | Heavy (60) |
|---|---|---|---|
| 1 | $31 (models $1) | $36 (models $6) | $47 (models $17) |
| 5 | $37 (models $7) | $58 (models $28) | $114 (models $84) |
| 25 | $65 (models $35) | $169 (models $139) | $448 (models $418) |
| 50 | $100 (models $70) | $309 (models $279) | $867 (models $837) |
| 100 | $869 (models $139) | $1,288 (models $558) | $2,403 (models $1,673) |

### Scaling past 50 users

The 100-user rows are dominated by Cloudflare Access ($700 of them).
Options if it gets there:
- **Stay at 50 or fewer Access users,** e.g. one Access seat per firm,
  with the app's own accounts for the individuals behind it.
- **Use AWS-native sign-in** (Amazon Cognito) in front instead. It's
  roughly free at this scale, but means code changes, and gives up
  Cloudflare's edge protection.
- **Pay the $7/user,** if the users bring matching value.

### Cost controls (recommended from day one)

- **AWS Budgets alerts** at, say, $40 and $75/month, by email. They're
  free.
- **A per-user monthly case cap** in the app (e.g. 100 cases), so one
  account can't run up a bill. That's a small code change.
- **Record token usage and cost on every case** (Bedrock reports it), and
  show it in the case's report and the evaluation summary. Real spend is
  then visible, not estimated.

## Development and testing costs (before any real users)

Worked out 2026-10-05, from the measured tokens per case and the price
table above. The prices were not re-checked that day.

**Nothing is charged while idle.** On-demand Bedrock bills per token, with
no minimum and no charge for having model access. The ~$30/month fixed
cost only starts when the EC2 server is created (Phase 3). Development
and evaluation from the laptop (hybrid B) need no server.

| Activity | What it sends | Cost |
|---|---|---|
| Unit tests, and the web UI on the demo model | Nothing | $0 |
| `evaluate --research-only` (3 cases) | 3 small calls | A few cents at most |
| One full evaluation (3 cases, 2 rounds) | 24 calls, ~117k in / ~21k out | $0.03 to $1.29, by model (below) |
| Model comparison: 5 models x 3 cases x 3 repeats | 45 cases | ~$3.20 |

One full evaluation, by model: gpt-oss-120b $0.03, Qwen3 32B $0.04,
Mistral Large 3 $0.09, DeepSeek v3.2 $0.11, Qwen3 235B $0.12, Llama 3.3
70B $0.15, Claude Haiku 4.5 $0.25, Claude Sonnet 5 $0.65, Claude Opus 5.5
$1.29.

A month of development, by how hard the evaluation is run:

| Evaluations a month | Cases | Qwen3 32B | Qwen3 235B | Claude Sonnet 5 | Claude Opus 5.5 |
|---|---|---|---|---|---|
| 10 (light) | 30 | $0.40 | $1.20 | $6.50 | $13 |
| 30 (regular) | 90 | $1.30 | $3.60 | $19 | $39 |
| 200 (heavy: 10 a day) | 600 | $8 | $24 | $129 | $257 |

**What could make it cost more than this:**
- **Longer drafts.** The estimate assumes ~1,000-word drafts. A model
  that writes twice as much roughly doubles the output cost, which is
  about half the total on most of these models.
- **Reasoning tokens.** Models that think before answering may bill that
  thinking as output. Unmeasured: the first real run will show it.
- **The 3a check,** once built, adds about 30% to each case.
- **A runaway loop** in new code. The retry logic must give up after a
  few attempts.

**Controls for this stage:** an AWS Budgets alert at $10 and $25, a cheap
default model in the evaluation config, and `--research-only`, `--case`
and `--rounds 1` for quick iterations.

## Data handling (still needs the lawyer)

Moving to AWS changes two things from today:
1. **Model calls:** case text is sent to Bedrock. AWS states that Bedrock
   doesn't store or log prompts, doesn't train on them, and doesn't share
   them with model providers. **Use regional endpoints** (e.g.
   `us-west-2`) so processing stays in a known region.
2. **Storage:** accounts and case files live on an AWS disk instead of
   your own server. They're encrypted at rest, deleted after 90 days as
   now, and in a single AWS account you control.

This is the "local vs. Bedrock" question already listed for the lawyer.
**Don't move real case material until they've agreed.** Fictional
evaluation cases are fine before then.

## Two ways to do it

| | A. Everything on AWS (this plan) | B. Hybrid: app stays on your server, models on Bedrock |
|---|---|---|
| Models | Bedrock | Bedrock |
| App, law index, cases | EC2 | smark_iq server (as now) |
| Fixed cost | ~$30/month | $0 |
| Depends on your home server and internet | No | Yes |
| GPU freed for Open WebUI | Yes | Yes |
| Case files leave your premises | Yes (AWS disk) | No (only model calls go out) |

**Recommendation:**
- **Start with B as a stepping stone:** the Bedrock client runs from the
  laptop and the current server for evaluation. It's cheap, and it shows
  which model is worth paying for.
- **Then move to A,** for independence from home hardware and internet.

B is also a legitimate end state if the lawyer prefers case files to stay
on your own hardware.

## Code changes

1. **Bedrock client** (`battle_test/bedrock_client.py`), with the same
   `chat()` interface as the Ollama client, so the pipeline doesn't
   change:
   - Uses the Bedrock **Converse / ConverseStream** API (via `boto3`), so
     live draft streaming keeps working.
   - **JSON replies** (research and selection): ask for JSON in the prompt
     and parse it. The pipeline already falls back safely when a reply
     isn't usable. Where the model supports it, use tool-use structured
     output instead.
   - **Retries** with backoff on throttling.
   - **Records token usage** for every call.
2. **Config:**
   - `[models] provider = "ollama" | "bedrock"`
   - a Bedrock region and model IDs per role (plaintiff, defendant, and a
     separate `checker` model for 3a)
   - `max_tokens`
   - Credentials come from the environment: the EC2 IAM role, or an AWS
     profile on the laptop. **Never in config files or git.**
3. **Cost tracking:** per-case token totals and estimated cost go into the
   result, the Markdown report, the results page and the evaluation
   summary. A small price table in config drives the estimate.
4. **Concurrency:** a `[web] workers = N` setting. With Bedrock there's
   no single GPU, so several cases can run at once (e.g. 3–4). It stays 1
   for Ollama.
5. **Optional per-user monthly case cap** (see Cost controls).
6. **Tests:** Bedrock calls are stubbed (`botocore.stub.Stubber`): no
   network and no cost in unit tests.
7. **Deployment files:**
   - `config.aws.toml` (the Bedrock provider, the volume paths, and the
     same deployed-mode settings as the server)
   - an arm64-friendly image. `python:3.12-slim` and `pyarrow` both have
     arm64 builds, so likely no change is needed.

## Step-by-step

### Phase 0 — Decisions
1. **The lawyer:** agree to case material being processed by Bedrock and
   stored on AWS (A), or only processed (B).
2. **Region:** `us-west-2` (Oregon) is closest to Utah and carries the
   models above. `us-east-1` is the alternative.
3. **Budget:** the alert levels, and whether to enforce a per-user cap.
4. **Model choice:** made in Phase 2, from evaluation data.

### Phase 1 — AWS account setup (you, in the AWS console)
1. Secure the account: an **MFA** on the root user. Day to day, use an
   admin user through IAM Identity Center, never root.
2. **AWS Budgets:** alerts at the chosen levels.
3. **Bedrock model access:** request access to the candidate models in
   the chosen region.
4. **A laptop credential for evaluation:** an Identity Center (SSO)
   profile, or a least-privilege IAM user allowed only `bedrock:Converse*`
   / `InvokeModel*` on the chosen models. Use `aws configure sso`. Nothing
   goes in the repo.

**Phase 1 under way (2026-10-06).** The steps are written out in
`docs/running-on-bedrock.md`.
- **Reached:** step 3, model access. Steps 1 and 2 (account, MFA,
  Identity Center user, budget alerts) came before it.
- **Found: the Bedrock console has no "Model access" page any more,** so
  step 3 as written couldn't be done. Models are understood to be
  enabled on the account's first call to them, with a one-time use-case
  form for Anthropic models only. That's from general knowledge of the
  change, not checked against this account: the first real run confirms
  it.
- **Sign-in done (2026-10-06):** AWS CLI 2.37.10, profile `battle-test`
  (account 548108386131, `AdministratorAccess`, `us-west-2`).
  `aws configure sso` signed in but stopped before saving a profile, so
  `~/.aws/config` was written by hand. It holds no secrets.
- **Model IDs filled in** in `config.bedrock.toml`, from
  `list-foundation-models` and `list-inference-profiles`. Qwen3 32B,
  Qwen3 235B and DeepSeek v3.2 run on demand in `us-west-2`. **Llama 3.3
  70B and Claude Sonnet 5 are only offered through a `us.` inference
  profile,** which may process a call in any US region. That weakens
  "processing stays in a known region" for those two: one more point for
  the lawyer.
- **Prices, re-checked 2026-10-06, partly:** Qwen3 32B, Qwen3 235B and
  DeepSeek v3.2 match the config. The pricing page couldn't be read
  reliably for Llama 3.3 70B (one reading gave $0.40 / $0.60, against
  $0.99 / $1.32 in the config) or Claude Sonnet 5 (not found). Check
  those two by eye before trusting their cost estimates.
- **First real call tried (2026-10-06), and blocked by AWS:** `Error
  002: Access to Bedrock models is not allowed for this account`.
  - It's account-wide: Qwen3 32B, DeepSeek v3.2 and Llama 3.3 70B all
    return it, from our client and from the AWS CLI alike.
  - It isn't the sign-in, the model IDs or per-model access:
    `get-foundation-model-availability` reports the models as authorised
    and available, and the quotas are normal.
  - Nothing was billed: no call got as far as a model.
  - **Next, and it needs you:** an AWS Support case (Account and billing)
    asking for Bedrock access on account 548108386131 in `us-west-2`.
  - Claude Sonnet 5 also shows its agreement as not available, so the
    Anthropic use-case form is still to submit.
  - The client now explains this error instead of suggesting a
    temperature setting.
- **A payment method didn't lift it (2026-10-14).** No card had been on
  the account. One was added as the root user (the Identity Center
  sign-in can't open the billing pages). Straight afterwards, the
  research-only check on Qwen3 32B still failed on its first call with
  the same Error 002, so nothing was billed. Card verification may take
  a while to reach Bedrock, so one retry later is worth it. If that
  fails too, the AWS Support case is the next step.
- **Retried later the same day: still Error 002, nothing billed.**
  Checked from our side (free calls), all normal:
  - Sign-in: `AdministratorAccess` through Identity Center.
  - The account is the management account of its own organization
    (`o-gbo39h8te3`, all features), so no service control policy
    applies to it.
  - No AWS Budgets actions exist, so the $10 / $25 alerts can't be
    blocking anything.
  - `get-foundation-model-availability` for Qwen3 32B in `us-west-2`:
    agreement AVAILABLE, AUTHORIZED, entitlement AVAILABLE, region
    AVAILABLE.
  - `freetier get-account-plan-state` returns "Missing data for
    account", which suggests it isn't on AWS's newer free account plan.
    That's not certain.
  - **Conclusion:** the block is a flag on AWS's side, outside anything
    the account's settings control. Only AWS Support can lift it.
- **AWS Support case opened 2026-10-14** (Account and billing), asking
  for Bedrock model invocation on account 548108386131, with the
  checks above. Waiting for AWS's reply. When it comes: `aws sso login
  --profile battle-test`, then the $0.002 research-only check.
- **Other region tried (2026-10-15): also blocked.** One AWS CLI
  `converse` call to Qwen3 32B (on demand) in `us-east-1` returned the
  same Error 002, so nothing was billed. The block covers the account,
  not one region. Worth adding to the Support case if AWS asks.
- **Anthropic use-case form submitted 2026-10-15** (for Claude Sonnet
  5). `get-use-case-for-model-access` returns it: company smark_iq,
  industry Legal, 1 user, use case "a legal adversarial system to test
  legal complaints against theoretical responses". **It isn't the cause
  of Error 002:** the block started on 2026-10-06, nine days earlier.
- **Decided 2026-10-15: no Ollama Cloud stopgap.** Wait for Bedrock
  rather than split testing across two providers, which could cause
  problems later. The lookup below is kept for reference only. Its one
  lasting finding: **gpt-oss 120B is on Bedrock on demand in
  `us-west-2` (`openai.gpt-oss-120b-1:0`) and is very cheap**. Added
  to the candidates in Phase 2 below.
- **Stopgap while blocked: a bigger checker on Ollama Cloud (looked up
  2026-10-15, not used).** The server's or laptop's Ollama passes
  the call on to Ollama's servers, so no local GPU or disk is needed and
  `evaluate --checker <cloud tag>` runs it with no code or config change.
  Sample cases only.
  - **Pricing** (ollama.com/pricing): plans are Free ($0, starter
    credits, 1 request at a time), Pro ($20/month, $60 credits, 3 at a
    time) and Max ($100/month). Use is billed per million tokens, at half
    price outside 12:00–18:00 UTC on weekdays and all weekend. The page
    doesn't say which models the Free plan covers.
  - **Data:** "Prompt or response data is never logged or trained on."
    Hosted mainly in the US, with partners held to zero data retention.
  - **No Qwen3 235B or DeepSeek v3.2** (Ollama retired v3.2 in July
    2026). Models also offered on demand on Bedrock in `us-west-2`, so a
    result carries over:

    | Model | Ollama $/M in / out | Checker cost, 9 runs (est.) |
    |---|---|---|
    | gpt-oss 120B | 0.15 / 0.60 | ~$0.07 |
    | Nemotron 3 Super 120B | 0.015 / 0.60 | ~$0.02 |
    | Mistral Large 3 (675B) | 0.50 / 1.50 | ~$0.20 |

    Estimates use the 14B checker's ~36,000 in / 3,000 out a case;
    allow 2–3× for longer output. Ollama-only options include DeepSeek
    v4 Pro ($1.32 / $3.96, ~$0.55) and GLM 5.3 (~$0.57; on Bedrock only
    through an inference profile).
  - **Caveat:** a host can serve the same model with different settings
    or quantisation, so a Bedrock run still confirms the result.
- **Two providers in one run: built 2026-10-14, at no cost.** The
  server's 14B drafts through the tunnel and a Bedrock model is the
  checker (`config.tunnel-bedrock.toml`, `[models] checker_provider =
  "bedrock"`, default checker `qwen3-235b`).
  - `models.make_client()` returns a `SplitClient`, which sends the
    checker model's calls to Bedrock and the rest to Ollama, so the
    pipeline, evaluation and web app are unchanged.
  - The cost estimate counts the Bedrock checker only.
  - Tested with stand-ins (4 new tests, 271 in all). **No Bedrock call
    was made:** the account is still blocked.
  - **Estimated cost of the first comparison, 3 runs per case:** the
    checker used about 36,000 tokens in and 3,000 out per case on the
    14B (both checks). Tokenizers differ, so treat these as rough:
    - Qwen3 235B: about $0.03 a case, **about $0.25 for 9 runs**.
    - Claude Sonnet 5: about $0.11 a case, **about $1.00 for 9 runs**,
      once the Anthropic use-case form is done.
- **Bedrock for both drafting and checking (decided 2026-10-14):** the
  end state is everything on Bedrock. Checker-only runs come first only
  to show what each change adds, and because drafting is about 80% of a
  case's tokens. `config.bedrock.toml` now names a checker
  (`qwen3-235b`), so all-Bedrock runs include both checks.
  - **Planned order once the block lifts:**
    1. The $0.002 research-only check.
    2. Qwen3 235B: a checker-only run (`config.tunnel-bedrock.toml`,
       about $0.25 for 9 runs) and an all-Bedrock run
       (`config.bedrock.toml --model qwen3-235b`, about $0.70).
    3. gpt-oss 120B (added 2026-10-15), the same two runs:
       checker-only (`config.tunnel-bedrock.toml --checker
       gpt-oss-120b`, about $0.07 for 9 runs) and all-Bedrock
       (`config.bedrock.toml --model gpt-oss-120b`, about $0.20).
       Needs a `[bedrock.models.gpt-oss-120b]` entry first, in both
       `config.bedrock.toml` and `config.tunnel-bedrock.toml` (each
       has its own model list).
    4. Claude Sonnet 5 for everything (about $2.90, or more if its
       tokenizer counts about 30% higher). Its use-case form went in on
       2026-10-15.
  - These estimates come from the 14B's token use, about 115,000 in and
    6,500 out a case with both checks. Larger models usually write
    longer drafts, so expect more output.
- **Found in the code, and fixed:** `evaluate --research-only` didn't
  record token usage, so on Bedrock it would have reported no tokens or
  cost. It now prints a "Model usage" line under the total, as full runs
  do.
- **Server:** on `9e43916`, one commit behind GitHub (`d530527`, the
  Bedrock groundwork). Nothing there needs it yet.

### Phase 2 — Code, then evaluate from the laptop (hybrid B)
1. Build the code changes above, with tests.
2. Run the evaluation set on candidate models from the laptop, e.g.
   Qwen3 235B, Llama 3.3 70B, DeepSeek v3.2, gpt-oss 120B and Claude
   Sonnet 5, with
   `--label bedrock-<model>`.
   - It should cost a few dollars in total (3 cases × ~$0.01–0.28 per run
     × a few models).
   - It reports quality **and** measured cost per case.
3. Pick the drafting and checking models, and record the choice in
   `plans/plan.md`.

**Step 1 done (2026-10-05), on the laptop, at no cost.** No AWS call
has been made yet: the client was tested against a stand-in.
- **Built:**
  - `battle_test/bedrock_client.py`: Converse streaming, token usage from
    each call, at most 4 attempts per call.
  - `provider = "ollama" | "bedrock"` in `[models]`, and
    `config.bedrock.toml` with the five candidate models and their prices.
  - Per-case token totals and estimated cost in the Markdown report, the
    evaluation summary and `results.json`.
  - `evaluate --repeats N` and `--model NAME`, and a total row.
  - Research and selection replies are now read leniently (a code fence
    or a sentence around the JSON is ignored), for every provider.
- **Decided 2026-10-05:**
  - Budget alerts at **$10 and $25** for this phase (the $40 / $75 in
    Phase 0 were sized for production).
  - Compare **Qwen3 32B, Qwen3 235B, Llama 3.3 70B, DeepSeek v3.2 and
    Claude Sonnet 5**, with **3 runs per case**.
  - **Added 2026-10-15: gpt-oss 120B** (`openai.gpt-oss-120b-1:0`, on
    demand in `us-west-2`). It's the cheapest candidate (about $0.013 a
    case with the check) and a 120B checker is the size the 3a notes
    call for. Not yet in `config.bedrock.toml`.
  - Region `us-west-2`.
  - Where to embed for semantic search (server GPU or Bedrock) waits
    until that step.
- **Not built yet:** the cost on the web results page, the `checker` role
  for 3a, and `config.aws.toml`. They wait until the web app runs on
  Bedrock.
- **`[web] workers` built (2026-10-08).** It sets how many cases run at
  once.
  - `config.bedrock.toml` has `workers = 4`, one more than the per-user
    cap of 3, so one user can't hold every place. The Ollama configs
    stay at 1, and a config that sets more than 1 on Ollama is refused.
  - Checked with the demo model on the real pipeline and law index: six
    two-round cases, four running at once, all finished correctly.
  - **Not checked against Bedrock,** which is still blocked. Whether four
    cases at once meets any throttling there is unknown.
  - Found and fixed on the way: two cases submitted in the same second
    showed the same queue position.
- **Found:**
  - botocore's `max_attempts` counts retries after the first try, so 4
    meant 5 calls. The client uses `total_max_attempts` instead.
  - Recent Claude models reject a `temperature`, so each model has a
    `temperature = false` switch.
  - Model IDs are left blank in `config.bedrock.toml`. They have to come
    from the Bedrock console once the account has access.
  - Ollama now reports token counts too, so local runs show usage, with
    no cost because no price is set.
- **Checked on the real local model** (laptop `qwen2.5:7b`, Utah case,
  1 round): the run completed through the new provider switch and
  reported **7 calls, 34,837 tokens in, 5,301 out**.
  - **The per-case token estimate above looks low.** That's one round.
    The 39k-in estimate was for two, counted as characters ÷ 4. A
    two-round case is likely nearer 45–50k in, so expect costs roughly a
    fifth to a quarter higher than the tables. Tokenizers differ by model,
    so Bedrock's own counts will settle it.
  - Score, for the record: core cited 1/4, off-topic 3. Earlier two-round
    runs of this case scored 2/4 and 1. It's a single run, so this is
    within the noise, and nothing in this change alters what Ollama is
    asked or how its replies are read.
- **Still unmeasured:** real Bedrock token counts and cost, whether reasoning
  models bill their thinking as output, and how each model handles the
  JSON requests. The first real run answers all three for a few cents.
- **Next, and it needs you:** the AWS account, model access, the model
  IDs, and `aws sso login` (Phase 1).

### Phase 3 — Provision the AWS server
1. **EC2 `t4g.medium`:**
   - Amazon Linux 2023 or Ubuntu, arm64.
   - A 30 GB encrypted gp3 disk.
   - **A security group with no inbound rules.** The tunnel is outbound,
     and admin goes through **SSM Session Manager**, so there's no SSH
     port.
2. **An IAM role** on the instance, allowed to call only the chosen
   Bedrock models, plus the SSM managed policy.
3. Install Docker. Clone the repo with a new read-only deploy key.
   **Build the image there.** It's Linux, so none of the Windows
   credential-manager limits apply, and builds work remotely.
4. Build the law index (`corpus build`, ~1 min), then validate it
   (`evaluate --validate`).
5. Create accounts over the SSM session (interactive, so you do this).
   Alternatively, copy `users.sqlite` from the smark_iq server so existing
   accounts carry over.

### Phase 4 — Cut over (no DNS change)
1. Put the **existing** `battle-test` tunnel token in the EC2 `.env`, and
   start `docker compose up -d` there.
   - Cloudflare treats it as a second replica of the same tunnel, so both
     servers briefly serve the site.
2. Check the EC2 copy works: health, sign-in, and one fictional case end
   to end on the chosen Bedrock models.
3. **Stop the connector on the smark_iq server** (`docker compose stop
   cloudflared`). All traffic now goes to AWS. The route and DNS never
   changed.
4. Run the Phase 5 checks from the server migration plan (headers,
   cookies, refusals, live progress), and test from a phone on mobile
   data.

### Phase 5 — Decommission on the smark_iq server
1. After a few days of AWS running cleanly: `docker compose stop` for
   battle_test on the smark_iq server, then `docker compose down`, which
   is deliberate removal this time.
2. Keep the volumes for a week as a fallback, then remove them:
   - `docker volume rm battle_test_battle-cases battle_test_battle-law`
   - Accounts were already copied. Cases are fictional, or expire anyway.
3. The server's GPU is now Open WebUI's alone. Remove `keep_alive` from
   anything still pointing there.
4. Update `plans/plan.md` (start/stop guide, Status) and the README for
   the new home.

## Rollback

- **During cutover:** start the connector on the smark_iq server again
  (`docker compose up -d` there), and stop it on EC2. That takes seconds,
  with no DNS change.
- **After decommissioning:** redeploy on the smark_iq server from git,
  using the same deployment files. The law index rebuilds in a minute,
  and accounts are restored from the backup.

## Open questions

- **The lawyer:** processing on Bedrock, storage on AWS (A) or not (B),
  and Cloudflare in transit (an existing question).
- **Models:** which drafting and checking models, chosen from the Phase 2
  evaluation.
- **Users:** expected count and usage. Over 50 changes the Cloudflare
  Access math.
- **Budget:** alert levels, and whether to cap cases per user.
- **Prices:** check them again before committing, since they change. The
  Llama 3.3 70B price rose between 2026-09 and 2026-10.
