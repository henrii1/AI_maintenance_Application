import pytest

from backend.retrieval.search import Retriever
from tests.conftest import login
from tests.content_fixture import build_tiny_content
from tests.fakes import HashEmbedder


@pytest.fixture
def client(make_client, settings):
    build_tiny_content(settings.content_dir)
    return make_client(retriever=Retriever.open(settings.content_dir, HashEmbedder()))


def test_sets_require_login(client, settings):
    assert client.get("/api/sets").status_code == 401
    login(client, settings)
    assert client.get("/api/sets").json() == [
        {"id": "demo", "name": "Demo Compressor", "equipment": {"name": "Demo Compressor", "manufacturer": "Acme"}}]


def test_assets_listing_and_static(client, settings):
    login(client, settings)
    cad = client.get("/api/sets/demo/assets?type=cad").json()
    assert [a["id"] for a in cad] == ["cad1/sheet-1"]
    assert client.get(cad[0]["image_url"]).status_code == 200
    assert client.get("/api/sets/nope/assets").status_code == 404


def test_content_docs_guarded(client, settings):
    assert client.get("/content-docs/man.pdf").status_code == 401
    login(client, settings)
    r = client.get("/content-docs/man.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert client.get("/content-docs/zzz.pdf").status_code == 404
    assert client.get("/content-docs/..%2Fapp.pdf").status_code == 404
