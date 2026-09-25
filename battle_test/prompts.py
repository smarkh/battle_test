"""System and task prompts for the plaintiff and defendant roles."""

# v1 state coverage (see plans/plan.md). Federal law applies in every run.
SUPPORTED_STATES = {"UT": "Utah", "CA": "California", "TX": "Texas"}

# General-jurisdiction state trial court, so the complaint names the right
# court. (The first Utah run filed in a nonexistent "Circuit Court".)
TRIAL_COURTS = {
    "UT": "the Utah District Court for the appropriate judicial district and county",
    "CA": "the Superior Court of California for the appropriate county",
    "TX": "the Texas District Court for the appropriate county",
}

# Models cite from memory unless told not to, and memory is often outdated
# or invented (the first Utah run cited a long-renumbered statute). So they
# may cite only sections pulled from the law index for this request, and
# every citation is checked in code afterwards anyway. Case law can't be
# checked until plan step 4, so no cases at all for now.
_CITATION_RULE = """\
You may cite ONLY the statutes, court rules and constitutional provisions \
listed under AUTHORITIES in the request, and only for what their quoted text \
actually says. Write each citation exactly as it appears in that list. Do NOT \
cite anything else from memory, even if you believe you know it, and do NOT \
cite any court cases. Wherever the argument needs legal authority that is not \
in the list, write a placeholder in exactly this form:
[CITATION NEEDED: short description of the legal authority required]"""

_FACT_RULE = """\
Do not invent facts. Use only the facts in the materials provided. Where a \
fact you need is missing, write [FACT NEEDED: description] instead."""

_NOT_A_JUDGE = """\
You are an advocate, not a judge. Do not predict, declare, or rule on the \
outcome of the case."""

_FORMAT_RULE = """\
Every filing uses the same caption as the complaint: the plaintiff's actual \
name first, then "v.", then the defendant's actual name, whichever side you \
represent. Do not make up a date or attorney details: write [DATE] for the \
date and [ATTORNEY NAME, BAR NUMBER, AND CONTACT DETAILS] for the signature \
block."""


def _system(role_description: str, state: str) -> str:
    return "\n\n".join(
        [
            role_description,
            f"The case is governed by {state} law and any applicable federal law.",
            _CITATION_RULE,
            _FACT_RULE,
            _NOT_A_JUDGE,
            _FORMAT_RULE,
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


# ---------------------------------------------------------------------------
# Research and selection (JSON replies)
# ---------------------------------------------------------------------------

# No example query here: the 7B model copied the example word for word
# into both sides' research, crowding out queries about the actual case.
_QUERY_STYLE = """\
For each legal question about THIS case, write one short search query in the \
formal vocabulary statutes and court rules use (terms such as "action", \
"commenced within", "recover", "liable", "obligation", "breach") rather than \
everyday phrasing. Each query must be about a specific issue in this case. \
Respond with JSON only, in exactly this form, with 6 to 10 queries:
{"queries": ["...", "..."]}"""


def plaintiff_research_task(state: str, materials_title: str, materials: str) -> str:
    return f"""\
Before drafting, work out which {state} and federal statutes and court rules \
the plaintiff will need. Consider: each cause of action and its elements, \
limitation periods, damages and interest, attorney fees, and jurisdiction \
and venue. (The state's summary judgment rule is supplied separately.)

{_QUERY_STYLE}

{wrap(materials_title, materials)}"""


def defendant_research_task(state: str, complaint: str, motion: str) -> str:
    return f"""\
Before drafting the defendant's opposition, work out which {state} and federal \
statutes and court rules the defendant will need. Consider: defenses and \
affirmative defenses to each claim, limitation periods, limits on damages, \
mitigation, and contract terms and excuses for non-performance. (The \
state's summary judgment rule is supplied separately.)

{_QUERY_STYLE}

{wrap("Complaint", complaint)}

{wrap("Motion for summary judgment", motion)}"""


def select_task(side: str, materials_title: str, materials: str, candidates: str, limit: int) -> str:
    return f"""\
Below is the case and a numbered list of candidate authorities found by \
searching the law index. Choose the ones that would genuinely help the \
{side} in THIS case. Skip any that are about unrelated subjects (for example \
insurance, taxes or public agencies when the case is a private contract \
dispute). Choose at most {limit}. Respond with JSON only, in exactly this form:
{{"selected": [numbers]}}

{wrap(materials_title, materials)}

{wrap("Candidate authorities", candidates)}"""


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------

def complaint_task(court: str, facts: str, authorities: str) -> str:
    return f"""\
Draft a civil complaint to be filed in {court}, based only on the case \
information below. Include: a caption, the parties, \
jurisdiction and venue, factual allegations in numbered paragraphs, each \
cause of action as a separate count with its elements, and a prayer for relief.

{wrap("Authorities", authorities)}

{wrap("Case information", facts)}"""


def motion_task(state: str, complaint: str, authorities: str) -> str:
    return f"""\
Using the complaint below, draft the plaintiff's motion for summary judgment \
in {state} court. Include: an introduction; a statement of undisputed \
material facts in numbered paragraphs, each tied to the complaint; the \
summary judgment standard; an argument section showing, for each claim, why \
no genuine dispute of material fact exists and the plaintiff is entitled to \
judgment as a matter of law; and a conclusion stating the relief requested.

{wrap("Authorities", authorities)}

{wrap("Complaint", complaint)}"""


def opposition_task(state: str, complaint: str, motion: str, authorities: str) -> str:
    return f"""\
Below are the plaintiff's complaint and motion for summary judgment. Draft the \
defendant's opposition to the motion for summary judgment in {state} court. \
Include: an introduction; a response to each numbered undisputed fact, \
stating whether it is disputed and why; the genuine disputes of material fact \
that preclude summary judgment; the legal weaknesses in each of the \
plaintiff's claims; the defendant's defenses and affirmative defenses; and a \
conclusion asking the court to deny the motion. Where the plaintiff cites an \
authority, check its quoted text below and point out where the plaintiff \
overstates or misreads it.

{wrap("Authorities", authorities)}

{wrap("Complaint", complaint)}

{wrap("Motion for summary judgment", motion)}"""


def reply_task(state: str, complaint: str, motion: str, opposition: str, authorities: str) -> str:
    return f"""\
Below are your complaint, your motion for summary judgment, and the \
defendant's opposition. Draft the plaintiff's reply in support of the motion \
for summary judgment in {state} court. Respond point by point to the \
defendant's opposition: show why each claimed factual dispute is not genuine \
or not material, rebut each legal argument and defense, and conclude by \
renewing the request for summary judgment.

{wrap("Authorities", authorities)}

{wrap("Complaint", complaint)}

{wrap("Motion for summary judgment", motion)}

{wrap("Opposition", opposition)}"""
