import asyncio
import json

import anthropic
import httpx
import pytest

from backend.api.chat import Turn, run_turn
from backend.auth import quota
from backend.db import connect_app, utcnow
from backend.retrieval.search import Retriever
from tests.conftest import approve_in_db, login
from tests.content_fixture import build_tiny_content
from tests.fakes import FakeAsyncAnthropic, HashEmbedder, message, text_block, tool_use, usage

ANSWER = ["**Safety:** isolate. ", "### 1. Install\n- Lay the plate [p.88]."]
TOOLS = {"groups": [{"category": "oem", "tools": [{"name": "Crosshead alignment tool", "purpose": "align",
                                                   "source": "manual", "source_ref": "p.12", "icon": "oem-tool",
                                                   "figure_id": "man/crosshead-alignment-tool"}]}]}


class Script:
    """Programmable fake LLM: counts calls per tool name; behaviour switches per test."""

    def __init__(self):
        self.new_task = False
        self.tools_error = False
        self.chat_error = False
        self.delay = 0.0
        self.client = FakeAsyncAnthropic(self.create, self.stream)

    def names(self, kw):
        return [t.get("name") for t in kw.get("tools", [])]

    def count(self, name):
        return sum(name in self.names(kw) for kw in self.client.messages.calls)

    def create(self, kw):
        if "rewrite" in self.names(kw):
            return message(tool_use("rewrite", {"query": "crosshead installation plate", "new_task": self.new_task}))
        if self.tools_error:
            return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
        return message(tool_use("submit_tools", TOOLS), u=usage(5000, 400))

    def stream(self, kw):
        chunks = list(ANSWER)
        if self.chat_error:
            chunks.append(anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")))
        final = message(text_block("".join(ANSWER)),
                        tool_use("present_assets", {"figure_ids": ["man/crosshead-alignment-tool"]}),
                        stop_reason="tool_use", u=usage(7000, 900))
        return chunks, final, self.delay


def events(resp) -> list[tuple[str, dict]]:
    out = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def script():
    return Script()


@pytest.fixture
def client(make_client, settings, script):
    build_tiny_content(settings.content_dir)
    c = make_client(retriever=Retriever.open(settings.content_dir, HashEmbedder()), llm=script.client)
    login(c, settings)
    return c


def ask(c, message="How do I install the crosshead?", conversation_id=None, set_id="demo"):
    return c.post("/api/chat", json={"set_id": set_id, "message": message, "conversation_id": conversation_id})


def db(settings):
    return connect_app(settings.data_dir / "app.db")


def test_requires_login_and_known_set(make_client, settings, script):
    build_tiny_content(settings.content_dir)
    c = make_client(retriever=Retriever.open(settings.content_dir, HashEmbedder()), llm=script.client)
    assert ask(c).status_code == 401
    login(c, settings)
    assert ask(c, set_id="nope").status_code == 404


def test_first_question_streams_answer_assets_tools(client, settings, script):
    ev = events(ask(client))
    kinds = [k for k, _ in ev]
    assert kinds[0] == "meta" and kinds[-1] == "done"
    assert ev[0][1]["remaining_today"] == 2
    assert "".join(d["text"] for k, d in ev if k == "token") == "".join(ANSWER)
    assets = next(d for k, d in ev if k == "assets")
    assert assets["figure_ids"] == ["man/crosshead-alignment-tool"]
    assert assets["citations"] == [{"page": 88, "doc_id": "man",
                                    "section_path": "Section 4 - Part Replacement › Crosshead Installation"}]
    assert assets["figures"][0]["image_url"] == "/content/figures/man/crosshead-alignment-tool.png"
    tools = next(d for k, d in ev if k == "tools")
    assert tools["groups"][0]["tools"][0]["name"] == "Crosshead alignment tool"
    assert tools["figures"][0]["id"] == "man/crosshead-alignment-tool"
    conn = db(settings)
    assert [r["role"] for r in conn.execute("SELECT role FROM messages ORDER BY id")] == ["user", "assistant"]
    assert {r["call_type"] for r in conn.execute("SELECT call_type FROM llm_calls")} == {"chat", "tools"}
    assert client.get("/api/me").json()["remaining"] == 2
    assert script.count("rewrite") == 0
    chat_req = next(kw for kw in script.client.messages.calls if "present_assets" in script.names(kw))
    assert "Demo Compressor" in chat_req["system"]
    tools_req = next(kw for kw in script.client.messages.calls if "submit_tools" in script.names(kw))
    assert "Recommended tools" in tools_req["messages"][0]["content"]  # always-include Tools section


def test_follow_up_uses_history_and_skips_tools(client, script):
    cid = events(ask(client))[0][1]["conversation_id"]
    ev = events(ask(client, "and the pin?", cid))
    assert "tools" not in [k for k, _ in ev]
    assert script.count("rewrite") == 1 and script.count("submit_tools") == 1
    chat_reqs = [kw for kw in script.client.messages.calls if "present_assets" in script.names(kw)]
    assert len(chat_reqs[-1]["messages"]) == 3  # user, assistant, new user


def test_new_task_follow_up_reruns_tools(client, script):
    cid = events(ask(client))[0][1]["conversation_id"]
    script.new_task = True
    ev = events(ask(client, "now the oil pump?", cid))
    assert "tools" in [k for k, _ in ev] and script.count("submit_tools") == 2


def test_tools_failure_does_not_break_chat(client, settings, script):
    script.tools_error = True
    ev = events(ask(client))
    assert next(d for k, d in ev if k == "tools") == {"error": "Couldn't load tools."}
    assert "assets" in [k for k, _ in ev] and ev[-1][0] == "done"
    assert client.get("/api/me").json()["remaining"] == 2  # still charged: the answer succeeded


def test_chat_failure_releases_quota(client, settings, script):
    script.chat_error = True
    ev = events(ask(client))
    assert ("error", {"code": "upstream_error", "message": "The assistant is busy. Please try again."}) in ev
    assert client.get("/api/me").json()["remaining"] == 3
    assert db(settings).execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0] == 0


