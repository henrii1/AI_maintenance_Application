"""content.db: built by ingestion, opened read-only by the app."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, equipment_json TEXT NOT NULL,
  always_include_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, file_name TEXT NOT NULL, role TEXT NOT NULL, title TEXT NOT NULL,
  sha256 TEXT NOT NULL, page_count INTEGER NOT NULL, pdf_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS set_documents (
  set_id TEXT NOT NULL, doc_id TEXT NOT NULL, PRIMARY KEY (set_id, doc_id));
CREATE TABLE IF NOT EXISTS chunks (
  rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, doc_id TEXT NOT NULL, section_path TEXT NOT NULL,
  page_start INTEGER NOT NULL, page_end INTEGER NOT NULL, text TEXT NOT NULL, token_count INTEGER NOT NULL,
  has_warning INTEGER NOT NULL, figure_refs TEXT NOT NULL, embedding BLOB);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks (doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5 (
  chunk_id UNINDEXED, section_path, text, tokenize = 'porter unicode61');
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, asset_type TEXT NOT NULL, caption TEXT NOT NULL,
  caption_norm TEXT NOT NULL, page INTEGER NOT NULL, bbox TEXT NOT NULL, legend_text TEXT NOT NULL,
  section_path TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL, components TEXT NOT NULL,
  flags TEXT NOT NULL, image_path TEXT NOT NULL, thumb_path TEXT NOT NULL, width INTEGER NOT NULL,
  height INTEGER NOT NULL, image_sha TEXT NOT NULL, embedding BLOB);
CREATE INDEX IF NOT EXISTS assets_doc ON assets (doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5 (
  asset_id UNINDEXED, text, tokenize = 'porter unicode61');
CREATE TABLE IF NOT EXISTS vision_cache (
  image_sha TEXT PRIMARY KEY, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def connect_content(path: Path, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_content_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
