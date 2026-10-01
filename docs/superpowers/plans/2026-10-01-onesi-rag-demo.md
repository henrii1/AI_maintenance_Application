# Onesi RAG Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a gated, deployable three-pane maintenance assistant for the Ariel KBE compressor. It answers technician questions from the OEM manuals with cited step-by-step procedures, shows the relevant manual figure or CAD drawing, and lists the tools needed.

**Architecture:** An offline ingestion CLI turns each manual set (`corpus/<set>/`) into `content/` (SQLite `content.db` with chunks, figures, FTS5 and embeddings, plus cropped PNGs). A single FastAPI service serves the Onesi static site, the `/demo` and `/admin` pages and the JSON/SSE API. Each question runs hybrid retrieval once, then two Claude Haiku calls in parallel (streamed chat answer + tools list), merged into one SSE stream. Runtime user data (access requests, sessions, quotas, logs) lives in `data/app.db` on a volume.

**Tech Stack:** Python 3.12+, uv, FastAPI, uvicorn, PyMuPDF, fastembed (`BAAI/bge-small-en-v1.5`), NumPy, SQLite (FTS5), anthropic SDK (AsyncAnthropic), httpx (Resend), itsdangerous, pytest + pytest-asyncio. The frontend is vanilla HTML/CSS/JS with marked + DOMPurify from cdnjs. Deployed as a Docker image on Railway.

**Spec:** `docs/superpowers/specs/2026-10-01-onesi-rag-demo-design.md`

**Spec deltas (decided while planning against the real PDFs):**
- Citations are derived server-side from `[p.N]` markers in the answer, mapped to the retrieved chunk on that page. The `present_assets` tool carries only `figure_ids`.
- CAD sheet titles come from `manifest.yaml` (`title:`), not from title-block parsing.
- CAD sheets become figure candidates when their sheet chunk is retrieved, so no separate score threshold is needed.
- Tool definitions do not use `strict: true`. Every tool input is validated server-side (figure IDs against the candidate list, icon keys against `icons.json`, enums against their lists), so schema strictness isn't load-bearing and it avoids model-support differences.
- Fuzzy figure lookup uses `difflib.SequenceMatcher` ratio ≥ 0.6.
- Source PDFs are served at `/content-docs/<doc_id>.pdf`.
- Quota is **reserved atomically before** the LLM calls and **released** if the chat fails or the client disconnects. This gives the spec's "charge only on success" without a race.
- A caption with no detectable graphics (only the two flowcharts on manual pp.123–124) is reported in the review page and not stored until `overrides.yaml` gives it a bbox.

## Global Constraints

- Python `>=3.12`; Docker base `python:3.12-slim`; dependencies managed with `uv` (`uv.lock` committed).
- Every Claude call defaults to model `claude-haiku-4-5`, configurable via `CHAT_MODEL`, `TOOLS_MODEL`, `REWRITE_MODEL`, `VISION_MODEL`. No extended thinking.
- Web search is off by default. `TOOLS_WEB_SEARCH=1` adds `{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}` to the tools call only.
- `DAILY_QUESTION_LIMIT=10` per user and `GLOBAL_DAILY_LIMIT=500` across all users, per UTC day. Admin sessions are unlimited.
- Access codes: 8 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, displayed `XXXX-XXXX`, single-use, 7-day expiry, stored as SHA-256. 5 failed redeems per email per hour triggers a lockout.
- Session cookie `onesi_session`: 30 days, `HttpOnly`, `SameSite=Lax`, `Secure` when `PUBLIC_BASE_URL` starts with `https`. Admin cookie `onesi_admin`: signed, 12 h.
- Auth endpoints never reveal whether an email exists. Request, new-code and redeem failures return generic messages.
- Figure IDs are `<doc-id>/<caption-slug>`. The slug is lowercase ASCII, with runs of non-alphanumerics turned into `-`, and is ≤ 80 chars. CAD IDs are `<doc-id>/sheet-1`.
- `content/` is committed and read-only at runtime. `data/` is gitignored.
- Branding is "Onesi". New pages reuse the CSS tokens in `web/assets/style.css` (`--navy`, `--steel`, `--mist`, `--line`, `--cyan`, `--cyan-deep`, `--orange`, `--font-head`, `--font-body`).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Two questions sent at once by a user with one question left.** Expect exactly one to run and the other to get `quota_exceeded`. Pinned by `test_reserve_is_atomic_at_limit` (Task 11).
2. **Email typed with different case or whitespace** (`" Tech@Example.com"` on request, `"tech@example.com"` on redeem). Expect it to be treated as the same person. Pinned by `test_email_normalized_across_flow` (Task 12).
3. **Code typed lowercase, without the dash, or with spaces** (`"k7qm 4xpd"`). Expect it to be accepted. Pinned by `test_redeem_accepts_loose_code_format` (Task 11).
4. **Browser closed mid-answer.** Expect upstream tasks cancelled, the reserved quota released and no assistant message stored. Pinned by `test_disconnect_releases_quota` (Task 16).
5. **A `conversation_id` that belongs to another user.** Expect 404, with no history leaked. Pinned by `test_foreign_conversation_rejected` (Task 16).

**Task order:** 1–9 build and run ingestion (Task 9 produces the real `content/`), 10 retrieval, 11–13 access gating and admin API, 14–16 Claude calls and the chat stream, 17–19 frontend, 20 eval, 21 Docker/deploy. Each task leaves the test suite green.

---

## File Map

```
pyproject.toml, uv.lock, .gitignore, .dockerignore, .env.example, Dockerfile, docker-compose.yml, railway.json, README.md
corpus/ariel-kbe/{manifest.yaml, overrides.yaml, docs/*.pdf, cad/*.PDF}
backend/
  config.py                 Settings (env) + production check
  embeddings.py             Embedder protocol, FastEmbedder, blob helpers
  content_db.py             content.db schema + connect
  db.py                     app.db schema/migrations + time helpers
  ingest/
    manifest.py             Manifest/DocSpec loading + validation
    models.py               Asset, Chunk dataclasses
    pdfutil.py              body-band text blocks, caption/slug normalisation, hashing
    sections.py             TOC → Section list, section_at()
    render.py               crop/page → PNG + thumbnail
    figures.py              caption-anchored figure detection + extraction
    cad.py                  CAD sheet asset + sheet chunk
    chunker.py              section-aware text chunks + figure_refs
    describe.py             Claude vision pass + cache
    index.py                write docs/chunks/assets/FTS/embeddings
    review.py               static review gallery
    __main__.py             CLI
  retrieval/
    search.py               Retriever (FTS + vector + RRF, candidates, lookups)
  llm/
    common.py               pricing, cost estimate, tool_choice helper, tool-input extraction
    client.py               AsyncAnthropic factory, usage logging
    prompts.py              system prompts + context builders
    rewrite.py              follow-up → standalone query
    chat.py                 streamed answer + present_assets
    tools.py                tools list via submit_tools
  auth/
    ratelimit.py            in-memory sliding-window limiter
    email.py                Mailer (Resend/console) + templates
    requests.py             access request CRUD
    codes.py                code generation/issue/redeem/lockout
    sessions.py             session tokens
    quota.py                reserve/release/status
  api/
    app.py                  create_app(), static mounts, pages, healthz
    deps.py                 db/viewer/admin dependencies, cookies
    access.py               /api/access/*, /api/me
    admin.py                /api/admin/*
    assets.py               /api/sets*, /content-docs/*
    chat.py                 /api/chat (SSE), /api/tools/retry
web/                        Onesi site (moved) + demo.html, admin.html, assets/{demo.css, demo.js, admin.js, icons.json}
content/                    generated: content.db, figures/, cad/, docs/, review/
scripts/eval_answers.py     on-demand answer quality eval (real API)
tests/                      conftest.py, fakes.py, content_fixture.py, golden/retrieval.yaml, test_*.py
```

---
## Phase 1 — Foundation and ingestion

