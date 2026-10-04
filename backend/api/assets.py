"""Manual sets, their figures/CAD sheets, and session-guarded source PDFs."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from backend.api.deps import get_retriever, require_viewer, settings_of

router = APIRouter()
DOC_ID = re.compile(r"^[a-z0-9-]+$")


@router.get("/api/sets", dependencies=[Depends(require_viewer)])
def list_sets(request: Request):
    return get_retriever(request).sets()


@router.get("/api/sets/{set_id}/assets", dependencies=[Depends(require_viewer)])
def list_assets(set_id: str, request: Request, type: str | None = None):
    r = get_retriever(request)
    if r.get_set(set_id) is None:
        raise HTTPException(404, "Unknown equipment set.")
    if type not in (None, "figure", "cad"):
        raise HTTPException(422, "type must be figure or cad")
    return [a.public() for a in r.list_assets(set_id, type)]


@router.get("/content-docs/{doc_id}.pdf", dependencies=[Depends(require_viewer)])
def source_pdf(doc_id: str, request: Request):
    path = settings_of(request).content_dir / "docs" / f"{doc_id}.pdf"
    if not DOC_ID.match(doc_id) or not path.is_file():
        raise HTTPException(404, "Not found.")
    return FileResponse(path, media_type="application/pdf")
