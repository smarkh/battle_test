"""Pull the text out of an uploaded complaint: Word (.docx), PDF or plain text.

Everything happens here, on the server. The file itself is never stored:
its text goes back into the form for the user to check, and only what they
then submit becomes the case's input.

Uploads come from users, so every parser is fenced in: a size limit on the
file, a limit on how much a .docx may expand to, and a page limit on PDFs.
"""

import io
import re
import zipfile
from collections import Counter

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# A .docx is a zip. A small one can expand to gigabytes (a "zip bomb"), so
# its contents are measured before anything opens it.
MAX_DOCX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 200
KINDS = {".docx": "Word", ".pdf": "PDF", ".txt": "text"}


class ExtractError(Exception):
    """Why a file couldn't be read, worded for the person who uploaded it."""


def extract_text(filename: str, data: bytes) -> str:
    """The text of an uploaded file, tidied for the models to read."""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in KINDS:
        raise ExtractError("Upload a Word (.docx), PDF (.pdf) or text (.txt) file. "
                           "For an older Word .doc, save it as .docx first.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ExtractError(f"The file is too large: the limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if not data:
        raise ExtractError("The file is empty.")
    text = {".docx": _docx, ".pdf": _pdf, ".txt": _txt}[suffix](data)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ExtractError(f"No text was found in this {KINDS[suffix]} file.")
    return text


# ---------------------------------------------------------------------------
# Plain text
# ---------------------------------------------------------------------------

def _txt(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    raise ExtractError("This text file isn't in an encoding that can be read. Save it as UTF-8 and try again.")


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
          (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


def _roman(n: int) -> str:
    out = ""
    for value, letters in _ROMAN:
        while n >= value:
            out, n = out + letters, n - value
    return out


def _number(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        s = chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)
        return s.upper() if fmt == "upperLetter" else s
    if fmt in ("lowerRoman", "upperRoman"):
        return _roman(n).upper() if fmt == "upperRoman" else _roman(n)
    if fmt == "bullet":
        return "•"
    return str(n)


class _Numbering:
    """Word's automatic paragraph numbers ("1.", "a.") aren't part of the
    text, but a complaint's numbered paragraphs are referred to by number,
    so they're rebuilt. Formats and start values come from the document;
    restarts and fancier patterns are approximated as plain counting."""

    def __init__(self, doc):
        self.formats: dict[tuple[str, str], tuple[str, int, str]] = {}  # (numId, level) -> (fmt, start, text)
        self.counters: dict[tuple[str, str], int] = {}
        try:
            root = doc.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return
        abstract = {}
        for a in root.findall(f"{_W}abstractNum"):
            levels = {}
            for lvl in a.findall(f"{_W}lvl"):
                fmt, start, text = lvl.find(f"{_W}numFmt"), lvl.find(f"{_W}start"), lvl.find(f"{_W}lvlText")
                levels[lvl.get(f"{_W}ilvl")] = (
                    fmt.get(f"{_W}val") if fmt is not None else "decimal",
                    int(start.get(f"{_W}val")) if start is not None else 1,
                    text.get(f"{_W}val") if text is not None else "%1.",
                )
            abstract[a.get(f"{_W}abstractNumId")] = levels
        for num in root.findall(f"{_W}num"):
            ref = num.find(f"{_W}abstractNumId")
            if ref is not None:
                for level, spec in abstract.get(ref.get(f"{_W}val"), {}).items():
                    self.formats[(num.get(f"{_W}numId"), level)] = spec

    def label(self, paragraph) -> str:
        num_pr = _num_pr(paragraph)
        if num_pr is None:
            return ""
        num_id = num_pr.find(f"{_W}numId")
        level = num_pr.find(f"{_W}ilvl")
        num_id = num_id.get(f"{_W}val") if num_id is not None else None
        level = level.get(f"{_W}val") if level is not None else "0"
        if not num_id or num_id == "0":
            return ""
        fmt, start, text = self.formats.get((num_id, level), ("decimal", 1, "%1."))
        key = (num_id, level)
        self.counters[key] = self.counters.get(key, start - 1) + 1
        # A higher level restarts the levels below it.
        for other in [k for k in self.counters if k[0] == num_id and int(k[1]) > int(level)]:
            del self.counters[other]
        if fmt == "none":
            return ""
        shown = _number(self.counters[key], fmt)
        return (re.sub(r"%\d", shown, text) if fmt != "bullet" else "•") + " "


def _num_pr(paragraph):
    """A paragraph's numbering, set on the paragraph or by its style."""
    p_pr = paragraph._p.pPr
    if p_pr is not None and p_pr.numPr is not None:
        return p_pr.numPr
    style = paragraph.style
    while style is not None:
        s_pr = style.element.pPr
        if s_pr is not None and s_pr.numPr is not None:
            return s_pr.numPr
        style = style.base_style
    return None


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(info.file_size for info in z.infolist()) > MAX_DOCX_EXPANDED_BYTES:
                raise ExtractError("This Word file is too large to read.")
    except zipfile.BadZipFile:
        raise ExtractError("This isn't a readable Word (.docx) file. For an older .doc, save it as .docx first.")

    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    try:
        doc = Document(io.BytesIO(data))
    except Exception:  # python-docx raises several kinds for a damaged file
        raise ExtractError("This Word file couldn't be read. It may be damaged.")

    numbering = _Numbering(doc)
    blocks = []
    # Paragraphs and tables in document order: complaint captions are often tables.
    for child in doc.element.body.iterchildren():
        if child.tag == f"{_W}p":
            paragraph = Paragraph(child, doc)
            text = paragraph.text.strip()
            if text:
                blocks.append(numbering.label(paragraph) + text)
        elif child.tag == f"{_W}tbl":
            for row in Table(child, doc).rows:
                cells = []
                for cell in row.cells:
                    text = " ".join(cell.text.split())
                    if text and (not cells or cells[-1] != text):  # merged cells repeat their text
                        cells.append(text)
                if cells:
                    blocks.append(" | ".join(cells))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}(?:\s+of\s+\d{1,4})?$|^-\s*\d{1,4}\s*-$", re.IGNORECASE)
# Pleading paper numbers each line down the margin (1 to 28 on California
# pleadings). They come out as lines holding only a small number.
_LINE_NUMBER = re.compile(r"^\d{1,2}$")
# A line that starts a new paragraph rather than continuing the last one:
# a numbered or lettered paragraph, a bullet, or a heading.
_HEADING = re.compile(r"^[A-Z][A-Z0-9 ,.'&:;()-]{3,}$")
_STARTS_BLOCK = re.compile(rf"^(?:\d{{1,3}}\.\s|\(?[a-z0-9]{{1,3}}\)\s|[•▪◦-]\s)|{_HEADING.pattern}")


def _pdf(data: bytes) -> str:
    if not data.lstrip().startswith(b"%PDF"):
        raise ExtractError("This isn't a readable PDF file.")
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractError("This PDF is password-protected. Save an unprotected copy and upload that.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ExtractError(f"This PDF has {len(reader.pages)} pages: the limit is {MAX_PDF_PAGES}.")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ExtractError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, RecursionError):
        raise ExtractError("This PDF couldn't be read. It may be damaged.")
    if not "".join(pages).strip():
        raise ExtractError("No text was found in this PDF. It looks scanned (a picture of the pages), and "
                           "scanned PDFs can't be read yet. Paste the text instead, or upload the Word file.")
    return _tidy_pdf(pages)


def _tidy_pdf(pages: list[str]) -> str:
    """Join the PDF's pages back into paragraphs: drop running headers and
    footers, page numbers and pleading line numbers, mend words split at a
    line end, and join lines that the page width broke."""
    page_lines = [[" ".join(line.split()) for line in page.splitlines()] for page in pages]
    # A line found on at least half of the pages (3 or more) is a header or
    # footer. Digits are ignored when comparing, so "Complaint - Page 2" and
    # "Complaint - Page 3" count as the same line.
    def shape(line: str) -> str:
        return re.sub(r"\d+", "#", line)

    seen = Counter(shape(line) for lines in page_lines for line in {shape(x) for x in lines if x})
    repeated = {s for s, n in seen.items() if len(pages) >= 3 and n >= len(pages) / 2}

    lines: list[str] = []
    for page in page_lines:
        for line in page:
            if shape(line) in repeated or _PAGE_NUMBER.match(line) or _LINE_NUMBER.match(line):
                continue
            lines.append(line)

    paragraphs: list[str] = []
    for line in lines:
        if not line:
            if paragraphs and paragraphs[-1]:
                paragraphs.append("")
            continue
        last = paragraphs[-1] if paragraphs else ""
        if last and not _STARTS_BLOCK.match(line) and not _HEADING.match(last) \
                and not re.search(r"[.:;?!]$", last):
            if re.search(r"[a-z]-$", last) and line[:1].islower():
                paragraphs[-1] = last[:-1] + line  # a word split across lines
            else:
                paragraphs[-1] = f"{last} {line}"
        else:
            paragraphs.append(line)
    return "\n\n".join(p for p in paragraphs if p)
