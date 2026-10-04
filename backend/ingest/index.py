"""Write documents, chunks, assets, FTS rows and embeddings into content.db."""
from __future__ import annotations

import json
from dataclasses import asdict

from backend.embeddings import Embedder, to_blob
from backend.ingest.manifest import Manifest
from backend.ingest.models import Asset, Chunk


def chunk_embed_text(c: Chunk) -> str:
    return f"{c.section_path}\n{c.text}"


def asset_search_text(a: Asset) -> str:
    return "\n".join(x for x in [a.caption, a.legend_text, a.description, ", ".join(a.components),
                                 a.section_path] if x)


def upsert_set(conn, m: Manifest) -> None:
    conn.execute("INSERT OR REPLACE INTO sets (id, name, equipment_json, always_include_json) VALUES (?, ?, ?, ?)",
                 (m.id, m.equipment.get("name", m.id), json.dumps(m.equipment),
                  json.dumps([asdict(a) for a in m.always_include_sections])))
    conn.commit()


def doc_is_current(conn, doc_id: str, fingerprint: str) -> bool:
    row = conn.execute("SELECT sha256 FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return bool(row) and row["sha256"] == fingerprint


def link_documents(conn, set_id: str, doc_ids: list[str]) -> None:
    marks = ",".join("?" * len(doc_ids))
    conn.execute(f"DELETE FROM set_documents WHERE set_id = ? AND doc_id NOT IN ({marks})", (set_id, *doc_ids))
    conn.executemany("INSERT OR IGNORE INTO set_documents (set_id, doc_id) VALUES (?, ?)",
                     [(set_id, d) for d in doc_ids])
    conn.commit()


def replace_document(conn, *, doc_id: str, file_name: str, role: str, title: str, sha256: str, page_count: int,
                     pdf_path: str, chunks: list[Chunk], assets: list[Asset], embedder: Embedder) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id = ?)", (doc_id,))
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    conn.execute("DELETE FROM assets_fts WHERE asset_id IN (SELECT id FROM assets WHERE doc_id = ?)", (doc_id,))
    conn.execute("DELETE FROM assets WHERE doc_id = ?", (doc_id,))
    conn.execute("INSERT OR REPLACE INTO documents (id, file_name, role, title, sha256, page_count, pdf_path)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?)", (doc_id, file_name, role, title, sha256, page_count, pdf_path))
    cvecs = embedder.embed_passages([chunk_embed_text(c) for c in chunks])
    for c, v in zip(chunks, cvecs):
        conn.execute("INSERT INTO chunks (id, doc_id, section_path, page_start, page_end, text, token_count,"
                     " has_warning, figure_refs, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (c.id, c.doc_id, c.section_path, c.page_start, c.page_end, c.text, c.token_count,
                      int(c.has_warning), json.dumps(c.figure_refs), to_blob(v)))
        conn.execute("INSERT INTO chunks_fts (chunk_id, section_path, text) VALUES (?, ?, ?)",
                     (c.id, c.section_path, c.text))
    texts = [asset_search_text(a) for a in assets]
    avecs = embedder.embed_passages(texts)
    for a, text, v in zip(assets, texts, avecs):
        conn.execute("INSERT INTO assets (id, doc_id, asset_type, caption, caption_norm, page, bbox, legend_text,"
                     " section_path, kind, description, components, flags, image_path, thumb_path, width, height,"
                     " image_sha, embedding) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (a.id, a.doc_id, a.asset_type, a.caption, a.caption_norm, a.page, json.dumps(list(a.bbox)),
                      a.legend_text, a.section_path, a.kind, a.description, json.dumps(a.components),
                      json.dumps(a.flags), a.image_path, a.thumb_path, a.width, a.height, a.image_sha, to_blob(v)))
        conn.execute("INSERT INTO assets_fts (asset_id, text) VALUES (?, ?)", (a.id, text))
    conn.commit()
