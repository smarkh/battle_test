# Running the lawyers' beta

How to add a beta tester, look after them during the beta, and end it. The
decisions and the expected cost are in `plans/beta-plan.md`. The server
commands assume you're connected over SSH, in the battle_test folder
(`docs/running-on-server.md`).

**Before the first tester:** the steps in `plans/beta-plan.md`, "Before
the presentation, and before the first tester". In particular, Cloudflare
Access must be in front of the site, and the Bedrock API key must be on the
server.

## What a tester gets

- **15 cases in total, for 60 days** (the Beta plan). After either runs
  out they can still sign in, read, download and delete their cases, but
  can't start new ones.
- At most 3 cases waiting or running at once. A case takes about 6
  minutes. A run that fails isn't counted.
- **Fictional cases only.** Real client material isn't allowed until the
  lawyer has signed off on case data going to AWS.
- Finished cases are deleted after 90 days, or sooner by the tester.
- Each tester sees only their own cases. You (the admin) see when cases
  were started and their state, never their content.

## Add a tester

You need their **email address** (for Cloudflare Access) and a
**username** for them. Usernames are letters, digits, `.`, `_` and `-`,
not an email address. A simple pattern: first initial and surname,
`jdoe`.

1. **Cloudflare Access:** Zero Trust → Access → Access groups →
   `battle-beta` → add their email. (`docs/running-on-server.md`,
   "Cloudflare Access".)
2. **The account (server):**
   ```powershell
   docker compose run --rm battle-test python -m battle_test.web.users beta jdoe --days 60
   ```
   Several at once: `beta jdoe asmith bwong --days 60`. Each gets its own
   setup link. Nothing is created if any name is taken or invalid.
   - Several lawyers from one firm who'd rather share one pool: add
     `--firm doe-smith` (letters, digits, `.`, `_`, `-`). Two testers then
     share 30 cases.
   - The admin pages (`/admin`) can do the same, one account at a time:
     add the account on the **Beta** plan, then set its paid-through date.
3. **Send them the email below,** with their username and setup link,
   from your own email. The link works once and expires after **72
   hours**. If they miss it, `invite jdoe` prints a new one.
4. **Note** who they are, their email, username and the date, somewhere
   private (not in the repo). The app stores only the username.

### The email to send

