"""Hybrid retrieval over content.db: FTS5 BM25 + cosine similarity, fused with reciprocal rank fusion."""
from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import numpy as np

from backend.content_db import connect_content
from backend.embeddings import Embedder, from_blob
from backend.ingest.pdfutil import normalize_caption

RRF_K = 60
POOL = 20
FUZZY_MIN = 0.6
STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "how", "i", "if",
             "in", "is", "it", "me", "my", "of", "on", "or", "should", "so", "that", "the", "this", "to", "what",
             "when", "where", "which", "why", "with", "you", "your"}
FTS = {"chunks": ("chunks_fts", "chunk_id"), "assets": ("assets_fts", "asset_id")}


@dataclass(frozen=True)
class ChunkHit:
    id: str
    doc_id: str
    doc_title: str
    section_path: str
    page_start: int
    page_end: int
    text: str
    figure_refs: tuple[str, ...]
    has_warning: bool
    score: float


@dataclass(frozen=True)
class AssetInfo:
    id: str
    doc_id: str
    asset_type: str
    caption: str
    kind: str
    description: str
    page: int
    section_path: str
    image_url: str
    thumb_url: str
    pdf_url: str
    image_sha: str

    def public(self) -> dict:
        d = asdict(self)
        d.pop("image_sha")
        return d


def fts_query(text: str) -> str:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS]
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(toks))


def rrf(*rankings: list[str], k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, id_ in enumerate(ranking):
            scores[id_] += 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])


