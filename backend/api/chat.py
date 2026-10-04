"""POST /api/chat: rewrite → retrieve → (chat stream ‖ tools) merged into one SSE stream."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.deps import Viewer, get_retriever, require_viewer, settings_of
from backend.auth import quota
from backend.config import Settings
from backend.db import connect_app, iso, utcnow
from backend.llm.chat import ChatContext, stream_answer
from backend.llm.client import log_call, make_client
from backend.llm.rewrite import rewrite_query
from backend.llm.tools import ToolsContext, ToolsResult, load_icon_keys, recommend_tools
from backend.retrieval.search import Retriever

log = logging.getLogger(__name__)
router = APIRouter()
HISTORY_MESSAGES = 12  # last 6 turns
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
BUSY = "The assistant is busy. Please try again."


class ChatIn(BaseModel):
    set_id: str = Field(max_length=64)
    conversation_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class RetryIn(BaseModel):
    conversation_id: str = Field(max_length=64)


@dataclass
class Turn:
    settings: Settings
    llm: object
    retriever: Retriever
    conn: object
    email: str
    is_admin: bool
    set_id: str
    set_info: dict
    conversation_id: str
    history: list[dict]
    message: str
    remaining: int | None
    icon_keys: list[str]


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _llm(request: Request):
    if request.app.state.llm is None:
        request.app.state.llm = make_client(settings_of(request))
    return request.app.state.llm


def _icon_keys(request: Request) -> list[str]:
    if getattr(request.app.state, "icon_keys", None) is None:
        request.app.state.icon_keys = load_icon_keys(settings_of(request).web_dir)
    return request.app.state.icon_keys


def _owned_conversation(conn, conversation_id: str, email: str, set_id: str | None = None):
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None or row["email"] != email or (set_id and row["set_id"] != set_id):
        raise HTTPException(404, "Conversation not found.")
    return row


def _history(conn, conversation_id: str) -> list[dict]:
    rows = conn.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                        (conversation_id, HISTORY_MESSAGES)).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def _tools_call(t: Turn, query: str, chunks: list, candidates: list) -> tuple[ToolsResult, list[dict]]:
    extra_chunks = []
    for inc in t.set_info.get("always_include", []):
        extra_chunks += await asyncio.to_thread(t.retriever.section_chunks, t.set_id, inc["doc"], inc["section_prefix"])
    seen = {c.id for c in chunks}
    all_chunks = chunks + [c for c in extra_chunks if c.id not in seen]
    extra_fig_ids = [ref for c in extra_chunks for ref in c.figure_refs]
    have = {a.id for a in candidates}
    all_figs = candidates + [a for a in t.retriever.get_assets(list(dict.fromkeys(extra_fig_ids))) if a.id not in have]
    ctx = ToolsContext(equipment=t.set_info["equipment"], chunks=all_chunks, candidates=all_figs, question=t.message)
    result = await recommend_tools(t.llm, t.settings.tools_model, ctx, t.icon_keys, t.settings.tools_web_search)
    used = [tool["figure_id"] for g in result.groups for tool in g["tools"] if tool["figure_id"]]
    return result, [a.public() for a in t.retriever.get_assets(list(dict.fromkeys(used)))]


async def run_turn(t: Turn) -> AsyncIterator[str]:
    s = t.settings
    completed = False
    tasks: list[asyncio.Task] = []
    try:
        yield sse("meta", {"conversation_id": t.conversation_id, "remaining_today": t.remaining})
        rw = await rewrite_query(t.llm, s.rewrite_model, t.history, t.message)
        if rw.usage is not None:
            log_call(t.conn, conversation_id=t.conversation_id, email=t.email, call_type="rewrite",
                     model=s.rewrite_model, usage=rw.usage, now=utcnow())
        chunks = await asyncio.to_thread(t.retriever.search_chunks, t.set_id, rw.query)
        candidates = await asyncio.to_thread(t.retriever.figure_candidates, t.set_id, rw.query, chunks)
        ctx = ChatContext(equipment=t.set_info["equipment"], chunks=chunks, candidates=candidates,
                          history=t.history, question=t.message,
                          resolve=lambda name: t.retriever.resolve_figure(t.set_id, name))
        queue: asyncio.Queue = asyncio.Queue()

        async def chat_job():
            try:
                async for kind, payload in stream_answer(t.llm, s.chat_model, ctx):
                    await queue.put(("chat", kind, payload))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("chat call failed: %s", e)
                await queue.put(("chat", "error", None))
            finally:
                queue.put_nowait(("chat", "end", None))

        async def tools_job():
            try:
                await queue.put(("tools", "result", await _tools_call(t, rw.query, chunks, candidates)))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("tools call failed: %s", e)
                await queue.put(("tools", "error", None))
            finally:
                queue.put_nowait(("tools", "end", None))

        tasks.append(asyncio.create_task(chat_job()))
        if not t.history or rw.new_task:
            tasks.append(asyncio.create_task(tools_job()))
        open_jobs = len(tasks)
        answer: dict | None = None
        assets: dict | None = None
        tools_groups: list | None = None
        chat_failed = False
        while open_jobs:
            source, kind, payload = await queue.get()
            if kind == "end":
                open_jobs -= 1
            elif source == "chat" and kind == "token":
                yield sse("token", {"text": payload})
            elif source == "chat" and kind == "assets":
                assets = payload
                figures = [a.public() for a in t.retriever.get_assets(payload["figure_ids"])]
                yield sse("assets", {**payload, "figures": figures})
            elif source == "chat" and kind == "final":
                answer = payload
                log_call(t.conn, conversation_id=t.conversation_id, email=t.email, call_type="chat",
                         model=s.chat_model, usage=payload["usage"], now=utcnow())
            elif source == "chat" and kind == "error":
                chat_failed = True
                yield sse("error", {"code": "upstream_error", "message": BUSY})
            elif source == "tools" and kind == "result":
                result, figures = payload
                tools_groups = result.groups
                log_call(t.conn, conversation_id=t.conversation_id, email=t.email, call_type="tools",
                         model=s.tools_model, usage=result.usage, now=utcnow())
                yield sse("tools", {"groups": result.groups, "figures": figures})
            elif source == "tools" and kind == "error":
                yield sse("tools", {"error": "Couldn't load tools."})
        if answer is not None and not chat_failed:
            now = iso(utcnow())
            t.conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                           (t.conversation_id, t.message, now))
            t.conn.execute("INSERT INTO messages (conversation_id, role, content, query, assets_json, tools_json,"
                           " created_at) VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
                           (t.conversation_id, answer["text"], rw.query, json.dumps(assets),
                            json.dumps(tools_groups) if tools_groups is not None else None, now))
            completed = True
        yield sse("done", {})
    finally:
        for task in tasks:
            task.cancel()
        if not completed:
            quota.release(t.conn, t.email, utcnow())
        t.conn.close()


@router.post("/api/chat")
async def chat(body: ChatIn, request: Request, viewer: Viewer = Depends(require_viewer)):
    settings = settings_of(request)
    retriever = get_retriever(request)
    set_info = retriever.get_set(body.set_id)
    if set_info is None:
        raise HTTPException(404, "Unknown equipment set.")
    conn = connect_app(settings.data_dir / "app.db")
    try:
        now = utcnow()
        if body.conversation_id:
            _owned_conversation(conn, body.conversation_id, viewer.email, body.set_id)
            conversation_id, history = body.conversation_id, _history(conn, body.conversation_id)
        else:
            conversation_id, history = uuid4().hex, []
            conn.execute("INSERT INTO conversations (id, email, set_id, created_at) VALUES (?, ?, ?, ?)",
                         (conversation_id, viewer.email, body.set_id, iso(now)))
        q = quota.try_reserve(conn, viewer.email, settings, now, is_admin=viewer.is_admin)
    except BaseException:
        conn.close()
        raise
    if not q.allowed:
        conn.close()
        msg = ("You've used today's questions." if q.reason == "user"
               else "The demo has reached today's overall limit.")
        body_events = [sse("error", {"code": "quota_exceeded", "message": f"{msg} It resets at midnight UTC.",
                                     "resets_at": q.resets_at}), sse("done", {})]
        return StreamingResponse(iter(body_events), media_type="text/event-stream", headers=SSE_HEADERS)
    turn = Turn(settings=settings, llm=_llm(request), retriever=retriever, conn=conn, email=viewer.email,
                is_admin=viewer.is_admin, set_id=body.set_id, set_info=set_info, conversation_id=conversation_id,
                history=history, message=body.message.strip(), remaining=q.remaining,
                icon_keys=_icon_keys(request))
    return StreamingResponse(run_turn(turn), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/tools/retry")
async def retry_tools(body: RetryIn, request: Request, viewer: Viewer = Depends(require_viewer)):
    settings = settings_of(request)
    retriever = get_retriever(request)
    conn = connect_app(settings.data_dir / "app.db")
    try:
        conv = _owned_conversation(conn, body.conversation_id, viewer.email)
        last = conn.execute("SELECT * FROM messages WHERE conversation_id = ? AND role = 'assistant'"
                            " ORDER BY id DESC LIMIT 1", (conv["id"],)).fetchone()
        user = conn.execute("SELECT content FROM messages WHERE conversation_id = ? AND role = 'user'"
                            " ORDER BY id DESC LIMIT 1", (conv["id"],)).fetchone()
        if last is None:
            raise HTTPException(404, "Nothing to retry yet.")
        set_info = retriever.get_set(conv["set_id"])
        chunks = await asyncio.to_thread(retriever.search_chunks, conv["set_id"], last["query"])
        candidates = await asyncio.to_thread(retriever.figure_candidates, conv["set_id"], last["query"], chunks)
        turn = Turn(settings=settings, llm=_llm(request), retriever=retriever, conn=conn, email=viewer.email,
                    is_admin=viewer.is_admin, set_id=conv["set_id"], set_info=set_info, conversation_id=conv["id"],
                    history=[], message=user["content"] if user else last["query"], remaining=None,
                    icon_keys=_icon_keys(request))
        try:
            result, figures = await _tools_call(turn, last["query"], chunks, candidates)
        except Exception as e:
            log.warning("tools retry failed: %s", e)
            raise HTTPException(502, "Couldn't load tools.")
        conn.execute("UPDATE messages SET tools_json = ? WHERE id = ?", (json.dumps(result.groups), last["id"]))
        log_call(conn, conversation_id=conv["id"], email=viewer.email, call_type="tools", model=settings.tools_model,
                 usage=result.usage, now=utcnow())
        return {"groups": result.groups, "figures": figures}
    finally:
        conn.close()
