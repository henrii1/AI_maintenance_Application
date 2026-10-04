import pytest

from backend.llm.chat import ChatContext, citations_from, pick_figures, stream_answer
from backend.llm.tools import ToolsContext, ToolsError, load_icon_keys, normalize_groups, recommend_tools
from backend.retrieval.search import AssetInfo, ChunkHit
from tests.conftest import ROOT
from tests.fakes import FakeAsyncAnthropic, message, text_block, tool_use, usage


def chunk(id_, page, doc="kbe-manual"):
    return ChunkHit(id=id_, doc_id=doc, doc_title="Manual", section_path=f"S{page}", page_start=page,
                    page_end=page, text="t", figure_refs=(), has_warning=False, score=0.0)


def fig(id_, page=88):
    return AssetInfo(id=id_, doc_id="kbe-manual", asset_type="figure", caption=id_, kind="line_drawing",
                     description="d", page=page, section_path="S", image_url="/i", thumb_url="/t", pdf_url="/p",
                     image_sha=id_)


CTX = ChatContext(equipment={"name": "KBE"}, chunks=[chunk("c1", 88), chunk("c2", 12)],
                  candidates=[fig("kbe-manual/a"), fig("kbe-manual/b")], history=[], question="How?",
                  resolve=lambda name: "kbe-manual/b" if "bee" in name else None)


def test_citations_map_pages_to_chunks():
    cites = citations_from("Do X [p.88]. Then Y [p.12] and again [p.88]. Unknown [p.5]. Range [p. 12-13]", CTX.chunks)
    assert cites == [{"page": 88, "doc_id": "kbe-manual", "section_path": "S88"},
                     {"page": 12, "doc_id": "kbe-manual", "section_path": "S12"},
                     {"page": 5, "doc_id": None, "section_path": None}]


def test_pick_figures_validates_and_falls_back():
    assert pick_figures(["kbe-manual/b", "kbe-manual/a", "kbe-manual/b"], CTX) == ["kbe-manual/b", "kbe-manual/a"]
    assert pick_figures(["the bee figure"], CTX) == ["kbe-manual/b"]   # fuzzy resolve
    assert pick_figures(["nonsense"], CTX) == ["kbe-manual/a"]         # top candidate fallback
    assert pick_figures(None, CTX) == ["kbe-manual/a"]
    empty = ChatContext(equipment={}, chunks=[], candidates=[], history=[], question="q", resolve=lambda n: None)
    assert pick_figures(None, empty) == []


async def test_stream_answer_events_and_request():
    final = message(text_block("Install it [p.88]."), tool_use("present_assets", {"figure_ids": ["kbe-manual/b"]}),
                    stop_reason="tool_use")
    client = FakeAsyncAnthropic(stream_handler=lambda kw: (["Install ", "it [p.88]."], final, 0))
    events = [e async for e in stream_answer(client, "claude-haiku-4-5", CTX)]
    assert events[:2] == [("token", "Install "), ("token", "it [p.88].")]
    assert events[2] == ("assets", {"figure_ids": ["kbe-manual/b"],
                                    "citations": [{"page": 88, "doc_id": "kbe-manual", "section_path": "S88"}]})
    assert events[3][0] == "final" and events[3][1]["text"] == "Install it [p.88]."
    req = client.messages.calls[0]
    assert req["tools"][0]["input_schema"]["properties"]["figure_ids"]["items"]["enum"] == ["kbe-manual/a", "kbe-manual/b"]
    assert req["tool_choice"] == {"type": "auto"}
    assert req["messages"][-1]["role"] == "user" and "<question>" in req["messages"][-1]["content"]


async def test_stream_answer_without_candidates_sends_no_tools():
    ctx = ChatContext(equipment={}, chunks=[], candidates=[], history=[], question="q", resolve=lambda n: None)
    client = FakeAsyncAnthropic(stream_handler=lambda kw: (["Not covered."], message(text_block("Not covered.")), 0))
    events = [e async for e in stream_answer(client, "claude-haiku-4-5", ctx)]
    assert "tools" not in client.messages.calls[0]
    assert events[-2] == ("assets", {"figure_ids": [], "citations": []})


def test_icon_keys_loaded_from_web():
    keys = load_icon_keys(ROOT / "web")
    assert "generic-tool" in keys and "oem-tool" in keys and "torque-wrench" in keys and len(keys) == 32


