import pymupdf
import pytest

from backend.ingest.figures import extract_figures, find_page_figures
from backend.ingest.render import render_asset
from backend.ingest.sections import build_sections
from tests.conftest import MANUAL_PDF, SET_DIR


@pytest.fixture(scope="module")
def manual():
    return pymupdf.open(MANUAL_PDF)


def _page(manual, n):
    return find_page_figures(manual[n - 1])


def test_detects_all_manual_captions(manual):
    found = [f for p in manual for f in find_page_figures(p)]
    assert len(found) == 85
    assert sorted(f.page for f in found if f.rect is None) == [123, 124]  # flowcharts → overrides.yaml


def test_crosshead_installation_includes_legend(manual):
    [fig] = _page(manual, 88)
    assert fig.caption == "Crosshead Installation"
    assert "Metal Plate" in fig.legend_text
    assert fig.rect.y1 > 340


def test_side_by_side_captions_split_without_body_text(manual):
    figs = _page(manual, 104)
    assert [f.caption for f in figs] == ["Swabbing Oil Passages", "Swabbing Oil Gallery", "Pen Light Inspection"]
    assert figs[0].rect.y1 < 260 and figs[1].rect.y1 < 260


def test_two_figures_on_one_page(manual):
    figs = _page(manual, 18)
    assert len(figs) == 2
    assert figs[0].rect.y1 < figs[1].rect.y0


def test_pseudo_caption_in_body_text_is_skipped(manual):
    assert all("shows the" not in f.caption for f in _page(manual, 55))


def test_caution_box_excluded_and_side_legend_included(manual):
    [fig90] = _page(manual, 90)
    assert fig90.rect.y1 < 390  # CAUTION box starts at ~y=394
    assert fig90.rect.x1 > 530  # right-hand legend column
    [fig100] = _page(manual, 100)
    assert "Main Bearing Cap Screw" in fig100.legend_text


def test_extract_figures_ids_overrides_and_problems(manual):
    sections = build_sections(manual, "KBE Maintenance & Repair Manual")
    overrides = {
        "kbe-manual/crosshead-installation": {"caption": "Crosshead Installation (Tool and Plate)"},
        "kbe-manual/typical-magnet-assemblies": {"exclude": True},
        "kbe-manual/crosshead-clearance-verification-correction-flowchart": {"bbox": [57, 80, 555, 700]},
    }
    assets, problems = extract_figures(manual, "kbe-manual", "manual", sections, lambda fid: overrides.get(fid, {}))
    ids = [a.id for a in assets]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("kbe-manual/") for i in ids)
    assert "kbe-manual/typical-magnet-assemblies" not in ids
    ch = next(a for a in assets if a.id == "kbe-manual/crosshead-installation")
    assert ch.caption == "Crosshead Installation (Tool and Plate)"
    assert ch.source_caption == "Crosshead Installation"
    assert ch.section_path.endswith("Crosshead Installation")
    assert ch.asset_type == "figure" and ch.page == 88
    flow = next(a for a in assets if a.id == "kbe-manual/crosshead-clearance-verification-correction-flowchart")
    assert "overridden" in flow.flags
    assert len(problems) == 1 and "p.124" in problems[0]
    # 85 detected − 1 excluded (magnet assemblies) − 1 unresolved (p.124 flowchart, reported as a problem)
    assert len(assets) == 83


def test_render_asset_writes_png_and_thumb(manual, tmp_path):
    sections = build_sections(manual, "KBE Maintenance & Repair Manual")
    assets, _ = extract_figures(manual, "kbe-manual", "manual", sections, lambda fid: {})
    asset = next(a for a in assets if a.id == "kbe-manual/crosshead-installation")
    render_asset(manual, asset, tmp_path)
    assert asset.image_path == "figures/kbe-manual/crosshead-installation.png"
    assert (tmp_path / asset.image_path).is_file()
    assert (tmp_path / asset.thumb_path).is_file()
    assert asset.width > 500 and asset.height > 500
    assert len(asset.image_sha) == 64
    thumb = pymupdf.Pixmap(str(tmp_path / asset.thumb_path))
    assert 380 <= thumb.width <= 420
