"""Turn one real Battle Test result (Markdown, fictional sample case) into a
styled Word file for the beta package, with an introduction for the reader."""

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

SRC = Path(sys.argv[1])
FACTS = Path(sys.argv[2])
OUT = Path(sys.argv[3])

NAVY = RGBColor(0x1B, 0x2A, 0x41)
BRASS = RGBColor(0x9A, 0x6B, 0x1F)
GREY = RGBColor(0x55, 0x5D, 0x6B)
RED = RGBColor(0xA3, 0x1F, 0x1F)

doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Inches(8.5), Inches(11)
for side in ("left_margin", "right_margin"):
    setattr(sec, side, Inches(1))
sec.top_margin = sec.bottom_margin = Inches(1)

styles = doc.styles
normal = styles["Normal"]
normal.font.name = "Times New Roman"
normal.font.size = Pt(11.5)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
normal.paragraph_format.space_after = Pt(6)
for level, size, colour in ((1, 20, NAVY), (2, 15, NAVY), (3, 12, NAVY), (4, 11.5, NAVY)):
    st = styles[f"Heading {level}"]
    st.font.name = "Cambria"
    st.element.rPr.rFonts.set(qn("w:asciiTheme"), "") if False else None
    st.font.size = Pt(size)
    st.font.bold = True
    st.font.italic = False
    st.font.color.rgb = colour
    st.paragraph_format.space_before = Pt(14 if level < 3 else 10)
    st.paragraph_format.space_after = Pt(6)
    st.paragraph_format.keep_with_next = True
    rpr = st.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.append(fonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        fonts.set(qn(attr), "Cambria")
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        if fonts.get(qn(attr)) is not None:
            del fonts.attrib[qn(attr)]


def shade(paragraph, fill):
    ppr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    ppr.append(shd)


def left_border(paragraph, colour="9A6B1F"):
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    for k, v in (("w:val", "single"), ("w:sz", "18"), ("w:space", "8"), ("w:color", colour)):
        left.set(qn(k), v)
    borders.append(left)
    ppr.append(borders)


def run_shade(run, fill):
    rpr = run._r.get_or_add_rPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    rpr.append(shd)


def hyperlink(paragraph, text, url):
    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    colour = OxmlElement("w:color")
    colour.set(qn("w:val"), "1F4E8C")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rpr.append(colour)
    rpr.append(underline)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


TOKEN = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[⚠ MAY NOT SUPPORT THIS\]|"
                   r"\[CITATION NEEDED[^\]]*\]|\[❌[^\]]*\]|\[! [^\]]*\])")


def add_inline(paragraph, text, *, bold=False, italic=False, size=None, colour=None, font=None):
    pos = 0
    for m in TOKEN.finditer(text):
        if m.start() > pos:
            _run(paragraph, text[pos:m.start()], bold, italic, size, colour, font)
        tok = m.group(0)
        if tok.startswith("**"):
            add_inline(paragraph, tok[2:-2], bold=True, italic=italic, size=size, colour=colour, font=font)
        elif tok.startswith("`"):
            _run(paragraph, tok[1:-1], bold, italic, size, colour, font)
        elif tok.startswith("[⚠") or tok.startswith("[❌") or tok.startswith("[!"):
            r = _run(paragraph, tok, True, False, size, RED, "Arial")
            r.font.size = Pt(9)
            run_shade(r, "FBE3E3")
        elif tok.startswith("[CITATION NEEDED"):
            r = _run(paragraph, tok, True, False, size, RGBColor(0x6B, 0x4A, 0x0B), "Arial")
            r.font.size = Pt(9)
            run_shade(r, "F6ECD2")
        elif tok.startswith("["):
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", tok).groups()
            hyperlink(paragraph, label, url)
        else:  # *italic*
            add_inline(paragraph, tok[1:-1], bold=bold, italic=True, size=size, colour=colour, font=font)
        pos = m.end()
    if pos < len(text):
        _run(paragraph, text[pos:], bold, italic, size, colour, font)


def _run(paragraph, text, bold, italic, size, colour, font):
    r = paragraph.add_run(text)
    r.bold = bold or None
    r.italic = italic or None
    if size:
        r.font.size = Pt(size)
    if colour is not None:
        r.font.color.rgb = colour
    if font:
        r.font.name = font
    return r


def para(text="", style=None, **kw):
    p = doc.add_paragraph(style=style)
    if text:
        add_inline(p, text, **kw)
    return p


def label(text):
    p = doc.add_paragraph()
    r = p.add_run(text.upper())
    r.font.name = "Arial"
    r.font.size = Pt(8.5)
    r.font.bold = True
    r.font.color.rgb = BRASS
    p.paragraph_format.space_after = Pt(2)
    return p


