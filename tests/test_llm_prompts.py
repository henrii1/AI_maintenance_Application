import anthropic
import httpx

from backend.db import connect_app, utcnow
from backend.llm.client import log_call
from backend.llm.prompts import chat_system, chat_user_content, format_excerpts, format_figures, tools_system
from backend.llm.rewrite import rewrite_query
from backend.retrieval.search import AssetInfo, ChunkHit
from tests.fakes import FakeAsyncAnthropic, message, tool_use, usage

EQUIP = {"name": "Ariel KBE Reciprocating Compressor", "manufacturer": "Ariel Corporation"}
CHUNK = ChunkHit(id="m:1", doc_id="kbe-manual", doc_title="KBE Maintenance & Repair Manual",
                 section_path="Section 4 › Crosshead Installation", page_start=87, page_end=88,
                 text="Lay a 3/16 inch plate.", figure_refs=(), has_warning=True, score=0.1)
FIG = AssetInfo(id="kbe-manual/crosshead-installation", doc_id="kbe-manual", asset_type="figure",
                caption="Crosshead Installation", kind="exploded_parts", description="Crosshead with tool.",
                page=88, section_path="S", image_url="/x.png", thumb_url="/x.t.png", pdf_url="/p", image_sha="s")
HISTORY = [{"role": "user", "content": "How do I install the crosshead?"},
           {"role": "assistant", "content": "### 1. Prepare ..."}]


def test_system_prompts_are_grounded():
    s = chat_system(EQUIP)
    assert "Ariel KBE Reciprocating Compressor" in s and "Ariel Corporation" in s
    assert "[p.88]" in s and "Not in the provided documentation" in s and "present_assets" in s
    t = tools_system(EQUIP)
    assert "submit_tools" in t and '"manual"' in t and "generic-tool" in t


def test_context_formatting():
    ex = format_excerpts([CHUNK])
    assert 'doc="KBE Maintenance & Repair Manual"' in ex and 'pages="87-88"' in ex and 'warning="yes"' in ex
    assert "Lay a 3/16 inch plate." in ex
    figs = format_figures([FIG])
    assert "kbe-manual/crosshead-installation" in figs and "p.88" in figs and "exploded_parts" in figs
    assert format_figures([]) == "(no figures available)"
    user = chat_user_content([CHUNK], [FIG], "How?")
    assert user.index("<excerpts>") < user.index("<figures>") < user.index("<question>")


async def test_rewrite_skips_without_history():
    client = FakeAsyncAnthropic()
    r = await rewrite_query(client, "claude-haiku-4-5", [], "How do I install the crosshead?")
    assert (r.query, r.new_task, r.usage) == ("How do I install the crosshead?", True, None)
    assert client.messages.calls == []


async def test_rewrite_uses_tool_output():
    client = FakeAsyncAnthropic(lambda kw: message(tool_use("rewrite", {"query": "crosshead pin torque",
                                                                         "new_task": False})))
    r = await rewrite_query(client, "claude-haiku-4-5", HISTORY, "what torque for the pin?")
    assert r.query == "crosshead pin torque" and r.new_task is False and r.usage is not None
    req = client.messages.calls[0]
    assert req["tool_choice"] == {"type": "tool", "name": "rewrite"}
    assert "what torque for the pin?" in req["messages"][0]["content"]
    assert "How do I install the crosshead?" in req["messages"][0]["content"]


async def test_rewrite_falls_back_on_error_or_missing_tool():
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    r = await rewrite_query(FakeAsyncAnthropic(lambda kw: err), "claude-haiku-4-5", HISTORY, "and then?")
    assert (r.query, r.new_task) == ("and then?", False)
    r = await rewrite_query(FakeAsyncAnthropic(lambda kw: message()), "claude-haiku-4-5", HISTORY, "and then?")
    assert (r.query, r.new_task) == ("and then?", False)


def test_log_call(tmp_path):
    conn = connect_app(tmp_path / "app.db")
    cost = log_call(conn, conversation_id="c1", email="t@x.co", call_type="chat", model="claude-haiku-4-5",
                    usage=usage(7000, 900), now=utcnow())
    assert round(cost, 4) == 0.0115
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["input_tokens"] == 7000 and row["output_tokens"] == 900 and row["call_type"] == "chat"
