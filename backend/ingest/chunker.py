"""Split a document into section-aware chunks (≤800 tokens) that remember which figures they reference."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

import pymupdf

from backend.ingest.models import Asset, Chunk
from backend.ingest.pdfutil import approx_tokens, canon, page_blocks
from backend.ingest.sections import Section, section_at

DOT_LEADER = re.compile(r"\.{6,}\s*\d*\s*$")  # table-of-contents lines
WARNING_WORDS = re.compile(r"\b(WARNING|CAUTION|DANGER)\b")
MAX_TOKENS = 800
MIN_TOKENS = 80
OVERLAP_MAX = 120
SPLIT_WORDS = 450  # oversized blocks (tables) are cut into ≤450-word pieces (≈585 tokens)


@dataclass(frozen=True)
class Piece:
    text: str
    page: int


def _inside_figure(bbox, rects: list[pymupdf.Rect]) -> bool:
    r = pymupdf.Rect(bbox)
    area = r.get_area() or 1.0
    return any((r & fr).get_area() / area >= 0.6 for fr in rects)


def _collect(doc, sections: list[Section], figures: list[Asset]) -> list[tuple[str, list[Piece]]]:
    fig_rects: dict[int, list[pymupdf.Rect]] = defaultdict(list)
    for a in figures:
        fig_rects[a.page].append(pymupdf.Rect(a.bbox))
    spans: dict[str, list[Piece]] = {}
    for page in doc:
        n = page.number + 1
        for b in page_blocks(page):
            if _inside_figure(b.bbox, fig_rects[n]) or any(DOT_LEADER.search(line) for line in b.lines):
                continue
            path = section_at(sections, n, b.bbox[1])
            words = b.text.split()
            for i in range(0, len(words), SPLIT_WORDS):
                spans.setdefault(path, []).append(Piece(" ".join(words[i:i + SPLIT_WORDS]), n))
    return list(spans.items())


def pack(pieces: list[Piece]) -> list[list[Piece]]:
    groups: list[list[Piece]] = []
    cur: list[Piece] = []
    tok = 0
    for p in pieces:
        t = approx_tokens(p.text)
        if cur and tok + t > MAX_TOKENS:
            groups.append(cur)
            last = cur[-1]
            lt = approx_tokens(last.text)
            cur, tok = ([last], lt) if lt <= OVERLAP_MAX else ([], 0)
        cur.append(p)
        tok += t
    if cur:
        groups.append(cur)
    return groups


def figure_refs_for(text: str, section_path: str, page_start: int, page_end: int,
                    figures: list[Asset]) -> list[str]:
    hay = canon(text)
    refs: list[str] = []
    for a in figures:
        cap = canon(a.source_caption or a.caption)
        mentioned = bool(cap) and f"figure {cap}" in hay
        co_located = a.section_path == section_path and page_start <= a.page <= page_end
        if (mentioned or co_located) and a.id not in refs:
            refs.append(a.id)
    return refs


def chunk_document(doc, doc_id: str, sections: list[Section], figures: list[Asset]) -> list[Chunk]:
    chunks: list[Chunk] = []
    spans = _collect(doc, sections, figures)
    carry: list[Piece] = []
    for i, (path, pieces) in enumerate(spans):
        pieces = carry + pieces
        carry = []
        if sum(approx_tokens(p.text) for p in pieces) < MIN_TOKENS and i < len(spans) - 1:
            carry = pieces  # tiny heading intro: merge into the next section
            continue
        for group in pack(pieces):
            text = "\n\n".join(p.text for p in group)
            ps, pe = min(p.page for p in group), max(p.page for p in group)
            chunks.append(Chunk(
                id=f"{doc_id}:{len(chunks) + 1:04d}", doc_id=doc_id, section_path=path,
                page_start=ps, page_end=pe, text=text, token_count=approx_tokens(text),
                has_warning=bool(WARNING_WORDS.search(text)),
                figure_refs=figure_refs_for(text, path, ps, pe, figures)))
    return chunks
