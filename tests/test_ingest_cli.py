import shutil

import pytest

from backend.config import Settings
from backend.content_db import connect_content
from backend.ingest.__main__ import ingest_set
from tests.conftest import SET_DIR
from tests.fakes import FakeAnthropic, HashEmbedder, message, tool_use

VISION = {"kind": "line_drawing", "complete": True, "has_body_text": False,
          "description": "A test description.", "components": ["guide"]}


@pytest.fixture
def mini(tmp_path):
    corpus = tmp_path / "corpus" / "mini"
    (corpus / "docs").mkdir(parents=True)
    (corpus / "cad").mkdir()
    shutil.copy(SET_DIR / "docs" / "ER-130.pdf", corpus / "docs")
    shutil.copy(SET_DIR / "cad" / "OL0782_S1.PDF", corpus / "cad")
    (corpus / "manifest.yaml").write_text(
        "id: mini\nequipment: {name: Mini Set}\ndocuments:\n"
        "  - {id: er-130, file: docs/ER-130.pdf, role: engineering_reference, title: ER-130 Cylinders}\n"
        "  - {id: ol0782, file: cad/OL0782_S1.PDF, role: cad, title: OL0782 Encoder Shaft}\n")
    settings = Settings(_env_file=None, corpus_dir=tmp_path / "corpus", content_dir=tmp_path / "content")
    return settings


def test_ingest_end_to_end_and_idempotent(mini):
    client = FakeAnthropic(lambda kw: message(tool_use("record_figure", VISION)))
    s1 = ingest_set(mini, "mini", force=False, client=client, embedder=HashEmbedder())
    assert s1.docs_processed == 2 and s1.docs_skipped == 0
    assert s1.figures >= 2 and s1.cad == 1 and s1.chunks >= 3
    conn = connect_content(mini.content_dir / "content.db", readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM set_documents WHERE set_id='mini'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == s1.chunks
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL").fetchone()[0] == 0
    row = conn.execute("SELECT * FROM assets WHERE id='er-130/compressor-guide-compartments'").fetchone()
    assert row["description"] == "A test description."
    assert (mini.content_dir / row["image_path"]).is_file()
    assert (mini.content_dir / "docs" / "er-130.pdf").is_file()
    assert s1.review_path.is_file() and "compressor-guide-compartments" in s1.review_path.read_text()
    calls = len(client.messages.calls)

    s2 = ingest_set(mini, "mini", force=False, client=client, embedder=HashEmbedder())
    assert s2.docs_processed == 0 and s2.docs_skipped == 2

    s3 = ingest_set(mini, "mini", force=True, client=client, embedder=HashEmbedder())
    assert s3.docs_processed == 2
    assert len(client.messages.calls) == calls  # vision cache hit for every image
    assert s3.chunks == s1.chunks              # re-ingest replaces, never duplicates


def test_skip_vision(mini):
    s = ingest_set(mini, "mini", force=False, client=None, embedder=HashEmbedder())
    assert s.vision_cost == 0 and s.figures >= 2


def test_ingest_detects_override_changes(mini):
    client = FakeAnthropic(lambda kw: message(tool_use("record_figure", VISION)))
    s1 = ingest_set(mini, "mini", force=False, client=client, embedder=HashEmbedder())
    conn = connect_content(mini.content_dir / "content.db", readonly=False)
    asset_count_before = conn.execute("SELECT COUNT(*) FROM assets WHERE id='er-130/compressor-guide-compartments'").fetchone()[0]
    conn.close()

    # Add an overrides.yaml that excludes a figure
    (mini.corpus_dir / "mini" / "overrides.yaml").write_text(
        "figures:\n"
        "  er-130/compressor-guide-compartments:\n"
        "    exclude: true\n")

    # Re-run ingest with force=False; should re-process er-130 due to override change
    s2 = ingest_set(mini, "mini", force=False, client=client, embedder=HashEmbedder())
    assert s2.docs_processed == 1 and s2.docs_skipped == 1

    # Verify the excluded asset is gone
    conn = connect_content(mini.content_dir / "content.db", readonly=True)
    asset_count_after = conn.execute("SELECT COUNT(*) FROM assets WHERE id='er-130/compressor-guide-compartments'").fetchone()[0]
    assert asset_count_before > 0 and asset_count_after == 0
    conn.close()
