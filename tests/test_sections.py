import pymupdf

from backend.ingest.sections import Section, build_sections, section_at
from tests.conftest import MANUAL_PDF


def test_manual_sections_follow_toc():
    doc = pymupdf.open(MANUAL_PDF)
    secs = build_sections(doc, "KBE Maintenance & Repair Manual")
    assert len(secs) == len(doc.get_toc()) + 1  # + front-matter fallback
    assert secs[0] == Section("KBE Maintenance & Repair Manual", 1, 0.0)
    positions = [(s.page, s.y) for s in secs]
    assert positions == sorted(positions)


def test_section_lookup():
    doc = pymupdf.open(MANUAL_PDF)
    secs = build_sections(doc, "KBE Maintenance & Repair Manual")
    assert section_at(secs, 88, 100) == "Section 4 - Part Replacement › Crossheads › Crosshead Installation"
    assert section_at(secs, 10, 300) == "Section 1 - Tools › Ariel Optional Tools"
    assert section_at(secs, 1, 300) == "KBE Maintenance & Repair Manual"


def test_document_without_toc_uses_fallback():
    doc = pymupdf.open()
    doc.new_page()
    assert build_sections(doc, "Untitled Sheet") == [Section("Untitled Sheet", 1, 0.0)]
