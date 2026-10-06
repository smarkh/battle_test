"""Turn a saved result (the JSON form of a CaseRun) into a Word or PDF file.

Both are built in memory from the same list of blocks, so the two formats
can't drift apart, and nothing extra is stored with the case: a download is
generated when it's asked for.
"""

import io
import re
import unicodedata
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import Pt, RGBColor
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from battle_test import report
from battle_test.web import render

FORMATS = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

# The Markdown report's disclaimer, as plain text.
DISCLAIMER = re.sub(r"^> |\*\*|`", "", report.DISCLAIMER).replace("⚠ mark", "[⚠ …] mark")
FOOTER = "Draft for attorney review. Not legal advice."

_FLAG_COLOUR = "B3261E"
_PLACEHOLDER_COLOUR = "FFF2C7"
_CHECK_COLUMNS = ["Document", "In force", "In force, not shown to model", "Problems", "Ambiguous",
                  "Unchecked", "Citation needed"]

# Pieces of a draft that are styled: the checker's inline marks, the
# models' placeholders, and Markdown bold.
_INLINE = re.compile(r"(?P<flag>\[⚠ [^\]]*\])|(?P<placeholder>\[(?:CITATION|FACT) NEEDED:[^\]]*\])"
                     r"|\*\*(?P<bold>.+?)\*\*")


@dataclass(frozen=True)
class Block:
    kind: str  # "title", "heading", "para", "note", "bullets", "table" or "pagebreak"
    text: str = ""
    items: tuple = ()  # bullets: (text, url) pairs. table: rows, the first being the header.


def spans(text: str) -> list[tuple[str, str]]:
    """`text` as (piece, style) pairs: style is "", "bold", "flag" or "placeholder"."""
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], ""))
        style = m.lastgroup
        out.append((m.group("bold") if style == "bold" else m.group(), style))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], ""))
    return out


def blocks(result: dict, title: str, generated: str) -> list[Block]:
    """The whole document set, in reading order, for either format."""
    meta = result.get("law_meta", {})
    out = [
        Block("title", f"Battle test: {title}"),
        Block("note", DISCLAIMER),
        Block("bullets", items=tuple((line, "") for line in (
            f"Jurisdiction: {result['state']} and federal law",
            f"Law checked against: statutes, court rules and constitutions current as of "
            f"{meta.get('snapshot_date', '?')} ({meta.get('attribution', 'Open US Law')}, "
            f"snapshot {meta.get('snapshot', '?')})",
            f"Generated: {generated}",
            f"Rounds: {result['rounds']}",
            f"Plaintiff model: {result['plaintiff_model']}",
            f"Defendant model: {result['defendant_model']}",
        ))),
        Block("heading", "Citation check"),
    ]
    rows = [tuple(_CHECK_COLUMNS)]
    for doc in result["documents"]:
        s = render.check_summary(doc)
        rows.append((doc["title"], *(str(s[k]) for k in ("verified", "unseen", "problems", "ambiguous",
                                                         "unchecked", "placeholders"))))
    out.append(Block("table", items=tuple(rows)))
    for doc in result["documents"]:
        notable = [(f"{n['label']}: {n['text']}" + (f" (matched {n['matched']})" if n["matched"] else ""), "")
                   for n in render.notable_checks(doc)]
        notable += [(f"⚠ not checked (case law, or a format the checker can't read): {line}", "")
                    for line in doc["unchecked"]]
        if notable:
            out += [Block("para", f"**{doc['title']}**"), Block("bullets", items=tuple(notable))]

    for doc in result["documents"]:
        source = "LLM-generated" if doc["generated"] else "user-supplied"
        out += [Block("pagebreak"), Block("heading", doc["title"]),
                Block("note", f"{doc['role'].title()} · {source}")]
        out += [Block("para", p.strip("\n")) for p in re.split(r"\n\s*\n", doc["text"]) if p.strip()]

    out += [Block("pagebreak"), Block("heading", "Appendix: authorities and research")]
    for role, queries in result.get("research", {}).items():
        out.append(Block("para", f"**{role.title()} research queries:** " + "; ".join(queries)))
    authorities = render.authorities(result)
    if authorities:
        out.append(Block("para", "**Authorities quoted to the models:**"))
        out.append(Block("bullets", items=tuple(
            (f"{a['citation']} {a['section_title']} (given for: {', '.join(a['given_for'])})"
             + ("" if a.get("source_url") else " ⚠ no official source URL"), a.get("source_url") or "")
            for a in authorities)))
    return out


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def to_docx(result: dict, title: str, generated: str) -> bytes:
    doc = Document()
    doc.core_properties.title = f"Battle test: {title}"
    doc.core_properties.author = "battle_test"
    doc.core_properties.comments = FOOTER
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Times New Roman", Pt(12)
    doc.sections[0].footer.paragraphs[0].text = FOOTER

    def write(paragraph, text: str) -> None:
        for piece, style in spans(text):
            for i, line in enumerate(piece.split("\n")):
                if i:
                    paragraph.add_run().add_break()  # captions and signature blocks keep their lines
                run = paragraph.add_run(line)
                if style in ("bold", "flag"):
                    run.bold = True
                if style == "flag":
                    run.font.color.rgb = RGBColor.from_string(_FLAG_COLOUR)
                if style == "placeholder":
                    run.font.highlight_color = WD_COLOR_INDEX.YELLOW

    for block in blocks(result, title, generated):
        if block.kind == "title":
            doc.add_heading(block.text, level=0)
        elif block.kind == "heading":
            doc.add_heading(block.text, level=1)
        elif block.kind == "pagebreak":
            doc.add_page_break()
        elif block.kind == "note":
            doc.add_paragraph().add_run(block.text).italic = True
        elif block.kind == "para":
            write(doc.add_paragraph(), block.text)
        elif block.kind == "bullets":
            for text, url in block.items:
                write(doc.add_paragraph(style="List Bullet"), f"{text} {url}".strip())
        elif block.kind == "table":
            table = doc.add_table(rows=0, cols=len(block.items[0]), style="Table Grid")
            for n, row in enumerate(block.items):
                for cell, value in zip(table.add_row().cells, row):
                    cell.paragraphs[0].add_run(value).bold = n == 0
            doc.add_paragraph()
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

