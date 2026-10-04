"""Streamed, grounded procedure answer + figure selection via the present_assets tool."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from backend.llm.common import tool_input
from backend.llm.prompts import chat_system, chat_user_content

MAX_FIGURES = 3
CITE = re.compile(r"\[(?:([A-Z][A-Z0-9-]{1,15})\s+)?pp?\.?\s*(\d+)(?:\s*[-–]\s*\d+)?\]")


@dataclass
class ChatContext:
    equipment: dict
    chunks: list
    candidates: list
    history: list[dict]
    question: str
    resolve: Callable[[str], str | None] = field(default=lambda name: None)


def present_assets_tool(ids: list[str]) -> dict:
    return {"name": "present_assets",
            "description": "Choose the figures to display beside the answer, most useful first.",
            "input_schema": {"type": "object",
                             "properties": {"figure_ids": {"type": "array", "items": {"type": "string", "enum": ids},
                                                           "maxItems": MAX_FIGURES}},
                             "required": ["figure_ids"]}}


def _cite_target(ref: str | None, page: int, chunks: list):
    """Return (doc_id, section_path) for a citation, or (None, None) when it can't be placed unambiguously."""
    def has(c):
        return c.page_start <= page <= c.page_end
    if ref:
        docs = [c for c in chunks if c.doc_id.upper() == ref]
        if not docs:
            return None, None
        c = next((c for c in docs if has(c)), None)
        return docs[0].doc_id, (c.section_path if c else None)
    c = next((c for c in chunks if has(c) and getattr(c, "doc_role", "manual") == "manual"), None)
    if c is None:
        hits = [c for c in chunks if has(c)]
        if hits and len({c.doc_id for c in hits}) == 1:
            c = hits[0]
    return (c.doc_id, c.section_path) if c else (None, None)


def citations_from(text: str, chunks: list) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str | None, int]] = set()
    for m in CITE.finditer(text):
        ref, page = m.group(1), int(m.group(2))
        if (ref, page) in seen:
            continue
        seen.add((ref, page))
        doc_id, section = _cite_target(ref, page, chunks)
        out.append({"page": page, "ref": ref, "doc_id": doc_id, "section_path": section})
    return out


def pick_figures(ids: list[str] | None, ctx: ChatContext) -> list[str]:
    allowed = {a.id for a in ctx.candidates}
    out: list[str] = []
    if not isinstance(ids, list):
        ids = []
    for fid in ids:
        if not isinstance(fid, str):
            continue
        if fid not in allowed:
            fid = ctx.resolve(fid)
        if fid and fid in allowed and fid not in out:
            out.append(fid)
    if not out and ctx.candidates:
        out = [ctx.candidates[0].id]
    return out[:MAX_FIGURES]


async def stream_answer(client, model: str, ctx: ChatContext) -> AsyncIterator[tuple[str, Any]]:
    messages = [{"role": m["role"], "content": m["content"]} for m in ctx.history]
    messages.append({"role": "user", "content": chat_user_content(ctx.chunks, ctx.candidates, ctx.question)})
    kwargs: dict = dict(model=model, max_tokens=2000, system=chat_system(ctx.equipment), messages=messages)
    if ctx.candidates:
        kwargs["tools"] = [present_assets_tool([a.id for a in ctx.candidates])]
        kwargs["tool_choice"] = {"type": "auto"}
    async with client.messages.stream(**kwargs) as stream:
        async for event in stream:
            if event.type == "text":
                yield "token", event.text
        final = await stream.get_final_message()
    text = "".join(b.text for b in final.content if getattr(b, "type", None) == "text")
    chosen = tool_input(final, "present_assets") or {}
    figure_ids = chosen.get("figure_ids")
    if not isinstance(figure_ids, list):
        figure_ids = None
    yield "assets", {"figure_ids": pick_figures(figure_ids, ctx),
                     "citations": citations_from(text, ctx.chunks)}
    yield "final", {"text": text, "usage": final.usage, "stop_reason": final.stop_reason}