def test_quota_exceeded_makes_no_llm_calls(client, script):
    for _ in range(3):
        assert events(ask(client))[-1][0] == "done"
    calls = len(script.client.messages.calls)
    ev = events(ask(client))
    assert ev[0][0] == "error" and ev[0][1]["code"] == "quota_exceeded" and "resets_at" in ev[0][1]
    assert len(script.client.messages.calls) == calls


def test_foreign_conversation_rejected(client, make_client, settings, script):
    cid = events(ask(client))[0][1]["conversation_id"]
    other = make_client(retriever=client.app.state.retriever, llm=script.client)
    login(other, settings, "other@example.com")
    assert ask(other, "show me", cid).status_code == 404
    assert other.post("/api/tools/retry", json={"conversation_id": cid}).status_code == 404


def test_admin_chats_without_quota(make_client, settings, script):
    build_tiny_content(settings.content_dir)
    c = make_client(retriever=Retriever.open(settings.content_dir, HashEmbedder()), llm=script.client)
    c.post("/api/admin/login", json={"password": "pw"})
    for _ in range(4):
        assert events(ask(c))[-1][0] == "done"


def test_tools_retry_updates_message_without_quota(client, settings, script):
    script.tools_error = True
    cid = events(ask(client))[0][1]["conversation_id"]
    script.tools_error = False
    r = client.post("/api/tools/retry", json={"conversation_id": cid})
    assert r.status_code == 200 and r.json()["groups"][0]["category"] == "oem"
    row = db(settings).execute("SELECT tools_json FROM messages WHERE role='assistant'").fetchone()
    assert json.loads(row["tools_json"])[0]["category"] == "oem"
    assert client.get("/api/me").json()["remaining"] == 2


async def test_disconnect_releases_quota(settings, script):
    build_tiny_content(settings.content_dir)
    approve_in_db(settings, "tech@example.com")
    conn = db(settings)
    assert quota.try_reserve(conn, "tech@example.com", settings, utcnow()).allowed
    conn.execute("INSERT INTO conversations (id, email, set_id, created_at) VALUES ('c1','tech@example.com','demo','x')")
    script.delay = 0.05
    retriever = Retriever.open(settings.content_dir, HashEmbedder())
    turn = Turn(settings=settings, llm=script.client, retriever=retriever, conn=conn, email="tech@example.com",
                is_admin=False, set_id="demo", set_info=retriever.get_set("demo"), conversation_id="c1",
                history=[], message="How do I install the crosshead?", remaining=2,
                icon_keys=["oem-tool", "generic-tool"])
    gen = run_turn(turn)
    first = await gen.__anext__()
    assert first.startswith("event: meta")
    await gen.__anext__()  # next event (a token or the tools result)
    await gen.aclose()     # browser went away
    check = db(settings)
    assert check.execute("SELECT questions FROM usage_daily").fetchone()["questions"] == 0
    assert check.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    await asyncio.sleep(0.1)  # cancelled tasks must not write afterwards
    assert check.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
