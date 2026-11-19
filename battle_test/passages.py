"""Cut a law section's text into pieces, for excerpts and the passage index.

Two sizes are built from the same cuts:
- blocks (about BLOCK_CHARS): what grounding.relevant_excerpt picks from
  when it shows the part of a long section that matters.
- passages (up to PASSAGE_CHARS): what keyword search ranks, so a long
  section is found by its best part rather than penalised for its length
  (plans/plan.md, "Passages, decided 2026-10-22", stage 2). Stage 3 will
  embed the same passages, so PASSAGE_CHARS matches the 1,500 characters
  semantic search embeds of each section.

Standard library only: the index build and the pipeline both use it.
"""

import re

BLOCK_CHARS = 600  # long paragraphs are split into pieces about this size
PASSAGE_CHARS = 1500  # a passage is consecutive blocks up to this size
# Stored in the index's meta table. Change it whenever the cuts change, so an
# index built with other cuts is noticed (stage 3's vectors depend on them).
PASSAGES_VERSION = f"1:{PASSAGE_CHARS}"

_SUBDIVISION = re.compile(r"\s(?=\(\w{1,4}\)\s)")  # before "(a) ", "(12) ", "(iv) "


def parts(block: str) -> list[str]:
    """A block cut at its subdivisions ("(a)", "(1)"), or else at sentence ends."""
    pieces = _SUBDIVISION.split(block)
    return pieces if len(pieces) > 1 else re.split(r"(?<=[.;:])\s+", block)


def split_long(paragraph: str) -> list[str]:
    """A paragraph cut into pieces of about BLOCK_CHARS, at subdivisions
    ("(a)", "(1)") where it has them, else at sentence ends."""
    if len(paragraph) <= BLOCK_CHARS:
        return [paragraph]
    pieces: list[str] = []
    for part in parts(paragraph):
        if pieces and len(pieces[-1]) + 1 + len(part) <= BLOCK_CHARS:
            pieces[-1] += " " + part
        else:
            pieces.append(part)
    return pieces


def blocks(text: str) -> list[str]:
    """The section's paragraphs, whitespace tidied, long ones split."""
    paragraphs = [" ".join(p.split()) for p in text.splitlines() if p.strip()]
    return [piece for p in paragraphs for piece in split_long(p)]


def _hard_split(block: str, limit: int) -> list[str]:
    """A block with no usable break (a table, a list with no full stops)
    cut at spaces into pieces of at most `limit`."""
    pieces: list[str] = []
    while len(block) > limit:
        cut = block.rfind(" ", 0, limit + 1)
        cut = cut if cut > 0 else limit
        pieces.append(block[:cut])
        block = block[cut:].lstrip()
    return pieces + ([block] if block else [])


def split(text: str, limit: int = PASSAGE_CHARS) -> list[str]:
    """The section as passages of at most `limit` characters, in order and
    without overlap. A section that fits is one passage."""
    tidy = " ".join(text.split())
    if len(tidy) <= limit:
        return [tidy] if tidy else []
    out: list[str] = []
    current = ""
    for block in blocks(text):
        for piece in _hard_split(block, limit) if len(block) > limit else [block]:
            if current and len(current) + 1 + len(piece) > limit:
                out.append(current)
                current = piece
            else:
                current = f"{current} {piece}" if current else piece
    if current:
        out.append(current)
    return out
