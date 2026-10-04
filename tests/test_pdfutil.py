import pymupdf

from backend.ingest.pdfutil import (approx_tokens, canon, normalize_caption, page_blocks,
                                    sha256_bytes, slugify)
from tests.conftest import MANUAL_PDF


def test_page_blocks_strip_running_header_and_footer():
    page = pymupdf.open(MANUAL_PDF)[87]  # manual p.88
    texts = [b.text for b in page_blocks(page)]
    joined = " ".join(texts)
    assert "Crosshead Installation" in joined
    assert "Page 88 of 154" not in joined
    assert "KBE Maintenance & Repair Manual - Section 4" not in joined
    assert all(b.page == 88 for b in page_blocks(page))


def test_text_block_helpers():
    page = pymupdf.open(MANUAL_PDF)[87]
    caption = next(b for b in page_blocks(page) if b.text.startswith("Figure: Crosshead Installation"))
    assert caption.text == "Figure: Crosshead Installation"
    assert caption.word_count == 3


def test_caption_normalisation_and_slug():
    assert canon("RTD’s & Thermo-couples") == "rtds thermo couples"
    assert normalize_caption("Figure: Crosshead  Installation") == "crosshead installation"
    assert slugify("Figure: VVCP Disassembly/Assembly") == "vvcp-disassembly-assembly"
    assert slugify("A-18255 Proximity Switch Wiring Connections for Unit in Operation") == \
        "a-18255-proximity-switch-wiring-connections-for-unit-in-operation"
    assert len(slugify("x " * 100)) <= 80
    assert not slugify("x " * 100).endswith("-")


def test_approx_tokens_and_hash():
    assert approx_tokens("one two three four five six seven eight nine ten") == 13
    assert approx_tokens("") == 0
    assert len(sha256_bytes(b"abc")) == 64