class Retriever:
    def __init__(self, conn, embedder: Embedder):
        self.conn = conn
        self.embedder = embedder
        self._lock = threading.RLock()
        self._matrices: dict[tuple[str, str], tuple[list[str], np.ndarray]] = {}
        self._qvec = lru_cache(maxsize=256)(self.embedder.embed_query)

    @classmethod
    def open(cls, content_dir: Path, embedder: Embedder) -> "Retriever":
        return cls(connect_content(Path(content_dir) / "content.db", readonly=True), embedder)

    def _q(self, sql: str, params=()) -> list:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    # ---- sets & documents -------------------------------------------------------------------------
    def sets(self) -> list[dict]:
        return [{"id": r["id"], "name": r["name"], "equipment": json.loads(r["equipment_json"])}
                for r in self._q("SELECT * FROM sets ORDER BY name")]

    def get_set(self, set_id: str) -> dict | None:
        rows = self._q("SELECT * FROM sets WHERE id = ?", (set_id,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r["id"], "name": r["name"], "equipment": json.loads(r["equipment_json"]),
                "always_include": json.loads(r["always_include_json"])}

    def doc_exists(self, doc_id: str) -> bool:
        return bool(self._q("SELECT 1 FROM documents WHERE id = ?", (doc_id,)))

    # ---- ranking primitives -----------------------------------------------------------------------
    def _matrix(self, set_id: str, table: str) -> tuple[list[str], np.ndarray]:
        key = (set_id, table)
        with self._lock:
            if key not in self._matrices:
                rows = self.conn.execute(
                    f"SELECT x.id, x.embedding FROM {table} x JOIN set_documents s ON s.doc_id = x.doc_id"
                    f" WHERE s.set_id = ? AND x.embedding IS NOT NULL ORDER BY x.id", (set_id,)).fetchall()
                ids = [r["id"] for r in rows]
                m = np.stack([from_blob(r["embedding"]) for r in rows]) if rows else np.zeros((0, 1), np.float32)
                self._matrices[key] = (ids, m)
            return self._matrices[key]

    def _vector_rank(self, set_id: str, table: str, query: str, n: int) -> list[str]:
        ids, m = self._matrix(set_id, table)
        if not ids:
            return []
        sims = m @ self._qvec(query)
        return [ids[i] for i in np.argsort(-sims)[:n]]

    def _fts_rank(self, set_id: str, table: str, query: str, n: int) -> list[str]:
        q = fts_query(query)
        if not q:
            return []
        fts, col = FTS[table]
        rows = self._q(
            f"SELECT {fts}.{col} AS id FROM {fts} JOIN {table} x ON x.id = {fts}.{col}"
            f" JOIN set_documents s ON s.doc_id = x.doc_id"
            f" WHERE {fts} MATCH ? AND s.set_id = ? ORDER BY bm25({fts}) LIMIT ?", (q, set_id, n))
        return [r["id"] for r in rows]

    # ---- chunks -----------------------------------------------------------------------------------
    def _chunk_hits(self, scored: list[tuple[str, float]]) -> list[ChunkHit]:
        if not scored:
            return []
        ids = [i for i, _ in scored]
        rows = {r["id"]: r for r in self._q(
            f"SELECT c.*, d.title AS doc_title FROM chunks c JOIN documents d ON d.id = c.doc_id"
            f" WHERE c.id IN ({','.join('?' * len(ids))})", ids)}
        return [ChunkHit(id=r["id"], doc_id=r["doc_id"], doc_title=r["doc_title"], section_path=r["section_path"],
                         page_start=r["page_start"], page_end=r["page_end"], text=r["text"],
                         figure_refs=tuple(json.loads(r["figure_refs"])), has_warning=bool(r["has_warning"]),
                         score=s)
                for i, s in scored if (r := rows.get(i))]

    def search_chunks(self, set_id: str, query: str, k: int = 8) -> list[ChunkHit]:
        fused = rrf(self._fts_rank(set_id, "chunks", query, POOL), self._vector_rank(set_id, "chunks", query, POOL))
        return self._chunk_hits(fused[:k])

    def section_chunks(self, set_id: str, doc_id: str, section_prefix: str, limit: int = 6) -> list[ChunkHit]:
        rows = self._q("SELECT c.id FROM chunks c JOIN set_documents s ON s.doc_id = c.doc_id"
                       " WHERE s.set_id = ? AND c.doc_id = ? AND c.section_path LIKE ? ORDER BY c.id LIMIT ?",
                       (set_id, doc_id, section_prefix + "%", limit))
        return self._chunk_hits([(r["id"], 0.0) for r in rows])

    # ---- assets -----------------------------------------------------------------------------------
    @staticmethod
    def _asset_info(r) -> AssetInfo:
        return AssetInfo(id=r["id"], doc_id=r["doc_id"], asset_type=r["asset_type"], caption=r["caption"],
                         kind=r["kind"], description=r["description"], page=r["page"], section_path=r["section_path"],
                         image_url=f"/content/{r['image_path']}", thumb_url=f"/content/{r['thumb_path']}",
                         pdf_url=f"/content-docs/{r['doc_id']}.pdf#page={r['page']}", image_sha=r["image_sha"])

    def get_assets(self, ids: list[str]) -> list[AssetInfo]:
        if not ids:
            return []
        rows = {r["id"]: r for r in self._q(f"SELECT * FROM assets WHERE id IN ({','.join('?' * len(ids))})", ids)}
        return [self._asset_info(rows[i]) for i in ids if i in rows]

    def list_assets(self, set_id: str, asset_type: str | None = None) -> list[AssetInfo]:
        sql = "SELECT a.* FROM assets a JOIN set_documents s ON s.doc_id = a.doc_id WHERE s.set_id = ?"
        params: tuple = (set_id,)
        if asset_type:
            sql, params = sql + " AND a.asset_type = ?", (set_id, asset_type)
        return [self._asset_info(r) for r in self._q(sql + " ORDER BY a.doc_id, a.page, a.id", params)]

    def figure_candidates(self, set_id: str, query: str, chunks: list[ChunkHit], k: int = 6) -> list[AssetInfo]:
        referenced = list(dict.fromkeys(ref for c in chunks for ref in c.figure_refs))
        searched = [i for i, _ in rrf(self._fts_rank(set_id, "assets", query, POOL),
                                      self._vector_rank(set_id, "assets", query, POOL))]
        out: list[AssetInfo] = []
        seen_sha: set[str] = set()
        for a in self.get_assets(list(dict.fromkeys(referenced + searched))):
            if a.asset_type == "cad" and a.id not in referenced:
                continue  # CAD sheets only when their sheet chunk was retrieved
            if a.image_sha in seen_sha:
                continue
            seen_sha.add(a.image_sha)
            out.append(a)
            if len(out) == k:
                break
        return out

    def resolve_figure(self, set_id: str, name: str) -> str | None:
        rows = self._q("SELECT a.id, a.caption_norm FROM assets a JOIN set_documents s ON s.doc_id = a.doc_id"
                       " WHERE s.set_id = ?", (set_id,))
        if any(r["id"] == name for r in rows):
            return name
        target = normalize_caption(name.split("/")[-1].replace("-", " "))
        best_id, best = None, 0.0
        for r in rows:
            ratio = SequenceMatcher(None, target, r["caption_norm"]).ratio()
            if ratio > best:
                best_id, best = r["id"], ratio
        return best_id if best >= FUZZY_MIN else None
