import json

import anthropic
import httpx

from backend.content_db import connect_content, init_content_schema
from backend.ingest.describe import apply_vision
from backend.ingest.models import Asset
from tests.fakes import FakeAnthropic, message, tool_use

RESULT = {"kind": "exploded_parts", "complete": False, "has_body_text": True,
          "description": "Crosshead with installation tool and plate.", "components": ["crosshead", "plate"]}


def _asset(tmp_path, sha="a" * 64, asset_type="figure"):
    (tmp_path / "figures").mkdir(exist_ok=True)
    (tmp_path / "figures" / "x.png").write_bytes(b"\x89PNG fake")
    return Asset(id="d/x", doc_id="d", asset_type=asset_type, caption="Crosshead Installation", page=1,
                 bbox=(0, 0, 1, 1), section_path="S", legend_text="1 Tool 2 Plate",
                 image_path="figures/x.png", image_sha=sha)


def _conn(tmp_path):
    conn = connect_content(tmp_path / "content.db")
    init_content_schema(conn)
    return conn


def test_vision_applies_result_and_caches(tmp_path):
    client = FakeAnthropic(lambda kw: message(tool_use("record_figure", RESULT)))
    conn = _conn(tmp_path)
    a = _asset(tmp_path)
    cost = apply_vision(conn, client, "claude-haiku-4-5", [a], tmp_path)
    assert cost > 0
    assert a.kind == "exploded_parts"
    assert a.description == "Crosshead with installation tool and plate."
    assert a.components == ["crosshead", "plate"]
    assert {"incomplete", "has_body_text"} <= set(a.flags)
    req = client.messages.calls[0]
    assert req["tool_choice"] == {"type": "tool", "name": "record_figure"}
    assert req["messages"][0]["content"][0]["type"] == "image"
    assert "Crosshead Installation" in req["messages"][0]["content"][1]["text"]

    b = _asset(tmp_path)  # same image hash → served from cache
    assert apply_vision(conn, client, "claude-haiku-4-5", [b], tmp_path) == 0
    assert len(client.messages.calls) == 1
    assert json.loads(conn.execute("SELECT result_json FROM vision_cache").fetchone()[0])["kind"] == "exploded_parts"


def test_cad_kind_is_preserved(tmp_path):
    client = FakeAnthropic(lambda kw: message(tool_use("record_figure", {**RESULT, "complete": True,
                                                                         "has_body_text": False})))
    a = _asset(tmp_path, sha="b" * 64, asset_type="cad")
    a.kind = "cad"
    apply_vision(_conn(tmp_path), client, "claude-haiku-4-5", [a], tmp_path)
    assert a.kind == "cad" and a.flags == []


def test_api_failure_flags_asset_and_continues(tmp_path):
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    client = FakeAnthropic(lambda kw: err)
    a = _asset(tmp_path, sha="c" * 64)
    assert apply_vision(_conn(tmp_path), client, "claude-haiku-4-5", [a], tmp_path) == 0
    assert "vision_failed" in a.flags