> **Subject: Your Battle Test beta access**
>
> Hi [name],
>
> Thank you for trying Battle Test. Your access is ready.
>
> **1. Set your password (within 72 hours).** Open this link and choose a
> password. It works once:
> [setup link]
>
> Your username is **[username]**. You'll sign in with the username, not
> your email address.
>
> **2. Signing in afterwards:** go to https://battle.smarkiq.us. First,
> enter this email address. You'll be emailed a 6-digit code to type in.
> That keeps the site closed to everyone who isn't in the beta. Then sign
> in with your username and password.
>
> **What you have:** 15 cases over the next 60 days, until [date]. A case
> takes about 6 minutes. You can close the page and come back.
>
> **Please use fictional or fully anonymised facts only.** Don't enter real
> client information during the beta.
>
> Everything it produces is a draft for a lawyer to review, not legal
> advice. Every statute and rule it cites is checked against the law as of
> the date shown on each result. Case law isn't checked yet, and it may
> miss authorities or misstate what a section says.
>
> **Feedback** is the whole point. Reply to this email, or write to
> battle_test@smarkiq.us, with anything: what was useful, anything wrong
> (especially a statute or rule that's wrong or misstated), what was
> missing, features you'd want, roughly how much time it saved, and what
> you'd consider paying for it.
>
> Thank you,
> [your name]

## During the beta

| To… | Run (server) |
|---|---|
| See who has used what | `docker compose run --rm battle-test python -m battle_test.web.users usage` |
| List the accounts, with end dates and who hasn't set a password yet | `… users list` |
| A new setup link (missed or forgotten password) | `… users invite jdoe` |
| Give one tester more time | `… users paid jdoe 2027-02-28` |
| Give one tester more cases | `… users plan jdoe solo` (30 a month, counted from the 1st). There's no "+5 cases": changing the plan is the way. Their end date still applies. |
| Stop one tester at once | `… users disable jdoe`, and remove their email from `battle-beta` |

`…` is `docker compose run --rm battle-test python -m battle_test.web.users`.

- **Cost:** about $0.05 a case. `usage` multiplied by $0.05 is a good
  estimate. Each case's report has its own cost line. The AWS budget alerts
  ($10, $25) cover surprises (`plans/beta-plan.md`).
- **If a tester says a case failed:** `docker logs --tail 100 battle-test`.
  Common causes are in `docs/running-on-server.md`, "If the site can't be
  reached".
- **Feedback:** keep it together, by tester, in the same private place as
  your list of testers. Put the patterns, not the testers' words or names,
  into `plans/plan.md`.

## End the beta

1. Nothing has to happen on the day: each account stops starting cases on
   its own end date.
2. Afterwards: take the emails out of `battle-beta` in Cloudflare Access,
   and `disable` the accounts you won't carry on. Their cases are deleted
   after 90 days as usual. Let testers know before, so they can download
   what they want to keep.
3. Testers who become customers: `paid jdoe` (no end) and `plan jdoe solo`
   (or the plan they choose), once billing is decided.
4. Revoke the Bedrock API key if the site goes back to the 14B, or replace
   it before it expires if it stays on Bedrock (`docs/running-on-server.md`,
   "Bedrock API key").
5. Write up the results in `plans/beta-plan.md`, "What to measure during
   the beta".

## The package sent to the lawyers group

`beta-package/` holds the four files sent to the contact in the lawyers
group, who forwards them on without presenting them. Each must make sense to
a lawyer reading it cold.

| File | What it is |
|---|---|
| `1 - Read me first.docx` | The invitation: what it is, the beta offer, how to join |
| `2 - Battle Test overview.pptx` | 13 self-explanatory slides |
| `3 - Beta details and FAQ.docx` | How it works, limits, data handling, terms, questions |
| `4 - Sample result (fictional case).docx` | A real gpt-oss 120B result on the invented Utah roofing case |

**Only product material and fictional cases go in `beta-package/`.** It's the
one place `.gitignore` lets Word files into git.

**Before sending,** what the files describe must be live: the site on Bedrock
gpt-oss 120B, Cloudflare Access (sign-in starts with an emailed code), and
`battle_test@smarkiq.us` forwarding to Gmail (done 2026-11-20).

### Changing and rebuilding them

The text lives in the scripts in `scripts/beta-package/`. Change it there,
not only in the Word or PowerPoint file, or the next rebuild undoes the
change. They need Node with `docx` and `pptxgenjs` installed outside the repo
(`npm install docx@9.5.1 pptxgenjs@3.12.0` in a scratch folder, then
`$env:NODE_PATH` set to its `node_modules`). From the repo root:

```powershell
node scripts\beta-package\make_docs.js beta-package               # files 1 and 3
node scripts\beta-package\make_deck.js "beta-package\2 - Battle Test overview.pptx" <pptx skill>\scripts\apply_theme.js
.venv\Scripts\python scripts\beta-package\make_sample.py output\eval\20261105-104924-bedrock-gptoss120b-all\utah_roofing.md examples\utah_roofing_facts.md "beta-package\4 - Sample result (fictional case).docx"
```

- `make_deck.js` uses `apply_theme.js` from Claude's PowerPoint skill to set
  the theme colours.
- The sample's source is in `output/`, which is gitignored. If it's gone, run
  the Utah roofing case again on Bedrock (about $0.05) and use the new result.
- `scripts\beta-package\to_pdf.ps1 -In FILE -Out FILE.pdf` makes a PDF through
  Word or PowerPoint, if anyone asks for one.
