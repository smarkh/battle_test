"""Turn a saved result (the JSON form of a CaseRun) into a Word or PDF file.

Both are built in memory from the same list of blocks, so the two formats
can't drift apart, and nothing extra is stored with the case: a download is
generated when it's asked for. Both follow the web UI's look (navy and brass,
serif headings, ruled tables), so a download matches the page it came from.
"""

import io
import re
import unicodedata
from dataclasses import dataclass
from xml.sax.saxutils import escape, quoteattr

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from lxml import etree
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from battle_test import report
from battle_test.web import render

FORMATS = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

def disclaimer(support_checked: bool = False) -> str:
    """The Markdown report's disclaimer, as plain text."""
    return re.sub(r"^> |\*\*|`", "", report.disclaimer(support_checked)).replace("⚠ mark", "[⚠ …] mark")


DISCLAIMER = disclaimer()
FOOTER = "Draft for attorney review. Not legal advice."
BRAND = "Battle Test"
KICKER = "Battle Test · Adversarial case review"

# The web UI's palette (static/app.css).
NAVY, INK, MUTED, BRASS = "1F3358", "1B2130", "5C6270", "A8894A"
RULE, HAIR, PAPER = "C8C1B1", "DCD7CC", "F5F3EE"
_FLAG_COLOUR = "9A2A2A"
_PLACEHOLDER_COLOUR = "FBEFC8"
_CHECK_COLUMNS = ["Document", "In force", "In force, not shown to model", "Problems", "May not support",
                  "Ambiguous", "Unchecked", "Citation needed"]
_PROBLEMS = _CHECK_COLUMNS.index("Problems")

# Pieces of a draft that are styled: the checker's inline marks, the
# models' placeholders, and Markdown bold.
_INLINE = re.compile(r"(?P<flag>\[⚠ [^\]]*\])|(?P<placeholder>\[(?:CITATION|FACT) NEEDED:[^\]]*\])"
                     r"|\*\*(?P<bold>.+?)\*\*")


@dataclass(frozen=True)
class Block:
    # "kicker" (a small label above a title), "title" (the case), "doctitle"
    # (each document and part), "heading" (a section), "notice" (the
    # disclaimer), "facts", "para", "note", "bullets", "table" or "pagebreak".
    kind: str
    text: str = ""
    # bullets: (text, url) pairs. facts: (label, value) pairs. table: rows, the first being the header.
    items: tuple = ()


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


def _has_problems(value: str) -> bool:
    return value not in ("0", "–", "")


