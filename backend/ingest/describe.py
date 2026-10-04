"""One Claude vision call per figure/CAD image: kind, completeness checks, description, components."""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from backend.ingest.models import Asset
from backend.llm.common import estimate_cost, tool_choice_for, tool_input

log = logging.getLogger(__name__)

KINDS = ["photo", "exploded_parts", "line_drawing", "schematic", "chart", "flowchart", "cad"]

VISION_TOOL = {
    "name": "record_figure",
    "description": "Record what a cropped technical figure shows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": KINDS},
            "complete": {"type": "boolean",
                         "description": "False if the drawing looks cut off at an edge or the captioned subject is missing."},
            "has_body_text": {"type": "boolean",
                              "description": "True if a paragraph of running manual text (not labels, callouts or the caption) is included."},
            "description": {"type": "string", "description": "1-2 plain sentences a technician could search for."},
            "components": {"type": "array", "items": {"type": "string"}, "description": "Up to 10 named parts."},
        },
        "required": ["kind", "complete", "has_body_text", "description", "components"],
    },
}

PROMPT = """This image was cropped from an industrial reciprocating-compressor maintenance document.
Caption: {caption}
Callout legend: {legend}

Call record_figure exactly once describing the image."""


def describe_image(client, model: str, png: bytes, caption: str, legend: str):
    msg = client.messages.create(
        model=model, max_tokens=600, tools=[VISION_TOOL], tool_choice=tool_choice_for(model, "record_figure"),
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(png).decode()}},
            {"type": "text", "text": PROMPT.format(caption=caption, legend=legend or "(none)")},
        ]}])
    result = tool_input(msg, "record_figure")
    if result is None:
        raise RuntimeError("vision call returned no record_figure result")
    return result, msg.usage


def _apply(a: Asset, r: dict) -> None:
    if a.asset_type != "cad":
        a.kind = r["kind"] if r.get("kind") in KINDS else "line_drawing"
    a.description = str(r.get("description", "")).strip()
    a.components = [str(c).strip() for c in r.get("components", [])][:10]
    for flag, bad in (("incomplete", not r.get("complete", True)), ("has_body_text", r.get("has_body_text", False))):
        if bad and flag not in a.flags:
            a.flags.append(flag)


def apply_vision(conn, client, model: str, assets: list[Asset], content_dir: Path) -> float:
    cost = 0.0
    for a in assets:
        row = conn.execute("SELECT result_json FROM vision_cache WHERE image_sha = ?", (a.image_sha,)).fetchone()
        if row:
            result = json.loads(row["result_json"])
        elif client is None:
            continue  # --skip-vision: cache hits only
        else:
            try:
                result, usage = describe_image(client, model, (Path(content_dir) / a.image_path).read_bytes(),
                                               a.caption, a.legend_text)
            except (anthropic.APIError, RuntimeError) as e:
                log.warning("vision failed for %s: %s", a.id, e)
                a.flags.append("vision_failed")
                continue
            cost += estimate_cost(model, usage)
            conn.execute("INSERT OR REPLACE INTO vision_cache (image_sha, result_json, created_at) VALUES (?, ?, ?)",
                         (a.image_sha, json.dumps(result), datetime.now(timezone.utc).isoformat()))
            conn.commit()
        _apply(a, result)
    return cost