def page_break():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def set_cell_shading(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def table(rows, widths=None, header=True, size=9.5):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            cell = t.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            add_inline(p, value, bold=header and i == 0, size=size, font="Arial")
            if header and i == 0:
                set_cell_shading(cell, "E8E4DA")
            if widths:
                cell.width = widths[j]
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


# ---------------------------------------------------------------- introduction
label("Battle Test · Sample result")
h = doc.add_heading("A real result, on an invented case", level=1)
para("This is an unedited result from Battle Test, as a beta tester would download it. The case is "
     "fictional: every name, business and event in it was invented for testing. It was produced on "
     "2026-11-05 with the model the beta uses (gpt-oss 120B, hosted on Amazon Bedrock), in about "
     "7 minutes, for about five cents of computing.", colour=None)

doc.add_heading("How to read it", level=2)
for line in (
    "**Citation check** (the first section): every statute, rule and constitution citation in each document, "
    "checked in code against a copy of the law current as of 2026-09-30. It also lists the citations a second "
    "model thought may not, or may only partly, support the point they're cited for, with its reason.",
    "**The documents**: the complaint and summary judgment motion (the plaintiff's model), the opposition "
    "(the defendant's model), and the plaintiff's reply.",
    "**Marks in the text**: [⚠ MAY NOT SUPPORT THIS] means the checking model doubts the section says what "
    "the paragraph claims. [CITATION NEEDED: …] means the model wanted authority it wasn't shown, such as case "
    "law, which Battle Test doesn't check yet. Both are left in for the lawyer to resolve.",
    "**Appendix**: what each side searched for, and every section of law quoted to the models, with a link to "
    "its official source.",
):
    para(line, style="List Bullet")

doc.add_heading("What this sample shows, honestly", level=2)
para("The citations exist and are in force. The checker's flags are a mix: some catch real problems (the "
     "venue statute cited for something it doesn't say), and some are over-cautious. The drafts leave gaps "
     "where case law would go. That's the intended behaviour: a starting point for a lawyer, never a "
     "document to file.")

doc.add_heading("The facts entered", level=2)
facts = [line for line in FACTS.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]
for line in facts:
    para(line[2:], style="List Bullet")

page_break()

# ------------------------------------------------------------- the result itself
text = SRC.read_text(encoding="utf-8")
lines = text.splitlines()
i = 0
in_document = False
first_h2 = True


def flush_table(block):
    rows = []
    for row in block:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        rows.append(cells)
    if rows:
        short = {"unchecked lines": "not checked", "`[CITATION NEEDED]`": "citation needed",
                 }
        rows[0] = [short.get(c, c) for c in rows[0]]
        n = len(rows[0])
        if n == 8:
            widths = [Inches(w) for w in (1.4, 0.6, 0.75, 0.8, 0.75, 0.85, 0.65, 0.7)]
        else:
            widths = [Inches(1.6)] + [Inches(4.9 / (n - 1))] * (n - 1) if n > 2 else None
        t = table(rows, widths, size=8 if n > 5 else 9.5)
        t.autofit = False


while i < len(lines):
    raw = lines[i]
    line = raw.rstrip()
    stripped = line.strip()

    if stripped.startswith("|"):
        block = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            block.append(lines[i])
            i += 1
        flush_table(block)
        continue

    if stripped == "" :
        i += 1
        continue

    if re.fullmatch(r"-{3,}", stripped):
        i += 1
        continue

    if stripped.startswith("# "):
        label("Battle Test · Result")
        doc.add_heading("Whitfield v. Summit Peak Roofing (fictional) — Utah", level=1)
        i += 1
        continue

    if stripped.startswith("## "):
        heading = stripped[3:].strip()
        if not first_h2:
            page_break()
        first_h2 = False
        doc.add_heading(heading, level=1 if heading in ("Citation check",) else 1)
        in_document = heading not in ("Citation check",) and not heading.startswith("Appendix")
        i += 1
        continue

    m = re.match(r"^(#{3,4})\s+(.*)$", stripped)
    if m:
        doc.add_heading(m.group(2).strip(), level=len(m.group(1)) - 1)
        i += 1
        continue

    if stripped.startswith(">"):
        p = para(stripped.lstrip("> ").strip(), size=10, font="Arial")
        shade(p, "F4F1EA")
        left_border(p)
        i += 1
        continue

    m = re.match(r"^(\s*)[-*]\s+(.*)$", line)
    if m:
        p = para(m.group(2), style="List Bullet", size=10.5 if not in_document else None)
        # continuation lines indented under the bullet
        i += 1
        while i < len(lines) and lines[i].startswith("  ") and lines[i].strip() and \
                not re.match(r"^\s*[-*]\s+", lines[i]) and not re.match(r"^\s*\d+\.\s", lines[i]):
            p.add_run().add_break()
            add_inline(p, lines[i].strip(), size=9.5, colour=GREY)
            i += 1
        continue

    # Plain paragraph: join lines that end in two spaces as line breaks.
    p = doc.add_paragraph()
    first = True
    while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("#", "|", ">")) \
            and not re.fullmatch(r"-{3,}", lines[i].strip()) and not re.match(r"^\s*[-*]\s+", lines[i]):
        if not first:
            p.add_run().add_break()
        add_inline(p, lines[i].strip())
        first = False
        i += 1
        if not lines[i - 1].endswith("  "):
            break

# ---------------------------------------------------------------- footer
for section in doc.sections:
    fp = section.footer.paragraphs[0]
    r = fp.add_run("Battle Test sample result · fictional case · draft for attorney review, not legal advice")
    r.font.name = "Arial"
    r.font.size = Pt(8)
    r.font.color.rgb = GREY

doc.core_properties.title = "Battle Test sample result (fictional case)"
doc.core_properties.author = "Battle Test"
doc.save(OUT)
print("saved", OUT)
