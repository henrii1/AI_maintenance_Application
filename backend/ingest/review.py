"""Static contact sheet of every figure/CAD sheet in a set, flagged items first."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

CSS = """body{font:14px system-ui,sans-serif;margin:24px;background:#f6f8f9;color:#071a2b}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.card{background:#fff;border:1px solid #e4e9ed;border-radius:8px;padding:8px}
.card.flagged{border:2px solid #ff8a00}.card img{width:100%;height:200px;object-fit:contain;background:#fff}
.meta{font-size:12px;color:#5b6b7a}.flags{color:#c25e00;font-weight:600}.problems{background:#fff3e0;padding:12px;border-radius:8px}"""


def write_review(conn, set_id: str, out_path: Path, problems: list[str]) -> Path:
    rows = conn.execute("SELECT a.* FROM assets a JOIN set_documents s ON s.doc_id = a.doc_id"
                        " WHERE s.set_id = ? ORDER BY a.doc_id, a.page, a.id", (set_id,)).fetchall()
    rows = sorted(rows, key=lambda r: 0 if json.loads(r["flags"]) else 1)
    cards = []
    for r in rows:
        flags = json.loads(r["flags"])
        cards.append(
            f'<div class="card{" flagged" if flags else ""}"><img loading="lazy" src="../{escape(r["image_path"])}">'
            f'<div><b>{escape(r["caption"])}</b></div>'
            f'<div class="meta">{escape(r["id"])} · p.{r["page"]} · {escape(r["kind"])}</div>'
            f'<div class="flags">{escape(", ".join(flags))}</div>'
            f'<div class="meta">{escape(r["description"])}</div></div>')
    problem_html = ("<div class='problems'><b>Problems</b><ul>" +
                    "".join(f"<li>{escape(p)}</li>" for p in problems) + "</ul></div>") if problems else ""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(f"<!doctype html><meta charset='utf-8'><title>Review {escape(set_id)}</title>"
                        f"<style>{CSS}</style><h1>{escape(set_id)}: {len(rows)} assets</h1>{problem_html}"
                        f"<div class='grid'>{''.join(cards)}</div>")
    return out_path
