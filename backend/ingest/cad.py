"""CAD drawings: one full-sheet asset plus one searchable chunk (title block, notes, dimension tables)."""
from __future__ import annotations

from backend.ingest.models import Asset, Chunk
from backend.ingest.pdfutil import approx_tokens, normalize_ws

CAD_DPI = 150
MAX_WORDS = 600


def extract_cad(doc, doc_id: str, title: str) -> Asset:
    page = doc[0]
    return Asset(id=f"{doc_id}/sheet-1", doc_id=doc_id, asset_type="cad", caption=title, source_caption=title,
                 page=1, bbox=tuple(page.rect), section_path=f"Drawing {doc_id.upper()}", kind="cad")


def cad_chunk(doc, doc_id: str, asset: Asset) -> Chunk:
    words = normalize_ws(" ".join(p.get_text() for p in doc)).split()[:MAX_WORDS]
    text = f"{asset.caption}. {' '.join(words)}"
    return Chunk(id=f"{doc_id}:0001", doc_id=doc_id, section_path=asset.section_path, page_start=1,
                 page_end=doc.page_count, text=text, token_count=approx_tokens(text), has_warning=False,
                 figure_refs=[asset.id])
