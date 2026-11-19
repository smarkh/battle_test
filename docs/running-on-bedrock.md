# Running battle_test on Amazon Bedrock

Bedrock runs the models on AWS instead of on your own GPU. Only the model
calls move. The law index, the search, the citation check and the reports
still run on your machine.

> **Fictional cases only.** Every prompt carries the case text to AWS. Use
> the sample cases in `examples/` until the lawyer advising the project
> has agreed to real case material going there.

> **Status (2026-10-06): set up, but AWS is blocking Bedrock for the
> account.** Sign-in works (profile `battle-test`) and the model IDs are
> filled in. The first real call was refused with `Error 002: Access to
> Bedrock models is not allowed for this account`, on every model. It
> needs an AWS Support case: see Troubleshooting. No model has answered
> yet, so the commands under "Start" are still untested, and nothing has
> been billed. Expect to adjust this page after the first real run.

Commands are for PowerShell on the laptop, from the project folder.

## Sign-in links

**AWS account ID: `548108386131`.** It's also shown in the console's
top-right menu after signing in.

| Sign in as | Link | Use it for |
|---|---|---|
| `smarkh` (Identity Center, day to day) | https://d-9267cc86b3.awsapps.com/start | Everything except billing. The CLI's `aws sso login --profile battle-test` uses it too. |
| Root user | https://console.aws.amazon.com/ → choose **Root user** | Billing pages (payment methods, currency) and account settings only. Use the account's email, password and MFA. The console's top-right corner shows `smarkh` for both sign-ins, because that's also the account name. To tell which one you're in, open IAM → **Security credentials**: root shows a "Root user" badge. |

## What it costs

- **Nothing while idle.** Bedrock bills per token, only when a model is
  called. There's nothing to shut down to stop paying.
- **Roughly $0.01 to $0.25 per case,** depending on the model.
- **No server is involved.** The ~$30 a month AWS server in
  `plans/aws-bedrock-plan.md` is a later step and doesn't exist yet.

Every run reports its own token usage and estimated cost. See "See what a
run cost" below.

## One-time setup

These need you personally. None of it goes in a file.

Console menus move, so treat the click paths as approximate.

1. **Create the AWS account and a day-to-day user.**
   1. Create the account at `aws.amazon.com`, on the free Basic support
      plan.
   2. As root, put MFA on the root user (account menu → Security
      credentials → Assign MFA device).
   3. Enable **IAM Identity Center**, with the console region set to
      `us-west-2`. Keep the default **AWS-owned key** for encryption and
      don't add a second Region. A customer-managed key costs $1/month
      for each Region (see the KMS note of 2026-11-01 in
      `plans/aws-bedrock-plan.md`).
   4. In Identity Center, add a user for yourself, create a permission
      set from the predefined `AdministratorAccess`, and assign both to
      the account (AWS accounts → Assign users).
   5. Note the **AWS access portal URL** (like
      `https://d-xxxxxxxxxx.awsapps.com/start`) and the Identity Center
      region. Neither is a secret.
   6. Sign out of root. Sign in through the portal URL from now on.
2. **Set budget alerts.** Billing and Cost Management → Budgets → Create
   budget → Customize → Cost budget, monthly, $25. Add two alerts on
   actual cost, by email: 40% ($10) and 100% ($25). Alerts lag by hours
   and don't stop spending.
3. **Check the models are offered.** There is no "Model access" page to
   request them on any more (seen 2026-10-06). Bedrock enables a model
   the first time the account calls it. *This is unconfirmed until the
   first real run.*
   1. In the Bedrock console, in `us-west-2`, open **Model catalog** and
      check each model in `config.bedrock.toml` has a page: Qwen3 32B,
      Qwen3 235B, Llama 3.3 70B, DeepSeek v3.2 and Claude Sonnet 5.
   2. **Claude only:** Anthropic models need use-case details submitted
      once per account. Look for a "Submit use case details" banner on
      the Claude Sonnet 5 page, or choose "Open in playground", which
      brings the form up. No prompt needs sending.
   3. The first call to a model may fail with an access error and work
      a couple of minutes later.
