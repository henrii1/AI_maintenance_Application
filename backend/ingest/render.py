"""Render page regions to PNG + 400px-wide thumbnail."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from backend.ingest.models import Asset
from backend.ingest.pdfutil import sha256_bytes

PAD = 6.0
THUMB_WIDTH = 400


def render_clip(page: pymupdf.Page, rect: pymupdf.Rect | None, out_png: Path, out_thumb: Path,
                dpi: int) -> tuple[int, int, str]:
    clip = page.rect if rect is None else (pymupdf.Rect(rect) + (-PAD, -PAD, PAD, PAD)) & page.rect
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    data = pix.tobytes("png")
    out_png.write_bytes(data)
    zoom = THUMB_WIDTH / clip.width
    page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).save(out_thumb)
    return pix.width, pix.height, sha256_bytes(data)


def render_asset(doc: pymupdf.Document, asset: Asset, content_dir: Path, dpi: int = 200) -> None:
    content_dir = Path(content_dir)
    if asset.asset_type == "cad":
        rel = Path("cad") / f"{asset.doc_id}.png"
        rect = None
    else:
        rel = Path("figures") / asset.doc_id / f"{asset.id.split('/', 1)[1]}.png"
        rect = pymupdf.Rect(asset.bbox)
    thumb_rel = rel.with_suffix(".thumb.png")
    w, h, sha = render_clip(doc[asset.page - 1], rect, content_dir / rel, content_dir / thumb_rel, dpi)
    asset.image_path, asset.thumb_path = rel.as_posix(), thumb_rel.as_posix()
    asset.width, asset.height, asset.image_sha = w, h, sha
