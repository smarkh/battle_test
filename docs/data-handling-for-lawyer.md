# battle_test: how case data is handled

**For:** the supervising lawyer, for review and sign-off
**From:** Mark Hamilton
**Date:** 2026-10-31
**Status:** draft. Until this is signed off, only fictional cases are used.

battle_test drafts a complaint and a motion for summary judgment from a
case's facts, has a second AI model write the opposition, and checks every
statute and rule cited against a local copy of the law. The output is a
starting draft for a lawyer, not a document to file.

This note sets out where case information goes, who can see it, and how
long it's kept, and asks you to decide the points in section 8. It isn't a
contract. If you want terms with a client or a provider, it can be the
basis for them.

## 1. What data is involved

- **Case information** a user enters: the facts, the parties, the amounts,
  and any complaint they paste or upload (Word, PDF or text). This may
  include confidential or privileged client information and personal
  details.
- **What the system produces** from it: the drafts, the citation report
  and the downloads (Word, PDF, Markdown).
- **Account details:** a username, a firm name and a password (stored
  only as a hash).

An uploaded file is used only to extract its text. The file itself isn't
kept.

## 2. Where the data goes

1. **Your browser to Cloudflare to the app.** The site is reached through
   Cloudflare, which decrypts the traffic at its own servers and
   re-encrypts it to the app. Cloudflare therefore briefly handles case
   text in transit.
2. **The app to an AI model.** Every model request contains the case text.
   - **Until the beta:** the models ran on our own server, so case text
     didn't leave it.
   - **For the lawyers' beta (from late 2026-11): Amazon Bedrock** (AWS),
     processing in the `us-west-2` (Oregon) region, with gpt-oss 120B.
     Beta testers are asked to use **fictional cases only**, so no real
     case material goes to AWS before you decide question 1. Two of the candidate models (Llama 3.3 70B and
     Claude Sonnet 5) are only offered through AWS's "US" routing, which
     may process a request in **any US region**.
   - **For testing only: Ollama Cloud.** Hosted mainly in the US, but it
     may route requests to **Europe** when busy. Used for fictional cases
     only, and not proposed for real ones.
3. **Storage.**
   - **Today:** cases are stored on our own server, at the operator's
     premises.
   - **If the site moves to AWS:** cases would be stored on an encrypted
     AWS disk, in an AWS account the operator alone controls. The app
     could also stay on our own server while only the model requests go
     to AWS. Section 8 asks you to choose.

## 3. What the providers say they do

- **AWS (Bedrock):** doesn't store or log the prompts, doesn't use them to
  train models, and doesn't share them with the model makers (Anthropic,
  Meta, OpenAI and others). Data is encrypted in transit and at rest.
  Bedrock can be used for health data under a HIPAA business associate
  agreement. These are AWS's published statements; you may want to read
  AWS's service terms and data processing addendum yourself.
- **Ollama Cloud:** says prompt and response data is never logged or
  trained on, and that its hosting partners must keep no data.
- **Cloudflare:** handles the traffic as described in 2.1. Its own
  privacy terms govern what it does with traffic it passes on.

## 4. Retention and deletion

- Finished and failed cases are **deleted automatically 90 days** after
  they start. Users can delete a case sooner.
- Downloads a lawyer saves (Word, PDF, Markdown) are then in the lawyer's
  own records, and these rules no longer apply to them.
- Backups would cover account data only, not case files, so a deleted
  case can't come back from a backup.

## 5. Who can see it

- **Users** see only their own cases. Accounts in the same firm share a
  case allowance, not each other's cases.
- **Admin pages** manage accounts and plans and keep an activity log.
  They don't show case content.
- **The operator** (Mark Hamilton) runs the server and can technically
  reach the stored case files. The operator won't open case content
  unless a user asks for help with that case, or to fix a fault. *(Please
  confirm or change this commitment.)*
- **Before the beta's first tester:** Cloudflare Access will be in front
  of the whole site, so only approved email addresses can reach it. The AWS account's
  root user has multi-factor sign-in and no access keys.

## 6. Duties that stay with the lawyer

- **Client consent.** ABA Formal Opinion 512 (2024) says a lawyer may need
  a client's informed consent before putting confidential information
  into a generative AI tool. Your state's ethics rules may say more.
- **Checking the output.** Every citation is checked against the law
  index for existence and status, but not for whether it supports the
  point made. The arguments and facts must be reviewed like any draft.
  The tool's notes mark citations it doubts.

## 7. If something goes wrong

If the server, the AWS account or a user account is compromised, the
operator will:
- take the site offline
- tell the affected users and you within ___ hours
- keep a record of what happened

*(Please set the timeframe and say whether anyone else must be told.)*

## 8. Decisions needed from you

| # | Question | Your decision |
|---|---|---|
| 1 | May real case material be sent to **Amazon Bedrock** in `us-west-2`? | |
| 2 | May models that use AWS's **US-wide routing** (any US region) be used, or only `us-west-2`? | |
| 3 | Should case files be stored **on our own server** (only model requests go to AWS), or may the whole app run **on AWS**? | |
| 4 | Is Cloudflare handling case text in transit (2.1) acceptable? | |
| 5 | Is **90-day** automatic deletion compatible with your record-keeping duties? If not, what period? | |
| 6 | How should **client consent** be handled: per client, in the engagement letter, or not needed? | |
| 7 | Is the operator access commitment in section 5 acceptable? | |
| 8 | What timeframe for breach notice (section 7)? | |
| 9 | Anything else that must be in place before real cases are used? | |

Until these are answered, **only fictional cases are used**, on every
provider.

Reviewed by: ______________________  Date: ____________
