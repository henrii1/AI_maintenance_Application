from __future__ import annotations

from dataclasses import dataclass, field

from backend.ingest.pdfutil import normalize_caption


@dataclass
class Asset:
    id: str
    doc_id: str
    asset_type: str  # "figure" | "cad"
    caption: str
    page: int
    bbox: tuple[float, float, float, float]
    section_path: str
    source_caption: str = ""  # caption as printed in the PDF (used to match "see Figure: X")
    legend_text: str = ""
    kind: str = ""
    description: str = ""
    components: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    image_path: str = ""  # relative to content_dir
    thumb_path: str = ""
    width: int = 0
    height: int = 0
    image_sha: str = ""

    @property
    def caption_norm(self) -> str:
        return normalize_caption(self.caption)


@dataclass
class Chunk:
    id: str
    doc_id: str
    section_path: str
    page_start: int
    page_end: int
    text: str
    token_count: int
    has_warning: bool
    figure_refs: list[str] = field(default_factory=list)
