from pathlib import Path

import pytest
import yaml

from tests.conftest import ROOT

CONTENT = ROOT / "content"
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not (CONTENT / "content.db").is_file(), reason="run Task 9 ingestion first")]


def test_golden_retrieval():
    from backend.embeddings import get_embedder
    from backend.retrieval.search import Retriever
    r = Retriever.open(CONTENT, get_embedder(ROOT / ".cache" / "fastembed"))
    cases = yaml.safe_load((Path(__file__).parent / "golden" / "retrieval.yaml").read_text())
    failures = []
    for case in cases:
        chunks = r.search_chunks("ariel-kbe", case["query"])
        cands = [a.id for a in r.figure_candidates("ariel-kbe", case["query"], chunks)]
        ok = True
        if "section" in case:
            ok &= any(case["section"] in c.section_path for c in chunks)
        if "doc" in case:
            ok &= any(c.doc_id == case["doc"] for c in chunks)
        if "figure" in case:
            ok &= case["figure"] in cands
        if not ok:
            failures.append((case["query"], [c.section_path for c in chunks[:3]], cands))
    for f in failures:
        print("MISS:", f)
    assert len(failures) <= 2, f"{len(failures)} golden misses"