# The PDF uses the standard Times fonts, which need no font file but cover
# only Western European text (Windows-1252). The check marks become words,
# and anything else outside that set loses its accents or becomes "?".
_PDF_SYMBOLS = {"✅": "", "☑": "", "❌": "", "⚠": "!", "→": "->", "≥": ">=", "≤": "<="}


def pdf_text(text: str) -> str:
    out = []
    for ch in text:
        ch = _PDF_SYMBOLS.get(ch, ch)
        try:
            ch.encode("cp1252")
        except UnicodeEncodeError:
            ch = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode() or "?"
        out.append(ch)
    return "".join(out)


def _markup(text: str) -> str:
    """`text` as reportlab paragraph markup. Everything is escaped first, so
    model output can't inject tags."""
    parts = []
    for piece, style in spans(text):
        html = escape(pdf_text(piece)).replace("\n", "<br/>")
        if style == "bold":
            html = f"<b>{html}</b>"
        elif style == "flag":
            html = f'<font color="#{_FLAG_COLOUR}"><b>{html}</b></font>'
        elif style == "placeholder":
            html = f'<font backColor="#{_PLACEHOLDER_COLOUR}">{html}</font>'
        parts.append(html)
    return "".join(parts)


def to_pdf(result: dict, title: str, generated: str) -> bytes:
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=12, leading=16, spaceAfter=8)
    styles = {
        "title": ParagraphStyle("title", parent=body, fontName="Times-Bold", fontSize=18, leading=22, spaceAfter=12),
        "heading": ParagraphStyle("heading", parent=body, fontName="Times-Bold", fontSize=14, leading=18,
                                  spaceBefore=6, spaceAfter=8),
        "note": ParagraphStyle("note", parent=body, fontName="Times-Italic"),
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=18, bulletIndent=6, spaceAfter=4),
        "cell": ParagraphStyle("cell", parent=body, fontSize=9, leading=11, spaceAfter=0),    }

    story = []
    for block in blocks(result, title, generated):
        if block.kind == "pagebreak":
            story.append(PageBreak())
        elif block.kind == "bullets":
            for text, url in block.items:
                link = f' <link href={quoteattr(url)} color="blue">{escape(pdf_text(url))}</link>' if url else ""
                story.append(Paragraph(_markup(text) + link, styles["bullet"], bulletText="•"))
            story.append(Spacer(1, 6))
        elif block.kind == "table":
            width = 6.5 * inch
            first = 1.7 * inch  # leaves each count column wide enough for "Ambiguous" on one line
            rest = (width - first) / (len(block.items[0]) - 1)
            cells = [[Paragraph(_markup(f"**{v}**" if n == 0 else v), styles["cell"]) for v in row]
                     for n, row in enumerate(block.items)]
            table = Table(cells, colWidths=[first] + [rest] * (len(block.items[0]) - 1), repeatRows=1)
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 4),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                                       ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)]))
            story += [table, Spacer(1, 12)]
        else:
            story.append(Paragraph(_markup(block.text), styles.get(block.kind, body)))

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Times-Roman", 9)
        canvas.drawCentredString(letter[0] / 2, 0.6 * inch, f"{FOOTER}   Page {doc.page}")
        canvas.restoreState()

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=letter, leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch,
                      title=pdf_text(f"Battle test: {title}"), author="battle_test",
                      ).build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


BUILDERS = {"docx": to_docx, "pdf": to_pdf}
