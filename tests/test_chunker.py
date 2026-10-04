import re

import pymupdf
import pytest

from backend.ingest.chunker import MAX_TOKENS, Piece, chunk_document, figure_refs_for, pack
from backend.ingest.figures import extract_figures
from backend.ingest.models import Asset
from backend.ingest.sections import build_sections
from tests.conftest import MANUAL_PDF


@pytest.fixture(scope="module")
def manual_chunks():
    doc = pymupdf.open(MANUAL_PDF)
    sections = build_sections(doc, "KBE Maintenance & Repair Manual")
    figures, _ = extract_figures(doc, "kbe-manual", "manual", sections, lambda fid: {})
    return chunk_document(doc, "kbe-manual", sections, figures)


def test_chunks_exclude_running_headers_and_toc_leaders(manual_chunks):
    for c in manual_chunks:
        assert not re.search(r"Page \d+ of 154", c.text)
        assert "KBE Maintenance & Repair Manual - Section" not in c.text
        assert "......" not in c.text


def test_chunk_ids_and_size_bounds(manual_chunks):
    ids = [c.id for c in manual_chunks]
    assert ids == [f"kbe-manual:{i:04d}" for i in range(1, len(ids) + 1)]
    assert all(c.token_count <= MAX_TOKENS for c in manual_chunks)
    assert 120 <= len(manual_chunks) <= 700


def test_crosshead_installation_chunk(manual_chunks):
    c = next(c for c in manual_chunks if "Lay a 3/16 inch" in c.text)
    assert c.section_path.endswith("Crosshead Installation")
    assert c.page_start <= 88 <= c.page_end
    assert "kbe-manual/crosshead-alignment-tool" in c.figure_refs   # "(see Figure: Crosshead Alignment Tool)"
    assert "kbe-manual/crosshead-installation" in c.figure_refs     # same section and page
    assert "Metal Plate" not in c.text                               # legend text lives with the figure


def test_warning_flag(manual_chunks):
    flagged = [c for c in manual_chunks if c.has_warning]
    assert flagged
    assert all(re.search(r"\b(WARNING|CAUTION|DANGER)\b", c.text) for c in flagged)


def test_pack_respects_budget_and_overlap():
    short = Piece("x " * 50, 1)          # 65 tokens
    big = Piece("y " * 300, 2)           # 390 tokens
    # pieces over 120 tokens are never repeated as overlap
    assert pack([big, big, short, big]) == [[big, big], [short, big]]
    # a short trailing piece (≤120 tokens) is repeated at the start of the next chunk
    assert pack([big, short, big, big]) == [[big, short], [short, big], [big]]


def test_figure_refs_match_caption_mentions_and_section():
    a = Asset(id="d/oil-passages", doc_id="d", asset_type="figure", caption="Oil Passages (renamed)",
              source_caption="Oil Passages", page=5, bbox=(0, 0, 1, 1), section_path="S › Other")
    b = Asset(id="d/crank", doc_id="d", asset_type="figure", caption="Crank", source_caption="Crank",
              page=5, bbox=(0, 0, 1, 1), section_path="S › Here")
    assert figure_refs_for("Clean them (see Figure: Oil  Passages).", "S › Here", 5, 5, [a, b]) == ["d/oil-passages", "d/crank"]
    assert figure_refs_for("Unrelated text.", "S › Elsewhere", 9, 9, [a, b]) == []