### Task 1: Project scaffold, repo reorganisation, config, manifest

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `backend/__init__.py`, `backend/config.py`, `backend/ingest/__init__.py`, `backend/ingest/manifest.py`, `corpus/ariel-kbe/manifest.yaml`, `corpus/ariel-kbe/overrides.yaml`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_manifest.py`
- Move: `demo_data/*.pdf` → `corpus/ariel-kbe/docs/`, `demo_data/cad/*` → `corpus/ariel-kbe/cad/`, `resources/onesi-site (1)/*` → `web/`

**Interfaces:**
- Produces:
  - `backend.config.Settings` (fields listed below), `Settings.secure_cookies -> bool`, `Settings.check_production() -> None` (raises `RuntimeError`), `backend.config.get_settings() -> Settings`, `DEV_SECRET: str`.
  - `backend.ingest.manifest.load_manifest(set_dir: Path) -> Manifest`, `Manifest(id, set_dir, equipment: dict, documents: tuple[DocSpec, ...], always_include_sections: tuple[AlwaysInclude, ...], overrides: dict)`, `Manifest.path(doc) -> Path`, `Manifest.figure_override(figure_id: str) -> dict`, `DocSpec(id, file, role, title, applies_to)`, `AlwaysInclude(doc, section_prefix)`, `ManifestError`.
  - `tests/conftest.py` constants `ROOT`, `SET_DIR`, `MANUAL_PDF`.

- [ ] **Step 1: Move files and create the package skeleton**

```bash
cd /Users/emeraldhenry/AI_maintenance_Application
mkdir -p corpus/ariel-kbe/docs corpus/ariel-kbe/cad web backend/ingest tests
mv demo_data/*.pdf corpus/ariel-kbe/docs/
mv demo_data/cad/* corpus/ariel-kbe/cad/
cp -R "resources/onesi-site (1)/." web/ && rm -rf "resources/onesi-site (1)"
find . -name .DS_Store -delete
rm -rf demo_data
touch backend/__init__.py backend/ingest/__init__.py tests/__init__.py
ls corpus/ariel-kbe/docs corpus/ariel-kbe/cad web
```

Expected: 7 PDFs in `docs/`, `OL0511_S1.PDF` and `OL0782_S1.PDF` in `cad/`, and `index.html prototype.html team.html contact.html assets/ hero-video.mp4 helpline.png` in `web/`.

- [ ] **Step 2: Write `pyproject.toml` and `.gitignore`, then sync**

`pyproject.toml`:
```toml
[project]
name = "onesi"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic-settings>=2.4",
  "anthropic>=0.69",
  "pymupdf>=1.24",
  "fastembed>=0.4",
  "numpy>=1.26",
  "pyyaml>=6.0",
  "httpx>=0.27",
  "itsdangerous>=2.2",
]

[dependency-groups]
dev = ["pytest>=8.0", "pytest-asyncio>=0.24"]

[tool.uv]
package = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
  "integration: needs the real embedding model (downloads ~130MB once) and/or the built content/content.db",
]
```

`.gitignore`:
```
.DS_Store
__pycache__/
*.pyc
.venv/
.env
.pytest_cache/
.cache/
data/
```

Run: `uv sync`
Expected: creates `.venv/` and `uv.lock` without errors.

- [ ] **Step 3: Write the manifest and an empty overrides file**

`corpus/ariel-kbe/manifest.yaml`:
```yaml
id: ariel-kbe
equipment:
  name: Ariel KBE Reciprocating Compressor
  manufacturer: Ariel Corporation
  frame_class: KBE
documents:
  - {id: kbe-manual, file: docs/Ariel-KBE-Manual.pdf, role: manual, title: KBE Maintenance & Repair Manual}
  - {id: er-034, file: docs/ER-034.pdf, role: engineering_reference, title: "ER-034 Protection of Non-Lube Cylinders and Distance Pieces with VCI Powder", applies_to: ["ariel-*"]}
  - {id: er-063, file: docs/ER-063.pdf, role: engineering_reference, title: "ER-063 Fastener Torques for Reciprocating Compressors", applies_to: ["ariel-*"]}
  - {id: er-129, file: docs/ER-129.pdf, role: engineering_reference, title: "ER-129 Preserving Compressor Frames (Mineral, PAO, Ester Oil)", applies_to: ["ariel-*"]}
  - {id: er-130, file: docs/ER-130.pdf, role: engineering_reference, title: "ER-130 Preserving Cylinders and Distance Pieces (Mineral, PAO, Ester Oil)", applies_to: ["ariel-*"]}
  - {id: er-140, file: docs/ER-140.pdf, role: engineering_reference, title: "ER-140 Preserving Compressor Frames (PAG Oil)", applies_to: ["ariel-*"]}
  - {id: er-141, file: docs/ER-141.pdf, role: engineering_reference, title: "ER-141 Preserving Cylinders and Distance Pieces (PAG Oil)", applies_to: ["ariel-*"]}
  - {id: ol0511, file: cad/OL0511_S1.PDF, role: cad, title: "OL0511 Guide Support Outline, H:E:ET Class Cylinders"}
  - {id: ol0782, file: cad/OL0782_S1.PDF, role: cad, title: "OL0782 Encoder Shaft Attachment Details"}
always_include_sections:
  - {doc: kbe-manual, section_prefix: "Section 1 - Tools"}
```

`corpus/ariel-kbe/overrides.yaml`:
```yaml
# Per-figure fixes applied on every ingest run. Keys are figure IDs.
#   bbox: [x0, y0, x1, y1]   crop box in PDF points (overrides detection)
#   caption: "New caption"    display caption (ID is unchanged)
#   exclude: true             drop the figure
figures: {}
```

- [ ] **Step 4: Write the failing tests**

`tests/conftest.py`:
```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SET_DIR = ROOT / "corpus" / "ariel-kbe"
MANUAL_PDF = SET_DIR / "docs" / "Ariel-KBE-Manual.pdf"
```

`tests/test_config.py`:
```python
import pytest

from backend.config import Settings


def _clean(monkeypatch):
    for k in ["CHAT_MODEL", "DAILY_QUESTION_LIMIT", "TOOLS_WEB_SEARCH", "PUBLIC_BASE_URL",
              "SESSION_SECRET", "ADMIN_PASSWORD", "ANTHROPIC_API_KEY"]:
        monkeypatch.delenv(k, raising=False)


def test_defaults(monkeypatch):
    _clean(monkeypatch)
    s = Settings(_env_file=None)
    assert s.chat_model == "claude-haiku-4-5"
    assert s.tools_model == s.rewrite_model == s.vision_model == "claude-haiku-4-5"
    assert s.daily_question_limit == 10
    assert s.global_daily_limit == 500
    assert s.tools_web_search is False
    assert s.secure_cookies is False
    s.check_production()  # local http: no requirements


def test_env_override(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("CHAT_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://onesi.up.railway.app")
    s = Settings(_env_file=None)
    assert s.chat_model == "claude-sonnet-5-5"
    assert s.secure_cookies is True


def test_production_requires_secrets(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://onesi.up.railway.app")
    with pytest.raises(RuntimeError, match="SESSION_SECRET.*ADMIN_PASSWORD.*ANTHROPIC_API_KEY"):
        Settings(_env_file=None).check_production()
```

`tests/test_manifest.py`:
```python
import pytest

from backend.ingest.manifest import ManifestError, load_manifest
from tests.conftest import SET_DIR


def test_loads_real_manifest():
    m = load_manifest(SET_DIR)
    assert m.id == "ariel-kbe"
    assert [d.id for d in m.documents] == [
        "kbe-manual", "er-034", "er-063", "er-129", "er-130", "er-140", "er-141", "ol0511", "ol0782"]
    assert {d.role for d in m.documents} == {"manual", "engineering_reference", "cad"}
    assert m.always_include_sections[0].doc == "kbe-manual"
    assert m.always_include_sections[0].section_prefix == "Section 1 - Tools"
    assert m.path(m.documents[0]).is_file()
    assert m.equipment["name"] == "Ariel KBE Reciprocating Compressor"


def _write(tmp_path, body):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "manifest.yaml").write_text(body)
    return tmp_path


def test_rejects_unknown_role(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: brochure}\n")
    with pytest.raises(ManifestError, match="unknown role"):
        load_manifest(tmp_path)


def test_rejects_missing_file(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: missing.pdf, role: manual}\n")
    with pytest.raises(ManifestError, match="file not found"):
        load_manifest(tmp_path)


def test_rejects_duplicate_ids(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: manual}\n  - {id: a, file: a.pdf, role: cad}\n")
    with pytest.raises(ManifestError, match="duplicate"):
        load_manifest(tmp_path)


def test_rejects_unknown_always_include_doc(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: manual}\n"
                     "always_include_sections:\n  - {doc: zzz, section_prefix: Tools}\n")
    with pytest.raises(ManifestError, match="unknown doc"):
        load_manifest(tmp_path)


def test_reads_overrides(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: manual}\n")
    (tmp_path / "overrides.yaml").write_text("figures:\n  a/fig-one:\n    exclude: true\n")
    m = load_manifest(tmp_path)
    assert m.figure_override("a/fig-one") == {"exclude": True}
    assert m.figure_override("a/other") == {}


def test_empty_overrides_file(tmp_path):
    _write(tmp_path, "id: x\ndocuments:\n  - {id: a, file: a.pdf, role: manual}\n")
    (tmp_path / "overrides.yaml").write_text("figures: {}\n")
    assert load_manifest(tmp_path).figure_override("a/x") == {}
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.config'`.

- [ ] **Step 6: Implement config and manifest**

`backend/config.py`:
```python
"""Environment-driven settings. Every value can be set via an env var of the same name (upper-case)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    chat_model: str = "claude-haiku-4-5"
    tools_model: str = "claude-haiku-4-5"
    rewrite_model: str = "claude-haiku-4-5"
    vision_model: str = "claude-haiku-4-5"
    tools_web_search: bool = False

    daily_question_limit: int = 10
    global_daily_limit: int = 500

    admin_password: str = ""
    admin_email: str = ""
    session_secret: str = DEV_SECRET
    resend_api_key: str = ""
    email_from: str = "Onesi <onboarding@resend.dev>"
    public_base_url: str = "http://localhost:8000"

    data_dir: Path = Path("data")
    content_dir: Path = Path("content")
    corpus_dir: Path = Path("corpus")
    web_dir: Path = Path("web")
    embed_cache_dir: Path = Path(".cache/fastembed")

    @property
    def secure_cookies(self) -> bool:
        return self.public_base_url.startswith("https")

    def check_production(self) -> None:
        """Refuse to start a public (https) deployment with missing secrets."""
        if not self.secure_cookies:
            return
        missing = [name for name, ok in [
            ("SESSION_SECRET", self.session_secret != DEV_SECRET),
            ("ADMIN_PASSWORD", bool(self.admin_password)),
            ("ANTHROPIC_API_KEY", bool(self.anthropic_api_key)),
        ] if not ok]
        if missing:
            raise RuntimeError(f"Missing production settings: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`backend/ingest/manifest.py`:
```python
"""Load and validate a manual set's manifest.yaml (and optional overrides.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROLES = {"manual", "engineering_reference", "cad"}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class DocSpec:
    id: str
    file: str
    role: str
    title: str | None = None
    applies_to: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlwaysInclude:
    doc: str
    section_prefix: str


@dataclass(frozen=True)
class Manifest:
    id: str
    set_dir: Path
    equipment: dict
    documents: tuple[DocSpec, ...]
    always_include_sections: tuple[AlwaysInclude, ...] = ()
    overrides: dict = field(default_factory=dict)

    def path(self, doc: DocSpec) -> Path:
        return self.set_dir / doc.file

    def figure_override(self, figure_id: str) -> dict:
        return (self.overrides.get("figures") or {}).get(figure_id, {})


def load_manifest(set_dir: Path) -> Manifest:
    set_dir = Path(set_dir)
    raw = yaml.safe_load((set_dir / "manifest.yaml").read_text())
    docs: list[DocSpec] = []
    seen: set[str] = set()
    for d in raw.get("documents", []):
        spec = DocSpec(id=d["id"], file=d["file"], role=d["role"], title=d.get("title"),
                       applies_to=tuple(d.get("applies_to", [])))
        if spec.role not in ROLES:
            raise ManifestError(f"{spec.id}: unknown role {spec.role!r}")
        if spec.id in seen:
            raise ManifestError(f"duplicate document id {spec.id!r}")
        if not (set_dir / spec.file).is_file():
            raise ManifestError(f"{spec.id}: file not found: {spec.file}")
        seen.add(spec.id)
        docs.append(spec)
    always = tuple(AlwaysInclude(a["doc"], a["section_prefix"]) for a in raw.get("always_include_sections", []))
    for a in always:
        if a.doc not in seen:
            raise ManifestError(f"always_include_sections references unknown doc {a.doc!r}")
    ov_path = set_dir / "overrides.yaml"
    overrides = (yaml.safe_load(ov_path.read_text()) or {}) if ov_path.is_file() else {}
    return Manifest(id=raw["id"], set_dir=set_dir, equipment=raw.get("equipment", {}),
                    documents=tuple(docs), always_include_sections=always, overrides=overrides)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_manifest.py -v`
Expected: 10 passed.

- [ ] **Step 8: Commit**

```bash
git add -A pyproject.toml uv.lock .gitignore backend tests corpus web resources
git commit -m "chore: scaffold project, move corpus and site, add config and manifest loader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: PDF text utilities and content.db schema

**Files:**
- Create: `backend/ingest/pdfutil.py`, `backend/ingest/models.py`, `backend/content_db.py`
- Test: `tests/test_pdfutil.py`, `tests/test_content_db.py`

**Interfaces:**
- Consumes: `tests.conftest.MANUAL_PDF`.
- Produces:
  - `pdfutil`: `BODY_TOP = 45.0`, `BODY_BOTTOM = 750.0`, `TextBlock(page: int, bbox: tuple[float, float, float, float], lines: tuple[str, ...])` with `.text: str` and `.word_count: int`, `page_blocks(page: pymupdf.Page) -> list[TextBlock]`, `normalize_ws(s) -> str`, `canon(s) -> str`, `normalize_caption(s) -> str`, `slugify(s, max_len=80) -> str`, `approx_tokens(text) -> int`, `sha256_bytes(b) -> str`, `sha256_file(path) -> str`.
  - `models`: `Asset` and `Chunk` dataclasses (fields below).
  - `content_db`: `connect_content(path: Path, readonly: bool = False) -> sqlite3.Connection` (row_factory = `sqlite3.Row`), `init_content_schema(conn) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pdfutil.py`:
```python
import pymupdf

from backend.ingest.pdfutil import (approx_tokens, canon, normalize_caption, page_blocks,
                                    sha256_bytes, slugify)
from tests.conftest import MANUAL_PDF


def test_page_blocks_strip_running_header_and_footer():
    page = pymupdf.open(MANUAL_PDF)[87]  # manual p.88
    texts = [b.text for b in page_blocks(page)]
    joined = " ".join(texts)
    assert "Crosshead Installation" in joined
    assert "Page 88 of 154" not in joined
    assert "KBE Maintenance & Repair Manual - Section 4" not in joined
    assert all(b.page == 88 for b in page_blocks(page))


def test_text_block_helpers():
    page = pymupdf.open(MANUAL_PDF)[87]
    caption = next(b for b in page_blocks(page) if b.text.startswith("Figure: Crosshead Installation"))
    assert caption.text == "Figure: Crosshead Installation"
    assert caption.word_count == 3


def test_caption_normalisation_and_slug():
    assert canon("RTD’s & Thermo-couples") == "rtds thermo couples"
    assert normalize_caption("Figure: Crosshead  Installation") == "crosshead installation"
    assert slugify("Figure: VVCP Disassembly/Assembly") == "vvcp-disassembly-assembly"
    assert slugify("A-18255 Proximity Switch Wiring Connections for Unit in Operation") == \
        "a-18255-proximity-switch-wiring-connections-for-unit-in-operation"
    assert len(slugify("x " * 100)) <= 80
    assert not slugify("x " * 100).endswith("-")


def test_approx_tokens_and_hash():
    assert approx_tokens("one two three four five six seven eight nine ten") == 13
    assert approx_tokens("") == 0
    assert len(sha256_bytes(b"abc")) == 64
```

`tests/test_content_db.py`:
```python
import sqlite3

import pytest

from backend.content_db import connect_content, init_content_schema


def test_schema_and_fts(tmp_path):
    db = tmp_path / "content.db"
    conn = connect_content(db)
    init_content_schema(conn)
    init_content_schema(conn)  # idempotent
    conn.execute("INSERT INTO chunks (id, doc_id, section_path, page_start, page_end, text, token_count,"
                 " has_warning, figure_refs) VALUES ('d:0001','d','S',1,1,'Install the crosshead',4,0,'[]')")
    conn.execute("INSERT INTO chunks_fts (chunk_id, section_path, text) VALUES ('d:0001','S','Install the crosshead')")
    conn.commit()
    rows = conn.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH 'crossheads'").fetchall()
    assert [r["chunk_id"] for r in rows] == ["d:0001"]  # porter stemming


def test_readonly_connection_refuses_writes(tmp_path):
    db = tmp_path / "content.db"
    conn = connect_content(db)
    init_content_schema(conn)
    conn.close()
    ro = connect_content(db, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO sets (id, name, equipment_json) VALUES ('x','x','{}')")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pdfutil.py tests/test_content_db.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`backend/ingest/pdfutil.py`:
```python
"""Low-level PDF text helpers shared by the ingestion steps."""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf

# Running headers sit above y=45pt and footers below y=750pt on every Ariel document (US Letter, 792pt).
BODY_TOP = 45.0
BODY_BOTTOM = 750.0


@dataclass(frozen=True)
class TextBlock:
    page: int  # 1-based
    bbox: tuple[float, float, float, float]
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return normalize_ws(" ".join(self.lines))

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def page_blocks(page: pymupdf.Page) -> list[TextBlock]:
    """Text blocks in the body band of a page, in PyMuPDF's reading order."""
    out: list[TextBlock] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        lines = tuple(t for line in b["lines"] if (t := "".join(s["text"] for s in line["spans"]).strip()))
        if not lines:
            continue
        x0, y0, x1, y1 = b["bbox"]
        if y1 <= BODY_TOP or y0 >= BODY_BOTTOM:
            continue
        out.append(TextBlock(page.number + 1, (x0, y0, x1, y1), lines))
    return out


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def canon(s: str) -> str:
    """ASCII, lower-case, alphanumerics separated by single spaces."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def normalize_caption(s: str) -> str:
    return canon(re.sub(r"^\s*figure:\s*", "", s, flags=re.I))


def slugify(s: str, max_len: int = 80) -> str:
    return normalize_caption(s).replace(" ", "-")[:max_len].strip("-")


def approx_tokens(text: str) -> int:
    return math.ceil(len(text.split()) * 1.3)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())
```

`backend/ingest/models.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field

from backend.ingest.pdfutil import normalize_caption


@dataclass
class Asset:
    id: str
    doc_id: str
    asset_type: str  # "figure" | "cad"
    caption: str
    page: int
    bbox: tuple[float, float, float, float]
    section_path: str
    source_caption: str = ""  # caption as printed in the PDF (used to match "see Figure: X")
    legend_text: str = ""
    kind: str = ""
    description: str = ""
    components: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    image_path: str = ""  # relative to content_dir
    thumb_path: str = ""
    width: int = 0
    height: int = 0
    image_sha: str = ""

    @property
    def caption_norm(self) -> str:
        return normalize_caption(self.caption)


@dataclass
class Chunk:
    id: str
    doc_id: str
    section_path: str
    page_start: int
    page_end: int
    text: str
    token_count: int
    has_warning: bool
    figure_refs: list[str] = field(default_factory=list)
```

`backend/content_db.py`:
```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pdfutil.py tests/test_content_db.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/ingest/pdfutil.py backend/ingest/models.py backend/content_db.py tests/test_pdfutil.py tests/test_content_db.py
git commit -m "feat(ingest): add PDF text utilities, asset/chunk models, content.db schema

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: TOC-based sections

**Files:**
- Create: `backend/ingest/sections.py`
- Test: `tests/test_sections.py`

**Interfaces:**
- Consumes: `pdfutil.BODY_TOP`, `pdfutil.normalize_ws`.
- Produces: `Section(path: str, page: int, y: float)`, `build_sections(doc: pymupdf.Document, fallback_title: str) -> list[Section]` (ordered by `(page, y)`; the first entry starts at page 1), `section_at(sections, page: int, y: float) -> str`, `SEP = " › "`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sections.py`:
```python
import pymupdf

from backend.ingest.sections import Section, build_sections, section_at
from tests.conftest import MANUAL_PDF


def test_manual_sections_follow_toc():
    doc = pymupdf.open(MANUAL_PDF)
    secs = build_sections(doc, "KBE Maintenance & Repair Manual")
    assert len(secs) == len(doc.get_toc()) + 1  # + front-matter fallback
    assert secs[0] == Section("KBE Maintenance & Repair Manual", 1, 0.0)
    positions = [(s.page, s.y) for s in secs]
    assert positions == sorted(positions)


def test_section_lookup():
    doc = pymupdf.open(MANUAL_PDF)
    secs = build_sections(doc, "KBE Maintenance & Repair Manual")
    assert section_at(secs, 88, 100) == "Section 4 - Part Replacement › Crossheads › Crosshead Installation"
    assert section_at(secs, 10, 300) == "Section 1 - Tools › Ariel Optional Tools"
    assert section_at(secs, 1, 300) == "KBE Maintenance & Repair Manual"


def test_document_without_toc_uses_fallback():
    doc = pymupdf.open()
    doc.new_page()
    assert build_sections(doc, "Untitled Sheet") == [Section("Untitled Sheet", 1, 0.0)]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sections.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`backend/ingest/sections.py`:
```python
"""Turn a PDF's table of contents into positioned sections."""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from backend.ingest.pdfutil import BODY_TOP, normalize_ws

SEP = " › "


@dataclass(frozen=True)
class Section:
    path: str
    page: int
    y: float


def _heading_y(doc: pymupdf.Document, page: int, title: str) -> float:
    if not 1 <= page <= doc.page_count:
        return BODY_TOP
    for rect in doc[page - 1].search_for(title[:60]):
        if rect.y0 >= BODY_TOP:
            return rect.y0
    return BODY_TOP


def build_sections(doc: pymupdf.Document, fallback_title: str) -> list[Section]:
    toc = doc.get_toc()
    if not toc:
        return [Section(fallback_title, 1, 0.0)]
    out: list[Section] = []
    stack: list[str] = []
    prev = (0, 0.0)
    for level, title, page in toc:
        title = normalize_ws(title)
        stack = stack[: level - 1] + [title]
        pos = (page, _heading_y(doc, page, title))
        if pos < prev:  # keep positions monotonic if a heading search hit the wrong line
            pos = prev
        prev = pos
        out.append(Section(SEP.join(stack), pos[0], pos[1]))
    if (out[0].page, out[0].y) > (1, BODY_TOP):
        out.insert(0, Section(fallback_title, 1, 0.0))
    return out


def section_at(sections: list[Section], page: int, y: float) -> str:
    current = sections[0].path
    for s in sections:
        if (s.page, s.y) <= (page, y):
            current = s.path
        else:
            break
    return current
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_sections.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/ingest/sections.py tests/test_sections.py
git commit -m "feat(ingest): derive positioned sections from the PDF table of contents

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Figure detection, extraction, and rendering

The detection rules below were validated against the real manual during planning. They give 85 figures: the p.55 pseudo-caption is skipped, p.104 splits into separate figures, and side legends on pp.90/100 and CAUTION boxes are handled. The only figures with no graphics are the flowcharts on pp.123–124.

**Files:**
- Create: `backend/ingest/figures.py`, `backend/ingest/render.py`
- Test: `tests/test_figures.py`

**Interfaces:**
- Consumes: `pdfutil.page_blocks/TextBlock/BODY_TOP/BODY_BOTTOM/normalize_ws/slugify/sha256_bytes`, `sections.Section/section_at`, `models.Asset`.
- Produces:
  - `figures.FoundFigure(caption: str, page: int, rect: pymupdf.Rect | None, legend_text: str)`, `figures.find_page_figures(page) -> list[FoundFigure]`.
  - `figures.extract_figures(doc, doc_id: str, role: str, sections: list[Section], override: Callable[[str], dict]) -> tuple[list[Asset], list[str]]`, which returns the assets plus human-readable problems.
  - `render.render_clip(page, rect: pymupdf.Rect | None, out_png: Path, out_thumb: Path, dpi: int) -> tuple[int, int, str]` (width, height, sha256 of the PNG). `rect=None` renders the full page.
  - `render.render_asset(doc, asset: Asset, content_dir: Path, dpi: int = 200) -> None`, which fills `image_path`, `thumb_path`, `width`, `height`, `image_sha`. Figures go to `figures/<doc_id>/<slug>.png`, CAD to `cad/<doc_id>.png`.

- [ ] **Step 1: Write the failing tests**

`tests/test_figures.py`:
```python
import pymupdf
import pytest

from backend.ingest.figures import extract_figures, find_page_figures
from backend.ingest.render import render_asset
from backend.ingest.sections import build_sections
from tests.conftest import MANUAL_PDF, SET_DIR


@pytest.fixture(scope="module")
def manual():
    return pymupdf.open(MANUAL_PDF)


def _page(manual, n):
    return find_page_figures(manual[n - 1])


def test_detects_all_manual_captions(manual):
    found = [f for p in manual for f in find_page_figures(p)]
    assert len(found) == 85
    assert sorted(f.page for f in found if f.rect is None) == [123, 124]  # flowcharts → overrides.yaml


def test_crosshead_installation_includes_legend(manual):
    [fig] = _page(manual, 88)
    assert fig.caption == "Crosshead Installation"
    assert "Metal Plate" in fig.legend_text
    assert fig.rect.y1 > 340


def test_side_by_side_captions_split_without_body_text(manual):
    figs = _page(manual, 104)
    assert [f.caption for f in figs] == ["Swabbing Oil Passages", "Swabbing Oil Gallery", "Pen Light Inspection"]
    assert figs[0].rect.y1 < 260 and figs[1].rect.y1 < 260


def test_two_figures_on_one_page(manual):
    figs = _page(manual, 18)
    assert len(figs) == 2
    assert figs[0].rect.y1 < figs[1].rect.y0


def test_pseudo_caption_in_body_text_is_skipped(manual):
    assert all("shows the" not in f.caption for f in _page(manual, 55))


def test_caution_box_excluded_and_side_legend_included(manual):
    [fig90] = _page(manual, 90)
    assert fig90.rect.y1 < 390  # CAUTION box starts at ~y=394
    assert fig90.rect.x1 > 530  # right-hand legend column
    [fig100] = _page(manual, 100)
    assert "Main Bearing Cap Screw" in fig100.legend_text


def test_extract_figures_ids_overrides_and_problems(manual):
    sections = build_sections(manual, "KBE Maintenance & Repair Manual")
    overrides = {
        "kbe-manual/crosshead-installation": {"caption": "Crosshead Installation (Tool and Plate)"},
        "kbe-manual/typical-magnet-assemblies": {"exclude": True},
        "kbe-manual/crosshead-clearance-verification-correction-flowchart": {"bbox": [57, 80, 555, 700]},
    }
    assets, problems = extract_figures(manual, "kbe-manual", "manual", sections, lambda fid: overrides.get(fid, {}))
    ids = [a.id for a in assets]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("kbe-manual/") for i in ids)
    assert "kbe-manual/typical-magnet-assemblies" not in ids
    ch = next(a for a in assets if a.id == "kbe-manual/crosshead-installation")
    assert ch.caption == "Crosshead Installation (Tool and Plate)"
    assert ch.source_caption == "Crosshead Installation"
    assert ch.section_path.endswith("Crosshead Installation")
    assert ch.asset_type == "figure" and ch.page == 88
    flow = next(a for a in assets if a.id == "kbe-manual/crosshead-clearance-verification-correction-flowchart")
    assert "overridden" in flow.flags
    assert len(problems) == 1 and "p.124" in problems[0]
    # 85 detected − 1 excluded (magnet assemblies) − 1 unresolved (p.124 flowchart, reported as a problem)
    assert len(assets) == 83


def test_render_asset_writes_png_and_thumb(manual, tmp_path):
    sections = build_sections(manual, "KBE Maintenance & Repair Manual")
    assets, _ = extract_figures(manual, "kbe-manual", "manual", sections, lambda fid: {})
    asset = next(a for a in assets if a.id == "kbe-manual/crosshead-installation")
    render_asset(manual, asset, tmp_path)
    assert asset.image_path == "figures/kbe-manual/crosshead-installation.png"
    assert (tmp_path / asset.image_path).is_file()
    assert (tmp_path / asset.thumb_path).is_file()
    assert asset.width > 500 and asset.height > 500
    assert len(asset.image_sha) == 64
    thumb = pymupdf.Pixmap(str(tmp_path / asset.thumb_path))
    assert 380 <= thumb.width <= 420
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_figures.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.ingest.figures'`.

- [ ] **Step 3: Implement rendering**

`backend/ingest/render.py`:
```python
"""Render page regions to PNG + 400px-wide thumbnail."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from backend.ingest.models import Asset
from backend.ingest.pdfutil import sha256_bytes

PAD = 6.0
THUMB_WIDTH = 400


def render_clip(page: pymupdf.Page, rect: pymupdf.Rect | None, out_png: Path, out_thumb: Path,
                dpi: int) -> tuple[int, int, str]:
    clip = page.rect if rect is None else (pymupdf.Rect(rect) + (-PAD, -PAD, PAD, PAD)) & page.rect
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    data = pix.tobytes("png")
    out_png.write_bytes(data)
    zoom = THUMB_WIDTH / clip.width
    page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip).save(out_thumb)
    return pix.width, pix.height, sha256_bytes(data)


def render_asset(doc: pymupdf.Document, asset: Asset, content_dir: Path, dpi: int = 200) -> None:
    content_dir = Path(content_dir)
    if asset.asset_type == "cad":
        rel = Path("cad") / f"{asset.doc_id}.png"
        rect = None
    else:
        rel = Path("figures") / asset.doc_id / f"{asset.id.split('/', 1)[1]}.png"
        rect = pymupdf.Rect(asset.bbox)
    thumb_rel = rel.with_suffix(".thumb.png")
    w, h, sha = render_clip(doc[asset.page - 1], rect, content_dir / rel, content_dir / thumb_rel, dpi)
    asset.image_path, asset.thumb_path = rel.as_posix(), thumb_rel.as_posix()
    asset.width, asset.height, asset.image_sha = w, h, sha
```

- [ ] **Step 4: Implement figure detection and extraction**

`backend/ingest/figures.py`:
```python
"""Caption-anchored figure detection.

Ariel captions are standalone text blocks reading "Figure: <title>" placed ABOVE the graphic, with a
numbered callout legend below or beside it. Callout circles are often vector overlays, so we crop page
regions (caption ∪ graphics ∪ legend) rather than extracting embedded images.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import pymupdf

from backend.ingest.models import Asset
from backend.ingest.pdfutil import BODY_BOTTOM, BODY_TOP, TextBlock, normalize_ws, page_blocks, slugify
from backend.ingest.sections import Section, section_at

LEGEND_START = re.compile(r"^\d{1,2}(\s|$)")        # "1 Crosshead" / "1" — but not "2. Remove ..."
NUMBER_COLUMN = re.compile(r"^\d{1,2}( \d{1,2})*$")  # "1 2 3 4" column beside a label column
ADMONITION = re.compile(r"^(NOTE|CAUTION|WARNING|DANGER)\b")
LEGEND_REACH = 60.0   # pt below the graphics in which legend blocks are attached
SIDE_REACH = 40.0     # pt to the right of the graphics in which side legends are attached
MIN_UNCAPTIONED = 60.0
SMALL_FIGURE = 60.0


@dataclass(frozen=True)
class FoundFigure:
    caption: str
    page: int
    rect: pymupdf.Rect | None
    legend_text: str


def is_caption(b: TextBlock) -> bool:
    return b.lines[0].startswith("Figure:") and len(b.lines) <= 3 and not b.text.endswith(".")


def is_legend_like(b: TextBlock, blocks: list[TextBlock]) -> bool:
    if LEGEND_START.match(b.lines[0]):
        return True
    x0, y0, _, y1 = b.bbox
    return any(NUMBER_COLUMN.match(o.text) and o.bbox[3] > y0 and o.bbox[1] < y1 and 0 <= x0 - o.bbox[2] <= 25
               for o in blocks if o is not b)


def is_body(b: TextBlock, blocks: list[TextBlock]) -> bool:
    if is_caption(b) or is_legend_like(b, blocks):
        return False
    return b.word_count >= 10 or bool(ADMONITION.match(b.text))


def _graphics(page: pymupdf.Page, body_rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    width = page.rect.width
    rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    rects += [pymupdf.Rect(d["rect"]) for d in page.get_drawings() if d["rect"].width > 3 or d["rect"].height > 3]
    return [r for r in rects
            if r.y0 >= BODY_TOP and r.y1 <= BODY_BOTTOM and r.width < 0.9 * width
            and not any(r.contains(b) for b in body_rects)]  # drops NOTE/CAUTION box borders


def find_page_figures(page: pymupdf.Page) -> list[FoundFigure]:
    blocks = page_blocks(page)
    caps = [b for b in blocks if is_caption(b)]
    if not caps:
        return []
    body = [b for b in blocks if is_body(b, blocks)]
    gfx = _graphics(page, [pymupdf.Rect(b.bbox) for b in body])
    page_right = page.rect.width - 30
    out: list[FoundFigure] = []
    for c in caps:
        cr = pymupdf.Rect(c.bbox)
        right = min([o.bbox[0] - 4 for o in caps
                     if o is not c and o.bbox[0] > cr.x1 - 1 and o.bbox[1] < cr.y1 and o.bbox[3] > cr.y0]
                    + [page_right])
        left = cr.x0 - 10
        cap_bottom = min([o.bbox[1] for o in caps
                          if o is not c and o.bbox[1] > cr.y1 and o.bbox[0] < right and o.bbox[2] > left]
                         + [BODY_BOTTOM])
        stop = min([b.bbox[1] for b in body
                    if b.bbox[1] > cr.y1 and b.bbox[0] < right and b.bbox[2] > left + 15], default=BODY_BOTTOM)
        bottom = min(cap_bottom, stop)
        caption = normalize_ws(c.text.removeprefix("Figure:"))
        g = [r for r in gfx if r.y0 >= cr.y1 - 2 and r.y1 <= cap_bottom + 2 and r.y0 < stop
             and r.x0 >= left - 5 and r.x1 <= right + 5]
        if not g:
            out.append(FoundFigure(caption, page.number + 1, None, ""))
            continue
        box = pymupdf.Rect(g[0])
        for r in g[1:]:
            box |= r
        legend: list[TextBlock] = []
        changed = True
        while changed:
            changed = False
            for b in blocks:
                r = pymupdf.Rect(b.bbox)
                if b in legend or box.contains(r) or not is_legend_like(b, blocks):
                    continue
                if (cr.y1 - 2 <= r.y0 <= box.y1 + LEGEND_REACH and r.x0 <= box.x1 + SIDE_REACH
                        and r.x0 >= left - 5 and r.x1 <= right + 5 and r.y1 <= bottom + 2):
                    box |= r
                    legend.append(b)
                    changed = True
        legend.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        out.append(FoundFigure(caption, page.number + 1, box | cr, " ".join(b.text for b in legend)))
    return out


def _uncaptioned(page: pymupdf.Page, taken: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    rects = [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    return [r for r in rects
            if min(r.width, r.height) >= MIN_UNCAPTIONED and r.y0 >= BODY_TOP and r.y1 <= BODY_BOTTOM
            and not any(t.intersects(r) for t in taken)]


def extract_figures(doc: pymupdf.Document, doc_id: str, role: str, sections: list[Section],
                    override: Callable[[str], dict]) -> tuple[list[Asset], list[str]]:
    assets: list[Asset] = []
    problems: list[str] = []
    used: set[str] = set()

    def unique(fid: str) -> str:
        base, n = fid, 2
        while fid in used:
            fid, n = f"{base}-{n}", n + 1
        used.add(fid)
        return fid

    for page in doc:
        n = page.number + 1
        taken: list[pymupdf.Rect] = []
        for f in find_page_figures(page):
            fid = unique(f"{doc_id}/{slugify(f.caption)}")
            ov = override(fid)
            if ov.get("exclude"):
                continue
            flags: list[str] = []
            rect = f.rect
            if "bbox" in ov:
                rect = pymupdf.Rect(ov["bbox"])
                flags.append("overridden")
            if rect is None:
                problems.append(f"{fid} (p.{n}): caption found but no graphics detected; add a bbox in overrides.yaml")
                continue
            if rect.height < SMALL_FIGURE:
                flags.append("small")
            taken.append(rect)
            assets.append(Asset(
                id=fid, doc_id=doc_id, asset_type="figure", caption=ov.get("caption", f.caption),
                source_caption=f.caption, page=n, bbox=tuple(rect), legend_text=f.legend_text,
                section_path=section_at(sections, n, rect.y0), flags=flags))
        if role == "engineering_reference":
            for k, rect in enumerate(_uncaptioned(page, taken), start=1):
                fid = unique(f"{doc_id}/p{n}-image-{k}")
                if override(fid).get("exclude"):
                    continue
                section = section_at(sections, n, rect.y0)
                assets.append(Asset(
                    id=fid, doc_id=doc_id, asset_type="figure",
                    caption=f"{section.split(' › ')[-1]} (image, p.{n})", source_caption="",
                    page=n, bbox=tuple(rect), section_path=section, flags=["uncaptioned"]))
    return assets, problems
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_figures.py -v`
Expected: 8 passed. If `test_detects_all_manual_captions` reports a different count, print `[(f.page, f.caption) for f in found]` and compare against the 85 captions listed in spec §2 before touching thresholds.

- [ ] **Step 6: Commit**

```bash
git add backend/ingest/figures.py backend/ingest/render.py tests/test_figures.py
git commit -m "feat(ingest): caption-anchored figure detection, overrides, and PNG rendering

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Section-aware text chunker

**Files:**
- Create: `backend/ingest/chunker.py`
- Test: `tests/test_chunker.py`

**Interfaces:**
- Consumes: `pdfutil.page_blocks/approx_tokens/canon`, `sections.section_at`, `models.Asset/Chunk`, `figures.extract_figures` (tests only).
- Produces: `chunk_document(doc, doc_id: str, sections: list[Section], figures: list[Asset]) -> list[Chunk]` (IDs `"{doc_id}:{n:04d}"`, `token_count <= 800`), `figure_refs_for(text, section_path, page_start, page_end, figures) -> list[str]`, `pack(pieces: list[Piece]) -> list[list[Piece]]`, `Piece(text: str, page: int)`, constants `MAX_TOKENS=800`, `MIN_TOKENS=80`, `OVERLAP_MAX=120`.

- [ ] **Step 1: Write the failing tests**

`tests/test_chunker.py`:
```python
import re

import pymupdf
import pytest

from backend.ingest.chunker import MAX_TOKENS, Piece, chunk_document, figure_refs_for, pack
from backend.ingest.figures import extract_figures
from backend.ingest.models import Asset
from backend.ingest.sections import build_sections
from tests.conftest import MANUAL_PDF


@pytest.fixture(scope="module")
def manual_chunks():
    doc = pymupdf.open(MANUAL_PDF)
    sections = build_sections(doc, "KBE Maintenance & Repair Manual")
    figures, _ = extract_figures(doc, "kbe-manual", "manual", sections, lambda fid: {})
    return chunk_document(doc, "kbe-manual", sections, figures)


def test_chunks_exclude_running_headers_and_toc_leaders(manual_chunks):
    for c in manual_chunks:
        assert not re.search(r"Page \d+ of 154", c.text)
        assert "KBE Maintenance & Repair Manual - Section" not in c.text
        assert "......" not in c.text


def test_chunk_ids_and_size_bounds(manual_chunks):
    ids = [c.id for c in manual_chunks]
    assert ids == [f"kbe-manual:{i:04d}" for i in range(1, len(ids) + 1)]
    assert all(c.token_count <= MAX_TOKENS for c in manual_chunks)
    assert 120 <= len(manual_chunks) <= 700


def test_crosshead_installation_chunk(manual_chunks):
    c = next(c for c in manual_chunks if "Lay a 3/16 inch" in c.text)
    assert c.section_path.endswith("Crosshead Installation")
    assert c.page_start <= 88 <= c.page_end
    assert "kbe-manual/crosshead-alignment-tool" in c.figure_refs   # "(see Figure: Crosshead Alignment Tool)"
    assert "kbe-manual/crosshead-installation" in c.figure_refs     # same section and page
    assert "Metal Plate" not in c.text                               # legend text lives with the figure


def test_warning_flag(manual_chunks):
    flagged = [c for c in manual_chunks if c.has_warning]
    assert flagged
    assert all(re.search(r"\b(WARNING|CAUTION|DANGER)\b", c.text) for c in flagged)


def test_pack_respects_budget_and_overlap():
    short = Piece("x " * 50, 1)          # 65 tokens
    big = Piece("y " * 300, 2)           # 390 tokens
    # pieces over 120 tokens are never repeated as overlap
    assert pack([big, big, short, big]) == [[big, big], [short, big]]
    # a short trailing piece (≤120 tokens) is repeated at the start of the next chunk
    assert pack([big, short, big, big]) == [[big, short], [short, big], [big]]


def test_figure_refs_match_caption_mentions_and_section():
    a = Asset(id="d/oil-passages", doc_id="d", asset_type="figure", caption="Oil Passages (renamed)",
              source_caption="Oil Passages", page=5, bbox=(0, 0, 1, 1), section_path="S › Other")
    b = Asset(id="d/crank", doc_id="d", asset_type="figure", caption="Crank", source_caption="Crank",
              page=5, bbox=(0, 0, 1, 1), section_path="S › Here")
    assert figure_refs_for("Clean them (see Figure: Oil  Passages).", "S › Here", 5, 5, [a, b]) == ["d/oil-passages", "d/crank"]
    assert figure_refs_for("Unrelated text.", "S › Elsewhere", 9, 9, [a, b]) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_chunker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.ingest.chunker'`.

- [ ] **Step 3: Implement**

`backend/ingest/chunker.py`:
```python
"""Split a document into section-aware chunks (≤800 tokens) that remember which figures they reference."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

import pymupdf

from backend.ingest.models import Asset, Chunk
from backend.ingest.pdfutil import approx_tokens, canon, page_blocks
from backend.ingest.sections import Section, section_at

DOT_LEADER = re.compile(r"\.{6,}\s*\d*\s*$")  # table-of-contents lines
WARNING_WORDS = re.compile(r"\b(WARNING|CAUTION|DANGER)\b")
MAX_TOKENS = 800
MIN_TOKENS = 80
OVERLAP_MAX = 120
SPLIT_WORDS = 450  # oversized blocks (tables) are cut into ≤450-word pieces (≈585 tokens)


@dataclass(frozen=True)
class Piece:
    text: str
    page: int


def _inside_figure(bbox, rects: list[pymupdf.Rect]) -> bool:
    r = pymupdf.Rect(bbox)
    area = r.get_area() or 1.0
    return any((r & fr).get_area() / area >= 0.6 for fr in rects)


def _collect(doc, sections: list[Section], figures: list[Asset]) -> list[tuple[str, list[Piece]]]:
    fig_rects: dict[int, list[pymupdf.Rect]] = defaultdict(list)
    for a in figures:
        fig_rects[a.page].append(pymupdf.Rect(a.bbox))
    spans: dict[str, list[Piece]] = {}
    for page in doc:
        n = page.number + 1
        for b in page_blocks(page):
            if _inside_figure(b.bbox, fig_rects[n]) or any(DOT_LEADER.search(line) for line in b.lines):
                continue
            path = section_at(sections, n, b.bbox[1])
            words = b.text.split()
            for i in range(0, len(words), SPLIT_WORDS):
                spans.setdefault(path, []).append(Piece(" ".join(words[i:i + SPLIT_WORDS]), n))
    return list(spans.items())


def pack(pieces: list[Piece]) -> list[list[Piece]]:
    groups: list[list[Piece]] = []
    cur: list[Piece] = []
    tok = 0
    for p in pieces:
        t = approx_tokens(p.text)
        if cur and tok + t > MAX_TOKENS:
            groups.append(cur)
            last = cur[-1]
            lt = approx_tokens(last.text)
            cur, tok = ([last], lt) if lt <= OVERLAP_MAX else ([], 0)
        cur.append(p)
        tok += t
    if cur:
        groups.append(cur)
    return groups


def figure_refs_for(text: str, section_path: str, page_start: int, page_end: int,
                    figures: list[Asset]) -> list[str]:
    hay = canon(text)
    refs: list[str] = []
    for a in figures:
        cap = canon(a.source_caption or a.caption)
        mentioned = bool(cap) and f"figure {cap}" in hay
        co_located = a.section_path == section_path and page_start <= a.page <= page_end
        if (mentioned or co_located) and a.id not in refs:
            refs.append(a.id)
    return refs


def chunk_document(doc, doc_id: str, sections: list[Section], figures: list[Asset]) -> list[Chunk]:
    chunks: list[Chunk] = []
    spans = _collect(doc, sections, figures)
    carry: list[Piece] = []
    for i, (path, pieces) in enumerate(spans):
        pieces = carry + pieces
        carry = []
        if sum(approx_tokens(p.text) for p in pieces) < MIN_TOKENS and i < len(spans) - 1:
            carry = pieces  # tiny heading intro: merge into the next section
            continue
        for group in pack(pieces):
            text = "\n\n".join(p.text for p in group)
            ps, pe = min(p.page for p in group), max(p.page for p in group)
            chunks.append(Chunk(
                id=f"{doc_id}:{len(chunks) + 1:04d}", doc_id=doc_id, section_path=path,
                page_start=ps, page_end=pe, text=text, token_count=approx_tokens(text),
                has_warning=bool(WARNING_WORDS.search(text)),
                figure_refs=figure_refs_for(text, path, ps, pe, figures)))
    return chunks
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_chunker.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/ingest/chunker.py tests/test_chunker.py
git commit -m "feat(ingest): section-aware chunker with figure references

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: CAD sheets

**Files:**
- Create: `backend/ingest/cad.py`
- Test: `tests/test_cad.py`

**Interfaces:**
- Consumes: `models.Asset/Chunk`, `pdfutil.normalize_ws/approx_tokens`, `render.render_asset`.
- Produces: `extract_cad(doc, doc_id: str, title: str) -> Asset` (id `"{doc_id}/sheet-1"`, `asset_type="cad"`, `kind="cad"`, `section_path="Drawing {DOC_ID}"`), `cad_chunk(doc, doc_id: str, asset: Asset) -> Chunk` (id `"{doc_id}:0001"`, `figure_refs=[asset.id]`), `CAD_DPI = 150`.

- [ ] **Step 1: Write the failing test**

`tests/test_cad.py`:
```python
import pymupdf

from backend.ingest.cad import CAD_DPI, cad_chunk, extract_cad
from backend.ingest.render import render_asset
from tests.conftest import SET_DIR

CAD = SET_DIR / "cad" / "OL0782_S1.PDF"


def test_cad_asset_chunk_and_render(tmp_path):
    doc = pymupdf.open(CAD)
    asset = extract_cad(doc, "ol0782", "OL0782 Encoder Shaft Attachment Details")
    assert asset.id == "ol0782/sheet-1"
    assert asset.asset_type == "cad" and asset.kind == "cad"
    assert asset.section_path == "Drawing OL0782"

    chunk = cad_chunk(doc, "ol0782", asset)
    assert chunk.id == "ol0782:0001"
    assert chunk.figure_refs == ["ol0782/sheet-1"]
    assert chunk.text.startswith("OL0782 Encoder Shaft Attachment Details.")
    assert "KBE" in chunk.text and "ENCODER SHAFT" in chunk.text
    assert chunk.token_count <= 800

    render_asset(doc, asset, tmp_path, dpi=CAD_DPI)
    assert asset.image_path == "cad/ol0782.png"
    assert (tmp_path / "cad" / "ol0782.thumb.png").is_file()
    assert asset.width > 1500
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cad.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`backend/ingest/cad.py`:
```python
"""CAD drawings: one full-sheet asset plus one searchable chunk (title block, notes, dimension tables)."""
from __future__ import annotations

from backend.ingest.models import Asset, Chunk
from backend.ingest.pdfutil import approx_tokens, normalize_ws

CAD_DPI = 150
MAX_WORDS = 600


def extract_cad(doc, doc_id: str, title: str) -> Asset:
    page = doc[0]
    return Asset(id=f"{doc_id}/sheet-1", doc_id=doc_id, asset_type="cad", caption=title, source_caption=title,
                 page=1, bbox=tuple(page.rect), section_path=f"Drawing {doc_id.upper()}", kind="cad")


def cad_chunk(doc, doc_id: str, asset: Asset) -> Chunk:
    words = normalize_ws(" ".join(p.get_text() for p in doc)).split()[:MAX_WORDS]
    text = f"{asset.caption}. {' '.join(words)}"
    return Chunk(id=f"{doc_id}:0001", doc_id=doc_id, section_path=asset.section_path, page_start=1,
                 page_end=doc.page_count, text=text, token_count=approx_tokens(text), has_warning=False,
                 figure_refs=[asset.id])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cad.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/ingest/cad.py tests/test_cad.py
git commit -m "feat(ingest): CAD sheet assets and searchable sheet chunks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Shared LLM helpers and the vision pass

**Files:**
- Create: `backend/llm/__init__.py`, `backend/llm/common.py`, `backend/ingest/describe.py`, `tests/fakes.py`
- Test: `tests/test_llm_common.py`, `tests/test_describe.py`

**Interfaces:**
- Consumes: `models.Asset`, `content_db`.
- Produces:
  - `llm.common`: `PRICES: dict[str, tuple[float, float, float]]` ($/MTok input, output, cache-read), `WEB_SEARCH_USD = 0.01`, `usage_counts(usage) -> tuple[int, int, int]`, `web_search_count(usage) -> int`, `estimate_cost(model, usage) -> float`, `tool_choice_for(model, name) -> dict`, `tool_input(message, name) -> dict | None`.
  - `ingest.describe`: `KINDS: list[str]`, `VISION_TOOL: dict`, `describe_image(client, model, png: bytes, caption: str, legend: str) -> tuple[dict, usage]`, `apply_vision(conn, client, model, assets, content_dir) -> float` (USD spent).
  - `tests/fakes.py`: `usage(i=100, o=20, searches=0)`, `text_block(t)`, `tool_use(name, input)`, `message(*blocks, stop_reason="end_turn", u=None)`, `FakeAnthropic(handler)` (sync `.messages.create`), `FakeAsyncAnthropic(handler, stream_handler=None)` (async `.messages.create` and `.messages.stream`), `HashEmbedder`.

- [ ] **Step 1: Write the test fakes**

`tests/fakes.py`:
```python
"""Test doubles for the Anthropic SDK and the embedding model."""
from __future__ import annotations

import asyncio
import hashlib
import re
from types import SimpleNamespace as NS

import numpy as np


def usage(i: int = 100, o: int = 20, searches: int = 0):
    return NS(input_tokens=i, output_tokens=o, cache_read_input_tokens=0, cache_creation_input_tokens=0,
              server_tool_use=NS(web_search_requests=searches) if searches else None)


def text_block(t: str):
    return NS(type="text", text=t)


def tool_use(name: str, input: dict):
    return NS(type="tool_use", name=name, input=input, id="toolu_test")


def message(*blocks, stop_reason: str = "end_turn", u=None):
    return NS(content=list(blocks), stop_reason=stop_reason, usage=u or usage())


class _SyncMessages:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def create(self, **kw):
        self.calls.append(kw)
        r = self.handler(kw)
        if isinstance(r, Exception):
            raise r
        return r


class FakeAnthropic:
    """handler(kwargs) -> message | Exception"""

    def __init__(self, handler):
        self.messages = _SyncMessages(handler)


class _FakeStream:
    def __init__(self, chunks: list[str], final, delay: float):
        self._chunks, self._final, self._delay = chunks, final, delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            if self._delay:
                await asyncio.sleep(self._delay)
            if isinstance(c, Exception):
                raise c
            yield NS(type="text", text=c)

    async def get_final_message(self):
        return self._final


class _AsyncMessages:
    def __init__(self, handler, stream_handler):
        self.handler, self.stream_handler, self.calls = handler, stream_handler, []

    async def create(self, **kw):
        self.calls.append(kw)
        r = self.handler(kw)
        if isinstance(r, Exception):
            raise r
        return r

    def stream(self, **kw):
        self.calls.append(kw)
        chunks, final, delay = self.stream_handler(kw)
        return _FakeStream(chunks, final, delay)


class FakeAsyncAnthropic:
    """handler(kwargs) -> message | Exception for create();
    stream_handler(kwargs) -> (text_chunks, final_message, per_chunk_delay) for stream()."""

    def __init__(self, handler=None, stream_handler=None):
        self.messages = _AsyncMessages(handler or (lambda kw: AssertionError("unexpected create")),
                                       stream_handler or (lambda kw: ([], message(), 0)))


class HashEmbedder:
    """Deterministic bag-of-words vectors: lexical overlap ⇒ cosine similarity."""
    dim = 384

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, np.float32)
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_llm_common.py`:
```python
from backend.llm.common import estimate_cost, tool_choice_for, tool_input, web_search_count
from tests.fakes import message, text_block, tool_use, usage


def test_estimate_cost_haiku_and_web_search():
    assert estimate_cost("claude-haiku-4-5", usage(1_000_000, 0)) == 1.0
    assert estimate_cost("claude-haiku-4-5", usage(0, 1_000_000)) == 5.0
    assert round(estimate_cost("claude-haiku-4-5", usage(0, 0, searches=2)), 4) == 0.02
    assert estimate_cost("claude-unknown", usage(1_000_000, 0)) == 1.0  # falls back to Haiku pricing


def test_tool_choice_forced_only_where_supported():
    assert tool_choice_for("claude-haiku-4-5", "x") == {"type": "tool", "name": "x"}
    assert tool_choice_for("claude-sonnet-5-5", "x") == {"type": "auto"}
    assert tool_choice_for("claude-opus-5-5", "x") == {"type": "auto"}


def test_tool_input_and_search_count():
    msg = message(text_block("hi"), tool_use("submit", {"a": 1}), u=usage(searches=1))
    assert tool_input(msg, "submit") == {"a": 1}
    assert tool_input(msg, "other") is None
    assert web_search_count(msg.usage) == 1
    assert web_search_count(usage()) == 0
```

`tests/test_describe.py`:
```python
import json

import anthropic
import httpx

from backend.content_db import connect_content, init_content_schema
from backend.ingest.describe import apply_vision
from backend.ingest.models import Asset
from tests.fakes import FakeAnthropic, message, tool_use

RESULT = {"kind": "exploded_parts", "complete": False, "has_body_text": True,
          "description": "Crosshead with installation tool and plate.", "components": ["crosshead", "plate"]}


def _asset(tmp_path, sha="a" * 64, asset_type="figure"):
    (tmp_path / "figures").mkdir(exist_ok=True)
    (tmp_path / "figures" / "x.png").write_bytes(b"\x89PNG fake")
    return Asset(id="d/x", doc_id="d", asset_type=asset_type, caption="Crosshead Installation", page=1,
                 bbox=(0, 0, 1, 1), section_path="S", legend_text="1 Tool 2 Plate",
                 image_path="figures/x.png", image_sha=sha)


def _conn(tmp_path):
    conn = connect_content(tmp_path / "content.db")
    init_content_schema(conn)
    return conn


def test_vision_applies_result_and_caches(tmp_path):
    client = FakeAnthropic(lambda kw: message(tool_use("record_figure", RESULT)))
    conn = _conn(tmp_path)
    a = _asset(tmp_path)
    cost = apply_vision(conn, client, "claude-haiku-4-5", [a], tmp_path)
    assert cost > 0
    assert a.kind == "exploded_parts"
    assert a.description == "Crosshead with installation tool and plate."
    assert a.components == ["crosshead", "plate"]
    assert {"incomplete", "has_body_text"} <= set(a.flags)
    req = client.messages.calls[0]
    assert req["tool_choice"] == {"type": "tool", "name": "record_figure"}
    assert req["messages"][0]["content"][0]["type"] == "image"
    assert "Crosshead Installation" in req["messages"][0]["content"][1]["text"]

    b = _asset(tmp_path)  # same image hash → served from cache
    assert apply_vision(conn, client, "claude-haiku-4-5", [b], tmp_path) == 0
    assert len(client.messages.calls) == 1
    assert json.loads(conn.execute("SELECT result_json FROM vision_cache").fetchone()[0])["kind"] == "exploded_parts"


def test_cad_kind_is_preserved(tmp_path):
    client = FakeAnthropic(lambda kw: message(tool_use("record_figure", {**RESULT, "complete": True,
                                                                         "has_body_text": False})))
    a = _asset(tmp_path, sha="b" * 64, asset_type="cad")
    a.kind = "cad"
    apply_vision(_conn(tmp_path), client, "claude-haiku-4-5", [a], tmp_path)
    assert a.kind == "cad" and a.flags == []


def test_api_failure_flags_asset_and_continues(tmp_path):
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    client = FakeAnthropic(lambda kw: err)
    a = _asset(tmp_path, sha="c" * 64)
    assert apply_vision(_conn(tmp_path), client, "claude-haiku-4-5", [a], tmp_path) == 0
    assert "vision_failed" in a.flags
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_llm_common.py tests/test_describe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.llm'`.

- [ ] **Step 4: Implement**

`backend/llm/__init__.py`: empty file.

`backend/llm/common.py`:
```python
"""Model-agnostic helpers shared by every Claude call."""
from __future__ import annotations

# $ per million tokens: (input, output, cache read)
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
}
WEB_SEARCH_USD = 0.01  # $10 per 1,000 searches
# Models that return 400 on forced tool_choice ("tool"/"any"); use "auto" + prompt instruction instead.
NO_FORCED_TOOL_CHOICE = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-mythos-5-1")


def usage_counts(usage) -> tuple[int, int, int]:
    return (getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0,
            getattr(usage, "cache_read_input_tokens", 0) or 0)


def web_search_count(usage) -> int:
    stu = getattr(usage, "server_tool_use", None)
    return (getattr(stu, "web_search_requests", 0) or 0) if stu else 0


def estimate_cost(model: str, usage) -> float:
    pin, pout, pcache = PRICES.get(model, PRICES["claude-haiku-4-5"])
    i, o, c = usage_counts(usage)
    return (i * pin + o * pout + c * pcache) / 1_000_000 + web_search_count(usage) * WEB_SEARCH_USD


def tool_choice_for(model: str, name: str) -> dict:
    if model.startswith(NO_FORCED_TOOL_CHOICE):
        return {"type": "auto"}
    return {"type": "tool", "name": name}


def tool_input(message, name: str) -> dict | None:
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == name:
            return dict(block.input)
    return None
```

`backend/ingest/describe.py`:
```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_llm_common.py tests/test_describe.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/llm tests/fakes.py tests/test_llm_common.py tests/test_describe.py backend/ingest/describe.py
git commit -m "feat(ingest): Claude vision pass with cache; shared LLM pricing/tool helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Embeddings, indexing, review gallery, ingestion CLI

**Files:**
- Create: `backend/embeddings.py`, `backend/ingest/index.py`, `backend/ingest/review.py`, `backend/ingest/__main__.py`
- Test: `tests/test_ingest_cli.py`, `tests/test_embeddings.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces:
  - `embeddings`: `MODEL_NAME`, `Embedder` (Protocol: `dim`, `embed_passages(list[str]) -> np.ndarray[n, 384]`, `embed_query(str) -> np.ndarray[384]`, rows L2-normalised), `FastEmbedder(cache_dir=None)`, `get_embedder(cache_dir: Path | None) -> FastEmbedder` (cached), `to_blob(v) -> bytes`, `from_blob(b) -> np.ndarray`.
  - `ingest.index`: `chunk_embed_text(c) -> str`, `asset_search_text(a) -> str`, `upsert_set(conn, manifest)`, `doc_is_current(conn, doc_id, sha) -> bool`, `link_documents(conn, set_id, doc_ids)`, `replace_document(conn, *, doc_id, file_name, role, title, sha256, page_count, pdf_path, chunks, assets, embedder)`.
  - `ingest.review`: `write_review(conn, set_id, out_path: Path, problems: list[str]) -> Path`.
  - `ingest.__main__`: `IngestSummary`, `ingest_set(settings, set_id, *, force: bool, client, embedder) -> IngestSummary`, `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/test_embeddings.py`:
```python
import numpy as np
import pytest

from backend.embeddings import from_blob, get_embedder, to_blob


def test_blob_roundtrip():
    v = np.arange(4, dtype=np.float32)
    assert np.array_equal(from_blob(to_blob(v)), v)


@pytest.mark.integration
def test_fastembed_semantic_similarity(tmp_path):
    e = get_embedder(tmp_path / "fe")
    docs = e.embed_passages(["Tighten the crosshead balance nut with the hydraulic torque tool.",
                             "Daily maintenance: check frame oil level."])
    q = e.embed_query("how do I torque the crosshead nut")
    assert docs.shape == (2, 384)
    assert abs(float(np.linalg.norm(q)) - 1.0) < 1e-4
    assert float(docs[0] @ q) > float(docs[1] @ q)
```

`tests/test_ingest_cli.py`:
```python
import shutil

import pytest

from backend.config import Settings
from backend.content_db import connect_content
from backend.ingest.__main__ import ingest_set
from tests.conftest import SET_DIR
from tests.fakes import FakeAnthropic, HashEmbedder, message, tool_use

VISION = {"kind": "line_drawing", "complete": True, "has_body_text": False,
          "description": "A test description.", "components": ["guide"]}


@pytest.fixture
def mini(tmp_path):
    corpus = tmp_path / "corpus" / "mini"
    (corpus / "docs").mkdir(parents=True)
    (corpus / "cad").mkdir()
    shutil.copy(SET_DIR / "docs" / "ER-130.pdf", corpus / "docs")
    shutil.copy(SET_DIR / "cad" / "OL0782_S1.PDF", corpus / "cad")
    (corpus / "manifest.yaml").write_text(
        "id: mini\nequipment: {name: Mini Set}\ndocuments:\n"
        "  - {id: er-130, file: docs/ER-130.pdf, role: engineering_reference, title: ER-130 Cylinders}\n"
        "  - {id: ol0782, file: cad/OL0782_S1.PDF, role: cad, title: OL0782 Encoder Shaft}\n")
    settings = Settings(_env_file=None, corpus_dir=tmp_path / "corpus", content_dir=tmp_path / "content")
    return settings


def test_ingest_end_to_end_and_idempotent(mini):
    client = FakeAnthropic(lambda kw: message(tool_use("record_figure", VISION)))
    s1 = ingest_set(mini, "mini", force=False, client=client, embedder=HashEmbedder())
    assert s1.docs_processed == 2 and s1.docs_skipped == 0
    assert s1.figures >= 2 and s1.cad == 1 and s1.chunks >= 3
    conn = connect_content(mini.content_dir / "content.db", readonly=True)
    assert conn.execute("SELECT COUNT(*) FROM set_documents WHERE set_id='mini'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == s1.chunks
    assert conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NULL").fetchone()[0] == 0
    row = conn.execute("SELECT * FROM assets WHERE id='er-130/compressor-guide-compartments'").fetchone()
    assert row["description"] == "A test description."
    assert (mini.content_dir / row["image_path"]).is_file()
    assert (mini.content_dir / "docs" / "er-130.pdf").is_file()
    assert s1.review_path.is_file() and "compressor-guide-compartments" in s1.review_path.read_text()
    calls = len(client.messages.calls)

    s2 = ingest_set(mini, "mini", force=False, client=client, embedder=HashEmbedder())
    assert s2.docs_processed == 0 and s2.docs_skipped == 2

    s3 = ingest_set(mini, "mini", force=True, client=client, embedder=HashEmbedder())
    assert s3.docs_processed == 2
    assert len(client.messages.calls) == calls  # vision cache hit for every image
    assert s3.chunks == s1.chunks              # re-ingest replaces, never duplicates


def test_skip_vision(mini):
    s = ingest_set(mini, "mini", force=False, client=None, embedder=HashEmbedder())
    assert s.vision_cost == 0 and s.figures >= 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_embeddings.py tests/test_ingest_cli.py -v -m "not integration"`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.embeddings'`.

- [ ] **Step 3: Implement embeddings**

`backend/embeddings.py`:
```python
"""Local sentence embeddings (fastembed / ONNX, no torch)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    dim: int

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def _normalize_rows(m: np.ndarray) -> np.ndarray:
    return m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-9, None)


class FastEmbedder:
    dim = 384

    def __init__(self, cache_dir: Path | None = None, model_name: str = MODEL_NAME):
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir) if cache_dir else None)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        return _normalize_rows(np.asarray(list(self._model.passage_embed(texts)), dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize_rows(np.asarray(list(self._model.query_embed(text)), dtype=np.float32))[0]


@lru_cache(maxsize=1)
def get_embedder(cache_dir: Path | None = None) -> FastEmbedder:
    return FastEmbedder(cache_dir)


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float32).tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)
```

- [ ] **Step 4: Implement indexing and the review page**

`backend/ingest/index.py`:
```python
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


def doc_is_current(conn, doc_id: str, sha: str) -> bool:
    row = conn.execute("SELECT sha256 FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return bool(row) and row["sha256"] == sha


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
```

`backend/ingest/review.py`:
```python
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
```

- [ ] **Step 5: Implement the CLI**

`backend/ingest/__main__.py`:
```python
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
from backend.ingest.pdfutil import sha256_file
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
    init_content_schema(conn)
    upsert_set(conn, manifest)
    summary = IngestSummary()
    for spec in manifest.documents:
        path = manifest.path(spec)
        sha = sha256_file(path)
        if not force and doc_is_current(conn, spec.id, sha):
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
        replace_document(conn, doc_id=spec.id, file_name=path.name, role=spec.role, title=title, sha256=sha,
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
    conn.close()
    return summary


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
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_embeddings.py tests/test_ingest_cli.py -v`
Expected: 4 passed. The integration test downloads the model once into the tmp dir. If the network is unavailable, run with `-m "not integration"` and note it.

- [ ] **Step 7: Commit**

```bash
git add backend/embeddings.py backend/ingest/index.py backend/ingest/review.py backend/ingest/__main__.py tests/test_embeddings.py tests/test_ingest_cli.py
git commit -m "feat(ingest): embeddings, content indexing, review gallery, and ingest CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Run ingestion on Ariel KBE, review crops, fix overrides, commit content

This task is operational: the implementer runs the pipeline, inspects the output and commits the generated `content/`. Requires `ANTHROPIC_API_KEY` in `.env` (expected vision cost under $0.50).

**Files:**
- Modify: `corpus/ariel-kbe/overrides.yaml`
- Create (generated): `content/content.db`, `content/figures/**`, `content/cad/*`, `content/docs/*`, `content/review/ariel-kbe.html`

- [ ] **Step 1: Run the first ingestion**

```bash
cd /Users/emeraldhenry/AI_maintenance_Application
grep -q ANTHROPIC_API_KEY .env || echo "ANTHROPIC_API_KEY missing from .env — ask the user to add it"
uv run python -m backend.ingest ariel-kbe
```

Expected (approximately):
```
documents: 9 processed, 0 unchanged
chunks: 250-450  figures: ~95  cad sheets: 2
flagged: {...}
vision cost: $0.1x
review: content/review/ariel-kbe.html
PROBLEM: kbe-manual/crosshead-clearance-verification-correction-flowchart (p.123): ...
PROBLEM: kbe-manual/piston-rod-runout-verification-correction-flowchart (p.124): ...
```

If the API rejects a request, read the error. A model-ID error means checking `VISION_MODEL`. Do not proceed with `--skip-vision` unless the user agrees.

- [ ] **Step 2: Compute boxes for the two flowcharts**

```bash
uv run python - <<'EOF'
import pymupdf
d = pymupdf.open("corpus/ariel-kbe/docs/Ariel-KBE-Manual.pdf")
for n in (123, 124):
    p = d[n - 1]
    cap = next(b for b in p.get_text("blocks") if b[4].startswith("Figure:"))
    r = pymupdf.Rect()
    for dr in p.get_drawings():
        if dr["rect"].y0 > cap[3] + 40 and dr["rect"].y1 < 750 and dr["rect"].width < 0.95 * p.rect.width:
            r |= dr["rect"]
    print(n, [round(cap[0]), round(cap[1]), round(max(r.x1, cap[2])), round(r.y1)])
EOF
```

Render each candidate box and look at it before accepting it:
```bash
uv run python -c "
import pymupdf; d=pymupdf.open('corpus/ariel-kbe/docs/Ariel-KBE-Manual.pdf')
d[122].get_pixmap(dpi=60, clip=pymupdf.Rect(X0,Y0,X1,Y1)).save('/tmp/onesi-p123.png')"
```
(Substitute the printed numbers and view the PNG. Adjust if the flowchart is clipped.)

- [ ] **Step 3: Review every flagged crop in the gallery**

Open `content/review/ariel-kbe.html` (Playwright screenshot or a browser). For each flagged card (`incomplete`, `has_body_text`, `small`, `uncaptioned`, `vision_failed`), view the PNG and decide:
- crop is fine and the flag is a false positive: do nothing;
- crop includes body text or misses part of the drawing: add a corrected `bbox` (PDF points, from the page geometry) to `overrides.yaml`;
- image is not a useful figure (logo, table rule, decorative graphic): add `exclude: true`.

Also spot-check these known-tricky crops even if unflagged: p.33 (graph near the footer), p.137 (may include the small table below), p.143 (top of drawing).

Write the fixes, for example:
```yaml
figures:
  kbe-manual/crosshead-clearance-verification-correction-flowchart:
    bbox: [<from step 2>]
  kbe-manual/piston-rod-runout-verification-correction-flowchart:
    bbox: [<from step 2>]
```

- [ ] **Step 4: Re-run and confirm a clean result**

Run: `uv run python -m backend.ingest ariel-kbe --force`
Expected: no `PROBLEM:` lines. Vision cost is ~$0 for unchanged images (cache) and cents for re-cropped ones. Re-check the overridden crops in the gallery.

- [ ] **Step 5: Sanity-query the database**

```bash
uv run python - <<'EOF'
from backend.content_db import connect_content
c = connect_content("content/content.db", readonly=True)
for q in ["SELECT COUNT(*) FROM chunks", "SELECT COUNT(*) FROM assets WHERE asset_type='figure'",
          "SELECT COUNT(*) FROM assets WHERE asset_type='cad'", "SELECT COUNT(*) FROM assets WHERE description=''"]:
    print(q, c.execute(q).fetchone()[0])
print(c.execute("SELECT id, kind, description FROM assets WHERE id='kbe-manual/crosshead-installation'").fetchone()[:])
EOF
```
Expected: description count `0`, and the crosshead figure has a sensible description.

- [ ] **Step 6: Report to the user and commit**

Write a short summary for the user: counts, which figures were flagged, which overrides were added and why, and the vision cost. Then:

```bash
du -sh content
git add corpus/ariel-kbe/overrides.yaml content
git commit -m "content: ingest Ariel KBE manual set (figures, CAD, index)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
## Phase 2 — Retrieval

### Task 10: Hybrid retriever, figure candidates, fuzzy lookup, golden set

**Files:**
- Create: `backend/retrieval/__init__.py`, `backend/retrieval/search.py`, `tests/content_fixture.py`, `tests/golden/retrieval.yaml`
- Test: `tests/test_retrieval.py`, `tests/test_retrieval_golden.py`

**Interfaces:**
- Consumes: `content_db.connect_content`, `embeddings.Embedder/from_blob`, `ingest.index.replace_document/upsert_set/link_documents`, `ingest.models`, `pdfutil.normalize_caption`.
- Produces:
  - `ChunkHit(id, doc_id, doc_title, section_path, page_start, page_end, text, figure_refs: tuple[str, ...], has_warning: bool, score: float)`.
  - `AssetInfo(id, doc_id, asset_type, caption, kind, description, page, section_path, image_url, thumb_url, pdf_url, image_sha)` with `.public() -> dict` (everything except `image_sha`).
  - `fts_query(text) -> str`, `rrf(*rankings, k=60) -> list[tuple[str, float]]`.
  - `Retriever(conn, embedder)` and `Retriever.open(content_dir, embedder)`, with methods:
    - `.sets() -> list[dict]` (`id, name, equipment`);
    - `.get_set(set_id) -> dict | None` (adds `always_include: list[dict]`);
    - `.search_chunks(set_id, query, k=8) -> list[ChunkHit]`;
    - `.figure_candidates(set_id, query, chunks, k=6) -> list[AssetInfo]`;
    - `.section_chunks(set_id, doc_id, section_prefix, limit=6) -> list[ChunkHit]`;
    - `.get_assets(ids) -> list[AssetInfo]` (input order, unknown IDs skipped);
    - `.list_assets(set_id, asset_type=None) -> list[AssetInfo]`;
    - `.resolve_figure(set_id, name) -> str | None`;
    - `.doc_exists(doc_id) -> bool`.
  - `tests/content_fixture.py`: `build_tiny_content(content_dir: Path) -> None` builds set `demo` (docs `man`, `cad1`) used by later API tests.

- [ ] **Step 1: Write the tiny content fixture**

`tests/content_fixture.py`:
```python
"""A tiny, deterministic content.db (set "demo") for API and retrieval unit tests."""
from __future__ import annotations

from pathlib import Path

from backend.content_db import connect_content, init_content_schema
from backend.ingest.index import link_documents, replace_document, upsert_set
from backend.ingest.manifest import AlwaysInclude, Manifest
from backend.ingest.models import Asset, Chunk
from tests.fakes import HashEmbedder


def _asset(id_, page, caption, sha, asset_type="figure", section="S"):
    doc = id_.split("/")[0]
    rel = f"cad/{doc}.png" if asset_type == "cad" else f"figures/{id_}.png"
    return Asset(id=id_, doc_id=doc, asset_type=asset_type, caption=caption, source_caption=caption, page=page,
                 bbox=(0, 0, 100, 100), section_path=section, kind="cad" if asset_type == "cad" else "line_drawing",
                 description=f"Drawing of {caption.lower()}", image_path=rel,
                 thumb_path=rel.replace(".png", ".thumb.png"), width=100, height=100, image_sha=sha)


def _chunk(id_, section, page, text, refs, warn=False):
    return Chunk(id=id_, doc_id=id_.split(":")[0], section_path=section, page_start=page, page_end=page, text=text,
                 token_count=len(text.split()), has_warning=warn, figure_refs=refs)


def build_tiny_content(content_dir: Path) -> None:
    content_dir = Path(content_dir)
    conn = connect_content(content_dir / "content.db")
    init_content_schema(conn)
    upsert_set(conn, Manifest(id="demo", set_dir=Path("."), equipment={"name": "Demo Compressor", "manufacturer": "Acme"},
                              documents=(), always_include_sections=(AlwaysInclude("man", "Section 1 - Tools"),)))
    emb = HashEmbedder()
    man_chunks = [
        _chunk("man:0001", "Section 4 - Part Replacement › Crosshead Installation", 88,
               "Lay a 3/16 inch thick installation plate in the crosshead guide. Mount the crosshead installation "
               "tool (see Figure: Crosshead Alignment Tool). Rotate crosshead 90 degrees.",
               ["man/crosshead-installation", "man/crosshead-alignment-tool"]),
        _chunk("man:0002", "Section 1 - Tools › Recommended Tools", 12,
               "Recommended tools: torque wrench, feeler gauge, dial indicator, crosshead alignment tool.",
               ["man/optional-tools"]),
        _chunk("man:0003", "Section 3 - Maintenance › Daily Maintenance", 27,
               "Check frame oil level daily. CAUTION: surfaces are hot after shutdown.", [], warn=True),
        _chunk("man:0004", "Section 2 - Instrumentation › Proximity Switch A-18255", 17,
               "Wire the A-18255 proximity switch yellow leads to the control panel and green to ground.",
               ["man/a-18255-wiring"]),
    ]
    man_assets = [
        _asset("man/crosshead-installation", 88, "Crosshead Installation", "s1"),
        _asset("man/crosshead-alignment-tool", 89, "Crosshead Alignment Tool", "s2"),
        _asset("man/optional-tools", 10, "Optional Furnished Tools for KBE Compressors", "s3"),
        _asset("man/a-18255-wiring", 18, "A-18255 Proximity Switch Wiring", "s4"),
        _asset("man/crosshead-installation-copy", 90, "Crosshead Installation Copy", "s1"),  # same image
    ]
    replace_document(conn, doc_id="man", file_name="man.pdf", role="manual", title="Demo Manual", sha256="x",
                     page_count=154, pdf_path="docs/man.pdf", chunks=man_chunks, assets=man_assets, embedder=emb)
    cad = _asset("cad1/sheet-1", 1, "OL0782 Encoder Shaft Attachment Details", "s5", asset_type="cad",
                 section="Drawing CAD1")
    replace_document(conn, doc_id="cad1", file_name="cad1.pdf", role="cad", title="OL0782", sha256="y",
                     page_count=1, pdf_path="docs/cad1.pdf",
                     chunks=[_chunk("cad1:0001", "Drawing CAD1", 1,
                                    "OL0782 Encoder Shaft Attachment Details. Drill depth table for KBE frames.",
                                    ["cad1/sheet-1"])],
                     assets=[cad], embedder=emb)
    link_documents(conn, "demo", ["man", "cad1"])
    conn.close()
    for a in man_assets + [cad]:
        for rel in (a.image_path, a.thumb_path):
            (content_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            (content_dir / rel).write_bytes(b"\x89PNG\r\n\x1a\n")
    (content_dir / "docs").mkdir(exist_ok=True)
    (content_dir / "docs" / "man.pdf").write_bytes(b"%PDF-1.4 demo")
```

- [ ] **Step 2: Write the failing unit tests**

`tests/test_retrieval.py`:
```python
import pytest

from backend.retrieval.search import Retriever, fts_query, rrf
from tests.content_fixture import build_tiny_content
from tests.fakes import HashEmbedder


@pytest.fixture(scope="module")
def retriever(tmp_path_factory):
    d = tmp_path_factory.mktemp("content")
    build_tiny_content(d)
    return Retriever.open(d, HashEmbedder())


def test_fts_query_escapes_and_drops_stopwords():
    assert fts_query("How do I wire the A-18255?") == '"wire" OR "18255"'
    assert fts_query("the of and") == ""
    assert fts_query('crosshead "torque" crosshead') == '"crosshead" OR "torque"'


def test_rrf_fuses_rankings():
    fused = rrf(["a", "b"], ["a", "c"])
    assert [i for i, _ in fused] == ["a", "b", "c"] or [i for i, _ in fused] == ["a", "c", "b"]
    assert fused[0] == ("a", 2 / 61)
    assert dict(fused)["b"] == dict(fused)["c"] == 1 / 62


def test_sets_and_get_set(retriever):
    assert retriever.sets() == [{"id": "demo", "name": "Demo Compressor",
                                 "equipment": {"name": "Demo Compressor", "manufacturer": "Acme"}}]
    s = retriever.get_set("demo")
    assert s["always_include"] == [{"doc": "man", "section_prefix": "Section 1 - Tools"}]
    assert retriever.get_set("nope") is None


def test_search_chunks_lexical_and_part_numbers(retriever):
    hits = retriever.search_chunks("demo", "crosshead installation plate")
    assert hits[0].id == "man:0001"
    assert hits[0].doc_title == "Demo Manual"
    assert hits[0].figure_refs == ("man/crosshead-installation", "man/crosshead-alignment-tool")
    assert retriever.search_chunks("demo", "A-18255 wiring")[0].id == "man:0004"
    assert len(retriever.search_chunks("demo", "crosshead", k=2)) == 2


def test_figure_candidates_order_dedupe_and_cad_rule(retriever):
    chunks = retriever.search_chunks("demo", "crosshead installation plate", k=1)
    cands = retriever.figure_candidates("demo", "crosshead installation plate", chunks)
    ids = [c.id for c in cands]
    assert ids[:2] == ["man/crosshead-installation", "man/crosshead-alignment-tool"]
    assert "man/crosshead-installation-copy" not in ids  # same image hash as the original
    assert "cad1/sheet-1" not in ids                     # CAD only via its retrieved sheet chunk
    assert len(ids) <= 6
    assert cands[0].image_url == "/content/figures/man/crosshead-installation.png"
    assert cands[0].pdf_url == "/content-docs/man.pdf#page=88"


def test_cad_candidate_when_sheet_chunk_retrieved(retriever):
    chunks = retriever.search_chunks("demo", "encoder shaft drill depth")
    assert chunks[0].id == "cad1:0001"
    ids = [c.id for c in retriever.figure_candidates("demo", "encoder shaft drill depth", chunks)]
    assert ids[0] == "cad1/sheet-1"


def test_section_chunks_lists_and_lookup(retriever):
    assert [c.id for c in retriever.section_chunks("demo", "man", "Section 1 - Tools")] == ["man:0002"]
    assert [a.id for a in retriever.list_assets("demo", "cad")] == ["cad1/sheet-1"]
    assert [a.id for a in retriever.get_assets(["man/a-18255-wiring", "missing", "cad1/sheet-1"])] == \
        ["man/a-18255-wiring", "cad1/sheet-1"]
    assert "image_sha" not in retriever.get_assets(["cad1/sheet-1"])[0].public()
    assert retriever.doc_exists("man") and not retriever.doc_exists("zzz")


def test_resolve_figure_fuzzy(retriever):
    assert retriever.resolve_figure("demo", "man/crosshead-alignment-tool") == "man/crosshead-alignment-tool"
    assert retriever.resolve_figure("demo", "Crosshed Instalation") == "man/crosshead-installation"
    assert retriever.resolve_figure("demo", "Figure: A18255 proximity switch wiring") == "man/a-18255-wiring"
    assert retriever.resolve_figure("demo", "completely unrelated words here") is None
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_retrieval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.retrieval'`.

- [ ] **Step 4: Implement the retriever**

`backend/retrieval/__init__.py`: empty file.

`backend/retrieval/search.py`:
```python
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
```

- [ ] **Step 5: Run unit tests to verify pass**

Run: `uv run pytest tests/test_retrieval.py -v`
Expected: 8 passed.

- [ ] **Step 6: Write the golden set and integration test against the real content**

`tests/golden/retrieval.yaml`:
```yaml
- {query: "How do I install the crosshead?", section: "Crosshead Installation", figure: "kbe-manual/crosshead-installation"}
- {query: "main bearing cap torque order", figure: "kbe-manual/main-bearing-cap-or-connecting-rod-cap-torque-sequence"}
- {query: "proximity switch wiring colours", figure: "kbe-manual/a-18255-proximity-switch-wiring-connections-for-unit-in-operation"}
- {query: "how to prime the force feed lubricator pump", figure: "kbe-manual/pump-priming-and-adjustment"}
- {query: "check chain elongation", figure: "kbe-manual/measuring-to-check-chain-elongation"}
- {query: "three piece piston ring orientation", figure: "kbe-manual/three-piece-piston-ring-installation-orientation"}
- {query: "connecting rod thrust side clearance measurement", figure: "kbe-manual/typical-connecting-rod-thrust-side-clearance-measurement"}
- {query: "VVCP disassembly", figure: "kbe-manual/vvcp-disassembly-assembly"}
- {query: "oil sampling valve", figure: "kbe-manual/ariel-oil-sampling-valve"}
- {query: "thermostatic valve mixing mode", figure: "kbe-manual/thermostatic-valve-in-mixing-mode"}
- {query: "daily maintenance checklist", section: "Daily Maintenance"}
- {query: "preserve compressor frame for storage with PAG oil", doc: "er-140"}
- {query: "encoder shaft drill depth for KBE", figure: "ol0782/sheet-1"}
- {query: "guide support outline dimensions", figure: "ol0511/sheet-1"}
- {query: "fastener torque accuracy recommendations", section: "Recommendations for Torque Accuracy"}
```

`tests/test_retrieval_golden.py`:
```python
from pathlib import Path

import pytest
import yaml

from tests.conftest import ROOT

CONTENT = ROOT / "content"
pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not (CONTENT / "content.db").is_file(), reason="run Task 9 ingestion first")]


def test_golden_retrieval():
    from backend.embeddings import get_embedder
    from backend.retrieval.search import Retriever
    r = Retriever.open(CONTENT, get_embedder(ROOT / ".cache" / "fastembed"))
    cases = yaml.safe_load((Path(__file__).parent / "golden" / "retrieval.yaml").read_text())
    failures = []
    for case in cases:
        chunks = r.search_chunks("ariel-kbe", case["query"])
        cands = [a.id for a in r.figure_candidates("ariel-kbe", case["query"], chunks)]
        ok = True
        if "section" in case:
            ok &= any(case["section"] in c.section_path for c in chunks)
        if "doc" in case:
            ok &= any(c.doc_id == case["doc"] for c in chunks)
        if "figure" in case:
            ok &= case["figure"] in cands
        if not ok:
            failures.append((case["query"], [c.section_path for c in chunks[:3]], cands))
    for f in failures:
        print("MISS:", f)
    assert len(failures) <= 2, f"{len(failures)} golden misses"
```

- [ ] **Step 7: Run the golden test**

Run: `uv run pytest tests/test_retrieval_golden.py -v -s`
Expected: PASS with at most 2 `MISS:` lines. If more miss, look at whether each miss is an ID mismatch: list real IDs with `SELECT id FROM assets WHERE id LIKE '%<word>%'`. Fix the YAML only when the expected ID was simply misspelled. Otherwise investigate retrieval (FTS query, figure_refs) before relaxing anything.

- [ ] **Step 8: Commit**

```bash
git add backend/retrieval tests/content_fixture.py tests/golden tests/test_retrieval.py tests/test_retrieval_golden.py
git commit -m "feat(retrieval): hybrid FTS+vector search, figure candidates, fuzzy figure lookup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Phase 3 — Access gating

### Task 11: app.db, access requests, codes, sessions, quotas, rate limiter

**Files:**
- Create: `backend/db.py`, `backend/auth/__init__.py`, `backend/auth/ratelimit.py`, `backend/auth/requests.py`, `backend/auth/codes.py`, `backend/auth/sessions.py`, `backend/auth/quota.py`
- Test: `tests/test_auth_core.py`

**Interfaces:**
- Consumes: `config.Settings`.
- Produces:
  - `db`: `utcnow() -> datetime`, `iso(dt) -> str`, `day_key(dt) -> str` (`YYYY-MM-DD`), `connect_app(path) -> sqlite3.Connection` (autocommit, WAL, migrated, `row_factory=sqlite3.Row`), `tx(conn)` context manager (`BEGIN IMMEDIATE` … `COMMIT`/`ROLLBACK`).
  - `auth.ratelimit.RateLimiter(limit, window_seconds, clock=time.monotonic).hit(key) -> bool`.
  - `auth.requests`:
    - `STATUSES`;
    - `normalize_email(e) -> str`, `valid_email(e) -> bool`;
    - `create_request(conn, email, name, company, note, now) -> bool` (True if new);
    - `get_request(conn, email)`;
    - `set_status(conn, email, status, now) -> bool`, `set_daily_limit(conn, email, limit | None)`, `set_email_error(conn, email, err | None)`;
    - `list_requests(conn, statuses, now) -> list[dict]` (row fields + `questions_today`, `questions_total`, `last_active`).
  - `auth.codes`:
    - `ALPHABET`, `CODE_TTL`, `LOCKOUT_ATTEMPTS = 5`, `LOCKOUT_WINDOW`;
    - `generate_code() -> str`, `normalize_code(code) -> str`, `hash_secret(s) -> str`;
    - `issue_code(conn, email, now) -> str`;
    - `RedeemResult` (`OK`, `INVALID`, `LOCKED`), `redeem_code(conn, email, code, ip, now) -> RedeemResult`.
  - `auth.sessions`: `SESSION_TTL`, `create_session(conn, email, now) -> str`, `session_email(conn, token, now) -> str | None`, `delete_session(conn, token)`, `delete_sessions(conn, email)`.
  - `auth.quota`:
    - `QuotaStatus(allowed: bool, remaining: int | None, limit: int | None, reason: str | None, resets_at: str)`;
    - `user_limit(conn, email, settings) -> int`;
    - `status(conn, email, settings, now, is_admin=False) -> QuotaStatus`;
    - `try_reserve(conn, email, settings, now, is_admin=False) -> QuotaStatus`;
    - `release(conn, email, now)`, `reset_today(conn, email, now)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_auth_core.py`:
```python
import threading
from datetime import datetime, timedelta, timezone

import pytest

from backend.auth import codes, quota, requests as reqs, sessions
from backend.auth.codes import RedeemResult
from backend.auth.ratelimit import RateLimiter
from backend.config import Settings
from backend.db import connect_app

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
EMAIL = "tech@example.com"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "app.db"


@pytest.fixture
def conn(db):
    return connect_app(db)


@pytest.fixture
def settings():
    return Settings(_env_file=None, daily_question_limit=3, global_daily_limit=5)


def approve(conn, email=EMAIL):
    reqs.create_request(conn, email, "Tess", "Acme", "", NOW)
    reqs.set_status(conn, email, "approved", NOW)


def test_migrations_idempotent(db):
    connect_app(db).close()
    c = connect_app(db)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1


def test_requests_normalise_and_dedupe(conn):
    assert reqs.normalize_email("  Tech@Example.COM ") == EMAIL
    assert reqs.valid_email(EMAIL) and not reqs.valid_email("nope@") and not reqs.valid_email("a b@c.d")
    assert reqs.create_request(conn, EMAIL, "Tess", "Acme", "pumps", NOW) is True
    assert reqs.create_request(conn, EMAIL, "Tess", "Acme", "again", NOW) is False
    assert reqs.get_request(conn, EMAIL)["status"] == "pending"
    rows = reqs.list_requests(conn, ("pending",), NOW)
    assert rows[0]["email"] == EMAIL and rows[0]["questions_today"] == 0


def test_code_format_and_normalisation():
    code = codes.generate_code()
    assert len(code) == 9 and code[4] == "-"
    assert set(code.replace("-", "")) <= set(codes.ALPHABET)
    assert codes.normalize_code(" k7qm 4xpd ") == "K7QM-4XPD"
    assert codes.normalize_code("K7QM4XPD") == "K7QM-4XPD"


def test_redeem_accepts_loose_code_format(conn):
    approve(conn)
    code = codes.issue_code(conn, EMAIL, NOW)
    loose = code.replace("-", " ").lower()
    assert codes.redeem_code(conn, " TECH@example.com", loose, "1.1.1.1", NOW) is RedeemResult.OK


def test_redeem_rules(conn):
    approve(conn)
    code = codes.issue_code(conn, EMAIL, NOW)
    assert codes.redeem_code(conn, EMAIL, "AAAA-AAAA", "ip", NOW) is RedeemResult.INVALID
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW + timedelta(days=8)) is RedeemResult.INVALID  # expired
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW) is RedeemResult.OK
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW) is RedeemResult.INVALID  # single use


def test_redeem_requires_approval(conn):
    reqs.create_request(conn, EMAIL, "Tess", "Acme", "", NOW)
    code = codes.issue_code(conn, EMAIL, NOW)
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW) is RedeemResult.INVALID


def test_lockout_after_five_failures(conn):
    approve(conn)
    code = codes.issue_code(conn, EMAIL, NOW)
    for i in range(5):
        assert codes.redeem_code(conn, EMAIL, "BBBB-BBBB", "ip", NOW + timedelta(minutes=i)) is RedeemResult.INVALID
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW + timedelta(minutes=6)) is RedeemResult.LOCKED
    assert codes.redeem_code(conn, EMAIL, code, "ip", NOW + timedelta(minutes=70)) is RedeemResult.OK


def test_sessions(conn):
    approve(conn)
    token = sessions.create_session(conn, EMAIL, NOW)
    assert sessions.session_email(conn, token, NOW) == EMAIL
    assert sessions.session_email(conn, token, NOW + timedelta(days=31)) is None
    assert sessions.session_email(conn, "forged", NOW) is None
    reqs.set_status(conn, EMAIL, "revoked", NOW)
    assert sessions.session_email(conn, token, NOW) is None
    reqs.set_status(conn, EMAIL, "approved", NOW)
    sessions.delete_sessions(conn, EMAIL)
    assert sessions.session_email(conn, token, NOW) is None


def test_quota_user_limit_override_release_and_admin(conn, settings):
    approve(conn)
    for left in (2, 1, 0):
        st = quota.try_reserve(conn, EMAIL, settings, NOW)
        assert st.allowed and st.remaining == left and st.limit == 3
    st = quota.try_reserve(conn, EMAIL, settings, NOW)
    assert not st.allowed and st.reason == "user" and st.resets_at == "2026-10-02T00:00:00+00:00"
    quota.release(conn, EMAIL, NOW)
    assert quota.status(conn, EMAIL, settings, NOW).remaining == 1
    reqs.set_daily_limit(conn, EMAIL, 10)
    assert quota.status(conn, EMAIL, settings, NOW).remaining == 8
    quota.reset_today(conn, EMAIL, NOW)
    assert quota.status(conn, EMAIL, settings, NOW).remaining == 10
    assert quota.try_reserve(conn, "admin", settings, NOW, is_admin=True).allowed
    assert quota.try_reserve(conn, EMAIL, settings, NOW + timedelta(days=1)).remaining == 9  # new UTC day


def test_global_limit(conn, settings):
    for i in range(3):
        approve(conn, f"u{i}@x.com")
    used = 0
    for i in range(3):
        for _ in range(2):
            if quota.try_reserve(conn, f"u{i}@x.com", settings, NOW).allowed:
                used += 1
    assert used == 5
    st = quota.try_reserve(conn, "u2@x.com", settings, NOW)
    assert not st.allowed and st.reason == "global"


def test_reserve_is_atomic_at_limit(db, settings):
    c = connect_app(db)
    approve(c)
    quota.try_reserve(c, EMAIL, settings, NOW)
    quota.try_reserve(c, EMAIL, settings, NOW)  # one question left
    barrier = threading.Barrier(2)
    results = []

    def worker():
        conn = connect_app(db)
        barrier.wait()
        results.append(quota.try_reserve(conn, EMAIL, settings, NOW).allowed)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [False, True]


def test_rate_limiter_window():
    t = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: t[0])
    assert rl.hit("k") and rl.hit("k") and not rl.hit("k")
    assert rl.hit("other")
    t[0] = 61
    assert rl.hit("k")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auth_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.auth'`.

- [ ] **Step 3: Implement `backend/db.py`**

```python
"""app.db: runtime user data on the persistent volume."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS = [
    """
    CREATE TABLE access_requests (
      id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, company TEXT NOT NULL,
      note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', daily_limit INTEGER,
      email_error TEXT, created_at TEXT NOT NULL, decided_at TEXT);
    CREATE TABLE access_codes (
      id INTEGER PRIMARY KEY, email TEXT NOT NULL, code_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
      used_at TEXT, created_at TEXT NOT NULL);
    CREATE INDEX access_codes_email ON access_codes (email);
    CREATE TABLE sessions (
      token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL);
    CREATE TABLE redeem_attempts (
      id INTEGER PRIMARY KEY, email TEXT NOT NULL, ip TEXT NOT NULL, at TEXT NOT NULL, success INTEGER NOT NULL);
    CREATE TABLE usage_daily (
      email TEXT NOT NULL, day TEXT NOT NULL, questions INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (email, day));
    CREATE TABLE conversations (
      id TEXT PRIMARY KEY, email TEXT NOT NULL, set_id TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE messages (
      id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
      query TEXT, assets_json TEXT, tools_json TEXT, created_at TEXT NOT NULL);
    CREATE INDEX messages_conv ON messages (conversation_id, id);
    CREATE TABLE llm_calls (
      id INTEGER PRIMARY KEY, conversation_id TEXT, email TEXT, call_type TEXT NOT NULL, model TEXT NOT NULL,
      input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cache_read_tokens INTEGER NOT NULL,
      web_searches INTEGER NOT NULL, cost_usd REAL NOT NULL, created_at TEXT NOT NULL);
    """,
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def day_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).date().isoformat()


def connect_app(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {sql} PRAGMA user_version = {i}; COMMIT;")


@contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
```

- [ ] **Step 4: Implement the auth modules**

`backend/auth/__init__.py`: empty file.

`backend/auth/ratelimit.py`:
```python
"""In-memory sliding-window rate limiter (single-instance deployment)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window_seconds, clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True
```

`backend/auth/requests.py`:
```python
"""Access requests: a visitor asks for access; the admin approves, rejects, revokes or restores."""
from __future__ import annotations

import re
from datetime import datetime

from backend.db import day_key, iso

STATUSES = ("pending", "approved", "rejected", "revoked")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email)) and len(email) <= 254


def create_request(conn, email: str, name: str, company: str, note: str, now: datetime) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO access_requests (email, name, company, note, created_at)"
                       " VALUES (?, ?, ?, ?, ?)", (normalize_email(email), name.strip(), company.strip(),
                                                    note.strip(), iso(now)))
    return cur.rowcount == 1


def get_request(conn, email: str):
    return conn.execute("SELECT * FROM access_requests WHERE email = ?", (normalize_email(email),)).fetchone()


def set_status(conn, email: str, status: str, now: datetime) -> bool:
    assert status in STATUSES
    cur = conn.execute("UPDATE access_requests SET status = ?, decided_at = ? WHERE email = ?",
                       (status, iso(now), normalize_email(email)))
    return cur.rowcount == 1


def set_daily_limit(conn, email: str, limit: int | None) -> None:
    conn.execute("UPDATE access_requests SET daily_limit = ? WHERE email = ?", (limit, normalize_email(email)))


def set_email_error(conn, email: str, error: str | None) -> None:
    conn.execute("UPDATE access_requests SET email_error = ? WHERE email = ?", (error, normalize_email(email)))


def list_requests(conn, statuses: tuple[str, ...], now: datetime) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"""SELECT r.*, COALESCE(u.questions, 0) AS questions_today,
                   (SELECT COALESCE(SUM(questions), 0) FROM usage_daily WHERE email = r.email) AS questions_total,
                   (SELECT MAX(last_seen_at) FROM sessions WHERE email = r.email) AS last_active
            FROM access_requests r LEFT JOIN usage_daily u ON u.email = r.email AND u.day = ?
            WHERE r.status IN ({marks}) ORDER BY r.created_at DESC""", (day_key(now), *statuses)).fetchall()
    return [dict(r) for r in rows]
```

`backend/auth/codes.py`:
```python
"""One-time access codes: issue, redeem, lock out brute force."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from enum import Enum

from backend.auth.requests import get_request, normalize_email
from backend.db import iso, tx

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_TTL = timedelta(days=7)
LOCKOUT_ATTEMPTS = 5
LOCKOUT_WINDOW = timedelta(hours=1)


class RedeemResult(Enum):
    OK = "ok"
    INVALID = "invalid"
    LOCKED = "locked"


def generate_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    c = re.sub(r"[^A-Za-z0-9]", "", code).upper()
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c


def hash_secret(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def issue_code(conn, email: str, now: datetime) -> str:
    code = generate_code()
    conn.execute("INSERT INTO access_codes (email, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                 (normalize_email(email), hash_secret(code), iso(now + CODE_TTL), iso(now)))
    return code


def redeem_code(conn, email: str, code: str, ip: str, now: datetime) -> RedeemResult:
    email = normalize_email(email)
    fails = conn.execute("SELECT COUNT(*) FROM redeem_attempts WHERE email = ? AND success = 0 AND at > ?",
                         (email, iso(now - LOCKOUT_WINDOW))).fetchone()[0]
    if fails >= LOCKOUT_ATTEMPTS:
        return RedeemResult.LOCKED
    req = get_request(conn, email)
    row = None
    if req is not None and req["status"] == "approved":
        row = conn.execute("SELECT id FROM access_codes WHERE email = ? AND code_hash = ? AND used_at IS NULL"
                           " AND expires_at > ?", (email, hash_secret(normalize_code(code)), iso(now))).fetchone()
    with tx(conn):
        if row is not None:
            conn.execute("UPDATE access_codes SET used_at = ? WHERE id = ?", (iso(now), row["id"]))
        conn.execute("INSERT INTO redeem_attempts (email, ip, at, success) VALUES (?, ?, ?, ?)",
                     (email, ip, iso(now), int(row is not None)))
    return RedeemResult.OK if row is not None else RedeemResult.INVALID
```

`backend/auth/sessions.py`:
```python
"""Opaque session tokens (stored hashed) for approved users."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from backend.auth.codes import hash_secret
from backend.auth.requests import normalize_email
from backend.db import iso

SESSION_TTL = timedelta(days=30)


def create_session(conn, email: str, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token_hash, email, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                 (hash_secret(token), normalize_email(email), iso(now), iso(now + SESSION_TTL), iso(now)))
    return token


def session_email(conn, token: str, now: datetime) -> str | None:
    th = hash_secret(token)
    row = conn.execute("SELECT s.email FROM sessions s JOIN access_requests r ON r.email = s.email"
                       " WHERE s.token_hash = ? AND s.expires_at > ? AND r.status = 'approved'",
                       (th, iso(now))).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (iso(now), th))
    return row["email"]


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_secret(token),))


def delete_sessions(conn, email: str) -> None:
    conn.execute("DELETE FROM sessions WHERE email = ?", (normalize_email(email),))
```

`backend/auth/quota.py`:
```python
"""Daily question quotas, reserved atomically before LLM calls and released on failure."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.config import Settings
from backend.db import day_key, tx


@dataclass(frozen=True)
class QuotaStatus:
    allowed: bool
    remaining: int | None
    limit: int | None
    reason: str | None  # "user" | "global" | None
    resets_at: str


def _resets_at(now: datetime) -> str:
    tomorrow = (now.astimezone(timezone.utc) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc).isoformat()


def user_limit(conn, email: str, settings: Settings) -> int:
    row = conn.execute("SELECT daily_limit FROM access_requests WHERE email = ?", (email,)).fetchone()
    return row["daily_limit"] if row and row["daily_limit"] is not None else settings.daily_question_limit


def _used(conn, email: str, day: str) -> int:
    row = conn.execute("SELECT questions FROM usage_daily WHERE email = ? AND day = ?", (email, day)).fetchone()
    return row["questions"] if row else 0


def status(conn, email: str, settings: Settings, now: datetime, is_admin: bool = False) -> QuotaStatus:
    if is_admin:
        return QuotaStatus(True, None, None, None, _resets_at(now))
    limit = user_limit(conn, email, settings)
    remaining = max(limit - _used(conn, email, day_key(now)), 0)
    return QuotaStatus(remaining > 0, remaining, limit, None if remaining else "user", _resets_at(now))


def try_reserve(conn, email: str, settings: Settings, now: datetime, is_admin: bool = False) -> QuotaStatus:
    resets = _resets_at(now)
    day = day_key(now)
    with tx(conn):
        global_used = conn.execute("SELECT COALESCE(SUM(questions), 0) FROM usage_daily WHERE day = ?",
                                   (day,)).fetchone()[0]
        if not is_admin:
            if global_used >= settings.global_daily_limit:
                return QuotaStatus(False, 0, None, "global", resets)
            limit = user_limit(conn, email, settings)
            used = _used(conn, email, day)
            if used >= limit:
                return QuotaStatus(False, 0, limit, "user", resets)
        conn.execute("INSERT INTO usage_daily (email, day, questions) VALUES (?, ?, 1)"
                     " ON CONFLICT (email, day) DO UPDATE SET questions = questions + 1", (email, day))
    if is_admin:
        return QuotaStatus(True, None, None, None, resets)
    return QuotaStatus(True, limit - used - 1, limit, None, resets)


def release(conn, email: str, now: datetime) -> None:
    conn.execute("UPDATE usage_daily SET questions = MAX(questions - 1, 0) WHERE email = ? AND day = ?",
                 (email, day_key(now)))


def reset_today(conn, email: str, now: datetime) -> None:
    conn.execute("DELETE FROM usage_daily WHERE email = ? AND day = ?", (email, day_key(now)))
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_auth_core.py -v`
Expected: 13 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/db.py backend/auth tests/test_auth_core.py
git commit -m "feat(auth): app.db, access requests, one-time codes, sessions, atomic quotas

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Mailer, app factory, and the visitor access API

**Files:**
- Create: `backend/auth/email.py`, `backend/api/__init__.py`, `backend/api/deps.py`, `backend/api/app.py`, `backend/api/access.py`
- Modify: `tests/conftest.py` (add fixtures)
- Test: `tests/test_email.py`, `tests/test_api_access.py`

**Interfaces:**
- Consumes: Task 11 modules, `config.Settings`, `retrieval.search.Retriever`, `embeddings.get_embedder`.
- Produces:
  - `auth.email`: `SentEmail(to, subject, html, text)`, `Mailer(settings, http=None)` with `.send(to, subject, html, text) -> bool` and `.outbox` (deque of last 50), `admin_notification(settings, email, name, company, note) -> tuple[str, str, str]`, `access_code_email(settings, email, code) -> tuple[str, str, str]`.
  - `api.deps`:
    - `SESSION_COOKIE = "onesi_session"`, `ADMIN_COOKIE = "onesi_admin"`, `ADMIN_MAX_AGE = 43200`;
    - `Viewer(email: str, is_admin: bool)`;
    - `settings_of(request) -> Settings`, `app_db(request)` (generator dependency), `client_ip(request) -> str`;
    - `is_admin(request) -> bool`, `current_viewer(request, conn) -> Viewer | None`, `require_viewer` (dependency → Viewer or 401), `require_admin` (dependency → 401);
    - `get_retriever(request) -> Retriever` (lazy, cached on `app.state`);
    - `set_cookie(response, settings, name, value, max_age)`, `admin_signer(settings) -> TimestampSigner`.
  - `api.app.create_app(settings=None, *, retriever=None, llm=None, mailer=None, embedder=None) -> FastAPI`, with `app.state.settings/mailer/limiters/retriever/llm/embedder`. Run with `uvicorn backend.api.app:create_app --factory`.
  - `api.access.router`: `POST /api/access/request`, `POST /api/access/redeem`, `POST /api/access/new-code`, `POST /api/access/logout`, `GET /api/me`.
  - `tests/conftest.py` fixtures: `settings` (tmp dirs, admin pw `"pw"`, limit 3), `make_client(**create_app_kwargs) -> TestClient`, helpers `approve_in_db(settings, email) -> str` (returns a fresh code) and `login(client, settings, email) -> None`.

- [ ] **Step 1: Add shared test fixtures**

Append to `tests/conftest.py`:
```python
import pytest


@pytest.fixture
def settings(tmp_path):
    from backend.config import Settings
    return Settings(_env_file=None, data_dir=tmp_path / "data", content_dir=tmp_path / "content",
                    web_dir=ROOT / "web", admin_password="pw", admin_email="admin@onesi.test",
                    session_secret="test-secret", daily_question_limit=3, global_daily_limit=100)


@pytest.fixture
def make_client(settings):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.auth.email import Mailer

    def _make(**kwargs):
        kwargs.setdefault("mailer", Mailer(settings))
        return TestClient(create_app(settings, **kwargs))
    return _make


def approve_in_db(settings, email: str) -> str:
    from backend.auth import codes, requests as reqs
    from backend.db import connect_app, utcnow
    conn = connect_app(settings.data_dir / "app.db")
    reqs.create_request(conn, email, "Tess", "Acme", "", utcnow())
    reqs.set_status(conn, email, "approved", utcnow())
    return codes.issue_code(conn, email, utcnow())


def login(client, settings, email: str = "tech@example.com") -> None:
    code = approve_in_db(settings, email)
    r = client.post("/api/access/redeem", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
```

- [ ] **Step 2: Write the failing tests**

`tests/test_email.py`:
```python
import json

import httpx

from backend.auth.email import Mailer, access_code_email, admin_notification
from backend.config import Settings


def test_console_mode_records_outbox(capsys):
    s = Settings(_env_file=None)
    m = Mailer(s)
    assert m.send("a@b.co", "Hi", "<p>x</p>", "x") is True
    assert m.outbox[-1].to == "a@b.co"
    assert "Subject: Hi" in capsys.readouterr().out


def test_resend_payload_and_failure():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "1"})

    s = Settings(_env_file=None, resend_api_key="re_test", email_from="Onesi <hi@onesi.test>")
    m = Mailer(s, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert m.send("a@b.co", "Subj", "<b>h</b>", "t")
    assert seen["auth"] == "Bearer re_test"
    assert seen["body"] == {"from": "Onesi <hi@onesi.test>", "to": ["a@b.co"], "subject": "Subj",
                            "html": "<b>h</b>", "text": "t"}
    bad = Mailer(s, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert bad.send("a@b.co", "Subj", "h", "t") is False


def test_templates():
    s = Settings(_env_file=None, public_base_url="https://onesi.example")
    subj, html, text = access_code_email(s, "t@x.co", "K7QM-4XPD")
    assert "K7QM-4XPD" in text and "https://onesi.example/demo?email=t%40x.co&code=K7QM-4XPD" in html
    subj, html, text = admin_notification(s, "t@x.co", "Tess <b>", "Acme", "pumps")
    assert "t@x.co" in subj and "https://onesi.example/admin" in text and "&lt;b&gt;" in html
```

`tests/test_api_access.py`:
```python
from tests.conftest import approve_in_db, login


def test_healthz_and_site(make_client):
    c = make_client()
    assert c.get("/healthz").json()["app_db"] is True
    assert c.get("/").status_code == 200
    assert "ONESI" in c.get("/index.html").text


def test_request_access_notifies_admin_once(make_client):
    c = make_client()
    body = {"email": "tech@example.com", "name": "Tess", "company": "Acme", "note": "pumps"}
    assert c.post("/api/access/request", json=body).json() == {"ok": True}
    assert c.post("/api/access/request", json=body).json() == {"ok": True}
    outbox = c.app.state.mailer.outbox
    assert len(outbox) == 1 and outbox[0].to == "admin@onesi.test"


def test_honeypot_and_invalid_email(make_client):
    c = make_client()
    r = c.post("/api/access/request", json={"email": "bot@x.com", "name": "b", "company": "b", "website": "spam"})
    assert r.json() == {"ok": True} and len(c.app.state.mailer.outbox) == 0
    r = c.post("/api/access/request", json={"email": "not-an-email", "name": "b", "company": "b"})
    assert r.status_code == 422


def test_request_rate_limited_per_ip(make_client):
    c = make_client()
    codes = [c.post("/api/access/request", json={"email": f"u{i}@x.com", "name": "n", "company": "c"}).status_code
             for i in range(6)]
    assert codes == [200] * 5 + [429]


def test_email_normalized_across_flow(make_client, settings):
    c = make_client()
    c.post("/api/access/request", json={"email": " Tech@Example.com", "name": "Tess", "company": "Acme"})
    code = approve_in_db(settings, "tech@example.com")
    r = c.post("/api/access/redeem", json={"email": "TECH@example.COM ", "code": code})
    assert r.status_code == 200
    assert c.get("/api/me").json()["email"] == "tech@example.com"


def test_redeem_sets_cookie_and_me(make_client, settings):
    c = make_client()
    assert c.get("/api/me").status_code == 401
    login(c, settings)
    me = c.get("/api/me").json()
    assert me == {"email": "tech@example.com", "is_admin": False, "remaining": 3, "limit": 3}
    assert "onesi_session" in c.cookies


def test_bad_code_is_generic(make_client, settings):
    c = make_client()
    approve_in_db(settings, "tech@example.com")
    r = c.post("/api/access/redeem", json={"email": "tech@example.com", "code": "AAAA-AAAA"})
    assert r.status_code == 400 and r.json()["detail"] == "That code is invalid or has expired."
    r = c.post("/api/access/redeem", json={"email": "nobody@example.com", "code": "AAAA-AAAA"})
    assert r.status_code == 400 and r.json()["detail"] == "That code is invalid or has expired."


def test_new_code_only_emails_approved_users(make_client, settings):
    c = make_client()
    c.post("/api/access/request", json={"email": "pending@x.com", "name": "P", "company": "C"})
    before = len(c.app.state.mailer.outbox)
    assert c.post("/api/access/new-code", json={"email": "pending@x.com"}).json() == {"ok": True}
    assert len(c.app.state.mailer.outbox) == before
    approve_in_db(settings, "ok@x.com")
    assert c.post("/api/access/new-code", json={"email": "OK@x.com"}).json() == {"ok": True}
    last = c.app.state.mailer.outbox[-1]
    assert last.to == "ok@x.com" and "Your Onesi access code" in last.subject


def test_logout(make_client, settings):
    c = make_client()
    login(c, settings)
    c.post("/api/access/logout")
    assert c.get("/api/me").status_code == 401


def test_production_misconfig_refuses_start(settings):
    import pytest

    from backend.api.app import create_app
    bad = settings.model_copy(update={"public_base_url": "https://x.example", "anthropic_api_key": ""})
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        create_app(bad)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_email.py tests/test_api_access.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.auth.email'`.

- [ ] **Step 4: Implement the mailer**

`backend/auth/email.py`:
```python
"""Transactional email via Resend; prints to the console when no API key is configured."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from html import escape
from urllib.parse import quote

import httpx

from backend.config import Settings

log = logging.getLogger(__name__)
RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class SentEmail:
    to: str
    subject: str
    html: str
    text: str


class Mailer:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=10)
        self.outbox: deque[SentEmail] = deque(maxlen=50)

    def send(self, to: str, subject: str, html: str, text: str) -> bool:
        self.outbox.append(SentEmail(to, subject, html, text))
        if not self.settings.resend_api_key:
            print(f"\n===== EMAIL (console mode) =====\nTo: {to}\nSubject: {subject}\n\n{text}\n"
                  f"================================\n", flush=True)
            return True
        try:
            r = self.http.post(RESEND_URL, headers={"Authorization": f"Bearer {self.settings.resend_api_key}"},
                               json={"from": self.settings.email_from, "to": [to], "subject": subject,
                                     "html": html, "text": text})
            r.raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.error("email to %s failed: %s", to, e)
            return False


def admin_notification(settings: Settings, email: str, name: str, company: str, note: str) -> tuple[str, str, str]:
    url = f"{settings.public_base_url}/admin"
    subject = f"Onesi access request: {email}"
    text = f"{name} ({company}) <{email}> requested demo access.\n\nNote: {note or '-'}\n\nReview: {url}"
    html = (f"<p><b>{escape(name)}</b> ({escape(company)}) &lt;{escape(email)}&gt; requested demo access.</p>"
            f"<p>Note: {escape(note or '-')}</p><p><a href=\"{url}\">Review requests</a></p>")
    return subject, html, text


def access_code_email(settings: Settings, email: str, code: str) -> tuple[str, str, str]:
    link = f"{settings.public_base_url}/demo?email={quote(email)}&code={code}"
    subject = "Your Onesi access code"
    text = (f"Your Onesi demo access code is {code}\n\nOpen the demo: {link}\n\n"
            f"The code works once and expires in 7 days.")
    html = (f"<p>Your Onesi demo access code is</p><p style=\"font-size:24px;letter-spacing:2px\"><b>{code}</b></p>"
            f"<p><a href=\"{link}\">Open the demo</a></p><p>The code works once and expires in 7 days.</p>")
    return subject, html, text
```

- [ ] **Step 5: Implement dependencies, the app factory and access routes**

`backend/api/__init__.py`: empty file.

`backend/api/deps.py`:
```python
"""FastAPI dependencies: settings, DB connections, viewer identity, retriever."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, TimestampSigner

from backend.auth.sessions import session_email
from backend.config import Settings
from backend.db import connect_app, utcnow
from backend.embeddings import get_embedder
from backend.retrieval.search import Retriever

SESSION_COOKIE = "onesi_session"
ADMIN_COOKIE = "onesi_admin"
ADMIN_MAX_AGE = 12 * 3600
_retriever_lock = threading.Lock()


@dataclass(frozen=True)
class Viewer:
    email: str
    is_admin: bool


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def app_db(request: Request):
    conn = connect_app(settings_of(request).data_dir / "app.db")
    try:
        yield conn
    finally:
        conn.close()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def admin_signer(settings: Settings) -> TimestampSigner:
    return TimestampSigner(settings.session_secret, salt="onesi-admin")


def is_admin(request: Request) -> bool:
    raw = request.cookies.get(ADMIN_COOKIE)
    if not raw:
        return False
    try:
        return admin_signer(settings_of(request)).unsign(raw, max_age=ADMIN_MAX_AGE) == b"admin"
    except BadSignature:
        return False


def current_viewer(request: Request, conn) -> Viewer | None:
    if is_admin(request):
        return Viewer("admin", True)
    token = request.cookies.get(SESSION_COOKIE)
    email = session_email(conn, token, utcnow()) if token else None
    return Viewer(email, False) if email else None


def require_viewer(request: Request, conn=Depends(app_db)) -> Viewer:
    viewer = current_viewer(request, conn)
    if viewer is None:
        raise HTTPException(401, "Sign in to use the demo.")
    return viewer


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "Admin login required.")


def set_cookie(response, settings: Settings, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax",
                        secure=settings.secure_cookies, path="/")


def get_retriever(request: Request) -> Retriever:
    state = request.app.state
    if state.retriever is None:
        with _retriever_lock:
            if state.retriever is None:
                s = state.settings
                state.retriever = Retriever.open(s.content_dir, state.embedder or get_embedder(s.embed_cache_dir))
    return state.retriever
```

`backend/api/app.py`:
```python
"""Application factory: run with `uvicorn backend.api.app:create_app --factory`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.auth.email import Mailer
from backend.auth.ratelimit import RateLimiter
from backend.config import Settings, get_settings
from backend.db import connect_app


@dataclass
class Limiters:
    request_ip: RateLimiter = field(default_factory=lambda: RateLimiter(5, 3600))
    new_code_email: RateLimiter = field(default_factory=lambda: RateLimiter(3, 3600))
    admin_login_ip: RateLimiter = field(default_factory=lambda: RateLimiter(5, 900))


def create_app(settings: Settings | None = None, *, retriever=None, llm=None, mailer=None, embedder=None) -> FastAPI:
    from backend.api import access  # local imports avoid cycles with deps

    settings = settings or get_settings()
    settings.check_production()
    app = FastAPI(title="Onesi", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.mailer = mailer or Mailer(settings)
    app.state.limiters = Limiters()
    app.state.retriever = retriever
    app.state.llm = llm
    app.state.embedder = embedder
    connect_app(Path(settings.data_dir) / "app.db").close()  # run migrations at startup

    app.include_router(access.router)
    for name in ("admin", "assets", "chat"):  # routers added by later tasks
        try:
            module = __import__(f"backend.api.{name}", fromlist=["router"])
        except ModuleNotFoundError:
            continue
        app.include_router(module.router)

    web = Path(settings.web_dir)
    content = Path(settings.content_dir)

    @app.get("/healthz")
    def healthz():
        content_ok = (content / "content.db").is_file()
        try:
            connect_app(Path(settings.data_dir) / "app.db").close()
            db_ok = True
        except Exception:
            db_ok = False
        return {"ok": content_ok and db_ok, "content": content_ok, "app_db": db_ok}

    @app.get("/demo", include_in_schema=False)
    def demo_page():
        return FileResponse(web / "demo.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(web / "admin.html")

    app.mount("/content/figures", StaticFiles(directory=content / "figures", check_dir=False), name="figures")
    app.mount("/content/cad", StaticFiles(directory=content / "cad", check_dir=False), name="cad")
    app.mount("/", StaticFiles(directory=web, html=True), name="site")
    return app
```

The `__import__` loop lets this task ship before `admin.py`, `assets.py` and `chat.py` exist. Task 16 replaces it with plain imports once all routers exist.

`backend/api/access.py`:
```python
"""Visitor-facing access endpoints: request access, redeem a code, get a new code, sign out, who am I."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api.deps import SESSION_COOKIE, Viewer, app_db, client_ip, require_viewer, set_cookie, settings_of
from backend.auth import codes, quota, requests as reqs, sessions
from backend.auth.email import access_code_email, admin_notification
from backend.db import utcnow

router = APIRouter()
INVALID_CODE = "That code is invalid or has expired."


class AccessRequestIn(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=1000)
    website: str = ""  # honeypot: humans never see this field


class RedeemIn(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(max_length=32)


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


@router.post("/api/access/request")
def request_access(body: AccessRequestIn, request: Request, conn=Depends(app_db)):
    if body.website:
        return {"ok": True}
    email = reqs.normalize_email(body.email)
    if not reqs.valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    if not request.app.state.limiters.request_ip.hit(client_ip(request)):
        raise HTTPException(429, "Too many requests. Try again later.")
    settings = settings_of(request)
    if reqs.create_request(conn, email, body.name, body.company, body.note, utcnow()) and settings.admin_email:
        request.app.state.mailer.send(settings.admin_email,
                                      *admin_notification(settings, email, body.name, body.company, body.note))
    return {"ok": True}


@router.post("/api/access/redeem")
def redeem(body: RedeemIn, request: Request, response: Response, conn=Depends(app_db)):
    email = reqs.normalize_email(body.email)
    result = codes.redeem_code(conn, email, body.code, client_ip(request), utcnow())
    if result is not codes.RedeemResult.OK:
        raise HTTPException(400, INVALID_CODE)
    token = sessions.create_session(conn, email, utcnow())
    set_cookie(response, settings_of(request), SESSION_COOKIE, token, int(sessions.SESSION_TTL.total_seconds()))
    return {"ok": True, "email": email}


@router.post("/api/access/new-code")
def new_code(body: EmailIn, request: Request, conn=Depends(app_db)):
    email = reqs.normalize_email(body.email)
    if not request.app.state.limiters.new_code_email.hit(email):
        return {"ok": True}
    req = reqs.get_request(conn, email)
    if req is not None and req["status"] == "approved":
        code = codes.issue_code(conn, email, utcnow())
        request.app.state.mailer.send(email, *access_code_email(settings_of(request), email, code))
    return {"ok": True}


@router.post("/api/access/logout")
def logout(request: Request, response: Response, conn=Depends(app_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sessions.delete_session(conn, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/me")
def me(request: Request, viewer: Viewer = Depends(require_viewer), conn=Depends(app_db)):
    st = quota.status(conn, viewer.email, settings_of(request), utcnow(), is_admin=viewer.is_admin)
    return {"email": viewer.email, "is_admin": viewer.is_admin, "remaining": st.remaining, "limit": st.limit}
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_email.py tests/test_api_access.py -v`
Expected: 13 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/auth/email.py backend/api tests/conftest.py tests/test_email.py tests/test_api_access.py
git commit -m "feat(api): app factory, mailer, and visitor access endpoints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Admin API and asset/set endpoints

**Files:**
- Create: `backend/api/admin.py`, `backend/api/assets.py`
- Test: `tests/test_api_admin.py`, `tests/test_api_assets.py`

**Interfaces:**
- Consumes: `api.deps.*`, Task 11 auth modules, `auth.email`, `retrieval.search.Retriever`, `tests.content_fixture.build_tiny_content`, `tests.fakes.HashEmbedder`.
- Produces:
  - `admin.router`:
    - `POST /api/admin/login {password}`, `POST /api/admin/logout`;
    - `GET /api/admin/requests?status=pending|approved|closed`;
    - `POST /api/admin/requests/{email}/{action}` where action ∈ `approve|reject|revoke|restore|resend-code|reset-quota`;
    - `PATCH /api/admin/requests/{email} {daily_limit}`;
    - `GET /api/admin/requests/{email}/conversations`;
    - `GET /api/admin/stats`.
  - `assets.router`: `GET /api/sets`, `GET /api/sets/{set_id}/assets?type=figure|cad`, `GET /content-docs/{doc_id}.pdf`.

- [ ] **Step 1: Write the failing tests**

`tests/test_api_admin.py`:
```python
from backend.db import connect_app, iso, utcnow
from tests.conftest import login


def admin(c):
    assert c.post("/api/admin/login", json={"password": "pw"}).status_code == 200


def test_login_wrong_password_and_rate_limit(make_client):
    c = make_client()
    assert c.get("/api/admin/requests").status_code == 401
    codes = [c.post("/api/admin/login", json={"password": "nope"}).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]


def test_approve_emails_code_that_redeems(make_client):
    c = make_client()
    c.post("/api/access/request", json={"email": "tech@example.com", "name": "Tess", "company": "Acme"})
    admin(c)
    pending = c.get("/api/admin/requests?status=pending").json()
    assert [r["email"] for r in pending] == ["tech@example.com"]
    assert c.post("/api/admin/requests/tech@example.com/approve").json()["status"] == "approved"
    mail = c.app.state.mailer.outbox[-1]
    assert mail.to == "tech@example.com"
    code = mail.text.split("access code is ")[1].split()[0]
    c.post("/api/admin/logout")
    assert c.post("/api/access/redeem", json={"email": "tech@example.com", "code": code}).status_code == 200


def test_reject_revoke_restore(make_client, settings):
    c = make_client()
    login(c, settings, "tech@example.com")
    assert c.get("/api/me").status_code == 200
    a = make_client()
    admin(a)
    assert a.post("/api/admin/requests/tech@example.com/revoke").json()["status"] == "revoked"
    assert c.get("/api/me").status_code == 401          # sessions deleted
    closed = a.get("/api/admin/requests?status=closed").json()
    assert closed[0]["email"] == "tech@example.com"
    assert a.post("/api/admin/requests/tech@example.com/restore").json()["status"] == "pending"
    assert a.post("/api/admin/requests/nobody@x.com/approve").status_code == 404
    assert a.post("/api/admin/requests/tech@example.com/explode").status_code == 404


def test_limit_quota_reset_resend_stats(make_client, settings):
    c = make_client()
    login(c, settings, "tech@example.com")
    conn = connect_app(settings.data_dir / "app.db")
    conn.execute("INSERT INTO usage_daily (email, day, questions) VALUES ('tech@example.com', ?, 2)",
                 (utcnow().date().isoformat(),))
    conn.execute("INSERT INTO llm_calls (conversation_id, email, call_type, model, input_tokens, output_tokens,"
                 " cache_read_tokens, web_searches, cost_usd, created_at) VALUES ('c','tech@example.com','chat',"
                 "'claude-haiku-4-5',1,1,0,0,0.0123,?)", (iso(utcnow()),))
    a = make_client()
    admin(a)
    assert a.patch("/api/admin/requests/tech@example.com", json={"daily_limit": 20}).status_code == 200
    assert c.get("/api/me").json()["remaining"] == 18
    a.post("/api/admin/requests/tech@example.com/reset-quota")
    assert c.get("/api/me").json()["remaining"] == 20
    before = len(a.app.state.mailer.outbox)
    a.post("/api/admin/requests/tech@example.com/resend-code")
    assert len(a.app.state.mailer.outbox) == before + 1
    stats = a.get("/api/admin/stats").json()
    assert stats["cost_today_usd"] == 0.0123 and stats["global_limit"] == 100 and stats["pending"] == 0
    assert a.get("/api/admin/requests/tech@example.com/conversations").json() == []
    assert a.get("/api/me").json()["is_admin"] is True


def test_approve_records_email_failure(make_client):
    from backend.auth.email import Mailer

    class Failing(Mailer):
        def send(self, *a, **k):
            return False

    c = make_client(mailer=None)
    c.app.state.mailer = Failing(c.app.state.settings)
    c.post("/api/access/request", json={"email": "t@x.com", "name": "T", "company": "C"})
    admin(c)
    row = c.post("/api/admin/requests/t@x.com/approve").json()
    assert row["status"] == "approved" and row["email_error"] == "Email delivery failed (Resend)."
```

`tests/test_api_assets.py`:
```python
import pytest

from backend.retrieval.search import Retriever
from tests.conftest import login
from tests.content_fixture import build_tiny_content
from tests.fakes import HashEmbedder


@pytest.fixture
def client(make_client, settings):
    build_tiny_content(settings.content_dir)
    return make_client(retriever=Retriever.open(settings.content_dir, HashEmbedder()))


def test_sets_require_login(client, settings):
    assert client.get("/api/sets").status_code == 401
    login(client, settings)
    assert client.get("/api/sets").json() == [
        {"id": "demo", "name": "Demo Compressor", "equipment": {"name": "Demo Compressor", "manufacturer": "Acme"}}]


def test_assets_listing_and_static(client, settings):
    login(client, settings)
    cad = client.get("/api/sets/demo/assets?type=cad").json()
    assert [a["id"] for a in cad] == ["cad1/sheet-1"]
    assert client.get(cad[0]["image_url"]).status_code == 200
    assert client.get("/api/sets/nope/assets").status_code == 404


def test_content_docs_guarded(client, settings):
    assert client.get("/content-docs/man.pdf").status_code == 401
    login(client, settings)
    r = client.get("/content-docs/man.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert client.get("/content-docs/zzz.pdf").status_code == 404
    assert client.get("/content-docs/..%2Fapp.pdf").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api_admin.py tests/test_api_assets.py -v`
Expected: FAIL (404s, because the routers don't exist yet).

- [ ] **Step 3: Implement the admin router**

`backend/api/admin.py`:
```python
"""Admin endpoints: login, review/approve requests, per-user limits, usage and spend."""
from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api.deps import ADMIN_COOKIE, ADMIN_MAX_AGE, admin_signer, app_db, client_ip, require_admin, \
    set_cookie, settings_of
from backend.auth import codes, quota, requests as reqs, sessions
from backend.auth.email import access_code_email
from backend.db import day_key, utcnow

router = APIRouter()
STATUS_GROUPS = {"pending": ("pending",), "approved": ("approved",), "closed": ("rejected", "revoked")}
EMAIL_FAILED = "Email delivery failed (Resend)."


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


class LimitIn(BaseModel):
    daily_limit: int | None = Field(default=None, ge=0, le=1000)


@router.post("/api/admin/login")
def login(body: LoginIn, request: Request, response: Response):
    settings = settings_of(request)
    if not settings.admin_password:
        raise HTTPException(503, "Admin is disabled: set ADMIN_PASSWORD.")
    if not request.app.state.limiters.admin_login_ip.hit(client_ip(request)):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    if not hmac.compare_digest(body.password.encode(), settings.admin_password.encode()):
        raise HTTPException(401, "Wrong password.")
    set_cookie(response, settings, ADMIN_COOKIE, admin_signer(settings).sign(b"admin").decode(), ADMIN_MAX_AGE)
    return {"ok": True}


@router.post("/api/admin/logout")
def logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}


def _row(conn, email: str) -> dict:
    rows = [r for r in reqs.list_requests(conn, reqs.STATUSES, utcnow()) if r["email"] == email]
    if not rows:
        raise HTTPException(404, "No such request.")
    return rows[0]


def _send_code(request: Request, conn, email: str) -> None:
    code = codes.issue_code(conn, email, utcnow())
    ok = request.app.state.mailer.send(email, *access_code_email(settings_of(request), email, code))
    reqs.set_email_error(conn, email, None if ok else EMAIL_FAILED)


@router.get("/api/admin/requests", dependencies=[Depends(require_admin)])
def list_requests(status: str = "pending", conn=Depends(app_db)):
    if status not in STATUS_GROUPS:
        raise HTTPException(422, "status must be pending, approved or closed")
    return reqs.list_requests(conn, STATUS_GROUPS[status], utcnow())


@router.post("/api/admin/requests/{email}/{action}", dependencies=[Depends(require_admin)])
def act(email: str, action: str, request: Request, conn=Depends(app_db)):
    email = reqs.normalize_email(email)
    if reqs.get_request(conn, email) is None:
        raise HTTPException(404, "No such request.")
    now = utcnow()
    if action == "approve":
        reqs.set_status(conn, email, "approved", now)
        _send_code(request, conn, email)
    elif action == "reject":
        reqs.set_status(conn, email, "rejected", now)
    elif action == "revoke":
        reqs.set_status(conn, email, "revoked", now)
        sessions.delete_sessions(conn, email)
    elif action == "restore":
        reqs.set_status(conn, email, "pending", now)
    elif action == "resend-code":
        _send_code(request, conn, email)
    elif action == "reset-quota":
        quota.reset_today(conn, email, now)
    else:
        raise HTTPException(404, "Unknown action.")
    return _row(conn, email)


@router.patch("/api/admin/requests/{email}", dependencies=[Depends(require_admin)])
def patch(email: str, body: LimitIn, conn=Depends(app_db)):
    email = reqs.normalize_email(email)
    if reqs.get_request(conn, email) is None:
        raise HTTPException(404, "No such request.")
    reqs.set_daily_limit(conn, email, body.daily_limit)
    return _row(conn, email)


@router.get("/api/admin/requests/{email}/conversations", dependencies=[Depends(require_admin)])
def conversations(email: str, conn=Depends(app_db)):
    email = reqs.normalize_email(email)
    out = []
    for conv in conn.execute("SELECT * FROM conversations WHERE email = ? ORDER BY created_at DESC LIMIT 50",
                             (email,)).fetchall():
        msgs = conn.execute("SELECT role, content, assets_json, created_at FROM messages WHERE conversation_id = ?"
                            " ORDER BY id", (conv["id"],)).fetchall()
        out.append({"id": conv["id"], "set_id": conv["set_id"], "created_at": conv["created_at"],
                    "messages": [{"role": m["role"], "content": m["content"], "created_at": m["created_at"],
                                  "assets": json.loads(m["assets_json"]) if m["assets_json"] else None}
                                 for m in msgs]})
    return out


@router.get("/api/admin/stats", dependencies=[Depends(require_admin)])
def stats(request: Request, conn=Depends(app_db)):
    now = utcnow()
    day = day_key(now)
    questions = conn.execute("SELECT COALESCE(SUM(questions), 0) FROM usage_daily WHERE day = ?", (day,)).fetchone()[0]
    cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE substr(created_at, 1, 10) = ?",
                        (day,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM access_requests WHERE status = 'pending'").fetchone()[0]
    return {"questions_today": questions, "global_limit": settings_of(request).global_daily_limit,
            "cost_today_usd": round(cost, 4), "pending": pending}
```

- [ ] **Step 4: Implement the assets router**

`backend/api/assets.py`:
```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_api_admin.py tests/test_api_assets.py tests/test_api_access.py -v`
Expected: all passed (5 + 3 + 9).

- [ ] **Step 6: Commit**

```bash
git add backend/api/admin.py backend/api/assets.py tests/test_api_admin.py tests/test_api_assets.py
git commit -m "feat(api): admin request management, stats, set/asset listing, guarded PDFs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
## Phase 4 — Claude calls and the chat stream

### Task 14: Client factory, usage logging, prompts, follow-up rewrite

**Files:**
- Create: `backend/llm/client.py`, `backend/llm/prompts.py`, `backend/llm/rewrite.py`
- Test: `tests/test_llm_prompts.py`

**Interfaces:**
- Consumes: `llm.common`, `retrieval.search.ChunkHit/AssetInfo`, `db.iso`.
- Produces:
  - `client.make_client(settings) -> anthropic.AsyncAnthropic`, `client.log_call(conn, *, conversation_id, email, call_type, model, usage, now) -> float`.
  - `prompts.chat_system(equipment: dict) -> str`, `prompts.tools_system(equipment: dict) -> str`, `prompts.REWRITE_SYSTEM`, `prompts.format_excerpts(chunks) -> str`, `prompts.format_figures(assets) -> str`, `prompts.chat_user_content(chunks, candidates, question) -> str`, `prompts.tools_user_content(chunks, candidates, question) -> str`.
  - `rewrite.RewriteResult(query: str, new_task: bool, usage)`, `rewrite.REWRITE_TOOL`, `async rewrite.rewrite_query(client, model, history: list[dict], message: str) -> RewriteResult`.

- [ ] **Step 1: Write the failing tests**

`tests/test_llm_prompts.py`:
```python
import anthropic
import httpx

from backend.db import connect_app, utcnow
from backend.llm.client import log_call
from backend.llm.prompts import chat_system, chat_user_content, format_excerpts, format_figures, tools_system
from backend.llm.rewrite import rewrite_query
from backend.retrieval.search import AssetInfo, ChunkHit
from tests.fakes import FakeAsyncAnthropic, message, tool_use, usage

EQUIP = {"name": "Ariel KBE Reciprocating Compressor", "manufacturer": "Ariel Corporation"}
CHUNK = ChunkHit(id="m:1", doc_id="kbe-manual", doc_title="KBE Maintenance & Repair Manual",
                 section_path="Section 4 › Crosshead Installation", page_start=87, page_end=88,
                 text="Lay a 3/16 inch plate.", figure_refs=(), has_warning=True, score=0.1)
FIG = AssetInfo(id="kbe-manual/crosshead-installation", doc_id="kbe-manual", asset_type="figure",
                caption="Crosshead Installation", kind="exploded_parts", description="Crosshead with tool.",
                page=88, section_path="S", image_url="/x.png", thumb_url="/x.t.png", pdf_url="/p", image_sha="s")
HISTORY = [{"role": "user", "content": "How do I install the crosshead?"},
           {"role": "assistant", "content": "### 1. Prepare ..."}]


def test_system_prompts_are_grounded():
    s = chat_system(EQUIP)
    assert "Ariel KBE Reciprocating Compressor" in s and "Ariel Corporation" in s
    assert "[p.88]" in s and "Not in the provided documentation" in s and "present_assets" in s
    t = tools_system(EQUIP)
    assert "submit_tools" in t and '"manual"' in t and "generic-tool" in t


def test_context_formatting():
    ex = format_excerpts([CHUNK])
    assert 'doc="KBE Maintenance & Repair Manual"' in ex and 'pages="87-88"' in ex and 'warning="yes"' in ex
    assert "Lay a 3/16 inch plate." in ex
    figs = format_figures([FIG])
    assert "kbe-manual/crosshead-installation" in figs and "p.88" in figs and "exploded_parts" in figs
    assert format_figures([]) == "(no figures available)"
    user = chat_user_content([CHUNK], [FIG], "How?")
    assert user.index("<excerpts>") < user.index("<figures>") < user.index("<question>")


async def test_rewrite_skips_without_history():
    client = FakeAsyncAnthropic()
    r = await rewrite_query(client, "claude-haiku-4-5", [], "How do I install the crosshead?")
    assert (r.query, r.new_task, r.usage) == ("How do I install the crosshead?", True, None)
    assert client.messages.calls == []


async def test_rewrite_uses_tool_output():
    client = FakeAsyncAnthropic(lambda kw: message(tool_use("rewrite", {"query": "crosshead pin torque",
                                                                         "new_task": False})))
    r = await rewrite_query(client, "claude-haiku-4-5", HISTORY, "what torque for the pin?")
    assert r.query == "crosshead pin torque" and r.new_task is False and r.usage is not None
    req = client.messages.calls[0]
    assert req["tool_choice"] == {"type": "tool", "name": "rewrite"}
    assert "what torque for the pin?" in req["messages"][0]["content"]
    assert "How do I install the crosshead?" in req["messages"][0]["content"]


async def test_rewrite_falls_back_on_error_or_missing_tool():
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    r = await rewrite_query(FakeAsyncAnthropic(lambda kw: err), "claude-haiku-4-5", HISTORY, "and then?")
    assert (r.query, r.new_task) == ("and then?", False)
    r = await rewrite_query(FakeAsyncAnthropic(lambda kw: message()), "claude-haiku-4-5", HISTORY, "and then?")
    assert (r.query, r.new_task) == ("and then?", False)


def test_log_call(tmp_path):
    conn = connect_app(tmp_path / "app.db")
    cost = log_call(conn, conversation_id="c1", email="t@x.co", call_type="chat", model="claude-haiku-4-5",
                    usage=usage(7000, 900), now=utcnow())
    assert round(cost, 4) == 0.0115
    row = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["input_tokens"] == 7000 and row["output_tokens"] == 900 and row["call_type"] == "chat"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_llm_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.llm.client'`.

- [ ] **Step 3: Implement**

`backend/llm/client.py`:
```python
from __future__ import annotations

from datetime import datetime

import anthropic

from backend.config import Settings
from backend.db import iso
from backend.llm.common import estimate_cost, usage_counts, web_search_count


def make_client(settings: Settings) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None, max_retries=2, timeout=60.0)


def log_call(conn, *, conversation_id: str | None, email: str | None, call_type: str, model: str, usage,
             now: datetime) -> float:
    i, o, c = usage_counts(usage)
    cost = estimate_cost(model, usage)
    conn.execute("INSERT INTO llm_calls (conversation_id, email, call_type, model, input_tokens, output_tokens,"
                 " cache_read_tokens, web_searches, cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (conversation_id, email, call_type, model, i, o, c, web_search_count(usage), cost, iso(now)))
    return cost
```

`backend/llm/prompts.py`:
```python
"""System prompts and context formatting for the chat, tools and rewrite calls."""
from __future__ import annotations

CHAT_SYSTEM = """You are Onesi, a maintenance assistant for field technicians working on the {name} ({manufacturer}).

Answer only from the documentation excerpts in the user's message. They come from the OEM manual set for this equipment.

Rules:
1. Cite the page for every instruction in square brackets, like [p.88], using the pages shown on the excerpt headers.
2. Never state a torque, clearance, pressure, temperature, dimension, interval or part number that is not written in the excerpts. If the technician needs a value that is not there, write "Not in the provided documentation" and, if an excerpt says where it is (for example "see Appendix A"), name that place.
3. If the excerpts do not cover the question, say so in one or two sentences and suggest a more specific question. Do not answer from general knowledge.
4. For a procedure:
   - Start with a line beginning "**Safety:**" summarising any WARNING, CAUTION or NOTE content that applies (omit it if there is none).
   - Then numbered phases as markdown headings, for example "### 1. Prepare", "### 2. Remove", "### 3. Inspect", "### 4. Install", "### 5. Complete". Rename or drop phases to fit the task. Use short bullet steps under each.
   - End with a line beginning "Sources:" listing the cited pages.
5. For follow-up questions, answer directly and briefly; do not repeat the whole procedure.
6. After writing your answer, call the present_assets tool once with the figure IDs from the <figures> list that best illustrate the answer, most useful first (at most 3). Prefer figures that the excerpts you used refer to."""

TOOLS_SYSTEM = """You build the tool list a field technician needs for one maintenance job on the {name} ({manufacturer}).

Use the documentation excerpts first, especially the manual's Tools section:
- List the OEM/Ariel-specific tools the job needs in category "oem" with source "manual" and the page in source_ref (for example "p.10").
- Then add the common hand, power and measuring tools and consumables the job clearly needs, with source "general" (or "web" with the URL in source_ref if you used web search).

Rules:
- Only tools relevant to this job; 4 to 14 tools in total.
- spec: a short specification only if the excerpts state one (for example a torque range), otherwise null.
- icon: the closest icon key from the allowed list; use "generic-tool" if none fits and "oem-tool" for special OEM tools without a better match.
- figure_id: only when a figure in the <figures> list actually shows that tool, otherwise null.
- Call submit_tools exactly once."""

REWRITE_SYSTEM = """You turn a technician's latest chat message into a standalone search query for a compressor maintenance manual.
Resolve pronouns and references using the conversation (for example "that bolt" -> "connecting rod cap bolt").
Set new_task to true only if the latest message starts a different maintenance job from the one being discussed.
Call the rewrite tool once."""


def chat_system(equipment: dict) -> str:
    return CHAT_SYSTEM.format(name=equipment.get("name", "equipment"),
                              manufacturer=equipment.get("manufacturer", "the manufacturer"))


def tools_system(equipment: dict) -> str:
    return TOOLS_SYSTEM.format(name=equipment.get("name", "equipment"),
                               manufacturer=equipment.get("manufacturer", "the manufacturer"))


def _attr(s: str) -> str:
    return s.replace('"', "'")


def format_excerpts(chunks) -> str:
    if not chunks:
        return "<excerpts>\n(no matching documentation found)\n</excerpts>"
    parts = []
    for c in chunks:
        pages = f"{c.page_start}-{c.page_end}" if c.page_end != c.page_start else str(c.page_start)
        parts.append(f'<excerpt doc="{_attr(c.doc_title)}" section="{_attr(c.section_path)}" pages="{pages}"'
                     f' warning="{"yes" if c.has_warning else "no"}">\n{c.text}\n</excerpt>')
    return "<excerpts>\n" + "\n".join(parts) + "\n</excerpts>"


def format_figures(assets) -> str:
    if not assets:
        return "(no figures available)"
    return "\n".join(f"- id: {a.id} | {a.asset_type}/{a.kind} | p.{a.page} | {a.caption} | shows: {a.description}"
                     for a in assets)


def chat_user_content(chunks, candidates, question: str) -> str:
    return (f"{format_excerpts(chunks)}\n\n<figures>\n{format_figures(candidates)}\n</figures>\n\n"
            f"<question>\n{question}\n</question>")


def tools_user_content(chunks, candidates, question: str) -> str:
    return chat_user_content(chunks, candidates, question)
```

`backend/llm/rewrite.py`:
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_llm_prompts.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/llm/client.py backend/llm/prompts.py backend/llm/rewrite.py tests/test_llm_prompts.py
git commit -m "feat(llm): client factory, usage logging, grounded prompts, follow-up rewrite

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Streamed chat answer, tools list, and the icon set

**Files:**
- Create: `backend/llm/chat.py`, `backend/llm/tools.py`, `web/assets/icons.json`
- Test: `tests/test_llm_calls.py`

**Interfaces:**
- Consumes: `llm.common`, `llm.prompts`, `retrieval.search.ChunkHit/AssetInfo`.
- Produces:
  - `chat.ChatContext(equipment, chunks, candidates, history, question, resolve: Callable[[str], str | None])`, `chat.MAX_FIGURES = 3`, `chat.present_assets_tool(ids) -> dict`, `chat.citations_from(text, chunks) -> list[dict]`, `chat.pick_figures(ids, ctx) -> list[str]`, and `async chat.stream_answer(client, model, ctx)`. The last yields `("token", str)`, then `("assets", {"figure_ids", "citations"})`, then `("final", {"text", "usage", "stop_reason"})`.
  - `tools.GROUPS`, `tools.SOURCES`, `tools.MAX_TOOLS = 14`, `tools.ToolsContext(equipment, chunks, candidates, question)`, `tools.ToolsResult(groups: list[dict], usage)`, `tools.ToolsError`, `tools.load_icon_keys(web_dir) -> list[str]`, `tools.submit_tools_tool(icon_keys, figure_ids) -> dict`, `tools.normalize_groups(raw, icon_keys, allowed_figures) -> list[dict]`, `async tools.recommend_tools(client, model, ctx, icon_keys, web_search: bool) -> ToolsResult`.
  - `web/assets/icons.json`: `{icon_key: "<svg path d>"}` for 32 keys, including `generic-tool` and `oem-tool`.

- [ ] **Step 1: Create the icon set**

`web/assets/icons.json` (24×24 stroke icons; values are SVG `path` `d` attributes rendered with `fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"`):
```json
{
  "generic-tool": "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z",
  "oem-tool": "M12 2l3 6 6 1-4.5 4.5L18 20l-6-3-6 3 1.5-6.5L3 9l6-1z",
  "torque-wrench": "M3 21l9-9m0 0 2-2m0 0a3 3 0 1 0 4-4 3 3 0 0 0-4 4zM6 15l3 3",
  "impact-wrench": "M4 9h10v6H4zM14 11h5M8 15l-1 5h4l1-5",
  "socket-set": "M4 8h16v10H4zM7 8V6M12 8V5M17 8V6M7 13h0M12 13h0M17 13h0",
  "allen-key": "M7 3v12a3 3 0 0 0 3 3h7",
  "feeler-gauge": "M4 18 18 4M6 20 20 6M3 15 15 3",
  "dial-indicator": "M12 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM12 11l3-3M12 18v3",
  "micrometer": "M3 12h6M9 8v8h8V8zM17 12h4M5 9v6",
  "tape-measure": "M4 6h12v12H4zM16 16h5M10 12h0",
  "drill": "M3 8h11v5H3zM14 10h4l3 1-3 1h-4M7 13l-1 7h4l1-7",
  "pry-bar": "M4 20 18 6c1-1 2-1 3 0M4 20l-1-2",
  "puller": "M12 3v10M6 6h12M6 6v8l3 3M18 6v8l-3 3",
  "hoist": "M4 3h16M12 3v5M9 8h6l-1 4h-4zM12 12v4a2 2 0 1 1-2 2",
  "jack": "M4 20h16M7 20l2-6h6l2 6M10 14V6h4v8M9 6h6",
  "screwdriver": "M14 3l7 7-3 3-7-7zM11 10l-7 7v3h3l7-7",
  "pliers": "M8 3l4 9 4-9M12 12l-4 9M12 12l4 9",
  "hammer": "M14 4l6 6-3 3-6-6zM11 7l-8 8 3 3 8-8",
  "multimeter": "M7 3h10v18H7zM9 6h6v4H9zM12 14h0M10 18h4",
  "pressure-gauge": "M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM12 12l3-4M12 19v2",
  "thermometer": "M12 3a2 2 0 0 0-2 2v9a4 4 0 1 0 4 0V5a2 2 0 0 0-2-2z",
  "flashlight": "M9 3h6v5l-2 3v10h-2V11L9 8z",
  "ring-compressor": "M5 8h14v8H5zM5 12h14M9 8v8M15 8v8",
  "scraper": "M4 20l8-8m0 0 6-6 2 2-6 6z",
  "wire-brush": "M3 14h12v4H3zM5 14v-4M8 14v-4M11 14v-4M15 16h6",
  "rags": "M4 6c4-2 12 2 16 0v12c-4 2-12-2-16 0z",
  "solvent": "M9 3h6M10 3v4l-4 4v10h12V11l-4-4V3",
  "lubricant": "M6 10h10v10H6zM16 12l4-3M9 10V6h4v4",
  "sealant": "M8 3h8v6H8zM10 9v8l2 4 2-4V9",
  "gloves": "M7 21v-8L5 9a1 1 0 0 1 2-1l1 2V5a1 1 0 0 1 2 0v5V4a1 1 0 0 1 2 0v6V5a1 1 0 0 1 2 0v8l-1 8z",
  "safety-glasses": "M3 10h18M5 10a3 3 0 0 0 6 0M13 10a3 3 0 0 0 6 0",
  "lockout-tag": "M8 11V7a4 4 0 0 1 8 0v4M6 11h12v10H6zM12 15v2"
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_llm_calls.py`:
```python
import pytest

from backend.llm.chat import ChatContext, citations_from, pick_figures, stream_answer
from backend.llm.tools import ToolsContext, ToolsError, load_icon_keys, normalize_groups, recommend_tools
from backend.retrieval.search import AssetInfo, ChunkHit
from tests.conftest import ROOT
from tests.fakes import FakeAsyncAnthropic, message, text_block, tool_use, usage


def chunk(id_, page, doc="kbe-manual"):
    return ChunkHit(id=id_, doc_id=doc, doc_title="Manual", section_path=f"S{page}", page_start=page,
                    page_end=page, text="t", figure_refs=(), has_warning=False, score=0.0)


def fig(id_, page=88):
    return AssetInfo(id=id_, doc_id="kbe-manual", asset_type="figure", caption=id_, kind="line_drawing",
                     description="d", page=page, section_path="S", image_url="/i", thumb_url="/t", pdf_url="/p",
                     image_sha=id_)


CTX = ChatContext(equipment={"name": "KBE"}, chunks=[chunk("c1", 88), chunk("c2", 12)],
                  candidates=[fig("kbe-manual/a"), fig("kbe-manual/b")], history=[], question="How?",
                  resolve=lambda name: "kbe-manual/b" if "bee" in name else None)


def test_citations_map_pages_to_chunks():
    cites = citations_from("Do X [p.88]. Then Y [p.12] and again [p.88]. Unknown [p.5]. Range [p. 12-13]", CTX.chunks)
    assert cites == [{"page": 88, "doc_id": "kbe-manual", "section_path": "S88"},
                     {"page": 12, "doc_id": "kbe-manual", "section_path": "S12"},
                     {"page": 5, "doc_id": None, "section_path": None}]


def test_pick_figures_validates_and_falls_back():
    assert pick_figures(["kbe-manual/b", "kbe-manual/a", "kbe-manual/b"], CTX) == ["kbe-manual/b", "kbe-manual/a"]
    assert pick_figures(["the bee figure"], CTX) == ["kbe-manual/b"]   # fuzzy resolve
    assert pick_figures(["nonsense"], CTX) == ["kbe-manual/a"]         # top candidate fallback
    assert pick_figures(None, CTX) == ["kbe-manual/a"]
    empty = ChatContext(equipment={}, chunks=[], candidates=[], history=[], question="q", resolve=lambda n: None)
    assert pick_figures(None, empty) == []


async def test_stream_answer_events_and_request():
    final = message(text_block("Install it [p.88]."), tool_use("present_assets", {"figure_ids": ["kbe-manual/b"]}),
                    stop_reason="tool_use")
    client = FakeAsyncAnthropic(stream_handler=lambda kw: (["Install ", "it [p.88]."], final, 0))
    events = [e async for e in stream_answer(client, "claude-haiku-4-5", CTX)]
    assert events[:2] == [("token", "Install "), ("token", "it [p.88].")]
    assert events[2] == ("assets", {"figure_ids": ["kbe-manual/b"],
                                    "citations": [{"page": 88, "doc_id": "kbe-manual", "section_path": "S88"}]})
    assert events[3][0] == "final" and events[3][1]["text"] == "Install it [p.88]."
    req = client.messages.calls[0]
    assert req["tools"][0]["input_schema"]["properties"]["figure_ids"]["items"]["enum"] == ["kbe-manual/a", "kbe-manual/b"]
    assert req["tool_choice"] == {"type": "auto"}
    assert req["messages"][-1]["role"] == "user" and "<question>" in req["messages"][-1]["content"]


async def test_stream_answer_without_candidates_sends_no_tools():
    ctx = ChatContext(equipment={}, chunks=[], candidates=[], history=[], question="q", resolve=lambda n: None)
    client = FakeAsyncAnthropic(stream_handler=lambda kw: (["Not covered."], message(text_block("Not covered.")), 0))
    events = [e async for e in stream_answer(client, "claude-haiku-4-5", ctx)]
    assert "tools" not in client.messages.calls[0]
    assert events[-2] == ("assets", {"figure_ids": [], "citations": []})


def test_icon_keys_loaded_from_web():
    keys = load_icon_keys(ROOT / "web")
    assert "generic-tool" in keys and "oem-tool" in keys and "torque-wrench" in keys and len(keys) == 32


def test_normalize_groups_cleans_model_output():
    raw = {"groups": [
        {"category": "hand", "tools": [
            {"name": "Feeler gauge", "purpose": "clearance", "spec": None, "source": "manual", "source_ref": "p.98",
             "icon": "feeler-gauge", "figure_id": "kbe-manual/a"},
            {"name": "feeler GAUGE", "purpose": "dup", "source": "general", "icon": "x"},
            {"name": "", "purpose": "blank"}]},
        {"category": "weird", "tools": [{"name": "Bar", "icon": "nope", "source": "blog", "figure_id": "zzz"}]},
        {"category": "oem", "tools": [{"name": f"T{i}", "icon": "oem-tool", "source": "manual"} for i in range(20)]},
    ]}
    groups = normalize_groups(raw, ["feeler-gauge", "oem-tool", "generic-tool"], {"kbe-manual/a"})
    assert [g["category"] for g in groups] == ["oem", "hand"]
    hand = groups[1]["tools"]
    assert hand[0]["figure_id"] == "kbe-manual/a" and hand[0]["source_ref"] == "p.98"
    assert hand[1] == {"name": "Bar", "purpose": "", "spec": None, "source": "general", "source_ref": None,
                       "icon": "generic-tool", "figure_id": None}
    assert sum(len(g["tools"]) for g in groups) == 14


async def test_recommend_tools_request_shape_and_errors():
    raw = {"groups": [{"category": "hand", "tools": [{"name": "Torque wrench", "purpose": "p", "source": "general",
                                                      "icon": "torque-wrench"}]}]}
    client = FakeAsyncAnthropic(lambda kw: message(tool_use("submit_tools", raw), u=usage(5000, 500)))
    tctx = ToolsContext(equipment={"name": "KBE"}, chunks=CTX.chunks, candidates=CTX.candidates, question="q")
    res = await recommend_tools(client, "claude-haiku-4-5", tctx, ["torque-wrench", "generic-tool"], web_search=False)
    assert res.groups[0]["tools"][0]["name"] == "Torque wrench"
    req = client.messages.calls[0]
    assert req["tool_choice"] == {"type": "tool", "name": "submit_tools"}
    assert [t["name"] for t in req["tools"]] == ["submit_tools"]

    await recommend_tools(client, "claude-haiku-4-5", tctx, ["generic-tool"], web_search=True)
    req = client.messages.calls[1]
    assert req["tools"][1] == {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
    assert req["tool_choice"] == {"type": "auto"}

    with pytest.raises(ToolsError):
        await recommend_tools(FakeAsyncAnthropic(lambda kw: message(text_block("no tool"))), "claude-haiku-4-5",
                              tctx, ["generic-tool"], web_search=False)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_llm_calls.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.llm.chat'`.

- [ ] **Step 4: Implement the chat call**

`backend/llm/chat.py`:
```python
"""Streamed, grounded procedure answer + figure selection via the present_assets tool."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from backend.llm.common import tool_input
from backend.llm.prompts import chat_system, chat_user_content

MAX_FIGURES = 3
CITE = re.compile(r"\[p\.?\s*(\d+)(?:\s*[-–]\s*\d+)?\]")


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


def citations_from(text: str, chunks: list) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for m in CITE.finditer(text):
        page = int(m.group(1))
        if page in seen:
            continue
        seen.add(page)
        c = next((c for c in chunks if c.page_start <= page <= c.page_end), None)
        out.append({"page": page, "doc_id": c.doc_id if c else None, "section_path": c.section_path if c else None})
    return out


def pick_figures(ids: list[str] | None, ctx: ChatContext) -> list[str]:
    allowed = {a.id for a in ctx.candidates}
    out: list[str] = []
    for fid in ids or []:
        if fid not in allowed:
            fid = ctx.resolve(fid)
        if fid and fid not in out:
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
    yield "assets", {"figure_ids": pick_figures(chosen.get("figure_ids"), ctx),
                     "citations": citations_from(text, ctx.chunks)}
    yield "final", {"text": text, "usage": final.usage, "stop_reason": final.stop_reason}
```

- [ ] **Step 5: Implement the tools call**

`backend/llm/tools.py`:
```python
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
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_llm_calls.py -v`
Expected: 8 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/llm/chat.py backend/llm/tools.py web/assets/icons.json tests/test_llm_calls.py
git commit -m "feat(llm): streamed grounded answer with figure choice; validated tools list; icon set

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: `/api/chat` SSE orchestration and tools retry

**Files:**
- Create: `backend/api/chat.py`
- Modify: `backend/api/app.py` (replace the optional-router loop with plain imports)
- Test: `tests/test_api_chat.py`

**Interfaces:**
- Consumes:
  - `api.deps.*`, `auth.quota`, `db`, `retrieval.search.Retriever`;
  - `llm.client.make_client/log_call`, `llm.rewrite.rewrite_query`, `llm.chat.ChatContext/stream_answer`, `llm.tools.ToolsContext/recommend_tools/load_icon_keys`;
  - `tests.content_fixture.build_tiny_content`, `tests.fakes`.
- Produces:
  - `POST /api/chat` with body `{set_id, conversation_id?, message}`. It returns `text/event-stream` with events `meta {conversation_id, remaining_today}`, `token {text}`, `assets {figure_ids, citations, figures: [AssetInfo.public()]}`, `tools {groups, figures}` or `tools {error}`, `error {code, message, resets_at?}` and `done {}`.
  - `POST /api/tools/retry {conversation_id}` returns `{groups, figures}`.
  - `chat.Turn` dataclass and `async chat.run_turn(turn) -> AsyncIterator[str]` (used directly by the disconnect test).

- [ ] **Step 1: Write the failing tests**

`tests/test_api_chat.py`:
```python
import asyncio
import json

import anthropic
import httpx
import pytest

from backend.api.chat import Turn, run_turn
from backend.auth import quota
from backend.db import connect_app, utcnow
from backend.retrieval.search import Retriever
from tests.conftest import approve_in_db, login
from tests.content_fixture import build_tiny_content
from tests.fakes import FakeAsyncAnthropic, HashEmbedder, message, text_block, tool_use, usage

ANSWER = ["**Safety:** isolate. ", "### 1. Install\n- Lay the plate [p.88]."]
TOOLS = {"groups": [{"category": "oem", "tools": [{"name": "Crosshead alignment tool", "purpose": "align",
                                                   "source": "manual", "source_ref": "p.12", "icon": "oem-tool",
                                                   "figure_id": "man/crosshead-alignment-tool"}]}]}


class Script:
    """Programmable fake LLM: counts calls per tool name; behaviour switches per test."""

    def __init__(self):
        self.new_task = False
        self.tools_error = False
        self.chat_error = False
        self.delay = 0.0
        self.client = FakeAsyncAnthropic(self.create, self.stream)

    def names(self, kw):
        return [t.get("name") for t in kw.get("tools", [])]

    def count(self, name):
        return sum(name in self.names(kw) for kw in self.client.messages.calls)

    def create(self, kw):
        if "rewrite" in self.names(kw):
            return message(tool_use("rewrite", {"query": "crosshead installation plate", "new_task": self.new_task}))
        if self.tools_error:
            return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
        return message(tool_use("submit_tools", TOOLS), u=usage(5000, 400))

    def stream(self, kw):
        chunks = list(ANSWER)
        if self.chat_error:
            chunks.append(anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")))
        final = message(text_block("".join(ANSWER)),
                        tool_use("present_assets", {"figure_ids": ["man/crosshead-alignment-tool"]}),
                        stop_reason="tool_use", u=usage(7000, 900))
        return chunks, final, self.delay


def events(resp) -> list[tuple[str, dict]]:
    out = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def script():
    return Script()


@pytest.fixture
def client(make_client, settings, script):
    build_tiny_content(settings.content_dir)
    c = make_client(retriever=Retriever.open(settings.content_dir, HashEmbedder()), llm=script.client)
    login(c, settings)
    return c


def ask(c, message="How do I install the crosshead?", conversation_id=None, set_id="demo"):
    return c.post("/api/chat", json={"set_id": set_id, "message": message, "conversation_id": conversation_id})


def db(settings):
    return connect_app(settings.data_dir / "app.db")


def test_requires_login_and_known_set(make_client, settings, script):
    build_tiny_content(settings.content_dir)
    c = make_client(retriever=Retriever.open(settings.content_dir, HashEmbedder()), llm=script.client)
    assert ask(c).status_code == 401
    login(c, settings)
    assert ask(c, set_id="nope").status_code == 404


def test_first_question_streams_answer_assets_tools(client, settings, script):
    ev = events(ask(client))
    kinds = [k for k, _ in ev]
    assert kinds[0] == "meta" and kinds[-1] == "done"
    assert ev[0][1]["remaining_today"] == 2
    assert "".join(d["text"] for k, d in ev if k == "token") == "".join(ANSWER)
    assets = next(d for k, d in ev if k == "assets")
    assert assets["figure_ids"] == ["man/crosshead-alignment-tool"]
    assert assets["citations"] == [{"page": 88, "doc_id": "man",
                                    "section_path": "Section 4 - Part Replacement › Crosshead Installation"}]
    assert assets["figures"][0]["image_url"] == "/content/figures/man/crosshead-alignment-tool.png"
    tools = next(d for k, d in ev if k == "tools")
    assert tools["groups"][0]["tools"][0]["name"] == "Crosshead alignment tool"
    assert tools["figures"][0]["id"] == "man/crosshead-alignment-tool"
    conn = db(settings)
    assert [r["role"] for r in conn.execute("SELECT role FROM messages ORDER BY id")] == ["user", "assistant"]
    assert {r["call_type"] for r in conn.execute("SELECT call_type FROM llm_calls")} == {"chat", "tools"}
    assert client.get("/api/me").json()["remaining"] == 2
    assert script.count("rewrite") == 0
    chat_req = next(kw for kw in script.client.messages.calls if "present_assets" in script.names(kw))
    assert "Demo Compressor" in chat_req["system"]
    tools_req = next(kw for kw in script.client.messages.calls if "submit_tools" in script.names(kw))
    assert "Recommended tools" in tools_req["messages"][0]["content"]  # always-include Tools section


def test_follow_up_uses_history_and_skips_tools(client, script):
    cid = events(ask(client))[0][1]["conversation_id"]
    ev = events(ask(client, "and the pin?", cid))
    assert "tools" not in [k for k, _ in ev]
    assert script.count("rewrite") == 1 and script.count("submit_tools") == 1
    chat_reqs = [kw for kw in script.client.messages.calls if "present_assets" in script.names(kw)]
    assert len(chat_reqs[-1]["messages"]) == 3  # user, assistant, new user


def test_new_task_follow_up_reruns_tools(client, script):
    cid = events(ask(client))[0][1]["conversation_id"]
    script.new_task = True
    ev = events(ask(client, "now the oil pump?", cid))
    assert "tools" in [k for k, _ in ev] and script.count("submit_tools") == 2


def test_tools_failure_does_not_break_chat(client, settings, script):
    script.tools_error = True
    ev = events(ask(client))
    assert next(d for k, d in ev if k == "tools") == {"error": "Couldn't load tools."}
    assert "assets" in [k for k, _ in ev] and ev[-1][0] == "done"
    assert client.get("/api/me").json()["remaining"] == 2  # still charged: the answer succeeded


def test_chat_failure_releases_quota(client, settings, script):
    script.chat_error = True
    ev = events(ask(client))
    assert ("error", {"code": "upstream_error", "message": "The assistant is busy. Please try again."}) in ev
    assert client.get("/api/me").json()["remaining"] == 3
    assert db(settings).execute("SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0] == 0


def test_quota_exceeded_makes_no_llm_calls(client, script):
    for _ in range(3):
        assert events(ask(client))[-1][0] == "done"
    calls = len(script.client.messages.calls)
    ev = events(ask(client))
    assert ev[0][0] == "error" and ev[0][1]["code"] == "quota_exceeded" and "resets_at" in ev[0][1]
    assert len(script.client.messages.calls) == calls


def test_foreign_conversation_rejected(client, make_client, settings, script):
    cid = events(ask(client))[0][1]["conversation_id"]
    other = make_client(retriever=client.app.state.retriever, llm=script.client)
    login(other, settings, "other@example.com")
    assert ask(other, "show me", cid).status_code == 404
    assert other.post("/api/tools/retry", json={"conversation_id": cid}).status_code == 404


def test_admin_chats_without_quota(make_client, settings, script):
    build_tiny_content(settings.content_dir)
    c = make_client(retriever=Retriever.open(settings.content_dir, HashEmbedder()), llm=script.client)
    c.post("/api/admin/login", json={"password": "pw"})
    for _ in range(4):
        assert events(ask(c))[-1][0] == "done"


def test_tools_retry_updates_message_without_quota(client, settings, script):
    script.tools_error = True
    cid = events(ask(client))[0][1]["conversation_id"]
    script.tools_error = False
    r = client.post("/api/tools/retry", json={"conversation_id": cid})
    assert r.status_code == 200 and r.json()["groups"][0]["category"] == "oem"
    row = db(settings).execute("SELECT tools_json FROM messages WHERE role='assistant'").fetchone()
    assert json.loads(row["tools_json"])[0]["category"] == "oem"
    assert client.get("/api/me").json()["remaining"] == 2


async def test_disconnect_releases_quota(settings, script):
    build_tiny_content(settings.content_dir)
    approve_in_db(settings, "tech@example.com")
    conn = db(settings)
    assert quota.try_reserve(conn, "tech@example.com", settings, utcnow()).allowed
    conn.execute("INSERT INTO conversations (id, email, set_id, created_at) VALUES ('c1','tech@example.com','demo','x')")
    script.delay = 0.05
    retriever = Retriever.open(settings.content_dir, HashEmbedder())
    turn = Turn(settings=settings, llm=script.client, retriever=retriever, conn=conn, email="tech@example.com",
                is_admin=False, set_id="demo", set_info=retriever.get_set("demo"), conversation_id="c1",
                history=[], message="How do I install the crosshead?", remaining=2,
                icon_keys=["oem-tool", "generic-tool"])
    gen = run_turn(turn)
    first = await gen.__anext__()
    assert first.startswith("event: meta")
    await gen.__anext__()  # next event (a token or the tools result)
    await gen.aclose()     # browser went away
    check = db(settings)
    assert check.execute("SELECT questions FROM usage_daily").fetchone()["questions"] == 0
    assert check.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    await asyncio.sleep(0.1)  # cancelled tasks must not write afterwards
    assert check.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api_chat.py -v`
Expected: FAIL with `ImportError: cannot import name 'Turn' from 'backend.api.chat'` (module missing).

- [ ] **Step 3: Implement the chat endpoint**

`backend/api/chat.py`:
```python
"""POST /api/chat: rewrite → retrieve → (chat stream ‖ tools) merged into one SSE stream."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.api.deps import Viewer, get_retriever, require_viewer, settings_of
from backend.auth import quota
from backend.config import Settings
from backend.db import connect_app, iso, utcnow
from backend.llm.chat import ChatContext, stream_answer
from backend.llm.client import log_call, make_client
from backend.llm.rewrite import rewrite_query
from backend.llm.tools import ToolsContext, ToolsResult, load_icon_keys, recommend_tools
from backend.retrieval.search import Retriever

log = logging.getLogger(__name__)
router = APIRouter()
HISTORY_MESSAGES = 12  # last 6 turns
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
BUSY = "The assistant is busy. Please try again."


class ChatIn(BaseModel):
    set_id: str = Field(max_length=64)
    conversation_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class RetryIn(BaseModel):
    conversation_id: str = Field(max_length=64)


@dataclass
class Turn:
    settings: Settings
    llm: object
    retriever: Retriever
    conn: object
    email: str
    is_admin: bool
    set_id: str
    set_info: dict
    conversation_id: str
    history: list[dict]
    message: str
    remaining: int | None
    icon_keys: list[str]


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _llm(request: Request):
    if request.app.state.llm is None:
        request.app.state.llm = make_client(settings_of(request))
    return request.app.state.llm


def _icon_keys(request: Request) -> list[str]:
    if getattr(request.app.state, "icon_keys", None) is None:
        request.app.state.icon_keys = load_icon_keys(settings_of(request).web_dir)
    return request.app.state.icon_keys


def _owned_conversation(conn, conversation_id: str, email: str, set_id: str | None = None):
    row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    if row is None or row["email"] != email or (set_id and row["set_id"] != set_id):
        raise HTTPException(404, "Conversation not found.")
    return row


def _history(conn, conversation_id: str) -> list[dict]:
    rows = conn.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
                        (conversation_id, HISTORY_MESSAGES)).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def _tools_call(t: Turn, query: str, chunks: list, candidates: list) -> tuple[ToolsResult, list[dict]]:
    extra_chunks = []
    for inc in t.set_info.get("always_include", []):
        extra_chunks += await asyncio.to_thread(t.retriever.section_chunks, t.set_id, inc["doc"], inc["section_prefix"])
    seen = {c.id for c in chunks}
    all_chunks = chunks + [c for c in extra_chunks if c.id not in seen]
    extra_fig_ids = [ref for c in extra_chunks for ref in c.figure_refs]
    have = {a.id for a in candidates}
    all_figs = candidates + [a for a in t.retriever.get_assets(list(dict.fromkeys(extra_fig_ids))) if a.id not in have]
    ctx = ToolsContext(equipment=t.set_info["equipment"], chunks=all_chunks, candidates=all_figs, question=t.message)
    result = await recommend_tools(t.llm, t.settings.tools_model, ctx, t.icon_keys, t.settings.tools_web_search)
    used = [tool["figure_id"] for g in result.groups for tool in g["tools"] if tool["figure_id"]]
    return result, [a.public() for a in t.retriever.get_assets(list(dict.fromkeys(used)))]


async def run_turn(t: Turn) -> AsyncIterator[str]:
    s = t.settings
    completed = False
    tasks: list[asyncio.Task] = []
    try:
        yield sse("meta", {"conversation_id": t.conversation_id, "remaining_today": t.remaining})
        rw = await rewrite_query(t.llm, s.rewrite_model, t.history, t.message)
        if rw.usage is not None:
            log_call(t.conn, conversation_id=t.conversation_id, email=t.email, call_type="rewrite",
                     model=s.rewrite_model, usage=rw.usage, now=utcnow())
        chunks = await asyncio.to_thread(t.retriever.search_chunks, t.set_id, rw.query)
        candidates = await asyncio.to_thread(t.retriever.figure_candidates, t.set_id, rw.query, chunks)
        ctx = ChatContext(equipment=t.set_info["equipment"], chunks=chunks, candidates=candidates,
                          history=t.history, question=t.message,
                          resolve=lambda name: t.retriever.resolve_figure(t.set_id, name))
        queue: asyncio.Queue = asyncio.Queue()

        async def chat_job():
            try:
                async for kind, payload in stream_answer(t.llm, s.chat_model, ctx):
                    await queue.put(("chat", kind, payload))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("chat call failed: %s", e)
                await queue.put(("chat", "error", None))
            finally:
                queue.put_nowait(("chat", "end", None))

        async def tools_job():
            try:
                await queue.put(("tools", "result", await _tools_call(t, rw.query, chunks, candidates)))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("tools call failed: %s", e)
                await queue.put(("tools", "error", None))
            finally:
                queue.put_nowait(("tools", "end", None))

        tasks.append(asyncio.create_task(chat_job()))
        if not t.history or rw.new_task:
            tasks.append(asyncio.create_task(tools_job()))
        open_jobs = len(tasks)
        answer: dict | None = None
        assets: dict | None = None
        tools_groups: list | None = None
        chat_failed = False
        while open_jobs:
            source, kind, payload = await queue.get()
            if kind == "end":
                open_jobs -= 1
            elif source == "chat" and kind == "token":
                yield sse("token", {"text": payload})
            elif source == "chat" and kind == "assets":
                assets = payload
                figures = [a.public() for a in t.retriever.get_assets(payload["figure_ids"])]
                yield sse("assets", {**payload, "figures": figures})
            elif source == "chat" and kind == "final":
                answer = payload
                log_call(t.conn, conversation_id=t.conversation_id, email=t.email, call_type="chat",
                         model=s.chat_model, usage=payload["usage"], now=utcnow())
            elif source == "chat" and kind == "error":
                chat_failed = True
                yield sse("error", {"code": "upstream_error", "message": BUSY})
            elif source == "tools" and kind == "result":
                result, figures = payload
                tools_groups = result.groups
                log_call(t.conn, conversation_id=t.conversation_id, email=t.email, call_type="tools",
                         model=s.tools_model, usage=result.usage, now=utcnow())
                yield sse("tools", {"groups": result.groups, "figures": figures})
            elif source == "tools" and kind == "error":
                yield sse("tools", {"error": "Couldn't load tools."})
        if answer is not None and not chat_failed:
            now = iso(utcnow())
            t.conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                           (t.conversation_id, t.message, now))
            t.conn.execute("INSERT INTO messages (conversation_id, role, content, query, assets_json, tools_json,"
                           " created_at) VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
                           (t.conversation_id, answer["text"], rw.query, json.dumps(assets),
                            json.dumps(tools_groups) if tools_groups is not None else None, now))
            completed = True
        yield sse("done", {})
    finally:
        for task in tasks:
            task.cancel()
        if not completed:
            quota.release(t.conn, t.email, utcnow())
        t.conn.close()


@router.post("/api/chat")
async def chat(body: ChatIn, request: Request, viewer: Viewer = Depends(require_viewer)):
    settings = settings_of(request)
    retriever = get_retriever(request)
    set_info = retriever.get_set(body.set_id)
    if set_info is None:
        raise HTTPException(404, "Unknown equipment set.")
    conn = connect_app(settings.data_dir / "app.db")
    try:
        now = utcnow()
        if body.conversation_id:
            _owned_conversation(conn, body.conversation_id, viewer.email, body.set_id)
            conversation_id, history = body.conversation_id, _history(conn, body.conversation_id)
        else:
            conversation_id, history = uuid4().hex, []
            conn.execute("INSERT INTO conversations (id, email, set_id, created_at) VALUES (?, ?, ?, ?)",
                         (conversation_id, viewer.email, body.set_id, iso(now)))
        q = quota.try_reserve(conn, viewer.email, settings, now, is_admin=viewer.is_admin)
    except BaseException:
        conn.close()
        raise
    if not q.allowed:
        conn.close()
        msg = ("You've used today's questions." if q.reason == "user"
               else "The demo has reached today's overall limit.")
        body_events = [sse("error", {"code": "quota_exceeded", "message": f"{msg} It resets at midnight UTC.",
                                     "resets_at": q.resets_at}), sse("done", {})]
        return StreamingResponse(iter(body_events), media_type="text/event-stream", headers=SSE_HEADERS)
    turn = Turn(settings=settings, llm=_llm(request), retriever=retriever, conn=conn, email=viewer.email,
                is_admin=viewer.is_admin, set_id=body.set_id, set_info=set_info, conversation_id=conversation_id,
                history=history, message=body.message.strip(), remaining=q.remaining,
                icon_keys=_icon_keys(request))
    return StreamingResponse(run_turn(turn), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/api/tools/retry")
async def retry_tools(body: RetryIn, request: Request, viewer: Viewer = Depends(require_viewer)):
    settings = settings_of(request)
    retriever = get_retriever(request)
    conn = connect_app(settings.data_dir / "app.db")
    try:
        conv = _owned_conversation(conn, body.conversation_id, viewer.email)
        last = conn.execute("SELECT * FROM messages WHERE conversation_id = ? AND role = 'assistant'"
                            " ORDER BY id DESC LIMIT 1", (conv["id"],)).fetchone()
        user = conn.execute("SELECT content FROM messages WHERE conversation_id = ? AND role = 'user'"
                            " ORDER BY id DESC LIMIT 1", (conv["id"],)).fetchone()
        if last is None:
            raise HTTPException(404, "Nothing to retry yet.")
        set_info = retriever.get_set(conv["set_id"])
        chunks = await asyncio.to_thread(retriever.search_chunks, conv["set_id"], last["query"])
        candidates = await asyncio.to_thread(retriever.figure_candidates, conv["set_id"], last["query"], chunks)
        turn = Turn(settings=settings, llm=_llm(request), retriever=retriever, conn=conn, email=viewer.email,
                    is_admin=viewer.is_admin, set_id=conv["set_id"], set_info=set_info, conversation_id=conv["id"],
                    history=[], message=user["content"] if user else last["query"], remaining=None,
                    icon_keys=_icon_keys(request))
        try:
            result, figures = await _tools_call(turn, last["query"], chunks, candidates)
        except Exception as e:
            log.warning("tools retry failed: %s", e)
            raise HTTPException(502, "Couldn't load tools.")
        conn.execute("UPDATE messages SET tools_json = ? WHERE id = ?", (json.dumps(result.groups), last["id"]))
        log_call(conn, conversation_id=conv["id"], email=viewer.email, call_type="tools", model=settings.tools_model,
                 usage=result.usage, now=utcnow())
        return {"groups": result.groups, "figures": figures}
    finally:
        conn.close()
```

- [ ] **Step 4: Replace the optional-router loop in `backend/api/app.py`**

All four routers now exist. In `create_app`, replace:
```python
    from backend.api import access  # local imports avoid cycles with deps
```
with:
```python
    from backend.api import access, admin, assets, chat  # local imports avoid cycles with deps
```
and replace:
```python
    app.include_router(access.router)
    for name in ("admin", "assets", "chat"):  # routers added by later tasks
        try:
            module = __import__(f"backend.api.{name}", fromlist=["router"])
        except ModuleNotFoundError:
            continue
        app.include_router(module.router)
```
with:
```python
    for module in (access, admin, assets, chat):
        app.include_router(module.router)
```

- [ ] **Step 5: Run the chat tests, then the whole suite**

Run: `uv run pytest tests/test_api_chat.py -v`
Expected: 11 passed.

Run: `uv run pytest -m "not integration" -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add backend/api/chat.py backend/api/app.py tests/test_api_chat.py
git commit -m "feat(api): SSE chat turn with parallel tools call, quota reservation, and tools retry

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
## Phase 5 — Frontend

The frontend tasks are verified by running the app and driving it in a real browser (Playwright MCP tools), not by unit tests. Each task ends with explicit checks. Start the local server once for all three tasks:

```bash
cd /Users/emeraldhenry/AI_maintenance_Application
cat .env   # must contain ANTHROPIC_API_KEY; add ADMIN_PASSWORD=localadmin and ADMIN_EMAIL=you@example.com if missing
uv run uvicorn backend.api.app:create_app --factory --reload --port 8000
```

With no `RESEND_API_KEY`, access-code emails print in this terminal.

### Task 17: Demo page shell, styles, and access gate

**Files:**
- Create: `web/demo.html`, `web/assets/demo.css`, `web/assets/demo.js` (gate + app bootstrap; Task 18 extends it)
- Test: `tests/test_pages.py`

**Interfaces:**
- Consumes: `/api/me`, `/api/access/request`, `/api/access/redeem`, `/api/access/new-code`, `/api/access/logout`, `/api/sets`, `web/assets/icons.json`.
- Produces: the DOM IDs Task 18 relies on: `#gate`, `#app`, `#set-select`, `#quota`, `#who`, `#signout`, `#viewer`, `#viewer-meta`, `#thumbs`, `#chat`, `#starter`, `#composer`, `#input`, `#send`, `#new-job`, `#tools`, `#lightbox`, `.tabs [data-pane]`, `.seg [data-view]`. Also the module-level exports in `demo.js`: `state`, `api(path, body?)`, `updateQuota(me)`, `startApp(me)`.

- [ ] **Step 1: Write a failing smoke test for the pages**

`tests/test_pages.py`:
```python
def test_demo_and_admin_pages_served(make_client):
    c = make_client()
    demo = c.get("/demo")
    assert demo.status_code == 200 and 'id="gate"' in demo.text and "assets/demo.js" in demo.text
    assert c.get("/assets/demo.css").status_code == 200
    assert c.get("/assets/demo.js").status_code == 200
    assert c.get("/assets/icons.json").json()["generic-tool"]
```

Run: `uv run pytest tests/test_pages.py -v`
Expected: FAIL (`/demo` returns 404 / missing file).

- [ ] **Step 2: Write `web/demo.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Onesi Demo</title>
<meta name="description" content="Onesi demo: AI-assisted maintenance procedures, figures and tools from OEM documentation.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="assets/style.css">
<link rel="stylesheet" href="assets/demo.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.6/purify.min.js"></script>
</head>
<body class="demo-body">

<header class="app-bar">
  <a href="index.html" class="brand">ONESI<span class="dot">.</span></a>
  <select id="set-select" aria-label="Equipment" hidden></select>
  <div class="app-bar-right">
    <span id="quota" class="pill" hidden></span>
    <span id="who" class="who" hidden></span>
    <button id="signout" class="btn-link" type="button" hidden>Sign out</button>
  </div>
</header>

<main id="gate" class="gate" hidden>
  <section class="gate-card" data-step="request">
    <span class="label">Onesi demo</span>
    <h1>Try the maintenance assistant</h1>
    <p>Ask about maintenance on an Ariel KBE compressor and get step-by-step procedures from the OEM manual, the matching figure, and a tools list. Access is by invitation.</p>
    <form id="request-form" novalidate>
      <label>Email<input name="email" type="email" required autocomplete="email"></label>
      <label>Name<input name="name" required autocomplete="name"></label>
      <label>Company<input name="company" required autocomplete="organization"></label>
      <label>What would you use it for? <span class="opt">(optional)</span><textarea name="note" rows="2" maxlength="1000"></textarea></label>
      <input name="website" class="hp" tabindex="-1" autocomplete="off" aria-hidden="true">
      <p class="form-error" role="alert" hidden></p>
      <button class="btn btn-primary" type="submit">Request access</button>
    </form>
    <p class="gate-switch">Already have a code? <a href="#" data-goto="code">Enter it here</a></p>
  </section>

  <section class="gate-card" data-step="requested" hidden>
    <span class="label">Request received</span>
    <h1>Thanks, we'll be in touch</h1>
    <p>We review every request. If approved, you'll get an email with an access code.</p>
    <p class="gate-switch"><a href="#" data-goto="code">I have a code</a></p>
  </section>

  <section class="gate-card" data-step="code" hidden>
    <span class="label">Access code</span>
    <h1>Enter your access code</h1>
    <form id="code-form" novalidate>
      <label>Email<input name="email" type="email" required autocomplete="email"></label>
      <label>Access code<input name="code" required placeholder="XXXX-XXXX" autocomplete="one-time-code" class="code-input" maxlength="12"></label>
      <p class="form-error" role="alert" hidden></p>
      <button class="btn btn-primary" type="submit">Open the demo</button>
    </form>
    <p class="gate-switch"><a href="#" id="new-code">Email me a new code</a> · <a href="#" data-goto="request">Request access</a></p>
    <p class="form-note" id="new-code-note" hidden>If that email has been approved, a new code is on its way.</p>
  </section>
</main>

<main id="app" class="workspace" data-active="steps" hidden>
  <nav class="tabs" role="tablist" aria-label="Panels">
    <button role="tab" type="button" data-pane="diagram" aria-selected="false">Diagram<span class="badge-dot" hidden></span></button>
    <button role="tab" type="button" data-pane="steps" aria-selected="true">Steps</button>
    <button role="tab" type="button" data-pane="tools" aria-selected="false">Tools<span class="badge-dot" hidden></span></button>
  </nav>

  <section class="pane pane-diagram" data-pane="diagram" aria-label="Figures and drawings">
    <div class="pane-head">
      <div class="seg">
        <button type="button" data-view="figures" aria-pressed="true">Figures</button>
        <button type="button" data-view="drawings" aria-pressed="false">Drawings</button>
      </div>
    </div>
    <div class="viewer" id="viewer"><p class="empty">Ask a question and the most relevant manual figure appears here.</p></div>
    <div class="viewer-meta" id="viewer-meta"></div>
    <div class="thumbs" id="thumbs"></div>
  </section>

  <section class="pane pane-steps" data-pane="steps" aria-label="Assistant">
    <div class="pane-head">
      <h2>Maintenance assistant</h2>
      <button id="new-job" class="btn-link" type="button">New job</button>
    </div>
    <div class="chat" id="chat" aria-live="polite">
      <div class="starter" id="starter">
        <p>Ask about a maintenance task on the <b id="equip-name">selected equipment</b>.</p>
        <div class="chips">
          <button type="button" class="chip">How do I install the crosshead?</button>
          <button type="button" class="chip">What is the main bearing cap torque sequence?</button>
          <button type="button" class="chip">How do I prime the force feed lubricator pump?</button>
          <button type="button" class="chip">What is on the daily maintenance checklist?</button>
        </div>
      </div>
    </div>
    <form class="composer" id="composer">
      <textarea id="input" rows="1" placeholder="Ask a maintenance question…" maxlength="2000" required></textarea>
      <button class="btn btn-primary" id="send" type="submit">Send</button>
    </form>
    <p class="disclaimer">Answers come only from the OEM documentation shown. Check against the latest manual revision before starting work.</p>
  </section>

  <section class="pane pane-tools" data-pane="tools" aria-label="Tools">
    <div class="pane-head"><h2>Tools for this job</h2></div>
    <div id="tools"><p class="empty">The tools for the job appear here after your first question.</p></div>
  </section>
</main>

<div class="lightbox" id="lightbox" hidden>
  <button type="button" class="lb-close" aria-label="Close">×</button>
  <div class="lb-stage"><img alt=""></div>
  <p class="lb-hint">Scroll or pinch to zoom · drag to pan · double-click to reset</p>
</div>

<script type="module" src="assets/demo.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write `web/assets/demo.css`**

```css
/* Onesi demo workspace. Tokens come from style.css (:root). */
.demo-body { font-size: 15px; background: var(--mist); min-height: 100vh; display: flex; flex-direction: column; }
.demo-body [hidden] { display: none !important; }
/* reset marketing-site element rules from style.css (section padding, bullet-less lists, tall textareas) */
.demo-body section { padding: 0; }
.demo-body label { margin: 0; }
.demo-body textarea { min-height: 0; }
.md ul, .md ol { padding-left: 22px; }
.md ul li { list-style: disc; }
.md ol li { list-style: decimal; }

