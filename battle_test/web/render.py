"""Turn a saved result (the JSON form of a CaseRun) into HTML for the results page."""

import re
from collections import Counter

from markupsafe import Markup, escape

from battle_test import grounding, report

_CITED = grounding.CITED_STATUSES
_FLAG = re.compile(r"\[⚠ [^\]]*\]")
_PLACEHOLDER = re.compile(r"\[(?:CITATION|FACT) NEEDED:[^\]]*\]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")

STATUS_LABELS = {
    grounding.VERIFIED: ("ok", "✅ in force"),
    grounding.IN_FORCE_NOT_PROVIDED: ("ok-unseen", "☑ in force, but the model never saw its text"),
    grounding.AMBIGUOUS: ("warn", "⚠ ambiguous"),
    grounding.NOT_IN_FORCE: ("bad", "❌ not in force"),
    grounding.NOT_FOUND: ("bad", "❌ not found"),
    grounding.WRONG_JURISDICTION: ("bad", "❌ other state's law"),
}


def document_html(doc: dict) -> Markup:
    """The document's text with citations linked and problems highlighted.

    Everything is escaped first, then tags are added only around text we
    matched, so model output can never inject HTML.
    """
    html = str(escape(doc["text"]))
    html = _FLAG.sub(lambda m: f'<mark class="flag">{m.group()}</mark>', html)
    html = _PLACEHOLDER.sub(lambda m: f'<mark class="placeholder">{m.group()}</mark>', html)

    links = {}
    for check in doc["checks"]:
        section = check.get("section")
        if check["status"] in _CITED and section and section.get("source_url"):
            links[str(escape(check["text"]))] = (section["source_url"], section["citation"], check["status"])
    if links:
        # One pass, longest first, so "§ 1-1-12" isn't partly linked as "§ 1-1-1".
        pattern = re.compile("|".join(re.escape(t) for t in sorted(links, key=len, reverse=True)))

        def link(m: re.Match) -> str:
            url, citation, status = links[m.group()]
            css = STATUS_LABELS[status][0]
            return (f'<a class="cite {css}" href="{escape(url)}" target="_blank" '
                    f'rel="noopener noreferrer" title="{escape(citation)}: official source">{m.group()}</a>')

        html = pattern.sub(link, html)
    html = _BOLD.sub(r"<strong>\1</strong>", html)
    return Markup(html)


def support_checked(result: dict) -> bool:
    """Whether the "does it say that?" check ran. Results saved before it
    existed have no checker model."""
    return bool(result.get("checker_model"))


def check_summary(doc: dict, checked: bool = False) -> dict:
    """`checked`: the "does it say that?" check ran on this case."""
    counts = Counter(c["status"] for c in doc["checks"])
    return {
        "verified": counts[grounding.VERIFIED],
        "unseen": counts[grounding.IN_FORCE_NOT_PROVIDED],
        "problems": sum(counts[s] for s in grounding.PROBLEM_STATUSES),
        "doubted": sum(c.get("support") in grounding.DOUBTED for c in doc["checks"]) if checked else "–",
        "ambiguous": counts[grounding.AMBIGUOUS],
        "unchecked": len(doc["unchecked"]),
        "placeholders": sum(1 for m in _PLACEHOLDER.finditer(doc["text"]) if m.group().startswith("[CITATION")),
    }


def notable_checks(doc: dict) -> list[dict]:
    """Citations needing attention, for the list under the summary table."""
    notable = []
    for c in doc["checks"]:
        if c["status"] != grounding.VERIFIED:
            notable.append({"css": STATUS_LABELS[c["status"]][0], "label": STATUS_LABELS[c["status"]][1],
                            "text": c["text"], "matched": (c.get("section") or {}).get("citation"), "reason": ""})
        if c.get("support") in grounding.DOUBTED:
            notable.append({"css": "warn", "label": f"⚠ {grounding.SUPPORT_LABELS[c['support']]}",
                            "text": c["text"], "matched": None,
                            "reason": c.get("support_reason") or "none given"})
    return notable


def authorities(result: dict) -> list[dict]:
    """Every authority quoted to the models, with which documents it was given for."""
    seen: dict[str, dict] = {}
    for doc in result["documents"]:
        for s in doc["authorities"]:
            entry = seen.setdefault(s["citation"], {**s, "given_for": []})
            entry["given_for"].append(doc["title"])
    return list(seen.values())


def possible_references(result: dict) -> list[dict]:
    """The authorities no document cites, each with the start of its text."""
    cited = {c["section"]["citation"] for doc in result["documents"] for c in doc["checks"]
             if c.get("section") and c["status"] in _CITED}
    return [{**a, "excerpt": grounding.excerpt(a["text"], report.POSSIBLE_EXCERPT_CHARS, " …")}
            for a in authorities(result) if a["citation"] not in cited]