def test_normalize_groups_cleans_model_output():
    raw = {"groups": [
        {"category": "hand", "tools": [
            {"name": "Feeler gauge", "purpose": "clearance", "spec": None, "source": "manual", "source_ref": "p.98",
             "icon": "feeler-gauge", "figure_id": "kbe-manual/a"},
            {"name": "feeler GAUGE", "purpose": "dup", "source": "general", "icon": "x"},
            {"name": "", "purpose": "blank"}]},
        {"category": "weird", "tools": [{"name": "Bar", "icon": "nope", "source": "blog", "figure_id": "zzz"}]},
        {"category": "oem", "tools": [{"name": f"T{i}", "icon": "oem-tool", "source": "manual"} for i in range(20)]},
    ]}
    groups = normalize_groups(raw, ["feeler-gauge", "oem-tool", "generic-tool"], {"kbe-manual/a"})
    assert [g["category"] for g in groups] == ["oem", "hand"]
    hand = groups[1]["tools"]
    assert hand[0]["figure_id"] == "kbe-manual/a" and hand[0]["source_ref"] == "p.98"
    assert hand[1] == {"name": "Bar", "purpose": "", "spec": None, "source": "general", "source_ref": None,
                       "icon": "generic-tool", "figure_id": None}
    assert sum(len(g["tools"]) for g in groups) == 14


async def test_recommend_tools_request_shape_and_errors():
    raw = {"groups": [{"category": "hand", "tools": [{"name": "Torque wrench", "purpose": "p", "source": "general",
                                                      "icon": "torque-wrench"}]}]}
    client = FakeAsyncAnthropic(lambda kw: message(tool_use("submit_tools", raw), u=usage(5000, 500)))
    tctx = ToolsContext(equipment={"name": "KBE"}, chunks=CTX.chunks, candidates=CTX.candidates, question="q")
    res = await recommend_tools(client, "claude-haiku-4-5", tctx, ["torque-wrench", "generic-tool"], web_search=False)
    assert res.groups[0]["tools"][0]["name"] == "Torque wrench"
    req = client.messages.calls[0]
    assert req["tool_choice"] == {"type": "tool", "name": "submit_tools"}
    assert [t["name"] for t in req["tools"]] == ["submit_tools"]

    await recommend_tools(client, "claude-haiku-4-5", tctx, ["generic-tool"], web_search=True)
    req = client.messages.calls[1]
    assert req["tools"][1] == {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
    assert req["tool_choice"] == {"type": "auto"}

    with pytest.raises(ToolsError):
        await recommend_tools(FakeAsyncAnthropic(lambda kw: message(text_block("no tool"))), "claude-haiku-4-5",
                              tctx, ["generic-tool"], web_search=False)


def test_normalize_groups_tolerates_bad_types():
    raw = {"groups": ["x", {"category": ["oem"], "tools": "nope"}, {"category": "hand", "tools": [5, {"name": ["a"]}, {"name": "Wrench", "category": {}, "icon": ["x"], "source": 3, "figure_id": ["kbe-manual/a"], "purpose": 7}]}]}
    groups = normalize_groups(raw, ["feeler-gauge", "oem-tool", "generic-tool"], {"kbe-manual/a"})
    assert groups == [{"category": "hand", "tools": [{"name": "Wrench", "purpose": "", "spec": None, "source": "general", "source_ref": None, "icon": "generic-tool", "figure_id": None}]}]

    groups2 = normalize_groups({"groups": "bad"}, ["generic-tool"], set())
    assert groups2 == []


def test_web_source_ref_must_be_http_url():
    raw = {"groups": [{"category": "hand", "tools": [
        {"name": "Bad link", "purpose": "", "source": "web", "source_ref": "javascript:alert(1)", "icon": "generic-tool"},
        {"name": "Good link", "purpose": "", "source": "web", "source_ref": "https://example.com/x", "icon": "generic-tool"},
        {"name": "Manual ref", "purpose": "", "source": "manual", "source_ref": "p.42", "icon": "generic-tool"},
    ]}]}
    groups = normalize_groups(raw, ["generic-tool"], set())
    assert groups[0]["tools"][0]["source_ref"] is None  # javascript: rejected
    assert groups[0]["tools"][1]["source_ref"] == "https://example.com/x"  # https kept
    assert groups[0]["tools"][2]["source_ref"] == "p.42"  # manual source_ref kept


def test_pick_figures_ignores_bad_types():
    assert pick_figures([5, None, "kbe-manual/b"], CTX) == ["kbe-manual/b"]
    assert pick_figures("kbe-manual/b", CTX) == ["kbe-manual/a"]  # string not iterated → fallback

    empty = ChatContext(equipment={}, chunks=[], candidates=[], history=[], question="q", resolve=lambda n: None)
    assert pick_figures([5], empty) == []  # non-str ignored, no candidates for fallback
