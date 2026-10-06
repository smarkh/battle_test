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
| Understand what the system does and how a case flows through it | [how-it-works.md](how-it-works.md) |
| Find the file that does something | [code-guide.md](code-guide.md) |
| Set up and run it on your own machine | [running-locally.md](running-locally.md) |
| Start, stop, update or check the deployed copy | [running-on-server.md](running-on-server.md) |
| Run the models on Amazon Bedrock instead of your own GPU | [running-on-bedrock.md](running-on-bedrock.md) |
| Make a change: tests, evaluation, and the project's rules | [development.md](development.md) |

A sensible order for a newcomer: how-it-works, running-locally, code-guide,
development. Read running-on-server only when you need to touch the
server, and running-on-bedrock only when you want hosted models.

## Where things stand

- It runs in two places: on a development laptop, and on a home server,
  public at `https://battle.smarkiq.us`.
- Only **fictional** cases may be used on the public site, until Cloudflare
  Access is added in front of it.
- The models can also run on Amazon Bedrock. That's built but hasn't been
  run against AWS yet, and it's for fictional cases only too.
- The citation checking works. The quality of the legal choices doesn't
  yet: the models often pick the wrong law, because the search step doesn't
  find the right sections. That's the main open problem.

## Other documents

These docs describe how the system is and how to run it. The `plans/`
folder records why it is that way and what's next:

| File | What it holds |
|---|---|
| `plans/plan.md` | The design, every decision with its reason, the build steps with their results, evaluation scores, and open questions |
| `plans/server-migration-plan.md` | How the server deployment was built, what went wrong along the way, and the incident log |
| `plans/aws-bedrock-plan.md` | The plan to move the models to Amazon Bedrock and the app to AWS, with costs |
| `plans/profitability-plan.md` | Pricing, costs and profit scenarios |
| `README.md` (repo root) | A short overview and a usage reference for the commands |
