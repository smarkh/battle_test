# Running battle_test on Amazon Bedrock

Bedrock runs the models on AWS instead of on your own GPU. Only the model
calls move. The law index, the search, the citation check and the reports
still run on your machine.

> **Fictional cases only.** Every prompt carries the case text to AWS. Use
> the sample cases in `examples/` until the lawyer advising the project
> has agreed to real case material going there.

> **Status (2026-10-05): built, but never run against AWS.** The Bedrock
> client passes its tests against a stand-in. No AWS account existed yet,
> so every command below that reaches AWS is untested. Expect to adjust
> this page after the first real run.

Commands are for PowerShell on the laptop, from the project folder.

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

1. **Create the AWS account.** Put MFA on the root user, then create a
   day-to-day admin user through IAM Identity Center. Don't use root
   again.
2. **Set budget alerts** in AWS Budgets, at $10 and $25 a month.
3. **Request model access** in the Bedrock console, in `us-west-2`, for
   the models in `config.bedrock.toml`: Qwen3 32B, Qwen3 235B, Llama 3.3
   70B, DeepSeek v3.2 and Claude Sonnet 5. Some providers ask for a short
   use-case form first.
4. **Install the AWS CLI,** then set up sign-in:
   ```powershell
   aws configure sso
   ```
   Note the profile name it creates.
5. **Fill in the model IDs.** Each model in `config.bedrock.toml` has
   `id = ""`. Copy each ID from the Bedrock console (Model catalog, or
   Cross-region inference for an inference profile ID). These commands
   list them:
   ```powershell
   aws bedrock list-foundation-models --region us-west-2 --query "modelSummaries[].modelId"
   aws bedrock list-inference-profiles --region us-west-2 --query "inferenceProfileSummaries[].inferenceProfileId"
   ```
6. **Check the prices** in `config.bedrock.toml` against the Bedrock
   pricing page. They drive the cost estimate and date from 2026-10-03.
7. **If your profile isn't the default one,** set `profile = "NAME"` under
   `[bedrock]` in `config.bedrock.toml`. A profile name isn't a secret.
8. **Install the Python packages** if you haven't since `boto3` was added:
   ```powershell
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

## Before each session

Sign in. The sign-in lasts a few hours:
```powershell
aws sso login
aws sts get-caller-identity
```
The second command should print your account. Add `--profile NAME` to
both if you use a named profile.

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
| `--repeats 3` | Run each case three times. One run is too noisy to compare models |
| `--case utah_roofing` | Only that case |
| `--rounds 1` | Skip the reply, which is the longest call |
| `--research-only` | Three small calls, to check the search step for a cent or less |

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
- Cases still run one at a time.
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
- **`?` in place of a cost** means the config has no price for a model
  that was used. A partial total is never shown.
- **The estimate is tokens × the prices in the config.** The real charge
  is in the AWS Billing console, which lags by some hours.

## Change or add a model

Each model is a block in `config.bedrock.toml`:

```toml
[bedrock.models.qwen3-32b]
id = ""                    # from the Bedrock console
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
- **Several cases at once** in the web UI.
- **Cost on the web results page.**

## Troubleshooting

| Message | Cause and fix |
|---|---|
| `bedrock.models.NAME.id is empty` | Fill in that model's ID in `config.bedrock.toml` (setup step 5). No call was made. |
| `No usable AWS credentials`, or the sign-in has expired | Run `aws sso login`. Set `profile` under `[bedrock]` if you use a named profile. |
| `This AWS account or role isn't allowed to use that model` | Request access to the model in the Bedrock console (setup step 3). |
| `Bedrock doesn't know that model ID in this region` | Check the model's `id` and `bedrock.region`. Some models need an inference profile ID, not the plain model ID. |
| `Bedrock rejected the request` | The model may not accept a temperature (set `temperature = false`) or may need a smaller `max_tokens`. |
| `Still throttled after 4 attempts` | New accounts have low limits. Wait and run again, or ask AWS for a higher quota. |
| A warning that a reply was "cut short" | The model hit its output limit. Raise `max_tokens` under `[bedrock]`. |
| `returned no text` | The model refused or was filtered. The stop reason is in the message. |
| `The Bedrock provider needs boto3` | Run `pip install -r requirements.txt`. |
| Few authorities found, on a model that worked before | The model may be wrapping its JSON replies in a way the reader can't use. Compare "Core found by search" with an Ollama run. |

A failed call is retried at most three times, with a pause between tries,
and then the run stops with one of the messages above.
