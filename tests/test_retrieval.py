import pytest

from backend.retrieval.search import Retriever, fts_query, rrf
from tests.content_fixture import build_tiny_content
from tests.fakes import HashEmbedder


@pytest.fixture(scope="module")
def retriever(tmp_path_factory):
    d = tmp_path_factory.mktemp("content")
    build_tiny_content(d)
    return Retriever.open(d, HashEmbedder())


def test_fts_query_escapes_and_drops_stopwords():
    assert fts_query("How do I wire the A-18255?") == '"wire" OR "18255"'
    assert fts_query("the of and") == ""
    assert fts_query('crosshead "torque" crosshead') == '"crosshead" OR "torque"'


def test_rrf_fuses_rankings():
    fused = rrf(["a", "b"], ["a", "c"])
    assert [i for i, _ in fused] == ["a", "b", "c"] or [i for i, _ in fused] == ["a", "c", "b"]
    assert fused[0] == ("a", 2 / 61)
    assert dict(fused)["b"] == dict(fused)["c"] == 1 / 62


def test_sets_and_get_set(retriever):
    assert retriever.sets() == [{"id": "demo", "name": "Demo Compressor",
                                 "equipment": {"name": "Demo Compressor", "manufacturer": "Acme"}}]
    s = retriever.get_set("demo")
    assert s["always_include"] == [{"doc": "man", "section_prefix": "Section 1 - Tools"}]
    assert retriever.get_set("nope") is None


def test_search_chunks_lexical_and_part_numbers(retriever):
    hits = retriever.search_chunks("demo", "crosshead installation plate")
    assert hits[0].id == "man:0001"
    assert hits[0].doc_title == "Demo Manual"
    assert hits[0].figure_refs == ("man/crosshead-installation", "man/crosshead-alignment-tool")
    assert retriever.search_chunks("demo", "A-18255 wiring")[0].id == "man:0004"
    assert len(retriever.search_chunks("demo", "crosshead", k=2)) == 2


def test_figure_candidates_order_dedupe_and_cad_rule(retriever):
    chunks = retriever.search_chunks("demo", "crosshead installation plate", k=1)
    cands = retriever.figure_candidates("demo", "crosshead installation plate", chunks)
    ids = [c.id for c in cands]
    assert ids[:2] == ["man/crosshead-installation", "man/crosshead-alignment-tool"]
    assert "man/crosshead-installation-copy" not in ids  # same image hash as the original
    assert "cad1/sheet-1" not in ids                     # CAD only via its retrieved sheet chunk
    assert len(ids) <= 6
    assert cands[0].image_url == "/content/figures/man/crosshead-installation.png"
    assert cands[0].pdf_url == "/content-docs/man.pdf#page=88"


def test_cad_candidate_when_sheet_chunk_retrieved(retriever):
    chunks = retriever.search_chunks("demo", "encoder shaft drill depth")
    assert chunks[0].id == "cad1:0001"
    ids = [c.id for c in retriever.figure_candidates("demo", "encoder shaft drill depth", chunks)]
    assert ids[0] == "cad1/sheet-1"


def test_section_chunks_lists_and_lookup(retriever):
    assert [c.id for c in retriever.section_chunks("demo", "man", "Section 1 - Tools")] == ["man:0002"]
    assert [a.id for a in retriever.list_assets("demo", "cad")] == ["cad1/sheet-1"]
    assert [a.id for a in retriever.get_assets(["man/a-18255-wiring", "missing", "cad1/sheet-1"])] == \
        ["man/a-18255-wiring", "cad1/sheet-1"]
    assert "image_sha" not in retriever.get_assets(["cad1/sheet-1"])[0].public()
    assert retriever.doc_exists("man") and not retriever.doc_exists("zzz")


def test_resolve_figure_fuzzy(retriever):
    assert retriever.resolve_figure("demo", "man/crosshead-alignment-tool") == "man/crosshead-alignment-tool"
    assert retriever.resolve_figure("demo", "Crosshed Instalation") == "man/crosshead-installation"
    assert retriever.resolve_figure("demo", "Figure: A18255 proximity switch wiring") == "man/a-18255-wiring"
    assert retriever.resolve_figure("demo", "completely unrelated words here") is None
