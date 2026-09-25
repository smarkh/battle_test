# Plan — Legal Adversarial Argument System (battle_test)

Source: `../smark_iq/plans/ideation.md`, "Future Separate Projects" §B, revised
after a conversation with a lawyer about how these lawsuits actually proceed.
This is a standalone project, separate from the smark_iq deployment, but
expected to reuse lessons learned there (self-hosting, local RAG,
guardrails-style network isolation and data handling).

## Goal

Battle-test a lawsuit position before it is used for real. One model acts as
the plaintiff's lawyer (drafting the complaint and a summary judgment
motion), a second model acts as the defendant's lawyer (rebutting both), and
optionally the plaintiff's model replies to that rebuttal. The system is
argumentative lawyers, not a judge: it surfaces weaknesses and never
declares a winner.

## Flow

1. **Jurisdiction selection.** The user picks a US state. Arguments must
   apply that state's law in addition to federal law, since laws vary state
   by state.
2. **Complaint.** The user either:
   - inputs a complaint they wrote themselves, or
   - enters the case information and has the plaintiff model generate the
     complaint from it.
3. **Summary judgment motion.** From the complaint (user-written or
   generated), the plaintiff model drafts a summary judgment motion that
   cites **only current, accurate, and valid law** for the selected state
   and federal law.
4. **Rebuttal.** A second model, acting as the defendant's lawyer, takes the
   complaint and the summary judgment motion and responds to them.
5. **Output (round 1).** The system outputs the original complaint, the
   summary judgment motion, and the rebuttal.
6. **Round 2 (if feasible).** The plaintiff model replies to the rebuttal,
   and that reply is added to the output.
7. **No winner.** No winner is declared. At most, the output may include an
   estimated likelihood that the plaintiff's position succeeds, clearly
   labelled as an estimate and not a ruling.

## Components

- **Two model roles:** "plaintiff" (complaint, summary judgment motion,
  round-2 reply) and "defendant" (rebuttal). They could be two instances of
  the same model with different system prompts, or two different models for
  more independent takes.
- **Orchestration layer:** a custom script/pipeline that runs the steps above
  in order and passes each document to the next step. Open WebUI's
  single-conversation chat doesn't do this multi-role turn-taking out of the
  box.
- **State selector:** a US state input that decides which state law applies
  and is passed to both roles.
- **Complaint input:** two modes — paste/upload an existing complaint, or a
  structured form of case information for generation.
- **Legal authority source:** requirement 3 (only current, accurate, valid
  law) means the models can't rely on training data alone, since it may be
  outdated or invented. The project needs a grounded source of federal and
  state law, and every citation is checked against it. See "Law source (v1)"
  below.

## Law source (v1) — free sources only

Decided: v1 uses free sources only. Paid sources are revisited after v1.

### Statutes, regulations, constitutions, court rules — Open US Law

- [Open US Law](https://www.vaquill.ai/open-us-law) (Vaquill AI): the U.S.
  Code, federal and state regulations, and the codes, constitutions and court
  rules of all 50 states, DC and Puerto Rico. About 3M sections in one
  schema.
- Downloaded (~4 GB Parquet) and indexed **locally**, so statute lookups
  never send case facts off the machine.
- Licence: statute text is public domain; the compilation is CC BY 4.0.
  Credit line required: "Open US Law by Vaquill AI, CC BY 4.0."
- Refreshed quarterly (current snapshot v2026.08, dated 2026-08-14). Updating
  the local copy each quarter is a maintenance task, and every output states
  the snapshot date its statutes are current as of.
- A minority of state codes come from commercial aggregators, not official
  publishers, and have no official source URL. Citations resting on those
  records are flagged in the output.
- Statute citations produced by the models are checked against this local
  corpus. A cited section that isn't in the corpus is rejected.

### Case law — CourtListener (Free Law Project)

- [CourtListener REST API](https://www.courtlistener.com/help/api/rest/):
  search for relevant cases. Free, requires an API token.
- [Citation Lookup API](https://wiki.free.law/c/courtlistener/help/api/rest/v4/citation-lookup):
  every case citation the models produce is checked against ~18M real
  citations. Citations that don't exist, or are ambiguous, are rejected.
  - Limits: 60 valid citations per minute, 250 per request.
  - Case law only: it does not look up statutes. Statutes are checked
    against Open US Law instead.
- Privacy: citations are extracted locally with Eyecite (the open-source
  parser CourtListener itself uses) and only the citation strings are sent,
  never the complaint or case facts.

### Known gap — "still good law" for cases

- Confirming a case exists doesn't confirm it hasn't been overruled. That
  needs a citator (KeyCite, Shepard's), and no free one exists yet. Free Law
  Project is [building an open-source one](https://free.law/2025/05/01/citator/),
  still in progress and focused on Supreme Court cases. Revisit when it ships.
- v1 mitigation:
  1. Use CourtListener's "cited by" data to pull later opinions citing each
     case, and have a model scan them for negative treatment (overruled,
     reversed, criticised). This is a **warning signal only**, not a
     guarantee.
  2. Every case citation in the output is marked **"verify with
     KeyCite/Shepard's before filing."**
- What v1 can honestly claim: every citation exists, and statutes are
  current as of a stated snapshot date. It can't claim that every case is
  still good law.

### Deferred paid options (post-v1)

- [OpenLaws](https://openlaws.us/): same statute coverage with weekly federal
  and monthly state updates. Pricing not published; access by application.
- vLex/Fastcase (Cert citator), Westlaw (KeyCite), Lexis (Shepard's): would
  close the good-law gap, but APIs are sales-led or quote-only.

## Decisions made

- **End result:** no declared winner and no judge role. The output is the
  document set (complaint, motion, rebuttal, optional reply), optionally
  with a likelihood-of-success estimate.
- **Grounding:** arguments must be grounded in verified current law, not
  just the uploaded text.
- **Law source (v1):** free sources only. Open US Law (local) for statutes
  and regulations, CourtListener for case law and citation checks. Details
  in "Law source (v1)" above.

## Open questions

- **Coverage scope:** all 50 states plus federal law from the start, or
  launch with a few states and expand?
- **Likelihood estimate:** include it or not, and if so, how is it worded
  and justified so it doesn't read as a verdict?
- **Round 2:** confirm it's feasible (context length, runtime on local
  hardware) before committing to it.
- **Local vs. hosted models:** whether local models are strong enough for
  legal drafting, or whether a hosted API is needed, which ties into the
  data-sensitivity question below.
- **Data sensitivity:** complaints and case information are likely
  sensitive/privileged, so the same data-handling considerations as the tax
  idea (and smark_iq's `guardrails.md`) apply. The output also likely needs
  a disclaimer that it assists a licensed attorney's review rather than
  replacing it.