.app-bar { display: flex; align-items: center; gap: 16px; padding: 10px 20px; background: var(--navy); color: var(--white); }
.app-bar .brand { color: var(--white); text-decoration: none; }
.app-bar select { background: rgba(255,255,255,.08); color: var(--white); border: 1px solid rgba(255,255,255,.2); border-radius: 8px; padding: 6px 10px; font: inherit; }
.app-bar select option { color: var(--navy); }
.app-bar-right { margin-left: auto; display: flex; align-items: center; gap: 14px; }
.app-bar .pill { color: #cfe9f0; border-color: rgba(255,255,255,.2); }
.who { font-size: 13px; color: #b7c3cc; }
.btn-link { background: none; border: 0; color: var(--cyan-deep); font: inherit; cursor: pointer; padding: 4px; }
.app-bar .btn-link { color: var(--cyan); }

/* ---- gate ---- */
.gate { flex: 1; display: grid; place-items: center; padding: 32px 16px; }
.demo-body .gate-card { width: 100%; max-width: 460px; background: var(--white); border: 1px solid var(--line); border-radius: 14px; padding: 28px; }
.gate-card h1 { font-family: var(--font-head); font-size: 26px; margin: 0 0 10px; }
.gate-card form { display: grid; gap: 12px; margin-top: 16px; }
.gate-card label { display: grid; gap: 4px; font-size: 14px; font-weight: 500; }
.gate-card input, .gate-card textarea { font: inherit; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; }
.gate-card textarea { min-height: 60px; }
.gate-card .opt { color: var(--steel); font-weight: 400; }
.code-input { letter-spacing: 3px; text-transform: uppercase; font-family: var(--font-head); font-size: 18px; }
.hp { position: absolute; left: -9999px; }
.gate-switch, .form-note { font-size: 14px; color: var(--steel); margin-top: 14px; }
.form-error { color: #b42318; font-size: 14px; margin: 0; }

/* ---- workspace ---- */
.workspace { flex: 1; display: grid; grid-template-columns: 30fr 40fr 30fr; gap: 14px; padding: 14px; min-height: 0; height: calc(100vh - 56px); }
.pane { background: var(--white); border: 1px solid var(--line); border-radius: 12px; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.pane-head { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--line); }
.pane-head h2 { font-family: var(--font-head); font-size: 16px; margin: 0; }
.tabs { display: none; }
.empty { color: var(--steel); font-size: 14px; padding: 16px; margin: 0; }

/* diagram pane */
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { background: none; border: 0; padding: 6px 14px; font: inherit; font-size: 13px; cursor: pointer; color: var(--steel); }
.seg button[aria-pressed="true"] { background: var(--navy); color: var(--white); }
.viewer { flex: 1; min-height: 0; display: grid; place-items: center; padding: 12px; overflow: hidden; }
.viewer img { max-width: 100%; max-height: 100%; object-fit: contain; cursor: zoom-in; }
.viewer-meta { padding: 8px 16px; font-size: 13px; color: var(--steel); border-top: 1px solid var(--line); }
.viewer-meta b { color: var(--navy); display: block; font-size: 14px; }
.viewer-meta a { color: var(--cyan-deep); }
.thumbs { display: flex; gap: 8px; padding: 10px 12px; overflow-x: auto; border-top: 1px solid var(--line); min-height: 0; }
.thumbs:empty { display: none; }
.thumb { flex: 0 0 auto; width: 92px; background: none; border: 2px solid transparent; border-radius: 8px; padding: 4px; cursor: pointer; text-align: left; }
.thumb img { width: 100%; height: 64px; object-fit: contain; background: var(--mist); border-radius: 4px; }
.thumb span { display: block; font-size: 11px; color: var(--steel); line-height: 1.3; margin-top: 4px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.thumb[aria-current="true"] { border-color: var(--cyan); }

/* steps pane */
.chat { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 14px; }
.starter p { margin: 0 0 10px; color: var(--steel); }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { border: 1px solid var(--line); background: var(--mist); border-radius: 18px; padding: 7px 12px; font: inherit; font-size: 13.5px; cursor: pointer; text-align: left; }
.chip:hover { border-color: var(--cyan); }
.msg { max-width: 100%; }
.msg.user { align-self: flex-end; background: #e8f7fb; border-radius: 12px 12px 2px 12px; padding: 10px 14px; max-width: 85%; }
.msg.assistant { align-self: stretch; }
.msg.assistant .md { line-height: 1.6; }
.md h3 { font-family: var(--font-head); font-size: 15px; margin: 16px 0 6px; padding-left: 10px; border-left: 3px solid var(--cyan); }
.md ul, .md ol { margin: 4px 0 8px; }
.md p { margin: 6px 0; }
.md strong { color: var(--navy); }
.cite { border: 1px solid var(--line); background: var(--mist); border-radius: 10px; padding: 0 6px; font: inherit; font-size: 12px; color: var(--cyan-deep); cursor: pointer; }
.msg.error { color: #b42318; background: #fef3f2; border-radius: 8px; padding: 10px 14px; }
.typing::after { content: "Thinking…"; color: var(--steel); font-size: 14px; }
.composer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--line); }
.composer textarea { flex: 1; resize: none; font: inherit; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; max-height: 140px; }
.composer .btn { padding: 10px 18px; }
.disclaimer { font-size: 12px; color: var(--steel); margin: 0; padding: 0 14px 10px; }

/* tools pane */
#tools { overflow-y: auto; padding: 12px 14px; }
.tool-group h3 { font-family: var(--font-head); font-size: 14px; margin: 12px 0 8px; }
.tool-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.tool { border: 1px solid var(--line); border-radius: 10px; padding: 10px; display: flex; flex-direction: column; gap: 4px; background: var(--white); text-align: left; font: inherit; }
.tool.has-figure { cursor: pointer; }
.tool .ico { height: 56px; display: grid; place-items: center; color: var(--navy); background: var(--mist); border-radius: 8px; }
.tool .ico svg { width: 30px; height: 30px; }
.tool .ico img { max-height: 52px; max-width: 100%; object-fit: contain; }
.tool b { font-size: 13px; line-height: 1.3; }
.tool .purpose { font-size: 12px; color: var(--steel); line-height: 1.35; }
.tool .spec { font-size: 12px; color: var(--navy); }
.src { align-self: flex-start; font-size: 11px; border-radius: 10px; padding: 1px 8px; background: var(--mist); color: var(--steel); text-decoration: none; }
.src.manual { background: #e8f7fb; color: var(--cyan-deep); }
.skeleton { height: 120px; border-radius: 10px; background: linear-gradient(90deg, var(--mist), #eef2f4, var(--mist)); background-size: 200% 100%; animation: sk 1.2s infinite; }
@keyframes sk { to { background-position: -200% 0; } }
.tools-error { color: #b42318; font-size: 14px; }

/* lightbox */
.lightbox { position: fixed; inset: 0; background: rgba(7,26,43,.92); z-index: 50; display: flex; flex-direction: column; }
.lb-stage { flex: 1; overflow: hidden; display: grid; place-items: center; touch-action: none; cursor: grab; }
.lb-stage img { max-width: 92vw; max-height: 86vh; transform-origin: center center; user-select: none; -webkit-user-drag: none; background: var(--white); }
.lb-close { position: absolute; top: 10px; right: 16px; font-size: 32px; color: var(--white); background: none; border: 0; cursor: pointer; z-index: 1; }
.lb-hint { color: #b7c3cc; font-size: 12px; text-align: center; margin: 6px 0 12px; }

/* ---- phones and small tablets: tabs instead of three columns ---- */
@media (max-width: 899px) {
  .app-bar { padding: 8px 16px; gap: 10px; flex-wrap: wrap; }
  .who { display: none; }
  .workspace { display: flex; flex-direction: column; padding: 0; gap: 0; height: calc(100dvh - 52px); }
  .tabs { display: flex; background: var(--white); border-bottom: 1px solid var(--line); }
  .tabs button { flex: 1; background: none; border: 0; padding: 12px 0; font: inherit; font-weight: 600; color: var(--steel); position: relative; }
  .tabs button[aria-selected="true"] { color: var(--navy); box-shadow: inset 0 -2px 0 var(--cyan); }
  .badge-dot { position: absolute; top: 9px; margin-left: 4px; width: 7px; height: 7px; border-radius: 50%; background: var(--orange); }
  .pane { border-radius: 0; border: 0; flex: 1; display: none; }
  .workspace[data-active="diagram"] .pane-diagram,
  .workspace[data-active="steps"] .pane-steps,
  .workspace[data-active="tools"] .pane-tools { display: flex; }
  .demo-body .gate-card { padding: 22px 16px; }
}
```

- [ ] **Step 4: Write `web/assets/demo.js` (gate and bootstrap)**

```js
// Onesi demo — gate + app bootstrap. Conversation, figures and tools live in Task 18's section below.
export const state = { me: null, setId: null, equipment: null, conversationId: null, busy: false,
  icons: {}, figures: [], drawings: [], view: "figures", citations: [] };

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

export async function api(path, body) {
  const opts = { credentials: "same-origin", headers: {} };
  if (body !== undefined) { opts.method = "POST"; opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  return fetch(path, opts);
}

async function errorText(resp) {
  try { const j = await resp.json(); return typeof j.detail === "string" ? j.detail : "Please check the form and try again."; }
  catch { return "Something went wrong. Please try again."; }
}

// ---------- gate ----------
function showStep(step) {
  $("#gate").hidden = false;
  $("#app").hidden = true;
  $$(".gate-card").forEach(c => { c.hidden = c.dataset.step !== step; });
}

function setError(form, msg) {
  const el = $(".form-error", form);
  el.textContent = msg || "";
  el.hidden = !msg;
}

function bindGate() {
  $$("[data-goto]").forEach(a => a.addEventListener("click", e => { e.preventDefault(); showStep(a.dataset.goto); }));

  $("#request-form").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.currentTarget;
    if (!form.reportValidity()) return;
    const data = Object.fromEntries(new FormData(form));
    const resp = await api("/api/access/request", data);
    if (resp.ok) { $("#code-form").email.value = data.email; showStep("requested"); }
    else setError(form, await errorText(resp));
  });

  $("#code-form").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.currentTarget;
    if (!form.reportValidity()) return;
    setError(form, "");
    const resp = await api("/api/access/redeem", Object.fromEntries(new FormData(form)));
    if (!resp.ok) { setError(form, await errorText(resp)); return; }
    history.replaceState(null, "", location.pathname);
    const me = await api("/api/me");
    if (me.ok) startApp(await me.json());
  });

  $("#new-code").addEventListener("click", async e => {
    e.preventDefault();
    const form = $("#code-form");
    if (!form.email.reportValidity()) return;
    await api("/api/access/new-code", { email: form.email.value });
    $("#new-code-note").hidden = false;
  });
}

// ---------- app ----------
export function updateQuota(me) {
  const pill = $("#quota");
  pill.hidden = false;
  pill.textContent = me.is_admin ? "Admin · unlimited" : `${me.remaining} of ${me.limit} questions left today`;
}

export async function startApp(me) {
  state.me = me;
  $("#gate").hidden = true;
  $("#app").hidden = false;
  $("#who").textContent = me.email;
  $("#who").hidden = me.is_admin;
  $("#signout").hidden = false;
  updateQuota(me);
  const sets = await (await api("/api/sets")).json();
  const sel = $("#set-select");
  sel.innerHTML = "";
  for (const s of sets) sel.add(new Option(s.name, s.id));
  sel.hidden = sets.length < 2;
  selectSet(sets[0]);
  sel.addEventListener("change", () => selectSet(sets.find(s => s.id === sel.value)));
  document.dispatchEvent(new CustomEvent("onesi:ready"));
}

function selectSet(set) {
  state.setId = set.id;
  state.equipment = set.equipment;
  $("#equip-name").textContent = set.name;
  document.dispatchEvent(new CustomEvent("onesi:set-changed"));
}

async function init() {
  try { state.icons = await (await fetch("assets/icons.json")).json(); } catch { state.icons = {}; }
  bindGate();
  $("#signout").addEventListener("click", async () => {
    await api("/api/access/logout", {});
    await api("/api/admin/logout", {});
    location.href = "/demo";
  });
  const params = new URLSearchParams(location.search);
  const me = await api("/api/me");
  if (me.ok) { startApp(await me.json()); return; }
  if (params.get("code")) {
    const form = $("#code-form");
    form.email.value = params.get("email") || "";
    form.code.value = params.get("code");
    showStep("code");
  } else {
    showStep("request");
  }
}

init();
```

- [ ] **Step 5: Run the page test and check the gate in a browser**

Run: `uv run pytest tests/test_pages.py -v`
Expected: PASS.

Check the CDN URLs resolve: `curl -sI https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js | head -1` and the same for `dompurify/3.1.6/purify.min.js`. Expect `HTTP/2 200`. If either 404s, pick the nearest listed version from `https://cdnjs.com/libraries/<name>` and update `demo.html`.

In the browser (Playwright): open `http://localhost:8000/demo` at 1440×900 and verify:
1. The request form shows. Submit `you+demo@example.com`, and "Request received" appears. The server terminal prints the admin notification email.
2. Approve via SQL for now (the admin page comes in Task 19):
   `uv run python -c "from backend.auth import requests as r, codes; from backend.db import connect_app, utcnow; c=connect_app('data/app.db'); r.set_status(c,'you+demo@example.com','approved',utcnow()); print(codes.issue_code(c,'you+demo@example.com',utcnow()))"`
3. Click "I have a code", enter the email and printed code (try it lowercase without the dash), and the three-pane workspace appears with the quota pill "10 of 10 questions left today".
4. Resize to 390×844: only the Steps tab content shows, with Diagram/Steps/Tools tabs at the top.

Take a screenshot of each state and look at it.

- [ ] **Step 6: Commit**

```bash
git add web/demo.html web/assets/demo.css web/assets/demo.js tests/test_pages.py
git commit -m "feat(web): demo page shell, workspace styles, and access gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Conversation, figures and drawings, tools, mobile tabs, zoom lightbox

**Files:**
- Modify: `web/assets/demo.js` (append the workspace section)

**Interfaces:**
- Consumes: `state`, `api`, `updateQuota` from Task 17. The SSE contract from Task 16: `meta`, `token`, `assets {figure_ids, citations, figures}`, `tools {groups, figures}` or `tools {error}`, `error {code, message}`, `done`. Also `/api/tools/retry`, `/api/sets/{id}/assets?type=cad`, `/api/me`.
- Produces: the finished demo UI.

- [ ] **Step 1: Append the workspace code to `web/assets/demo.js`**

Append below `init();`:
```js
// =====================================================================================
// Workspace: chat stream, figure viewer, drawings, tools, mobile tabs, lightbox
// =====================================================================================
const GROUP_LABELS = { oem: "OEM tools", power: "Power tools", hand: "Hand tools", measuring: "Measuring", consumables: "Consumables" };
const CITE_RE = /\[p\.?\s*(\d+)(?:\s*[-–]\s*\d+)?\]/g;
const narrow = window.matchMedia("(max-width: 899px)");

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

function iconSvg(key) {
  const d = state.icons[key] || state.icons["generic-tool"] || "";
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  for (const [k, v] of Object.entries({ d, fill: "none", stroke: "currentColor", "stroke-width": "1.6",
    "stroke-linecap": "round", "stroke-linejoin": "round" })) p.setAttribute(k, v);
  svg.append(p);
  return svg;
}

// ---------- mobile tabs ----------
function activatePane(name) {
  $("#app").dataset.active = name;
  $$(".tabs [data-pane]").forEach(b => {
    b.setAttribute("aria-selected", String(b.dataset.pane === name));
    if (b.dataset.pane === name) $(".badge-dot", b)?.setAttribute("hidden", "");
  });
}
function flagPane(name) {
  if (!narrow.matches || $("#app").dataset.active === name) return;
  $(`.tabs [data-pane="${name}"] .badge-dot`)?.removeAttribute("hidden");
}

// ---------- viewer ----------
function showAsset(asset) {
  const viewer = $("#viewer");
  viewer.innerHTML = "";
  if (!asset) { viewer.append(el("p", { class: "empty", text: "No figure for this answer." })); $("#viewer-meta").innerHTML = ""; return; }
  const img = el("img", { src: asset.image_url, alt: asset.caption, onclick: () => openLightbox(asset) });
  viewer.append(img);
  const meta = $("#viewer-meta");
  meta.innerHTML = "";
  meta.append(el("b", { text: asset.caption }),
    el("span", { text: `${asset.asset_type === "cad" ? "Drawing" : "Manual"} · p.${asset.page} · ` }),
    el("a", { href: asset.pdf_url, target: "_blank", rel: "noopener", text: "Open in manual ↗" }));
  $$("#thumbs .thumb").forEach(t => t.setAttribute("aria-current", String(t.dataset.id === asset.id)));
}

function renderThumbs(list) {
  const box = $("#thumbs");
  box.innerHTML = "";
  for (const a of list) {
    box.append(el("button", { class: "thumb", type: "button", "data-id": a.id, title: a.caption, onclick: () => showAsset(a) },
      el("img", { src: a.thumb_url, alt: "" }), el("span", { text: a.caption })));
  }
}

function setView(view) {
  state.view = view;
  $$(".seg [data-view]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.view === view)));
  const list = view === "figures" ? state.figures : state.drawings;
  renderThumbs(list);
  showAsset(list[0] || null);
  if (!list.length) $("#viewer").innerHTML = `<p class="empty">${view === "figures"
    ? "Ask a question and the most relevant manual figure appears here." : "No drawings for this equipment."}</p>`;
}

function showFigures(figures) {
  // merge: newest answer's figures first, keep earlier ones from this job after them
  const seen = new Set(figures.map(f => f.id));
  state.figures = [...figures, ...state.figures.filter(f => !seen.has(f.id))];
  if (state.view !== "figures") setView("figures");
  renderThumbs(state.figures);
  showAsset(state.figures[0] || null);
  if (figures.length) flagPane("diagram");
}

async function loadDrawings() {
  const resp = await api(`/api/sets/${encodeURIComponent(state.setId)}/assets?type=cad`);
  state.drawings = resp.ok ? await resp.json() : [];
}

// ---------- lightbox with zoom/pan ----------
const lb = { scale: 1, x: 0, y: 0, pointers: new Map(), startDist: 0, startScale: 1 };
function applyLb() { $("#lightbox img").style.transform = `translate(${lb.x}px, ${lb.y}px) scale(${lb.scale})`; }
function openLightbox(asset) {
  Object.assign(lb, { scale: 1, x: 0, y: 0 });
  const img = $("#lightbox img");
  img.src = asset.image_url;
  img.alt = asset.caption;
  applyLb();
  $("#lightbox").hidden = false;
}
function bindLightbox() {
  const box = $("#lightbox"), stage = $(".lb-stage", box);
  const close = () => { box.hidden = true; };
  $(".lb-close", box).addEventListener("click", close);
  document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  stage.addEventListener("wheel", e => {
    e.preventDefault();
    lb.scale = Math.min(8, Math.max(1, lb.scale * Math.pow(1.0015, -e.deltaY)));
    applyLb();
  }, { passive: false });
  stage.addEventListener("dblclick", () => { Object.assign(lb, { scale: 1, x: 0, y: 0 }); applyLb(); });
  stage.addEventListener("pointerdown", e => { stage.setPointerCapture(e.pointerId); lb.pointers.set(e.pointerId, e); if (lb.pointers.size === 2) { const [a, b] = [...lb.pointers.values()]; lb.startDist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); lb.startScale = lb.scale; } });
  stage.addEventListener("pointermove", e => {
    const prev = lb.pointers.get(e.pointerId);
    if (!prev) return;
    lb.pointers.set(e.pointerId, e);
    if (lb.pointers.size === 2) {
      const [a, b] = [...lb.pointers.values()];
      lb.scale = Math.min(8, Math.max(1, lb.startScale * Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) / lb.startDist));
    } else { lb.x += e.clientX - prev.clientX; lb.y += e.clientY - prev.clientY; }
    applyLb();
  });
  const up = e => lb.pointers.delete(e.pointerId);
  stage.addEventListener("pointerup", up);
  stage.addEventListener("pointercancel", up);
}

// ---------- tools ----------
function toolsSkeleton() {
  const box = $("#tools");
  box.innerHTML = "";
  const grid = el("div", { class: "tool-grid" });
  for (let i = 0; i < 6; i++) grid.append(el("div", { class: "skeleton" }));
  box.append(grid);
}

function sourceChip(t) {
  if (t.source === "manual") return el("span", { class: "src manual", text: `Manual${t.source_ref ? " " + t.source_ref : ""}` });
  if (t.source === "web" && t.source_ref && t.source_ref.startsWith("https://"))
    return el("a", { class: "src", href: t.source_ref, target: "_blank", rel: "noopener noreferrer", text: "Web ↗" });
  return el("span", { class: "src", text: t.source === "web" ? "Web" : "General knowledge" });
}

function renderTools(groups, figures) {
  const byId = Object.fromEntries((figures || []).map(f => [f.id, f]));
  const box = $("#tools");
  box.innerHTML = "";
  if (!groups.length) { box.append(el("p", { class: "empty", text: "No specific tools identified for this question." })); return; }
  for (const g of groups) {
    const grid = el("div", { class: "tool-grid" });
    for (const t of g.tools) {
      const fig = t.figure_id && byId[t.figure_id];
      const ico = el("div", { class: "ico" }, fig ? el("img", { src: fig.thumb_url, alt: "" }) : iconSvg(t.icon));
      const card = el(fig ? "button" : "div", { class: `tool${fig ? " has-figure" : ""}`, type: fig ? "button" : null,
        title: fig ? "Show figure" : null, onclick: fig ? () => { showFigures([fig]); activatePane("diagram"); } : null },
        ico, el("b", { text: t.name }), t.purpose ? el("span", { class: "purpose", text: t.purpose }) : null,
        t.spec ? el("span", { class: "spec", text: t.spec }) : null, sourceChip(t));
      grid.append(card);
    }
    box.append(el("section", { class: "tool-group" }, el("h3", { text: GROUP_LABELS[g.category] || g.category }), grid));
  }
  flagPane("tools");
}

function toolsError() {
  const box = $("#tools");
  box.innerHTML = "";
  const retry = el("button", { class: "btn btn-primary", type: "button", text: "Retry", onclick: async () => {
    toolsSkeleton();
    const resp = await api("/api/tools/retry", { conversation_id: state.conversationId });
    if (resp.ok) { const j = await resp.json(); renderTools(j.groups, j.figures); } else toolsError();
  } });
  box.append(el("p", { class: "tools-error", text: "Couldn't load tools." }), retry);
}

// ---------- chat ----------
function markdown(text) {
  const withCites = text.replace(CITE_RE, (_, p) => `<button class="cite" type="button" data-page="${p}">p.${p}</button>`);
  return DOMPurify.sanitize(marked.parse(withCites), { ADD_ATTR: ["data-page"] });
}

function addMessage(role, text) {
  $("#starter").hidden = true;
  const node = el("div", { class: `msg ${role}` });
  if (role === "user") node.textContent = text;
  else node.append(el("div", { class: "md typing" }));
  $("#chat").append(node);
  node.scrollIntoView({ block: "end" });
  return node;
}

function onCite(page) {
  const fig = state.figures.find(f => f.page === page);
  if (fig) { showAsset(fig); activatePane("diagram"); return; }
  const cite = state.citations.find(c => c.page === page && c.doc_id);
  if (cite) window.open(`/content-docs/${cite.doc_id}.pdf#page=${page}`, "_blank", "noopener");
}

async function* sseEvents(resp) {
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (ev && data) yield [ev, JSON.parse(data)];
    }
  }
}

function setBusy(busy) {
  state.busy = busy;
  $("#send").disabled = busy;
  $("#input").disabled = busy;
  $$(".chip").forEach(c => { c.disabled = busy; });
}

async function ask(message) {
  if (state.busy || !message.trim()) return;
  setBusy(true);
  activatePane("steps");
  addMessage("user", message);
  const bubble = addMessage("assistant");
  const md = $(".md", bubble);
  const firstTurn = !state.conversationId;
  if (firstTurn) toolsSkeleton();
  let text = "", frame = 0;
  const paint = () => { frame = 0; md.innerHTML = markdown(text); bubble.scrollIntoView({ block: "end" }); };
  try {
    const resp = await api("/api/chat", { set_id: state.setId, conversation_id: state.conversationId, message });
    if (resp.status === 401) { location.reload(); return; }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    for await (const [ev, data] of sseEvents(resp)) {
      if (ev === "meta") {
        state.conversationId = data.conversation_id;
        if (data.remaining_today !== null && state.me && !state.me.is_admin) updateQuota({ ...state.me, remaining: data.remaining_today });
      } else if (ev === "token") {
        md.classList.remove("typing");
        text += data.text;
        if (!frame) frame = requestAnimationFrame(paint);
      } else if (ev === "assets") {
        state.citations = data.citations;
        showFigures(data.figures);
      } else if (ev === "tools") {
        if (data.error) toolsError(); else renderTools(data.groups, data.figures);
      } else if (ev === "error") {
        md.classList.remove("typing");
        bubble.classList.add("error");
        md.textContent = data.message;
        if (data.code === "quota_exceeded" && state.me) updateQuota({ ...state.me, remaining: 0 });
        if (firstTurn) $("#tools").innerHTML = '<p class="empty">The tools for the job appear here after your first question.</p>';
      }
    }
    if (text) { cancelAnimationFrame(frame); paint(); }
  } catch (err) {
    md.classList.remove("typing");
    bubble.classList.add("error");
    md.textContent = "Connection problem. Please try again.";
  } finally {
    setBusy(false);
    $("#input").focus();
  }
}

function newJob() {
  state.conversationId = null;
  state.figures = [];
  state.citations = [];
  $$("#chat .msg").forEach(m => m.remove());
  $("#starter").hidden = false;
  $("#tools").innerHTML = '<p class="empty">The tools for the job appear here after your first question.</p>';
  setView(state.view === "drawings" ? "drawings" : "figures");
}

function bindWorkspace() {
  $("#composer").addEventListener("submit", e => {
    e.preventDefault();
    const input = $("#input");
    const msg = input.value;
    input.value = "";
    input.style.height = "";
    ask(msg);
  });
  $("#input").addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
  });
  $("#input").addEventListener("input", e => { e.target.style.height = "auto"; e.target.style.height = `${e.target.scrollHeight}px`; });
  $$(".chip").forEach(c => c.addEventListener("click", () => ask(c.textContent)));
  $("#chat").addEventListener("click", e => { const b = e.target.closest(".cite"); if (b) onCite(Number(b.dataset.page)); });
  $("#new-job").addEventListener("click", newJob);
  $$(".tabs [data-pane]").forEach(b => b.addEventListener("click", () => activatePane(b.dataset.pane)));
  $$(".seg [data-view]").forEach(b => b.addEventListener("click", () => setView(b.dataset.view)));
  bindLightbox();
}

document.addEventListener("onesi:ready", bindWorkspace, { once: true });
document.addEventListener("onesi:set-changed", async () => { await loadDrawings(); newJob(); });
```

Note: `startApp` dispatches `onesi:set-changed` (via `selectSet`) before `onesi:ready`. Both listeners are registered at module load, so the order is fine: drawings load, then the workspace binds.

- [ ] **Step 2: Drive the full flow in the browser**

With the server running and a logged-in session from Task 17, at 1440×900:
1. Click the chip "How do I install the crosshead?". Expect the answer to stream into the center pane with a **Safety** line, `### 1. …` phases and `p.88` citation buttons.
2. The left pane shows a crosshead figure with caption, "Manual · p.88 · Open in manual ↗" and thumbnails.
3. The right pane goes from skeleton to grouped tool cards. OEM tools with figures show a thumbnail, and clicking one shows that figure on the left.
4. Click a `p.88` citation and the left pane jumps to the figure on p.88. Click "Open in manual ↗" and the PDF opens at the page.
5. Ask the follow-up "what torque for the crosshead pin nut?". The answer is shorter and references Appendix A or says it's not in the excerpts. The tools pane does **not** reset. The quota pill decrements.
6. Click "Drawings" and the two CAD sheets appear as thumbnails. Click one, then click the image: the lightbox opens and wheel zoom, drag pan and Esc work.
7. "New job" clears the chat and tools.
8. At 390×844, ask a question. The Steps tab streams the answer, and the Diagram and Tools tabs show orange dots that clear when opened.

Take screenshots at each numbered point and inspect them. Fix layout problems before committing: overflowing panes, unreadable text, broken images.

- [ ] **Step 3: Commit**

```bash
git add web/assets/demo.js
git commit -m "feat(web): streaming chat, figure/drawing viewer with zoom, tools cards, mobile tabs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 19: Admin page and site links

**Files:**
- Create: `web/admin.html`, `web/assets/admin.js`
- Modify: `web/prototype.html` (Access section + early-access form), `web/assets/script.js` (form submit handler)
- Test: extend `tests/test_pages.py`

**Interfaces:**
- Consumes: `/api/admin/*` (Task 13), `/api/access/request` (Task 12).
- Produces: `/admin` UI; the prototype page links to `/demo`; the early-access form creates access requests.

- [ ] **Step 1: Extend the page test (failing)**

Append to `tests/test_pages.py`:
```python
def test_admin_page_and_prototype_link(make_client):
    c = make_client()
    admin = c.get("/admin")
    assert admin.status_code == 200 and "assets/admin.js" in admin.text
    proto = c.get("/prototype.html").text
    assert 'href="/demo"' in proto and "Prototype Coming Soon" not in proto
    assert "data-access-form" in proto
```

Run: `uv run pytest tests/test_pages.py -v`. Expected: FAIL.

- [ ] **Step 2: Write `web/admin.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex">
<title>Onesi Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="assets/style.css">
<link rel="stylesheet" href="assets/demo.css">
<style>
  .admin { max-width: 1100px; margin: 0 auto; padding: 20px 16px; width: 100%; }
  .stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  .stat { background: var(--white); border: 1px solid var(--line); border-radius: 10px; padding: 10px 16px; }
  .stat b { display: block; font-family: var(--font-head); font-size: 20px; }
  .stat span { font-size: 12px; color: var(--steel); }
  .admin .seg { margin-bottom: 12px; background: var(--white); }
  .table-wrap { overflow-x: auto; background: var(--white); border: 1px solid var(--line); border-radius: 10px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { font-size: 12px; color: var(--steel); font-weight: 600; }
  td .note { color: var(--steel); font-size: 13px; }
  td .err { color: #b42318; font-size: 12px; }
  .actions { display: flex; gap: 6px; flex-wrap: wrap; }
  .actions button { font: inherit; font-size: 12.5px; padding: 5px 10px; border-radius: 6px; border: 1px solid var(--line); background: var(--white); cursor: pointer; }
  .actions button.primary { background: var(--navy); color: var(--white); border-color: var(--navy); }
  .limit { width: 64px; padding: 4px 6px; font: inherit; }
  dialog { max-width: 760px; width: 92vw; border: 1px solid var(--line); border-radius: 12px; }
  dialog .turn { border-bottom: 1px solid var(--line); padding: 8px 0; font-size: 14px; white-space: pre-wrap; }
</style>
</head>
<body class="demo-body">
<header class="app-bar">
  <a href="index.html" class="brand">ONESI<span class="dot">.</span></a><span class="who">Admin</span>
  <div class="app-bar-right"><a class="btn-link" href="/demo">Open demo</a><button id="logout" class="btn-link" type="button" hidden>Sign out</button></div>
</header>

<main class="gate" id="login-view" hidden>
  <section class="gate-card">
    <h1>Admin sign in</h1>
    <form id="login-form">
      <label>Password<input name="password" type="password" required autocomplete="current-password"></label>
      <p class="form-error" role="alert" hidden></p>
      <button class="btn btn-primary" type="submit">Sign in</button>
    </form>
  </section>
</main>

<main class="admin" id="admin-view" hidden>
  <div class="stats" id="stats"></div>
  <div class="seg" id="status-tabs">
    <button type="button" data-status="pending" aria-pressed="true">Pending</button>
    <button type="button" data-status="approved" aria-pressed="false">Approved</button>
    <button type="button" data-status="closed" aria-pressed="false">Rejected / revoked</button>
  </div>
  <div class="table-wrap"><table><thead id="thead"></thead><tbody id="rows"></tbody></table></div>
</main>

<dialog id="conv-dialog"><form method="dialog" style="text-align:right"><button class="btn-link">Close</button></form><div id="conv-body"></div></dialog>
<script type="module" src="assets/admin.js"></script>
</body>
</html>
```

- [ ] **Step 3: Write `web/assets/admin.js`**

```js
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let status = "pending";

async function api(path, { method = "GET", body } = {}) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  return fetch(path, opts);
}

function cell(text, cls) { const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = text ?? ""; return td; }
function button(label, onClick, primary = false) {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = label; if (primary) b.className = "primary";
  b.addEventListener("click", onClick);
  return b;
}
const when = iso => (iso ? new Date(iso).toLocaleString() : "—");

async function act(email, action) {
  const resp = await api(`/api/admin/requests/${encodeURIComponent(email)}/${action}`, { method: "POST" });
  if (!resp.ok) alert(`Failed: ${resp.status}`);
  await refresh();
}

async function showConversations(email) {
  const convs = await (await api(`/api/admin/requests/${encodeURIComponent(email)}/conversations`)).json();
  const body = $("#conv-body");
  body.innerHTML = "";
  const h = document.createElement("h3"); h.textContent = `${email}: ${convs.length} conversations`; body.append(h);
  for (const c of convs) for (const m of c.messages) {
    const div = document.createElement("div");
    div.className = "turn";
    div.textContent = `${m.role === "user" ? "Q" : "A"} · ${when(m.created_at)}\n${m.content}`;
    body.append(div);
  }
  $("#conv-dialog").showModal();
}

function renderRows(rows) {
  const head = $("#thead"), tbody = $("#rows");
  const cols = status === "approved" ? ["Who", "Today", "Total", "Last active", "Daily limit", ""] : ["Who", "Note", "Requested", ""];
  head.innerHTML = "";
  const tr = document.createElement("tr");
  cols.forEach(c => { const th = document.createElement("th"); th.textContent = c; tr.append(th); });
  head.append(tr);
  tbody.innerHTML = "";
  if (!rows.length) { const r = document.createElement("tr"); r.append(cell("Nothing here.")); tbody.append(r); return; }
  for (const r of rows) {
    const row = document.createElement("tr");
    const who = cell(`${r.name} · ${r.company}`);
    const email = document.createElement("div"); email.className = "note"; email.textContent = r.email; who.append(email);
    if (r.email_error) { const e = document.createElement("div"); e.className = "err"; e.textContent = r.email_error; who.append(e); }
    row.append(who);
    const actions = document.createElement("td");
    const box = document.createElement("div"); box.className = "actions"; actions.append(box);
    if (status === "pending") {
      row.append(cell(r.note, "note"), cell(when(r.created_at)));
      box.append(button("Approve", () => act(r.email, "approve"), true), button("Reject", () => act(r.email, "reject")));
    } else if (status === "approved") {
      const limit = document.createElement("input");
      limit.type = "number"; limit.min = "0"; limit.className = "limit"; limit.placeholder = "default";
      limit.value = r.daily_limit ?? "";
      limit.addEventListener("change", async () => {
        await api(`/api/admin/requests/${encodeURIComponent(r.email)}`, { method: "PATCH",
          body: { daily_limit: limit.value === "" ? null : Number(limit.value) } });
        refresh();
      });
      const limitCell = document.createElement("td"); limitCell.append(limit);
      row.append(cell(String(r.questions_today)), cell(String(r.questions_total)), cell(when(r.last_active)), limitCell);
      box.append(button("Conversations", () => showConversations(r.email)), button("Resend code", () => act(r.email, "resend-code")),
        button("Reset today", () => act(r.email, "reset-quota")), button("Revoke", () => act(r.email, "revoke")));
    } else {
      row.append(cell(r.status, "note"), cell(when(r.decided_at)));
      box.append(button("Restore to pending", () => act(r.email, "restore")));
    }
    row.append(actions);
    tbody.append(row);
  }
}

async function refresh() {
  const [rowsResp, statsResp] = await Promise.all([api(`/api/admin/requests?status=${status}`), api("/api/admin/stats")]);
  if (rowsResp.status === 401) { showLogin(); return; }
  renderRows(await rowsResp.json());
  const s = await statsResp.json();
  $("#stats").innerHTML = "";
  for (const [value, label] of [[s.pending, "pending requests"], [`${s.questions_today} / ${s.global_limit}`, "questions today"],
    [`$${s.cost_today_usd.toFixed(2)}`, "estimated spend today"]]) {
    const d = document.createElement("div"); d.className = "stat";
    const b = document.createElement("b"); b.textContent = value;
    const span = document.createElement("span"); span.textContent = label;
    d.append(b, span); $("#stats").append(d);
  }
}

function showLogin() { $("#login-view").hidden = false; $("#admin-view").hidden = true; $("#logout").hidden = true; }
function showAdmin() { $("#login-view").hidden = true; $("#admin-view").hidden = false; $("#logout").hidden = false; refresh(); }

$("#login-form").addEventListener("submit", async e => {
  e.preventDefault();
  const resp = await api("/api/admin/login", { method: "POST", body: { password: e.currentTarget.password.value } });
  const err = $(".form-error", e.currentTarget);
  if (resp.ok) { err.hidden = true; showAdmin(); }
  else { err.textContent = (await resp.json()).detail || "Sign-in failed."; err.hidden = false; }
});
$("#logout").addEventListener("click", async () => { await api("/api/admin/logout", { method: "POST", body: {} }); showLogin(); });
$$("#status-tabs [data-status]").forEach(b => b.addEventListener("click", () => {
  status = b.dataset.status;
  $$("#status-tabs [data-status]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  refresh();
}));

(async () => { (await api("/api/admin/stats")).ok ? showAdmin() : showLogin(); })();
```

- [ ] **Step 4: Point the prototype page at the demo and wire its form**

In `web/prototype.html`, replace this block:
```html
        <h2>Prototype coming soon.</h2>
        <p>The live prototype application is not yet connected to this page. Once available, this section will link directly to the working application.</p>
        <a class="btn btn-outline" style="opacity:.6; cursor:not-allowed;">Prototype Coming Soon</a>
```
with:
```html
        <h2>Try the live demo.</h2>
        <p>The demo runs on a real Ariel KBE compressor manual set. Access is by invitation: request it here or on the demo page and we'll email you a code.</p>
        <a class="btn btn-primary" href="/demo">Try the demo</a>
```
In the same file, change `<form data-form>` (the early-access form) to `<form data-form data-access-form>`, and change the success text `You're on the list. We'll be in touch when early access opens.` to `Request received. We'll email you an access code once approved.`

In `web/assets/script.js`, replace the form handler block (from `// Demo request / early access / contact forms` through its closing `});`) with:
```js
  // Demo request / early access / contact forms.
  // Forms marked data-access-form create a real demo access request; others keep placeholder behaviour.
  document.querySelectorAll('form[data-form]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var success = form.parentElement.querySelector('.form-success') || form.querySelector('.form-success');
      var btn = form.querySelector('button[type="submit"]');
      function done() {
        form.querySelectorAll('input, textarea, select').forEach(function (f) { f.disabled = true; });
        if (btn) { btn.disabled = true; btn.textContent = 'Submitted'; }
        if (success) success.classList.add('show');
      }
      if (!form.hasAttribute('data-access-form')) { done(); return; }
      var data = new FormData(form);
      fetch('/api/access/request', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: data.get('email'), name: data.get('name'), company: data.get('company'),
                               note: data.get('title') ? 'Job title: ' + data.get('title') : '' })
      }).then(function (r) {
        if (r.ok) { done(); } else if (btn) { btn.textContent = 'Please check the form and try again'; }
      }).catch(function () { if (btn) { btn.textContent = 'Network error, try again'; } });
    });
  });
```

- [ ] **Step 5: Run the test and verify in the browser**

Run: `uv run pytest tests/test_pages.py -v`. Expected: 2 passed.

In the browser:
1. `http://localhost:8000/prototype.html` shows "Try the demo". Submit the early-access form with a new email and it shows "Request received…".
2. `http://localhost:8000/admin`: sign in with the `.env` `ADMIN_PASSWORD`. The stats show 1+ pending, and the new request is listed.
3. Approve it. The server terminal prints the access-code email. The row moves to Approved.
4. In a private window, redeem that code on `/demo`. Ask one question. Back in admin, the Approved tab shows "Today 1" and Conversations shows the Q/A.
5. Set the daily limit to 1, then try to ask again in the demo: "You've used today's questions…". Reset today and asking works again.
6. Revoke. The demo session gets 401 and reloads to the gate.

Screenshot the admin page and check it at 390px width (the table scrolls horizontally).

- [ ] **Step 6: Commit**

```bash
git add web/admin.html web/assets/admin.js web/prototype.html web/assets/script.js tests/test_pages.py
git commit -m "feat(web): admin console; prototype page links to the demo and files access requests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
## Phase 6 — Quality check and deployment

### Task 20: Answer-quality eval and full end-to-end pass

**Files:**
- Create: `scripts/eval_answers.py`

**Interfaces:**
- Consumes: `Retriever`, `stream_answer`, `ChatContext`, `make_client`, `estimate_cost`, `tests/golden/retrieval.yaml`.
- Produces: a printed report with citations present, expected figure chosen, unsupported numeric specs and total cost.

- [ ] **Step 1: Write the eval script**

`scripts/eval_answers.py`:
```python
"""Answer-quality eval against the real Claude API (~$0.30 per run with Haiku 4.5).

Usage: uv run python scripts/eval_answers.py [--model claude-sonnet-5-5]
Checks per golden question: answer cites at least one page; the expected figure (if any) is among the
chosen figure_ids; every number-with-unit in the answer appears in the retrieved excerpt text.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import get_settings  # noqa: E402
from backend.embeddings import get_embedder  # noqa: E402
from backend.llm.chat import ChatContext, stream_answer  # noqa: E402
from backend.llm.client import make_client  # noqa: E402
from backend.llm.common import estimate_cost  # noqa: E402
from backend.retrieval.search import Retriever  # noqa: E402

SET_ID = "ariel-kbe"
NUM_UNIT = re.compile(r"(\d+(?:[.,/]\d+)?)\s?(?:ft[·\-\s]?lbs?|lb[·\-\s]?ft|N·m|Nm|in\.|inch(?:es)?|mm|psi[ag]?|°F|°C|%)",
                      re.I)


async def run(model: str) -> int:
    s = get_settings()
    retriever = Retriever.open(s.content_dir, get_embedder(s.embed_cache_dir))
    client = make_client(s)
    set_info = retriever.get_set(SET_ID)
    cases = yaml.safe_load((Path(__file__).resolve().parents[1] / "tests/golden/retrieval.yaml").read_text())
    total, failures = 0.0, 0
    for case in cases:
        q = case["query"]
        chunks = retriever.search_chunks(SET_ID, q)
        cands = retriever.figure_candidates(SET_ID, q, chunks)
        ctx = ChatContext(equipment=set_info["equipment"], chunks=chunks, candidates=cands, history=[], question=q,
                          resolve=lambda n: retriever.resolve_figure(SET_ID, n))
        text, assets = "", {"figure_ids": [], "citations": []}
        async for kind, payload in stream_answer(client, model, ctx):
            if kind == "assets":
                assets = payload
            elif kind == "final":
                text = payload["text"]
                total += estimate_cost(model, payload["usage"])
        evidence = " ".join(c.text for c in chunks)
        unsupported = sorted({m.group(0) for m in NUM_UNIT.finditer(text) if m.group(1) not in evidence})
        cited = bool(assets["citations"])
        fig_ok = case["figure"] in assets["figure_ids"] if "figure" in case else True
        ok = cited and fig_ok and not unsupported
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {q}\n      cited={cited} figure_ok={fig_ok} figures={assets['figure_ids']}"
              + (f"\n      unsupported specs: {unsupported}" if unsupported else ""))
    print(f"\n{len(cases) - failures}/{len(cases)} passed · model={model} · cost=${total:.3f}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=get_settings().chat_model)
    raise SystemExit(asyncio.run(run(ap.parse_args().model)))
```

- [ ] **Step 2: Run the full test suite, including integration tests**

Run: `uv run pytest -q`
Expected: all passed (the integration tests use the committed `content/` and the cached embedding model).

- [ ] **Step 3: Run the eval with Haiku and read every answer that fails**

Run: `uv run python scripts/eval_answers.py`
Expected: a report ending `N/15 passed · model=claude-haiku-4-5 · cost=$0.0x`.

For each FAIL, decide which kind it is:
- **unsupported spec**: the answer invented a number. Tighten rule 2 in `CHAT_SYSTEM` and rerun.
- **figure missed**: check whether the expected figure was in the candidates (retrieval) or the model chose a different reasonable one (acceptable).
- **no citation**: tighten rule 1.

Prompt changes go in `backend/llm/prompts.py` only. Re-run `uv run pytest tests/test_llm_prompts.py` after editing.

- [ ] **Step 4: Report to the user**

Summarise the pass count, cost per run, notable failures and any prompt changes. If quality on procedures looks thin, run `uv run python scripts/eval_answers.py --model claude-sonnet-5-5` once (~$0.60) and include the comparison. The decision to set `CHAT_MODEL=claude-sonnet-5-5` is the user's.

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_answers.py backend/llm/prompts.py
git commit -m "test: answer-quality eval script; prompt refinements from eval

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 21: Docker image, Railway config, README

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `railway.json`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: the whole app. Start command `uvicorn backend.api.app:create_app --factory`.
- Produces: a container that serves the app on `$PORT` with `DATA_DIR=/app/data` (volume) and `CONTENT_DIR=/app/content` (baked in).

- [ ] **Step 1: Write the container files**

`Dockerfile`:
```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/app/data \
    CONTENT_DIR=/app/content \
    WEB_DIR=/app/web \
    EMBED_CACHE_DIR=/app/.cache/fastembed

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend ./backend
# Download the embedding model at build time so cold starts don't fetch it.
RUN python -c "from backend.embeddings import FastEmbedder; FastEmbedder('/app/.cache/fastembed')"

COPY web ./web
COPY content ./content

EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.api.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
```

`.dockerignore`:
```
.git
.venv
.cache
.env
data
corpus
resources
docs
tests
scripts
**/__pycache__
**/.DS_Store
content/review
```

`docker-compose.yml`:
```yaml
services:
  onesi:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    volumes:
      - ./data:/app/data
```

`railway.json`:
```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": { "builder": "DOCKERFILE", "dockerfilePath": "Dockerfile" },
  "deploy": { "healthcheckPath": "/healthz", "healthcheckTimeout": 120, "restartPolicyType": "ON_FAILURE" }
}
```

`.env.example`:
```
ANTHROPIC_API_KEY=
ADMIN_PASSWORD=
ADMIN_EMAIL=
SESSION_SECRET=            # long random string in production: python -c "import secrets;print(secrets.token_urlsafe(48))"
PUBLIC_BASE_URL=http://localhost:8000
RESEND_API_KEY=            # empty = emails print to the server console
EMAIL_FROM=Onesi <access@your-domain.com>
DAILY_QUESTION_LIMIT=10
GLOBAL_DAILY_LIMIT=500
TOOLS_WEB_SEARCH=0
CHAT_MODEL=claude-haiku-4-5
TOOLS_MODEL=claude-haiku-4-5
REWRITE_MODEL=claude-haiku-4-5
VISION_MODEL=claude-haiku-4-5
```

- [ ] **Step 2: Write `README.md`**

````markdown
# Onesi: AI maintenance companion (demo)

A technician picks equipment, asks a maintenance question, and gets three panes:
- **Steps:** grounded, cited procedure from the OEM manuals, with follow-ups.
- **Diagram:** the manual figure or CAD drawing that goes with it.
- **Tools:** the tools for the job.

Design: `docs/superpowers/specs/2026-10-01-onesi-rag-demo-design.md`.

## Run locally

```bash
cp .env.example .env            # set ANTHROPIC_API_KEY and ADMIN_PASSWORD
uv sync
uv run uvicorn backend.api.app:create_app --factory --reload
```
Open http://localhost:8000/demo. Approve yourself at http://localhost:8000/admin. Without `RESEND_API_KEY`, the access-code email prints in the server terminal.

## Tests

```bash
uv run pytest -m "not integration"   # fast, no network
uv run pytest                        # + real embedding model and committed content
uv run python scripts/eval_answers.py  # answer quality against the real API (~$0.30)
```

## Add or update a manual set

1. Create `corpus/<set-id>/manifest.yaml` (copy `corpus/ariel-kbe/manifest.yaml`) and put PDFs under `docs/` and `cad/`.
2. `uv run python -m backend.ingest <set-id>` (add `--force` to re-process unchanged files).
3. Open `content/review/<set-id>.html`; fix any flagged crop in `corpus/<set-id>/overrides.yaml` and re-run.
4. Commit `content/` and the corpus, then push. The deploy picks it up.

## Deploy (Railway)

1. Push this repo to GitHub. In Railway: **New Project → Deploy from GitHub repo**. It builds the `Dockerfile`.
2. **Add a Volume** to the service, mounted at `/app/data` (holds `app.db`: access requests, sessions, usage).
3. **Variables:** `ANTHROPIC_API_KEY`, `ADMIN_PASSWORD`, `ADMIN_EMAIL`, `SESSION_SECRET` (random), `PUBLIC_BASE_URL` (the Railway URL, `https://…`), and optionally `RESEND_API_KEY` and `EMAIL_FROM` (needs a domain verified in Resend).
4. **Settings → Networking → Generate Domain.** Set `PUBLIC_BASE_URL` to it and redeploy.
5. Check `https://<domain>/healthz` returns `{"ok": true, ...}`. Then sign in at `/admin`.