def blocks(result: dict, title: str, generated: str) -> list[Block]:
    """The whole document set, in reading order, for either format."""
    meta = result.get("law_meta", {})
    checked = render.support_checked(result)
    out = [
        Block("kicker", KICKER),
        Block("title", title),
        Block("notice", disclaimer(checked)),
        Block("facts", items=(
            ("Jurisdiction", f"{result['state']} and federal law"),
            ("Law checked against", f"Statutes, court rules and constitutions current as of "
                                    f"{meta.get('snapshot_date', '?')} ({meta.get('attribution', 'Open US Law')}, "
                                    f"snapshot {meta.get('snapshot', '?')})"),
            ("Generated", generated),
            ("Rounds", str(result["rounds"])),
            ("Plaintiff model", result["plaintiff_model"]),
            ("Defendant model", result["defendant_model"]),
            *([("Citation checker model", result["checker_model"])] if checked else []),
        )),
        Block("heading", "Citation check"),
    ]
    rows = [tuple(_CHECK_COLUMNS)]
    for doc in result["documents"]:
        s = render.check_summary(doc, checked)
        rows.append((doc["title"], *(str(s[k]) for k in ("verified", "unseen", "problems", "doubted", "ambiguous",
                                                         "unchecked", "placeholders"))))
    out.append(Block("table", items=tuple(rows)))
    for doc in result["documents"]:
        notable = [(f"{n['label']}: {n['text']}" + (f" (matched {n['matched']})" if n["matched"] else "")
                    + (f". Checker's note: {n['reason']}" if n["reason"] else ""), "")
                   for n in render.notable_checks(doc)]
        notable += [(f"⚠ not checked (case law, or a format the checker can't read): {line}", "")
                    for line in doc["unchecked"]]
        if notable:
            out += [Block("para", f"**{doc['title']}**"), Block("bullets", items=tuple(notable))]

    for doc in result["documents"]:
        source = "LLM-generated" if doc["generated"] else "user-supplied"
        out += [Block("pagebreak"), Block("kicker", f"{doc['role'].title()} · {source}"),
                Block("doctitle", doc["title"])]
        out += [Block("para", p.strip("\n")) for p in re.split(r"\n\s*\n", doc["text"]) if p.strip()]

    possible = render.possible_references(result)
    if possible:
        out += [Block("pagebreak"), Block("kicker", "For the attorney to consider"),
                Block("doctitle", "Possible references"),
                Block("note", report.possible_references_note(bool(result.get("fit"))))]
        out.append(Block("bullets", items=tuple(
            (f"**{a['citation']}** {a['section_title']} — {a['excerpt']}"
             + (f" {report.HELD_BACK} Checker's note: {a['held_back']}" if a.get("held_back") else "")
             + ("" if a.get("source_url") else " ⚠ no official source URL"), a.get("source_url") or "")
            for a in possible)))

    out += [Block("pagebreak"), Block("kicker", "Appendix"), Block("doctitle", "Authorities and research")]
    research = result.get("research", {})
    if research:
        out.append(Block("heading", "Research queries"))
    for role, queries in research.items():
        out.append(Block("para", f"**{role.title()}:** " + "; ".join(queries)))
    authorities = render.authorities(result)
    if authorities:
        out.append(Block("heading", "Authorities quoted to the models"))
        out.append(Block("bullets", items=tuple(
            (f"{a['citation']} {a['section_title']} (given for: {', '.join(a['given_for'])})"
             + ("" if a.get("source_url") else " ⚠ no official source URL"), a.get("source_url") or "")
            for a in authorities)))
    dropped = render.set_aside(result)
    if dropped:
        out += [Block("heading", "Set aside before drafting"), Block("note", report.SET_ASIDE_NOTE)]
        out.append(Block("bullets", items=tuple(
            (f"{a['citation']} {a['section_title']}. Checker's note: {a['reason']}", a.get("source_url") or "")
            for a in dropped)))
    return out


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

# python-docx has no API for borders, shading or letter spacing, so those are
# added as XML. Word rejects elements out of schema order, so each goes in
# before the first sibling that must follow it.
_PPR_AFTER_BORDERS = {"shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap", "overflowPunct", "topLinePunct",
                      "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind",
                      "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc", "textDirection",
                      "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr",
                      "pPrChange"}
_PPR_AFTER_SHADING = _PPR_AFTER_BORDERS - {"shd"}
_RPR_AFTER_SPACING = {"w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
                      "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath"}
_TBLPR_AFTER_BORDERS = {"shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription", "tblPrChange"}
_TBLPR_AFTER_MARGINS = {"tblLook", "tblCaption", "tblDescription", "tblPrChange"}
_TCPR_AFTER_BORDERS = {"shd", "noWrap", "tcMar", "textDirection", "tcFitText", "vAlign", "hideMark"}
_TCPR_AFTER_SHADING = _TCPR_AFTER_BORDERS - {"shd"}
_RPR_AFTER_SHADING = {"fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath"}

# Palatino Linotype comes with Windows and Office; Word on a Mac substitutes Palatino.
HEADING_FONT, BODY_FONT, LABEL_FONT = "Palatino Linotype", "Times New Roman", "Arial"


def _put(parent, element, successors: set) -> None:
    """Insert `element` into `parent`, replacing any of the same tag, in schema order."""
    for old in parent.findall(element.tag):
        parent.remove(old)
    for i, child in enumerate(parent):
        if isinstance(child.tag, str) and etree.QName(child).localname in successors:
            parent.insert(i, element)
            return
    parent.append(element)