4. **Install the AWS CLI** (`winget install Amazon.AWSCLI`, then open a
   new terminal), and set up sign-in:
   ```powershell
   aws configure sso
   ```
   Give it the portal URL and Identity Center region from step 1, accept
   the default scopes, approve in the browser, then choose `us-west-2`,
   `json`, and a profile name such as `battle-test`. Check it with
   `aws sts get-caller-identity --profile battle-test`.
   - Run it in an ordinary PowerShell window. It asks questions, so it
     fails from inside Claude Code ("expecting a Windows console").
   - On 2026-10-06 it signed in but saved no profile, so
     `~/.aws/config` was written by hand: a `[profile battle-test]`
     block (`sso_session`, `sso_account_id`, `sso_role_name`, `region`,
     `output`) and a `[sso-session battle-test]` block (`sso_start_url`,
     `sso_region`, `sso_registration_scopes`). It holds no secrets.
5. **Fill in the model IDs.** *Done 2026-10-06 for the five models.* For
   a new model, copy its ID from the Bedrock console (Model catalog, or
   Cross-region inference for an inference profile ID). Some models
   (Llama 3.3 70B and Claude Sonnet 5 here) are only offered through a
   `us.` inference profile, which may process a call in any US region.
   These commands list them:
   ```powershell
   aws bedrock list-foundation-models --region us-west-2 --query "modelSummaries[].modelId"
   aws bedrock list-inference-profiles --region us-west-2 --query "inferenceProfileSummaries[].inferenceProfileId"
   ```
6. **Check the prices** in `config.bedrock.toml` against the Bedrock
   pricing page. They drive the cost estimate. Qwen3 32B, Qwen3 235B and
   DeepSeek v3.2 were confirmed on 2026-10-06. Llama 3.3 70B and Claude
   Sonnet 5 still date from 2026-10-03 and need checking by eye.
7. **The profile** is set under `[bedrock]` in `config.bedrock.toml`
   (`profile = "battle-test"`). Change it if yours has another name. A
   profile name isn't a secret.