The app refuses to start over https if `SESSION_SECRET`, `ADMIN_PASSWORD` or `ANTHROPIC_API_KEY` is missing.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_MODEL` / `TOOLS_MODEL` / `REWRITE_MODEL` / `VISION_MODEL` | `claude-haiku-4-5` | model per call |
| `TOOLS_WEB_SEARCH` | `0` | allow one web search in the tools call |
| `DAILY_QUESTION_LIMIT` | `10` | per user, per UTC day |
| `GLOBAL_DAILY_LIMIT` | `500` | all users, per UTC day (spend cap) |
| `DATA_DIR` / `CONTENT_DIR` | `data` / `content` | runtime DB / ingested content |
````

- [ ] **Step 3: Build and run the container locally**

```bash
docker compose up --build -d
sleep 5 && curl -s localhost:8000/healthz
```
Expected: `{"ok":true,"content":true,"app_db":true}`.

Then in the browser at `http://localhost:8000/demo`: sign in via `/admin` (password from `.env`), ask one question, and confirm the answer, figure and tools render. Stop with `docker compose down`. `./data/app.db` persists.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.yml railway.json .env.example README.md
git commit -m "chore: Docker image, Railway config, README with run/ingest/deploy steps

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hand over deployment to the user**

Deploying creates a public URL and uses the user's Railway account and API key, so stop here and ask the user before taking any outward-facing step. Give them the README "Deploy (Railway)" steps. Remind them that a Resend-verified domain is needed before real visitors can receive codes. Until then, codes for approved users can be read from the Railway logs.
