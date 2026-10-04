"""Tools list for the job: manual excerpts (+ optional single web search) -> grouped, validated tool cards."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.llm.common import tool_choice_for, tool_input
from backend.llm.prompts import tools_system, tools_user_content

GROUPS = ["oem", "power", "hand", "measuring", "consumables"]
SOURCES = ["manual", "web", "general"]
MAX_TOOLS = 14


class ToolsError(RuntimeError):
    pass


@dataclass
class ToolsContext:
    equipment: dict
    chunks: list
    candidates: list
    question: str


@dataclass
class ToolsResult:
    groups: list[dict]
    usage: Any


def load_icon_keys(web_dir: Path) -> list[str]:
    return list(json.loads((Path(web_dir) / "assets" / "icons.json").read_text()).keys())


def submit_tools_tool(icon_keys: list[str], figure_ids: list[str]) -> dict:
    tool = {"type": "object", "properties": {
        "name": {"type": "string"},
        "purpose": {"type": "string", "description": "What the tool is used for in this job (one short phrase)."},
        "spec": {"type": ["string", "null"]},
        "source": {"type": "string", "enum": SOURCES},
        "source_ref": {"type": ["string", "null"], "description": "Manual page like 'p.10', or a URL."},
        "icon": {"type": "string", "enum": icon_keys},
        "figure_id": {"type": ["string", "null"], "enum": [*figure_ids, None]},
    }, "required": ["name", "purpose", "source", "icon"]}
    return {"name": "submit_tools", "description": "Submit the grouped tool list for this job.",
            "input_schema": {"type": "object", "properties": {"groups": {"type": "array", "items": {
                "type": "object", "properties": {"category": {"type": "string", "enum": GROUPS},
                                                 "tools": {"type": "array", "items": tool}},
                "required": ["category", "tools"]}}}, "required": ["groups"]}}


def _clip(value, n: int) -> str | None:
    return str(value).strip()[:n] if value else None


def normalize_groups(raw: dict, icon_keys: list[str], allowed_figures: set[str]) -> list[dict]:
    by_cat: dict[str, list[dict]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    total = 0
    for g in raw.get("groups", []) or []:
        cat = g.get("category") if g.get("category") in by_cat else "hand"
        for t in g.get("tools", []) or []:
            name = str(t.get("name") or "").strip()
            if not name or name.lower() in seen or total >= MAX_TOOLS:
                continue
            seen.add(name.lower())
            total += 1
            by_cat[cat].append({
                "name": name[:80],
                "purpose": str(t.get("purpose") or "")[:200],
                "spec": _clip(t.get("spec"), 120),
                "source": t.get("source") if t.get("source") in SOURCES else "general",
                "source_ref": _clip(t.get("source_ref"), 300),
                "icon": t.get("icon") if t.get("icon") in icon_keys else "generic-tool",
                "figure_id": t.get("figure_id") if t.get("figure_id") in allowed_figures else None,
            })
    order = {g: i for i, g in enumerate(GROUPS)}
    return sorted(({"category": c, "tools": ts} for c, ts in by_cat.items() if ts), key=lambda g: order[g["category"]])


async def recommend_tools(client, model: str, ctx: ToolsContext, icon_keys: list[str], web_search: bool) -> ToolsResult:
    figure_ids = [a.id for a in ctx.candidates]
    tools: list[dict] = [submit_tools_tool(icon_keys, figure_ids)]
    if web_search:
        tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 1})
    msg = await client.messages.create(
        model=model, max_tokens=1500, system=tools_system(ctx.equipment), tools=tools,
        tool_choice={"type": "auto"} if web_search else tool_choice_for(model, "submit_tools"),
        messages=[{"role": "user", "content": tools_user_content(ctx.chunks, ctx.candidates, ctx.question)}])
    raw = tool_input(msg, "submit_tools")
    if raw is None:
        raise ToolsError(f"model did not return a tools list (stop_reason={msg.stop_reason})")
    return ToolsResult(normalize_groups(raw, icon_keys, set(figure_ids)), msg.usage)
