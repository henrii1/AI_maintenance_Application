"""python -m backend.ingest <set_id> [--force] [--skip-vision]"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from backend.config import Settings, get_settings
from backend.content_db import connect_content, init_content_schema
from backend.embeddings import Embedder, get_embedder
from backend.ingest.cad import CAD_DPI, cad_chunk, extract_cad
from backend.ingest.chunker import chunk_document
from backend.ingest.describe import apply_vision
from backend.ingest.figures import extract_figures
from backend.ingest.index import doc_is_current, link_documents, replace_document, upsert_set
from backend.ingest.manifest import load_manifest
from backend.ingest.pdfutil import sha256_bytes, sha256_file
from backend.ingest.render import render_asset
from backend.ingest.review import write_review
from backend.ingest.sections import build_sections


@dataclass
class IngestSummary:
    docs_processed: int = 0
    docs_skipped: int = 0
    chunks: int = 0
    figures: int = 0
    cad: int = 0
    flagged: Counter = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)
    vision_cost: float = 0.0
    review_path: Path | None = None

    def render(self) -> str:
        lines = [f"documents: {self.docs_processed} processed, {self.docs_skipped} unchanged",
                 f"chunks: {self.chunks}  figures: {self.figures}  cad sheets: {self.cad}",
                 f"flagged: {dict(self.flagged) or 'none'}",
                 f"vision cost: ${self.vision_cost:.4f}",
                 f"review: {self.review_path}"]
        lines += [f"PROBLEM: {p}" for p in self.problems]
        return "\n".join(lines)


def ingest_set(settings: Settings, set_id: str, *, force: bool, client, embedder: Embedder) -> IngestSummary:
    manifest = load_manifest(settings.corpus_dir / set_id)
    content_dir = Path(settings.content_dir)
    conn = connect_content(content_dir / "content.db")
    try:
        init_content_schema(conn)
        upsert_set(conn, manifest)
        summary = IngestSummary()
        for spec in manifest.documents:
            path = manifest.path(spec)
            doc_overrides = {fid: v for fid, v in (manifest.overrides.get("figures") or {}).items() if fid.startswith(spec.id + "/")}
            fingerprint = sha256_bytes(json.dumps({"file": sha256_file(path), "title": spec.title, "role": spec.role, "overrides": doc_overrides}, sort_keys=True).encode())
            if not force and doc_is_current(conn, spec.id, fingerprint):
                summary.docs_skipped += 1
                continue
            doc = pymupdf.open(path)
            title = spec.title or doc.metadata.get("title") or spec.id
            pdf_rel = f"docs/{spec.id}.pdf"
            (content_dir / "docs").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, content_dir / pdf_rel)
            if spec.role == "cad":
                asset = extract_cad(doc, spec.id, title)
                render_asset(doc, asset, content_dir, dpi=CAD_DPI)
                assets, chunks = [asset], [cad_chunk(doc, spec.id, asset)]
            else:
                shutil.rmtree(content_dir / "figures" / spec.id, ignore_errors=True)  # drop stale crops
                sections = build_sections(doc, title)
                assets, problems = extract_figures(doc, spec.id, spec.role, sections, manifest.figure_override)
                summary.problems += problems
                for a in assets:
                    render_asset(doc, a, content_dir)
                chunks = chunk_document(doc, spec.id, sections, assets)
            if client is not None:
                summary.vision_cost += apply_vision(conn, client, settings.vision_model, assets, content_dir)
            replace_document(conn, doc_id=spec.id, file_name=path.name, role=spec.role, title=title, sha256=fingerprint,
                             page_count=doc.page_count, pdf_path=pdf_rel, chunks=chunks, assets=assets,
                             embedder=embedder)
            summary.docs_processed += 1
        link_documents(conn, manifest.id, [d.id for d in manifest.documents])
        q = "FROM {t} x JOIN set_documents s ON s.doc_id = x.doc_id WHERE s.set_id = ?"
        summary.chunks = conn.execute("SELECT COUNT(*) " + q.format(t="chunks"), (set_id,)).fetchone()[0]
        for row in conn.execute("SELECT x.asset_type, x.flags " + q.format(t="assets"), (set_id,)):
            if row["asset_type"] == "cad":
                summary.cad += 1
            else:
                summary.figures += 1
            summary.flagged.update(json.loads(row["flags"]))
        summary.review_path = write_review(conn, set_id, content_dir / "review" / f"{set_id}.html", summary.problems)
        return summary
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.ingest")
    ap.add_argument("set_id")
    ap.add_argument("--force", action="store_true", help="re-process documents even if unchanged")
    ap.add_argument("--skip-vision", action="store_true", help="no Claude calls (descriptions stay empty)")
    args = ap.parse_args(argv)
    settings = get_settings()
    client = None
    if not args.skip_vision:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    summary = ingest_set(settings, args.set_id, force=args.force, client=client,
                         embedder=get_embedder(settings.embed_cache_dir))
    print(summary.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
