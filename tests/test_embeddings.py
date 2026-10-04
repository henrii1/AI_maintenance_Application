import numpy as np
import pytest

from backend.embeddings import from_blob, get_embedder, to_blob


def test_blob_roundtrip():
    v = np.arange(4, dtype=np.float32)
    assert np.array_equal(from_blob(to_blob(v)), v)


@pytest.mark.integration
def test_fastembed_semantic_similarity(tmp_path):
    e = get_embedder(tmp_path / "fe")
    docs = e.embed_passages(["Tighten the crosshead balance nut with the hydraulic torque tool.",
                             "Daily maintenance: check frame oil level."])
    q = e.embed_query("how do I torque the crosshead nut")
    assert docs.shape == (2, 384)
    assert abs(float(np.linalg.norm(q)) - 1.0) < 1e-4
    assert float(docs[0] @ q) > float(docs[1] @ q)
