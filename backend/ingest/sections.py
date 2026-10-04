"""Turn a PDF's table of contents into positioned sections."""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from backend.ingest.pdfutil import BODY_TOP, normalize_ws

SEP = " › "


@dataclass(frozen=True)
class Section:
    path: str
    page: int
    y: float


def _heading_y(doc: pymupdf.Document, page: int, title: str) -> float:
    if not 1 <= page <= doc.page_count:
        return BODY_TOP
    for rect in doc[page - 1].search_for(title[:60]):
        if rect.y0 >= BODY_TOP:
            return rect.y0
    return BODY_TOP


def build_sections(doc: pymupdf.Document, fallback_title: str) -> list[Section]:
    toc = doc.get_toc()
    if not toc:
        return [Section(fallback_title, 1, 0.0)]
    out: list[Section] = []
    stack: list[str] = []
    prev = (0, 0.0)
    for level, title, page in toc:
        title = normalize_ws(title)
        stack = stack[: level - 1] + [title]
        pos = (page, _heading_y(doc, page, title))
        if pos < prev:  # keep positions monotonic if a heading search hit the wrong line
            pos = prev
        prev = pos
        out.append(Section(SEP.join(stack), pos[0], pos[1]))
    if (out[0].page, out[0].y) > (1, BODY_TOP):
        out.insert(0, Section(fallback_title, 1, 0.0))
    return out


def section_at(sections: list[Section], page: int, y: float) -> str:
    current = sections[0].path
    for s in sections:
        if (s.page, s.y) <= (page, y):
            current = s.path
        else:
            break
    return current
