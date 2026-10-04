import pymupdf

from backend.ingest.cad import CAD_DPI, cad_chunk, extract_cad
from backend.ingest.render import render_asset
from tests.conftest import SET_DIR

CAD = SET_DIR / "cad" / "OL0782_S1.PDF"


def test_cad_asset_chunk_and_render(tmp_path):
    doc = pymupdf.open(CAD)
    asset = extract_cad(doc, "ol0782", "OL0782 Encoder Shaft Attachment Details")
    assert asset.id == "ol0782/sheet-1"
    assert asset.asset_type == "cad" and asset.kind == "cad"
    assert asset.section_path == "Drawing OL0782"

    chunk = cad_chunk(doc, "ol0782", asset)
    assert chunk.id == "ol0782:0001"
    assert chunk.figure_refs == ["ol0782/sheet-1"]
    assert chunk.text.startswith("OL0782 Encoder Shaft Attachment Details.")
    assert "KBE" in chunk.text and "ENCODER SHAFT" in chunk.text
    assert chunk.token_count <= 800

    render_asset(doc, asset, tmp_path, dpi=CAD_DPI)
    assert asset.image_path == "cad/ol0782.png"
    assert (tmp_path / "cad" / "ol0782.thumb.png").is_file()
    assert asset.width > 1500
