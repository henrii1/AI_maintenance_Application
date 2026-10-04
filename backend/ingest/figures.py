"""Caption-anchored figure detection.

Ariel captions are standalone text blocks reading "Figure: <title>" placed ABOVE the graphic, with a
numbered callout legend below or beside it. Callout circles are often vector overlays, so we crop page
regions (caption ∪ graphics ∪ legend) rather than extracting embedded images.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import pymupdf

from backend.ingest.models import Asset
from backend.ingest.pdfutil import BODY_BOTTOM, BODY_TOP, TextBlock, normalize_ws, page_blocks, slugify
from backend.ingest.sections import Section, section_at

LEGEND_START = re.compile(r"^\d{1,2}(\s|$)")        # "1 Crosshead" / "1" — but not "2. Remove ..."
NUMBER_COLUMN = re.compile(r"^\d{1,2}( \d{1,2})*$")  # "1 2 3 4" column beside a label column
ADMONITION = re.compile(r"^(NOTE|CAUTION|WARNING|DANGER)\b")
LEGEND_REACH = 60.0   # pt below the graphics in which legend blocks are attached
SIDE_REACH = 40.0     # pt to the right of the graphics in which side legends are attached
MIN_UNCAPTIONED = 60.0
SMALL_FIGURE = 60.0


@dataclass(frozen=True)
class FoundFigure:
    caption: str
    page: int
    rect: pymupdf.Rect | None
    legend_text: str


def is_caption(b: TextBlock) -> bool:
    return b.lines[0].startswith("Figure:") and len(b.lines) <= 3 and not b.text.endswith(".")


def is_legend_like(b: TextBlock, blocks: list[TextBlock]) -> bool:
    if LEGEND_START.match(b.lines[0]):
        return True
    x0, y0, _, y1 = b.bbox
    return any(NUMBER_COLUMN.match(o.text) and o.bbox[3] > y0 and o.bbox[1] < y1 and 0 <= x0 - o.bbox[2] <= 25
               for o in blocks if o is not b)


def is_body(b: TextBlock, blocks: list[TextBlock]) -> bool:
    if is_caption(b) or is_legend_like(b, blocks):
        return False
    return b.word_count >= 10 or bool(ADMONITION.match(b.text))


def _graphics(page: pymupdf.Page, body_rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    width = page.rect.width
    rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    rects += [pymupdf.Rect(d["rect"]) for d in page.get_drawings() if d["rect"].width > 3 or d["rect"].height > 3]
    return [r for r in rects
            if r.y0 >= BODY_TOP and r.y1 <= BODY_BOTTOM and r.width < 0.9 * width
            and not any(r.contains(b) for b in body_rects)]  # drops NOTE/CAUTION box borders


def find_page_figures(page: pymupdf.Page) -> list[FoundFigure]:
    blocks = page_blocks(page)
    caps = [b for b in blocks if is_caption(b)]
    if not caps:
        return []
    body = [b for b in blocks if is_body(b, blocks)]
    gfx = _graphics(page, [pymupdf.Rect(b.bbox) for b in body])
    page_right = page.rect.width - 30
    out: list[FoundFigure] = []
    for c in caps:
        cr = pymupdf.Rect(c.bbox)
        right = min([o.bbox[0] - 4 for o in caps
                     if o is not c and o.bbox[0] > cr.x1 - 1 and o.bbox[1] < cr.y1 and o.bbox[3] > cr.y0]
                    + [page_right])
        left = cr.x0 - 10
        cap_bottom = min([o.bbox[1] for o in caps
                          if o is not c and o.bbox[1] > cr.y1 and o.bbox[0] < right and o.bbox[2] > left]
                         + [BODY_BOTTOM])
        stop = min([b.bbox[1] for b in body
                    if b.bbox[1] > cr.y1 and b.bbox[0] < right and b.bbox[2] > left + 15], default=BODY_BOTTOM)
        bottom = min(cap_bottom, stop)
        caption = normalize_ws(c.text.removeprefix("Figure:"))
        g = [r for r in gfx if r.y0 >= cr.y1 - 2 and r.y1 <= cap_bottom + 2 and r.y0 < stop
             and r.x0 >= left - 5 and r.x1 <= right + 5]
        if not g:
            out.append(FoundFigure(caption, page.number + 1, None, ""))
            continue
        box = pymupdf.Rect(g[0])
        for r in g[1:]:
            box |= r
        gbox = pymupdf.Rect(box)
        legend: list[TextBlock] = []
        changed = True
        while changed:
            changed = False
            for b in blocks:
                r = pymupdf.Rect(b.bbox)
                if b in legend or gbox.contains(r) or not is_legend_like(b, blocks):
                    continue
                if (cr.y1 - 2 <= r.y0 <= box.y1 + LEGEND_REACH and r.x0 <= box.x1 + SIDE_REACH
                        and r.x0 >= left - 5 and r.x1 <= right + 5 and r.y1 <= bottom + 2):
                    box |= r
                    legend.append(b)
                    changed = True
        legend.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        out.append(FoundFigure(caption, page.number + 1, box | cr, " ".join(b.text for b in legend)))
    return out


def _uncaptioned(page: pymupdf.Page, taken: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    return [r for r in rects
            if min(r.width, r.height) >= MIN_UNCAPTIONED and r.y0 >= BODY_TOP and r.y1 <= BODY_BOTTOM
            and not any(t.intersects(r) for t in taken)]


def extract_figures(doc: pymupdf.Document, doc_id: str, role: str, sections: list[Section],
                    override: Callable[[str], dict]) -> tuple[list[Asset], list[str]]:
    assets: list[Asset] = []
    problems: list[str] = []
    used: set[str] = set()

    def unique(fid: str) -> str:
        base, n = fid, 2
        while fid in used:
            fid, n = f"{base}-{n}", n + 1
        used.add(fid)
        return fid

    for page in doc:
        n = page.number + 1
        taken: list[pymupdf.Rect] = []
        for f in find_page_figures(page):
            fid = unique(f"{doc_id}/{slugify(f.caption)}")
            ov = override(fid)
            if ov.get("exclude"):
                continue
            flags: list[str] = []
            rect = f.rect
            if "bbox" in ov:
                rect = pymupdf.Rect(ov["bbox"])
                flags.append("overridden")
            if rect is None:
                problems.append(f"{fid} (p.{n}): caption found but no graphics detected; add a bbox in overrides.yaml")
                continue
            if rect.height < SMALL_FIGURE:
                flags.append("small")
            taken.append(rect)
            assets.append(Asset(
                id=fid, doc_id=doc_id, asset_type="figure", caption=ov.get("caption", f.caption),
                source_caption=f.caption, page=n, bbox=tuple(rect), legend_text=f.legend_text,
                section_path=section_at(sections, n, rect.y0), flags=flags))
        if role == "engineering_reference":
            for k, rect in enumerate(_uncaptioned(page, taken), start=1):
                fid = unique(f"{doc_id}/p{n}-image-{k}")
                if override(fid).get("exclude"):
                    continue
                flags = ["uncaptioned"]
                if override(fid).get("bbox"):
                    rect = pymupdf.Rect(override(fid)["bbox"])
                    flags.append("overridden")
                section = section_at(sections, n, rect.y0)
                assets.append(Asset(
                    id=fid, doc_id=doc_id, asset_type="figure",
                    caption=f"{section.split(' › ')[-1]} (image, p.{n})", source_caption="",
                    page=n, bbox=tuple(rect), section_path=section, flags=flags))
    return assets, problems
