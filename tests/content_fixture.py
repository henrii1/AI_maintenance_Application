"""A tiny, deterministic content.db (set "demo") for API and retrieval unit tests."""
from __future__ import annotations

from pathlib import Path

from backend.content_db import connect_content, init_content_schema
from backend.ingest.index import link_documents, replace_document, upsert_set
from backend.ingest.manifest import AlwaysInclude, Manifest
from backend.ingest.models import Asset, Chunk
from tests.fakes import HashEmbedder


def _asset(id_, page, caption, sha, asset_type="figure", section="S"):
    doc = id_.split("/")[0]
    rel = f"cad/{doc}.png" if asset_type == "cad" else f"figures/{id_}.png"
    return Asset(id=id_, doc_id=doc, asset_type=asset_type, caption=caption, source_caption=caption, page=page,
                 bbox=(0, 0, 100, 100), section_path=section, kind="cad" if asset_type == "cad" else "line_drawing",
                 description=f"Drawing of {caption.lower()}", image_path=rel,
                 thumb_path=rel.replace(".png", ".thumb.png"), width=100, height=100, image_sha=sha)


def _chunk(id_, section, page, text, refs, warn=False):
    return Chunk(id=id_, doc_id=id_.split(":")[0], section_path=section, page_start=page, page_end=page, text=text,
                 token_count=len(text.split()), has_warning=warn, figure_refs=refs)


def build_tiny_content(content_dir: Path) -> None:
    content_dir = Path(content_dir)
    conn = connect_content(content_dir / "content.db")
    init_content_schema(conn)
    upsert_set(conn, Manifest(id="demo", set_dir=Path("."), equipment={"name": "Demo Compressor", "manufacturer": "Acme"},
                              documents=(), always_include_sections=(AlwaysInclude("man", "Section 1 - Tools"),)))
    emb = HashEmbedder()
    man_chunks = [
        _chunk("man:0001", "Section 4 - Part Replacement › Crosshead Installation", 88,
               "Lay a 3/16 inch thick installation plate in the crosshead guide. Mount the crosshead installation "
               "tool (see Figure: Crosshead Alignment Tool). Rotate crosshead 90 degrees.",
               ["man/crosshead-installation", "man/crosshead-alignment-tool"]),
        _chunk("man:0002", "Section 1 - Tools › Recommended Tools", 12,
               "Recommended tools: torque wrench, feeler gauge, dial indicator, crosshead alignment tool.",
               ["man/optional-tools"]),
        _chunk("man:0003", "Section 3 - Maintenance › Daily Maintenance", 27,
               "Check frame oil level daily. CAUTION: surfaces are hot after shutdown.", [], warn=True),
        _chunk("man:0004", "Section 2 - Instrumentation › Proximity Switch A-18255", 17,
               "Wire the A-18255 proximity switch yellow leads to the control panel and green to ground.",
               ["man/a-18255-wiring"]),
    ]
    man_assets = [
        _asset("man/crosshead-installation", 88, "Crosshead Installation", "s1"),
        _asset("man/crosshead-alignment-tool", 89, "Crosshead Alignment Tool", "s2"),
        _asset("man/optional-tools", 10, "Optional Furnished Tools for KBE Compressors", "s3"),
        _asset("man/a-18255-wiring", 18, "A-18255 Proximity Switch Wiring", "s4"),
        _asset("man/crosshead-installation-copy", 90, "Crosshead Installation Copy", "s1"),  # same image
    ]
    replace_document(conn, doc_id="man", file_name="man.pdf", role="manual", title="Demo Manual", sha256="x",
                     page_count=154, pdf_path="docs/man.pdf", chunks=man_chunks, assets=man_assets, embedder=emb)
    cad = _asset("cad1/sheet-1", 1, "OL0782 Encoder Shaft Attachment Details", "s5", asset_type="cad",
                 section="Drawing CAD1")
    replace_document(conn, doc_id="cad1", file_name="cad1.pdf", role="cad", title="OL0782", sha256="y",
                     page_count=1, pdf_path="docs/cad1.pdf",
                     chunks=[_chunk("cad1:0001", "Drawing CAD1", 1,
                                    "OL0782 Encoder Shaft Attachment Details. Drill depth table for KBE frames.",
                                    ["cad1/sheet-1"])],
                     assets=[cad], embedder=emb)
    link_documents(conn, "demo", ["man", "cad1"])
    conn.close()
    for a in man_assets + [cad]:
        for rel in (a.image_path, a.thumb_path):
            (content_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (content_dir / rel).write_bytes(b"\x89PNG\r\n\x1a\n")
    (content_dir / "docs").mkdir(exist_ok=True)
    (content_dir / "docs" / "man.pdf").write_bytes(b"%PDF-1.4 demo")