def _borders(tag: str, **sides):
    """A border element. Each side is (colour, width in eighths of a point), or None for no line."""
    element = OxmlElement(tag)
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        if side not in sides:
            continue
        line = OxmlElement(f"w:{side}")
        if sides[side] is None:
            line.set(qn("w:val"), "nil")
        else:
            colour, size = sides[side]
            line.set(qn("w:val"), "single")
            line.set(qn("w:sz"), str(size))
            line.set(qn("w:space"), "4" if tag == "w:pBdr" else "0")
            line.set(qn("w:color"), colour)
        element.append(line)
    return element


def _shading(fill: str):
    element = OxmlElement("w:shd")
    element.set(qn("w:val"), "clear")
    element.set(qn("w:color"), "auto")
    element.set(qn("w:fill"), fill)
    return element


def _rule(ppr, **sides) -> None:
    _put(ppr, _borders("w:pBdr", **sides), _PPR_AFTER_BORDERS)


def _tracking(rpr, points: float) -> None:
    element = OxmlElement("w:spacing")
    element.set(qn("w:val"), str(round(points * 20)))
    _put(rpr, element, _RPR_AFTER_SPACING)


def _font(font, name: str, size: float, colour: str, *, bold=False, italic=False, caps=False) -> None:
    font.name, font.size, font.bold, font.italic, font.all_caps = name, Pt(size), bold, italic, caps
    font.color.rgb = RGBColor.from_string(colour)


def _run(paragraph, text: str, name: str, size: float, colour: str, *, tracking: float = 0, **kw):
    run = paragraph.add_run(text)
    _font(run.font, name, size, colour, **kw)
    if tracking:
        _tracking(run._r.get_or_add_rPr(), tracking)
    return run


def _restyle(style, name: str, size: float, colour: str, *, before: float, after: float, rule: tuple,
             tracking: float = 0, **kw) -> None:
    """Replace a built-in style's theme fonts, colours and border with the site's."""
    rpr = style.element.get_or_add_rPr()
    for child in list(rpr):
        rpr.remove(child)
    _font(style.font, name, size, colour, **kw)
    if tracking:
        _tracking(rpr, tracking)
    _rule(style.element.get_or_add_pPr(), bottom=rule)
    style.paragraph_format.space_before, style.paragraph_format.space_after = Pt(before), Pt(after)


def _cell(cell, text: str, *, label=False, narrow=False, center=False, bad=False, fill: str = "", bottom: tuple | None = None):
    paragraph = cell.paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    if center:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if label:  # narrow: the citation check's count columns, where spaced capitals would break mid-word
        _run(paragraph, text, LABEL_FONT, 6.5 if narrow else 7, MUTED, bold=True, caps=True,
             tracking=0 if narrow else 0.6)
    else:
        _run(paragraph, text, BODY_FONT, 10.5, _FLAG_COLOUR if bad else INK, bold=bad)
    tcpr = cell._tc.get_or_add_tcPr()
    if bottom:
        _put(tcpr, _borders("w:tcBorders", bottom=bottom), _TCPR_AFTER_BORDERS)
    if fill:
        _put(tcpr, _shading(fill), _TCPR_AFTER_SHADING)


def _table(doc, rows: int, widths: list[float]):
    """A table ruled like the web page's: lines between rows, none down the sides."""
    table = doc.add_table(rows=0, cols=len(widths))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _put(table._tbl.tblPr, _borders("w:tblBorders", top=(RULE, 8), left=None, bottom=(RULE, 8), right=None,
                                    insideH=(HAIR, 4), insideV=None), _TBLPR_AFTER_BORDERS)
    margins = OxmlElement("w:tblCellMar")
    for side, twips in (("top", 60), ("left", 70), ("bottom", 60), ("right", 70)):
        margin = OxmlElement(f"w:{side}")
        margin.set(qn("w:w"), str(twips))
        margin.set(qn("w:type"), "dxa")
        margins.append(margin)
    _put(table._tbl.tblPr, margins, _TBLPR_AFTER_MARGINS)
    for _ in range(rows):
        for cell, width in zip(table.add_row().cells, widths):
            cell.width = Inches(width)
    return table


