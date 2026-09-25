"""System and task prompts for the plaintiff and defendant roles."""

# v1 state coverage (see plans/plan.md). Federal law applies in every run.
SUPPORTED_STATES = {"UT": "Utah", "CA": "California", "TX": "Texas"}

# Until the law corpus is wired in (plan step 3), the models must not cite
# anything from memory: training data can be outdated or invented. The
# placeholders mark exactly where verified citations will go.
_CITATION_RULE = """\
You do not yet have access to a verified legal database. Do NOT cite any \
specific statute, regulation, court rule, or case by name or number, even if \
you believe you know it. Wherever a legal citation belongs, write a \
placeholder in exactly this form:
[CITATION NEEDED: short description of the legal authority required]
For example: [CITATION NEEDED: {state} statute of limitations for breach of a written contract]"""

_FACT_RULE = """\
Do not invent facts. Use only the facts in the materials provided. Where a \
fact you need is missing, write [FACT NEEDED: description] instead."""

_NOT_A_JUDGE = """\
You are an advocate, not a judge. Do not predict, declare, or rule on the \
outcome of the case."""


def _system(role_description: str, state: str) -> str:
    return "\n\n".join(
        [
            role_description,
            f"The case is governed by {state} law and any applicable federal law.",
            _CITATION_RULE.format(state=state),
            _FACT_RULE,
            _NOT_A_JUDGE,
        ]
    )


def plaintiff_system(state: str) -> str:
    return _system(
        "You are an experienced civil litigation attorney representing the "
        "PLAINTIFF. Advocate forcefully but honestly for your client, and "
        "write in the formal style of a court filing.",
        state,
    )


def defendant_system(state: str) -> str:
    return _system(
        "You are an experienced civil litigation attorney representing the "
        "DEFENDANT who is being sued. Advocate forcefully but honestly for "
        "your client, attack every weakness in the plaintiff's position, and "
        "write in the formal style of a court filing.",
        state,
    )


def wrap(title: str, text: str) -> str:
    """Delimit a document so the model can tell where each one starts and ends."""
    tag = title.upper()
    return f"=== {tag} ===\n{text.strip()}\n=== END {tag} ==="


def complaint_task(state: str, facts: str) -> str:
    return f"""\
Draft a civil complaint to be filed in the appropriate {state} court, based \
only on the case information below. Include: a caption, the parties, \
jurisdiction and venue, factual allegations in numbered paragraphs, each \
cause of action as a separate count with its elements, and a prayer for relief.

{wrap("Case information", facts)}"""


def motion_task(state: str, complaint: str) -> str:
    return f"""\
Using the complaint below, draft the plaintiff's motion for summary judgment \
in {state} court. Include: an introduction; a statement of undisputed \
material facts in numbered paragraphs, each tied to the complaint; the \
summary judgment standard; an argument section showing, for each claim, why \
no genuine dispute of material fact exists and the plaintiff is entitled to \
judgment as a matter of law; and a conclusion stating the relief requested.

{wrap("Complaint", complaint)}"""


def opposition_task(state: str, complaint: str, motion: str) -> str:
    return f"""\
Below are the plaintiff's complaint and motion for summary judgment. Draft the \
defendant's opposition to the motion for summary judgment in {state} court. \
Include: an introduction; a response to each numbered undisputed fact, \
stating whether it is disputed and why; the genuine disputes of material fact \
that preclude summary judgment; the legal weaknesses in each of the \
plaintiff's claims; the defendant's defenses and affirmative defenses; and a \
conclusion asking the court to deny the motion.

{wrap("Complaint", complaint)}

{wrap("Motion for summary judgment", motion)}"""


def reply_task(state: str, complaint: str, motion: str, opposition: str) -> str:
    return f"""\
Below are your complaint, your motion for summary judgment, and the \
defendant's opposition. Draft the plaintiff's reply in support of the motion \
for summary judgment in {state} court. Respond point by point to the \
defendant's opposition: show why each claimed factual dispute is not genuine \
or not material, rebut each legal argument and defense, and conclude by \
renewing the request for summary judgment.

{wrap("Complaint", complaint)}

{wrap("Motion for summary judgment", motion)}

{wrap("Opposition", opposition)}"""
