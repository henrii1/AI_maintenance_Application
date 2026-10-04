"""Low-level PDF text helpers shared by the ingestion steps."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Running headers sit above y=45pt and footers below y=750pt on every Ariel document (US Letter, 792pt).
BODY_TOP = 45.0
BODY_BOTTOM = 750.0


@dataclass(frozen=True)
class TextBlock:
    page: int  # 1-based
    bbox: tuple[float, float, float, float]
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return normalize_ws(" ".join(self.lines))

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def page_blocks(page: pymupdf.Page) -> list[TextBlock]:
    """Text blocks in the body band of a page, in PyMuPDF's reading order."""
    out: list[TextBlock] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        lines = tuple(t for line in b["lines"] if (t := "".join(s["text"] for s in line["spans"]).strip()))
        if not lines:
            continue
        x0, y0, x1, y1 = b["bbox"]
        if y1 <= BODY_TOP or y0 >= BODY_BOTTOM:
            continue
        out.append(TextBlock(page.number + 1, (x0, y0, x1, y1), lines))
    return out


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def canon(s: str) -> str:
    """ASCII, lower-case, alphanumerics separated by single spaces."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def normalize_caption(s: str) -> str:
    return canon(re.sub(r"^\s*figure:\s*", "", s, flags=re.I))


def slugify(s: str, max_len: int = 80) -> str:
    return normalize_caption(s).replace(" ", "-")[:max_len].strip("-")


def approx_tokens(text: str) -> int:
    return math.ceil(len(text.split()) * 1.3)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())
