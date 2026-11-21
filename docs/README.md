# battle_test documentation

Start here if you're new to the project.

battle_test stress-tests a lawsuit before it's used for real. One model
acts as the plaintiff's lawyer and drafts a complaint and a motion for
summary judgment. A second model acts as the defendant's lawyer and writes
the opposition. Optionally the plaintiff's model replies. Every statute and
rule the models cite is checked, in code, against a local copy of the law.
No winner is declared.

## What to read

| If you want to… | Read |
|---|---|
| Join the project: install everything, get access, and learn how we push and deploy | [onboarding.md](onboarding.md) |
| Understand what the system does and how a case flows through it | [how-it-works.md](how-it-works.md) |
| Find the file that does something | [code-guide.md](code-guide.md) |
| Set up and run it on your own machine | [running-locally.md](running-locally.md) |
| Start, stop, update or check the deployed copy | [running-on-server.md](running-on-server.md) |
| Run the models on Amazon Bedrock instead of your own GPU | [running-on-bedrock.md](running-on-bedrock.md) |
| Add a beta tester, look after the beta, and end it | [beta.md](beta.md) |
| Make a change: tests, evaluation, and the project's rules | [development.md](development.md) |
| See how case data is handled, or get the lawyer's sign-off on it | [data-handling-for-lawyer.md](data-handling-for-lawyer.md) |

A sensible order for a newcomer: how-it-works, onboarding (to get set up),
code-guide, development, and running-locally as a reference. Read running-on-server only when you need to touch the
server, and running-on-bedrock only when you want hosted models.

## Where things stand

- It runs in two places: on a development laptop, and on a home server,
  public at `https://battle.smarkiq.us`.
- **A beta with a group of lawyers is being prepared (2026-11-20).** 5 to
  10 testers are expected, each with 15 cases over 60 days, fictional cases
  only. Expected cost: under $1 per tester. See `plans/beta-plan.md` for
  the decisions and cost, and [beta.md](beta.md) for running it.
- **The live site moves to Amazon Bedrock** (gpt-oss 120B, Oregon) for the
  beta. The config is ready; it goes live once the Bedrock API key is on
  the server ([running-on-server.md](running-on-server.md), "Bedrock API
  key").
- **Cloudflare Access** goes in front of the site before any tester signs
  in. Not set up yet.
- Only **fictional** cases may be used, on every provider, until the
  lawyer signs off ([data-handling-for-lawyer.md](data-handling-for-lawyer.md)).
- The citation checking works. The legal choices are better on gpt-oss
  120B (58–65% of the core law cited, nothing off-topic) than on the
  server's 14B (about 43%), but search still misses some of the right
  sections. That's the main open problem.

## Other documents

These docs describe how the system is and how to run it. The `plans/`
folder records why it is that way and what's next:

| File | What it holds |
|---|---|
| `plans/plan.md` | The design, every decision with its reason, the build steps with their results, evaluation scores, and open questions |
| `plans/server-migration-plan.md` | How the server deployment was built, what went wrong along the way, and the incident log |
| `plans/aws-bedrock-plan.md` | The plan to move the models to Amazon Bedrock and the app to AWS, with costs |
| `plans/profitability-plan.md` | Pricing, costs and profit scenarios |
| `plans/beta-plan.md` | The lawyers' beta: decisions, expected cost per tester, and the launch checklist |
| `README.md` (repo root) | A short overview and a usage reference for the commands |