8. **Install the Python packages** if you haven't since `boto3` was added:
   ```powershell
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

## Before each session

Sign in. The sign-in lasts a few hours:
```powershell
aws sso login --profile battle-test
aws sts get-caller-identity --profile battle-test
```
The second command should print your account.

Also check the law index exists (`data\law.sqlite`), as for any local run.

## Start

Nothing uses Bedrock unless you pass `--config config.bedrock.toml`.
Without it, every command runs on Ollama as before.

### One case from the command line

The cheapest real test, about a cent on Qwen3 32B:
```powershell
.venv\Scripts\python -m battle_test --config config.bedrock.toml --state UT --facts examples\utah_roofing_facts.md
```
It uses the models named under `[models]` in `config.bedrock.toml`. The
result is saved in `output\`.

### The evaluation

```powershell
.venv\Scripts\python -m battle_test.evaluate --config config.bedrock.toml --model qwen3-32b --label bedrock-qwen3-32b
.venv\Scripts\python -m battle_test.evaluate --config config.bedrock.toml --model qwen3-235b --label bedrock-qwen3-235b --repeats 3
.venv\Scripts\python -m battle_test.evaluate --config config.bedrock.toml --model qwen3-32b --research-only
```

| Option | Meaning |
|---|---|
| `--model NAME` | One of the names under `[bedrock.models]`, used for both roles |
| `--checker NAME` | The checker model, also from `[bedrock.models]`. The config's is `qwen3-235b`, so both checks (the fit check and "does it say that?") run unless you pass `--no-fit-check` or remove `checker` |
| `--repeats 3` | Run each case three times. One run is too noisy to compare models |
| `--case utah_roofing` | Only that case |
| `--rounds 1` | Skip the reply, which is the longest call |
| `--research-only` | Three small calls, to check the search step for a cent or less |

### Only the checker on Bedrock

`config.tunnel-bedrock.toml` drafts with the server's `qwen2.5:14b`
through the SSH tunnel, and sends only the checker's calls to Bedrock: the
fit check before drafting and the "does it say that?" check after. The
checker is where model judgement matters most, so this is the cheapest way
to see what a large model adds.

```powershell
ssh -N -L 11435:127.0.0.1:11434 smark@smark-iq        # in its own window, left open
.venv\Scripts\python -m battle_test.evaluate --config config.tunnel-bedrock.toml --label bedrock-checker-qwen3-235b
.venv\Scripts\python -m battle_test.evaluate --config config.tunnel-bedrock.toml --checker claude-sonnet-5 --label bedrock-checker-sonnet
```

- `[models] checker_provider = "bedrock"` is what splits the two. Calls go
  to a provider by model name, so the checker can't also be a drafting
  model.
- `--checker NAME` picks another of its `[bedrock.models]`.
- The cost shown counts only the Bedrock checker: the server's model
  costs nothing per call.
- The checker reads the case facts and passages of the drafts, so this
  sends case material to AWS too. Fictional cases only.

### Long evaluations on the server (set up 2026-11-05, first run 2026-11-18: works)

Run long Bedrock evaluations with semantic search on the server. The
laptop ran out of memory on 2026-11-05 doing it locally: the law index,
the vectors and the embedding model all load on the machine running the
evaluation. The models are still on Bedrock and billed the same. The server
does only the search, and the laptop can sleep once the run has started.

`config.server-bedrock.toml` is the server's config with
`provider = "bedrock"` and gpt-oss 120B by default.
`scripts\server-bedrock-eval.ps1` copies that config and short-lived AWS
credentials from the laptop's sign-in to `C:\Users\smark\battle-eval` on
the server. It then starts the run there in the background as the
container `battle-eval`. The container reads the credentials from that
folder, mounted read-only, so they're never on a command line or in
`docker inspect`. The server has no AWS CLI or sign-in of its own.

**(laptop)**
```powershell
aws sso login --profile battle-test-eval   # the Bedrock-only sign-in, set up below
.\scripts\server-bedrock-eval.ps1 -Label bedrock-gptoss120b-all-semantic -DryRun   # checks only
.\scripts\server-bedrock-eval.ps1 -Label bedrock-gptoss120b-all-semantic
```
`-Model`, `-Checker`, `-Repeats` (default 3) and `-Case NAME` (one or more)
work as the `evaluate` options do.

- **The run uses the server's built image,** so deploy first if the code
  changed (docs/running-on-server.md, "Update the code"). The config is
  copied fresh each time, so config changes don't need a deploy.
- **The credentials must last the whole run.** They're temporary and
  can't renew inside the container. The script stops if they have less
  than 3 hours left (`-MinHours`). If it stops even straight after
  `aws sso login`, the permission set's session duration is too short.
  Use the Bedrock-only permission set below, which lasts 4 hours. Don't
  just raise `AdministratorAccess` to 4 hours: those credentials could do
  anything in the account for that long, from the server.
- **Signed in again but still short of time?** `aws sso login` renews the
  sign-in, but the CLI keeps reusing the role credentials it fetched
  earlier (in `%USERPROFILE%\.aws\cli\cache\`) until they expire. Delete
  them and the next call fetches fresh ones, with the full 4 hours:
  `Remove-Item $HOME\.aws\cli\cache\*.json` (found 2026-11-18).
- Follow it, as the Ollama Cloud runs (docs/running-on-server.md):
  `ssh smark@smark-iq "docker logs --tail 5 battle-eval"`.
- **When it's finished:** copy the results to the laptop and delete them
  from the server (docs/running-on-server.md, "Copy the results to the
  laptop"). Then remove the container and the credentials:
  ```powershell
  ssh smark@smark-iq "docker rm battle-eval; Remove-Item -Recurse -Force C:\Users\smark\battle-eval"
  ```
- **To stop it early:** `ssh smark@smark-iq "docker stop battle-eval"`,
  then clean up as above.
- Fictional cases only. Each case's facts and drafts go to AWS.

#### The Bedrock-only permission set (one-time setup)

Server runs should use a sign-in that can only call models. If the
credentials copied to the server leaked, they could run up a Bedrock bill
for a few hours, but they couldn't read, change or delete anything else in
the account. `AdministratorAccess` stays for day-to-day work on the laptop.

Identity Center is free, and so are the steps and checks below. Console
menus move, so treat the click paths as approximate.

1. **Sign in** through the portal
   (https://d-9267cc86b3.awsapps.com/start) as `smarkh`. Open the
   console for account 548108386131 with `AdministratorAccess`. Set the
   console region to **US West (Oregon) `us-west-2`**, where Identity
   Center is.
2. **Create the permission set.** IAM Identity Center → Permission sets
   → **Create permission set**.
   1. Choose **Custom permission set** → Next.
   2. Leave AWS managed and customer managed policies empty. Expand
      **Inline policy** and paste this in place of what's there:
      ```json
      {
        "Version": "2012-10-17",
        "Statement": [
          {
            "Sid": "CallModelsOnly",
            "Effect": "Allow",
            "Action": [
              "bedrock:InvokeModel",
              "bedrock:InvokeModelWithResponseStream"
            ],
            "Resource": [
              "arn:aws:bedrock:us-west-2::foundation-model/*",
              "arn:aws:bedrock:us-west-2:548108386131:inference-profile/us.*",
              "arn:aws:bedrock:us-*::foundation-model/*"
            ]
          }
        ]
      }
      ```
      - battle_test calls `converse_stream`, which needs both actions.
      - The first resource covers models called directly in Oregon,
        such as gpt-oss 120B and Qwen.
      - The other two cover `us.` inference profiles, such as Llama 3.3
        70B and Claude Sonnet 5. A call through one needs the profile
        and the model in whichever US region it lands in.
      - Nothing outside the US, and nothing but model calls.
   3. Next. **Name:** `BedrockInvokeOnly`. **Description:** "Model calls
      only, for battle_test evaluations on the server". **Session
      duration: 4 hours.** Leave the relay state empty → Next →
      **Create**.
3. **Assign it.** IAM Identity Center → AWS accounts → tick
   548108386131 → **Assign users or groups** → Users → tick `smarkh` →
   Next → tick `BedrockInvokeOnly` → Next → **Submit**. Wait until it
   says the permission set is provisioned. That usually takes under a
   minute.
4. **Add a laptop profile.** Add this to `C:\Users\smark\.aws\config`.
   It holds no secrets. It shares the existing `battle-test` sign-in
   session, so one `aws sso login` covers both profiles.
   ```ini
   [profile battle-test-eval]
   sso_session = battle-test
   sso_account_id = 548108386131
   sso_role_name = BedrockInvokeOnly
   region = us-west-2
   output = json
   ```
5. **Check it.** All three commands are free:
   ```powershell
   aws sso login --profile battle-test-eval
   aws sts get-caller-identity --profile battle-test-eval   # the ARN should end in AWSReservedSSO_BedrockInvokeOnly_.../smarkh
   aws s3 ls --profile battle-test-eval                     # should fail with AccessDenied
   ```
   Then a dry run, which should report about 4 hours left:
   ```powershell
   .\scripts\server-bedrock-eval.ps1 -Profile battle-test-eval -Label test -DryRun
   ```
6. **The first paid check (about $0.05):** one run of one case, to prove
   the server end works before the full run:
   ```powershell
   .\scripts\server-bedrock-eval.ps1 -Profile battle-test-eval -Label server-bedrock-check -Case alaska_boiler -Repeats 1
   ```
   Clean up afterwards as above. The script uses this profile by
   default (`-Profile` picks another).

**Limits of this permission set:**
- **It can't switch on a new model.** A model sold through AWS
  Marketplace, such as Claude, is subscribed on its first call. That call
  needs `aws-marketplace` permissions that this set doesn't have. Make a
  new model's first call with `battle-test` (`AdministratorAccess`) from
  the laptop. After that, this set can call it.
- **It doesn't cap spending.** Budget alerts still apply but lag by hours.
  If the credentials might have leaked, don't wait for them to expire:
  delete the server's `battle-eval` folder. Then in the IAM console (not
  Identity Center) → Roles, open `AWSReservedSSO_BedrockInvokeOnly_...`
  → **Revoke sessions** tab → **Revoke active sessions**. That stops
  credentials already handed out, which removing the assignment alone
  doesn't do.

### The web UI

The web UI can run on Bedrock on the laptop. It keeps its own accounts and
cases, in `cases\web-bedrock\`, separate from the Ollama ones.

Create an account for it once (it asks for a password):
```powershell
.venv\Scripts\python -m battle_test.web.users --config config.bedrock.toml add NAME --admin --password --plan unlimited
```

Then start it and open **http://127.0.0.1:8002**:
```powershell
.venv\Scripts\python -m battle_test.web --config config.bedrock.toml
```

- It uses port 8002, so it can run alongside the Ollama one on 8000.
- Every case started here is sent to AWS and billed. Fictional cases only.
- Up to four cases run at once (`workers = 4` under `[web]`), and the
  rest wait in the queue. Each user can still have only three waiting or
  running, so one person can't take every place. Running several at once
  costs the same per case: it only spends it sooner.
- The results page doesn't show the cost yet. The Markdown download does.

## Stop

- **A command-line run or an evaluation:** press **Ctrl+C**. Nothing is
  saved for the case that was running. Evaluation cases that had already
  finished keep their files, but no summary is written.
- **The web UI:** press **Ctrl+C** in its window. A case that was running
  is marked failed ("interrupted") at the next start.
- **Billing stops when the calls stop.** An interrupted call is still
  billed for what it had read and written.
- **End the AWS sign-in,** so nothing on this machine can call Bedrock:
  ```powershell
  aws sso logout
  ```

**To go back to Ollama,** leave off `--config config.bedrock.toml`.
Nothing needs switching back.

**If spending looks wrong:** stop the run, run `aws sso logout`, and look
at Billing in the AWS console. Removing model access in the Bedrock
console blocks all further calls.

## See what a run cost

- **Per case:** the "Model usage" line near the top of each Markdown
  result: calls, tokens in and out, and estimated cost.
- **Per evaluation:** the Cost column and total row in `summary.md`, and
  the usage line under the table.
- **Per `--research-only` check:** the "Model usage" line under the total
  in `research.md`.
- **`?` in place of a cost** means the config has no price for a model
  that was used. A partial total is never shown.
- **The estimate is tokens × the prices in the config.** The real charge
  is in the AWS Billing console, which lags by some hours.

## Change or add a model

Each model is a block in `config.bedrock.toml`:

```toml
[bedrock.models.qwen3-32b]
id = "qwen.qwen3-32b-v1:0" # from the Bedrock console
input_per_million = 0.15   # US dollars
output_per_million = 1.20
```

- The block's name (`qwen3-32b`) is what `--model` and `[models]` use, and
  what reports show.
- `max_tokens = N` sets a lower output limit for one model.
- `temperature = false` is for models that reject a temperature setting,
  as recent Claude models do.
- To use different models for the two sides, set `plaintiff` and
  `defendant` under `[models]` and leave off `--model`.

## What isn't set up

- **Bedrock on the server or on AWS itself.** The public site at
  `battle.smarkiq.us` still runs on the server's own GPU. Moving it is
  Phase 3 onwards in `plans/aws-bedrock-plan.md`.
- **Cost on the web results page.**

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `bedrock.models.NAME.id is empty` | Fill in that model's ID in `config.bedrock.toml` (setup step 5). No call was made. |
| `No usable AWS credentials`, or the sign-in has expired | Run `aws sso login`. Set `profile` under `[bedrock]` if you use a named profile. |
| `aws sso login` fails with a KMS or encryption error | Identity Center's KMS key was scheduled for deletion on 2026-11-01. Sign in as root and cancel the deletion (see the KMS note of 2026-11-01 in `plans/aws-bedrock-plan.md`). |
| `This AWS account or role isn't allowed to use that model` | On a model's first call, wait a couple of minutes and run again. For Claude, submit the use-case form (setup step 3). |
| `Access to Bedrock models is not allowed for this account` (Error 002) | AWS is blocking Bedrock for the whole account, on every model. Seen on the first real call, 2026-10-06. Open an AWS Support case (Account and billing, free) asking for Bedrock access. Nothing in the config fixes it. |
| `Bedrock doesn't know that model ID in this region` | Check the model's `id` and `bedrock.region`. Some models need an inference profile ID, not the plain model ID. |
| `Bedrock rejected the request` | The model may not accept a temperature (set `temperature = false`) or may need a smaller `max_tokens`. |
| `Still throttled after 4 attempts` | New accounts have low limits. Wait and run again, or ask AWS for a higher quota. |
| A warning that a reply was "cut short" | The model hit its output limit. Raise `max_tokens` under `[bedrock]`. |
| `returned no text` | The model refused or was filtered. The stop reason is in the message. |
| `The Bedrock provider needs boto3` | Run `pip install -r requirements.txt`. |
| Few authorities found, on a model that worked before | The model may be wrapping its JSON replies in a way the reader can't use. Compare "Core found by search" with an Ollama run. |

A failed call is retried at most three times, with a pause between tries,
and then the run stops with one of the messages above.
