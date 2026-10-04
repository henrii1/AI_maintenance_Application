import pytest

from backend.ingest.manifest import ManifestError, load_manifest
from tests.conftest import SET_DIR


def test_loads_real_manifest():
    m = load_manifest(SET_DIR)
    assert m.id == "ariel-kbe"
    assert [d.id for d in m.documents] == [
        "kbe-manual", "er-034", "er-063", "er-129", "er-130", "er-140", "er-141", "ol0511", "ol0782"]
    assert {d.role for d in m.documents} == {"manual", "engineering_reference", "cad"}
    assert m.always_include_sections[0].doc == "kbe-manual"
    assert m.always_include_sections[0].section_prefix == "Section 1 - Tools"
    assert m.path(m.documents[0]).is_file()
    assert m.equipment["name"] == "Ariel KBE Reciprocating Compressor"


def _write(tmp_path, body):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "manifest.yaml").write_text(body)
    return tmp_path


def test_rejects_unknown_role(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: brochure}\n")
    with pytest.raises(ManifestError, match="unknown role"):
        load_manifest(tmp_path)


def test_rejects_missing_file(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: missing.pdf, role: manual}\n")
    with pytest.raises(ManifestError, match="file not found"):
        load_manifest(tmp_path)


def test_rejects_duplicate_ids(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: manual}\n  - {id: a, file: a.pdf, role: cad}\n")
    with pytest.raises(ManifestError, match="duplicate"):
        load_manifest(tmp_path)


def test_rejects_unknown_always_include_doc(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: manual}\n"
                     "always_include_sections:\n  - {doc: zzz, section_prefix: Tools}\n")
    with pytest.raises(ManifestError, match="unknown doc"):
        load_manifest(tmp_path)


def test_reads_overrides(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: manual}\n")
    (tmp_path / "overrides.yaml").write_text("figures:\n  a/fig-one:\n    exclude: true\n")
    m = load_manifest(tmp_path)
    assert m.figure_override("a/fig-one") == {"exclude": True}
    assert m.figure_override("a/other") == {}


def test_empty_overrides_file(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: manual}\n")
    (tmp_path / "overrides.yaml").write_text("figures: {}\n")
    assert load_manifest(tmp_path).figure_override("a/x") == {}
