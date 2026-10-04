"""Turn a follow-up into a standalone retrieval query and detect a change of job."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import anthropic

from backend.llm.common import tool_choice_for, tool_input
from backend.llm.prompts import REWRITE_SYSTEM

log = logging.getLogger(__name__)

REWRITE_TOOL = {
    "name": "rewrite",
    "description": "Return the standalone search query and whether the message starts a new maintenance job.",
    "input_schema": {"type": "object",
                     "properties": {"query": {"type": "string"}, "new_task": {"type": "boolean"}},
                     "required": ["query", "new_task"]},
}


@dataclass(frozen=True)
class RewriteResult:
    query: str
    new_task: bool
    usage: Any


async def rewrite_query(client, model: str, history: list[dict], message: str) -> RewriteResult:
    if not history:
        return RewriteResult(message, True, None)
    transcript = "\n".join(f"{m['role'].upper()}: {m['content'][:1500]}" for m in history)
    try:
        msg = await client.messages.create(
            model=model, max_tokens=200, system=REWRITE_SYSTEM, tools=[REWRITE_TOOL],
            tool_choice=tool_choice_for(model, "rewrite"),
            messages=[{"role": "user", "content": f"<conversation>\n{transcript}\n</conversation>\n\n"
                                                  f"Latest message: {message}"}])
    except anthropic.APIError as e:
        log.warning("rewrite failed: %s", e)
        return RewriteResult(message, False, None)
    out = tool_input(msg, "rewrite")
    if not out or not str(out.get("query", "")).strip():
        return RewriteResult(message, False, msg.usage)
    return RewriteResult(str(out["query"]).strip(), bool(out.get("new_task", False)), msg.usage)
