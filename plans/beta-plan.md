# Plan — The lawyers' beta

**Status: being prepared (2026-11-20).** A contact inside a group of about
20 lawyers will present Battle Test to them. 5 to 10 are expected to try it.
This file holds the decisions and the expected cost. How to run the beta
day to day (adding a tester, extending, ending) is in `docs/beta.md`.

## Decisions (2026-11-20)

| Question | Decision |
|---|---|
| Models | **gpt-oss 120B on Amazon Bedrock** for drafting and both checks, on the live site. It cited 58–65% of core law with 0 off-topic in the evaluations, against about 43% for the server's 14B. |
| Where the site runs | The home server, as now. Only the model calls go to AWS (Oregon, `us-west-2`). The AWS hosting question (`plans/aws-hosting-plan.md`) stays open. |
| Allowance per tester | **15 cases in total, for 60 days.** The plan `beta` in `config.server.toml`. The 60 days is each account's paid-through date. After it, testers can still sign in, read and download, but can't start cases. |
| Case material | **Fictional cases only** (decided 2026-11-06). The lawyer hasn't signed off on real case material going to AWS (`docs/data-handling-for-lawyer.md`). |
| Feedback and sign-ups | By email to **battle_test@smarkiq.us** (set up 2026-11-20: Cloudflare Email Routing forwards it to your Gmail). |
| Who can reach the site | **Cloudflare Access** in front of the whole site, including `/admin`, before any tester signs in. Email one-time PIN, allowed emails only. You set it up from `docs/running-on-server.md`, "Cloudflare Access". |
| AWS credentials for the live site | A **Bedrock API key**, Bedrock calls only, expiring after 90 days (longer than the beta). It's a file on the server, mounted into the container as a Docker secret. Never in git, the image, or `docker inspect`. `docs/running-on-server.md`, "Bedrock API key". |

## Expected cost

### Per case (measured, not estimated)

gpt-oss 120B on Bedrock, two rounds, both checks on:

| Source | Runs | Cost per case |
|---|---|---|
| Evaluation, 2026-11-05 (7 sample cases × 3) | 21 | $0.038 to $0.057, **mean $0.047** |
| Server check with semantic search, as the live site runs, 2026-11-18 | 1 | $0.047 |

A pasted or uploaded complaint that's much longer than the samples adds
input tokens. Even a doubled input stays under about $0.08 a case.

**Planning figure: $0.06 a case,** a little above the highest measured run.

### Per beta tester

| | Cases | Cost |
|---|---|---|
| A tester who uses every case | 15 | **about $0.70** (at the mean), **at most about $0.90** (at $0.06) |
| A tester who tries it a few times | 5 | about $0.25 |
| A tester who signs up and never runs a case | 0 | $0 |

The plan's limit is enforced in the app, so a tester can't go past 15. Runs
that fail aren't counted against them, but a failed run may still have
used some tokens (a few cents at most).

### For the whole beta

| Item | Cost |
|---|---|
| 5 testers using every case | $3.50 to $4.50 |
| 10 testers using every case | $7 to $9 |
| All 20 lawyers in the group, every case (the ceiling) | $14 to $18 |
| Before launch: one test case on the live site after the switch | about $0.05 |
| Before launch: the sample cases for real screenshots in the decks (D4), 4 to 7 cases | about $0.20 to $0.40 |
| Optional: the full semantic evaluation on the server (B6) | about $1 |
| Cloudflare Access | $0 (free up to 50 users) |
| AWS while idle, the API key, the IAM user | $0 |
| The home server | No change: it already runs. The GPU now runs only the search model. |

**Expected total for 5 to 10 testers: about $5 to $11, including the
launch checks.** Realistically less, since most testers won't use every
case. The AWS budget alerts at $10 and $25 (set 2026-10-05) still apply. If
the $10 alert fires during the beta, check `users usage` before anything
else.

### What isn't counted

- Your time: setting up accounts, answering questions, and reading
  feedback.
- A leaked API key. Someone holding it could run Bedrock calls on the
  account until it's revoked or expires. The key can call only Bedrock (and
  only gpt-oss 120B, once tightened as in the docs), and the budget alerts
  would show it. Revoke it at once if in doubt.
- Any move to a premium model. Claude Sonnet 5 for everything would be
  about $0.28 a case, about $4.20 per tester at 15 cases.

## Before the presentation, and before the first tester

In order. Ticked items are done.

1. [x] Code and config (2026-11-20): the `beta` plan, `users beta NAME...
   --days 60`, the live config on Bedrock with the API key file, tests.
2. [ ] **You:** commit and push. Send the beta deck and the presenter's
   brief to the contact only after steps 3 to 7 are done, or tell them
   that sign-ups start on a date you choose.
3. [ ] **You, in AWS:** create the Bedrock API key and tighten its
   permissions (`docs/running-on-server.md`, "Bedrock API key").
4. [ ] **You, on the server:** put the key in `secrets/bedrock_api_key`,
   then deploy. **Don't deploy this commit before the key file exists:**
   compose only warns about a missing secret file and starts anyway. The
   site comes up, but every case fails, and the app log says so ("WARNING:
   every case will fail…"). Checked 2026-11-20.
5. [ ] **One fictional case on the live site** (about $0.05, with your
   go-ahead): the Utah roofing sample. Check the result page, a Word and a
   PDF download, and the activity log.
6. [ ] **You, in Cloudflare:** Access in front of the site
   (`docs/running-on-server.md`, "Cloudflare Access"), with your own email
   allowed. Check from a private window and from a phone on mobile data
   (D3).
7. [ ] **Reliability (D5):** Tailscale "Run unattended", Windows Update
   active hours, and an outside uptime check on `/healthz`. Testers will
   notice a 13-hour outage.
8. [ ] **Fill the remaining placeholders** in the beta deck and the
   presenter's brief: your contact details, the feedback address, and the
   date sign-ups open.
9. [ ] Optional: the sample cases for real screenshots (D4), and the B6
   evaluation.

Then, for each lawyer who wants to try it, `docs/beta.md`, "Add a tester".

## What to measure during the beta

- **Cases per tester** (`users usage`), and how many signed up and never
  ran one.
- **Actual cost per case,** from each case's report, against $0.047.
- **Feedback,** grouped as in the beta deck: what was useful, what was
  wrong (especially a wrong or misstated statute), what was missing, which
  features they'd want, how many hours it saved, and what they'd pay.
- **Failures:** cases that failed, and why (`docker logs battle-test`).
