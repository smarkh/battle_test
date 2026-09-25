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
  outdated or invented. This settles the earlier open question: the project
  needs a grounded source of federal and state law, and citations should be
  checked against it.

## Decisions made

- **End result:** no declared winner and no judge role. The output is the
  document set (complaint, motion, rebuttal, optional reply), optionally
  with a likelihood-of-success estimate.
- **Grounding:** arguments must be grounded in verified current law, not
  just the uploaded text.

## Open questions

- **Law source and upkeep:** a local corpus (RAG, like the tax project idea),
  a trusted legal-research API, or both? How is it kept current, and how
  are citations checked, so that repealed statutes, overturned cases, and
  made-up citations are caught?
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