def _page_number(paragraph) -> None:
    """A PAGE field, which Word fills in on each page."""
    def field_char(kind: str) -> None:
        element = OxmlElement("w:fldChar")
        element.set(qn("w:fldCharType"), kind)
        _run(paragraph, "", LABEL_FONT, 7.5, MUTED)._r.append(element)

    field_char("begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    _run(paragraph, "", LABEL_FONT, 7.5, MUTED)._r.append(instruction)
    field_char("separate")
    _run(paragraph, "1", LABEL_FONT, 7.5, MUTED)
    field_char("end")


def to_docx(result: dict, title: str, generated: str) -> bytes:
    doc = Document()
    doc.core_properties.title = f"{BRAND}: {title}"
    doc.core_properties.author = BRAND
    doc.core_properties.comments = FOOTER

    section = doc.sections[0]
    section.left_margin = section.right_margin = section.top_margin = section.bottom_margin = Inches(1)
    width = section.page_width.inches - 2  # the text width: 6.5 inches on Letter
    normal = doc.styles["Normal"]
    _font(normal.font, BODY_FONT, 12, INK)
    normal.paragraph_format.space_after, normal.paragraph_format.line_spacing = Pt(6), 1.15
    _restyle(doc.styles["Title"], HEADING_FONT, 24, INK, before=0, after=12, rule=(BRASS, 12))
    _restyle(doc.styles["Heading 1"], HEADING_FONT, 19, INK, before=0, after=12, rule=(RULE, 4))
    _restyle(doc.styles["Heading 2"], LABEL_FONT, 8.5, NAVY, before=18, after=6, rule=(RULE, 4), bold=True,
             caps=True, tracking=1.5)

    # A running header on every page but the first (which has the title), and a footer on all of them.
    section.different_first_page_header_footer = True
    for name in ("Header", "Footer"):  # their own centre and right tabs would catch the tab before ours
        doc.styles[name].paragraph_format.tab_stops.clear_all()
    head = section.header.paragraphs[0]
    head.paragraph_format.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
    _rule(head._p.get_or_add_pPr(), bottom=(BRASS, 6))
    _run(head, BRAND, HEADING_FONT, 8.5, NAVY, bold=True, caps=True, tracking=1.5)
    _run(head, f"\t{title}", LABEL_FONT, 7.5, MUTED)
    for footer in (section.footer, section.first_page_footer):
        foot = footer.paragraphs[0]
        foot.paragraph_format.tab_stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
        _rule(foot._p.get_or_add_pPr(), top=(HAIR, 4))
        _run(foot, f"{FOOTER}\tPage ", LABEL_FONT, 7.5, MUTED)
        _page_number(foot)

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
                if style == "placeholder":  # shading, not a highlight: Word's highlights are only bright colours
                    _put(run._r.get_or_add_rPr(), _shading(_PLACEHOLDER_COLOUR), _RPR_AFTER_SHADING)

    for block in blocks(result, title, generated):
        if block.kind == "kicker":
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.keep_with_next = True
            _run(paragraph, block.text, LABEL_FONT, 8, BRASS, bold=True, caps=True, tracking=1.5)
        elif block.kind == "title":
            doc.add_heading(block.text, level=0)
        elif block.kind == "doctitle":
            doc.add_heading(block.text, level=1)
        elif block.kind == "heading":
            doc.add_heading(block.text, level=2)
        elif block.kind == "pagebreak":
            doc.add_page_break()
        elif block.kind == "notice":
            paragraph = doc.add_paragraph()
            ppr = paragraph._p.get_or_add_pPr()
            _rule(ppr, left=(BRASS, 24))
            _put(ppr, _shading(PAPER), _PPR_AFTER_SHADING)
            paragraph.paragraph_format.left_indent = paragraph.paragraph_format.right_indent = Pt(6)
            paragraph.paragraph_format.space_before, paragraph.paragraph_format.space_after = Pt(4), Pt(14)
            _run(paragraph, block.text, BODY_FONT, 10.5, INK)
        elif block.kind == "facts":
            table = _table(doc, len(block.items), [1.75, width - 1.75])
            for row, (name, value) in zip(table.rows, block.items):
                _cell(row.cells[0], name, label=True)
                _cell(row.cells[1], value)
        elif block.kind == "note":
            _run(doc.add_paragraph(), block.text, BODY_FONT, 11, MUTED, italic=True)
        elif block.kind == "para":
            write(doc.add_paragraph(), block.text)
        elif block.kind == "bullets":
            for text, url in block.items:
                paragraph = doc.add_paragraph(style="List Bullet")
                write(paragraph, text)
                if url:
                    _run(paragraph, f" {url}", LABEL_FONT, 8.5, NAVY)
        elif block.kind == "table":
            columns, first = len(block.items[0]), 1.4
            table = _table(doc, len(block.items), [first] + [(width - first) / (columns - 1)] * (columns - 1))
            for n, (row, values) in enumerate(zip(table.rows, block.items)):
                for i, (cell, value) in enumerate(zip(row.cells, values)):
                    if n == 0:
                        _cell(cell, value, label=True, narrow=i > 0, center=i > 0, fill=PAPER, bottom=(RULE, 12))
                    else:
                        _cell(cell, value, center=i > 0, bad=i == _PROBLEMS and _has_problems(value))
        if block.kind in ("facts", "table"):
            doc.add_paragraph().paragraph_format.space_after = Pt(4)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

# The PDF uses the standard Times and Helvetica fonts, which need no font file
# but cover only Western European text (Windows-1252). The check marks become
# words, and anything else outside that set loses its accents or becomes "?".
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


def _hex(colour: str):
    return colors.HexColor(f"#{colour}")


def to_pdf(result: dict, title: str, generated: str) -> bytes:
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=11, leading=15.5, spaceAfter=7,
                          textColor=_hex(INK))
    label = ParagraphStyle("label", parent=body, fontName="Helvetica-Bold", fontSize=6.8, leading=9, spaceAfter=0,
                           textColor=_hex(MUTED))
    cell = ParagraphStyle("cell", parent=body, fontSize=9.5, leading=12, spaceAfter=0)
    styles = {
        "kicker": ParagraphStyle("kicker", parent=body, fontName="Helvetica-Bold", fontSize=7.5, leading=10,
                                 spaceAfter=3, textColor=_hex(BRASS)),
        "title": ParagraphStyle("title", parent=body, fontSize=24, leading=28, spaceAfter=2),
        "doctitle": ParagraphStyle("doctitle", parent=body, fontSize=19, leading=23, spaceAfter=0),
        "heading": ParagraphStyle("heading", parent=body, fontName="Helvetica-Bold", fontSize=8, leading=10,
                                  spaceBefore=14, spaceAfter=0, textColor=_hex(NAVY)),
        "note": ParagraphStyle("note", parent=body, fontName="Times-Italic", textColor=_hex(MUTED)),
        "notice": ParagraphStyle("notice", parent=body, fontSize=10, leading=14, spaceAfter=0),
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=14, bulletIndent=3, spaceAfter=4,
                                 bulletColor=_hex(BRASS)),
    }
    centred = ParagraphStyle("centred", parent=cell, alignment=TA_CENTER)
    bad = ParagraphStyle("bad", parent=centred, textColor=_hex(_FLAG_COLOUR))
    head_centred = ParagraphStyle("head_centred", parent=label, fontSize=6.3, leading=8, alignment=TA_CENTER)
    # Under each title and heading: (line width, colour, space after).
    rules = {"title": (1.5, BRASS, 14), "doctitle": (0.6, RULE, 12), "heading": (0.5, RULE, 6)}
    # The text width: platypus pads its frame by 6 points a side inside the margins.
    pad = 6
    width = letter[0] - 2 * inch - 2 * pad
    ruled = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
             ("LINEABOVE", (0, 0), (-1, 0), 0.8, _hex(RULE)),
             ("LINEBELOW", (0, 0), (-1, -1), 0.4, _hex(HAIR)),
             ("LINEBELOW", (0, -1), (-1, -1), 0.8, _hex(RULE))]

    story, pending = [], []  # pending: kickers, titles and headings, kept with whatever follows them

    def add(*flowables) -> None:
        nonlocal pending
        if pending and flowables and not isinstance(flowables[0], PageBreak):
            story.append(KeepTogether(pending + [flowables[0]]))
            flowables, pending = flowables[1:], []
        story.extend(pending + list(flowables))
        pending = []

    for block in blocks(result, title, generated):
        if block.kind == "pagebreak":
            add(PageBreak())
        elif block.kind == "kicker":
            pending.append(Paragraph(_markup(block.text.upper()), styles["kicker"]))
        elif block.kind in rules:
            text = block.text.upper() if block.kind == "heading" else block.text
            thickness, colour, after = rules[block.kind]
            pending += [Paragraph(_markup(text), styles[block.kind]),
                        HRFlowable(width="100%", thickness=thickness, color=_hex(colour), spaceBefore=4,
                                   spaceAfter=after)]
        elif block.kind == "notice":
            box = Table([[Paragraph(_markup(block.text), styles["notice"])]], colWidths=[width])
            box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), _hex(PAPER)),
                                     ("LINEBEFORE", (0, 0), (0, -1), 3, _hex(BRASS)),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                     ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
            add(box, Spacer(1, 14))
        elif block.kind == "facts":
            table = Table([[Paragraph(_markup(name.upper()), label), Paragraph(_markup(value), cell)]
                           for name, value in block.items], colWidths=[1.7 * inch, width - 1.7 * inch])
            table.setStyle(TableStyle(ruled))
            add(table, Spacer(1, 6))
        elif block.kind == "bullets":
            for text, url in block.items:
                link = (f' <link href={quoteattr(url)} color="#{NAVY}"><font size="8.5">{escape(pdf_text(url))}'
                        f'</font></link>') if url else ""
                add(Paragraph(_markup(text) + link, styles["bullet"], bulletText="•"))
            add(Spacer(1, 6))
        elif block.kind == "table":
            columns = len(block.items[0])
            first = 1.45 * inch  # leaves each count column wide enough for "Ambiguous" on one line
            cells = [[Paragraph(_markup(v.upper()), label if i == 0 else head_centred)
                      for i, v in enumerate(block.items[0])]]
            for row in block.items[1:]:
                cells.append([Paragraph(_markup(f"**{v}**"), bad) if i == _PROBLEMS and _has_problems(v)
                              else Paragraph(_markup(v), cell if i == 0 else centred)
                              for i, v in enumerate(row)])
            table = Table(cells, colWidths=[first] + [(width - first) / (columns - 1)] * (columns - 1), repeatRows=1)
            table.setStyle(TableStyle(ruled + [("BACKGROUND", (0, 0), (-1, 0), _hex(PAPER)),
                                               ("LINEBELOW", (0, 0), (-1, 0), 1, _hex(RULE)),
                                               ("VALIGN", (0, 0), (-1, 0), "BOTTOM")]))
            add(table, Spacer(1, 12))
        else:
            add(Paragraph(_markup(block.text), styles.get(block.kind, body)))
    story.extend(pending)

    left, right = inch + pad, letter[0] - inch - pad

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(_hex(HAIR))
        canvas.setLineWidth(0.5)
        canvas.line(left, 0.78 * inch, right, 0.78 * inch)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_hex(MUTED))
        canvas.drawString(left, 0.6 * inch, FOOTER)
        canvas.drawRightString(right, 0.6 * inch, f"Page {doc.page}")
        canvas.restoreState()

    def header_and_footer(canvas, doc) -> None:
        footer(canvas, doc)
        canvas.saveState()
        top = letter[1] - 0.62 * inch
        brand = canvas.beginText(left, top)
        brand.setFont("Times-Bold", 8.5)
        brand.setCharSpace(1.4)
        brand.setFillColor(_hex(NAVY))
        brand.textOut(BRAND.upper())
        brand.setCharSpace(0)  # the spacing would otherwise carry over to the title
        canvas.drawText(brand)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_hex(MUTED))
        short = pdf_text(title)
        canvas.drawRightString(right, top, short if len(short) <= 70 else short[:69] + "…")
        canvas.setStrokeColor(_hex(BRASS))
        canvas.setLineWidth(0.8)
        canvas.line(left, top - 6, right, top - 6)
        canvas.restoreState()

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=letter, leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch,
                      title=pdf_text(f"{BRAND}: {title}"), author=BRAND,
                      ).build(story, onFirstPage=footer, onLaterPages=header_and_footer)
    return out.getvalue()


BUILDERS = {"docx": to_docx, "pdf": to_pdf}
