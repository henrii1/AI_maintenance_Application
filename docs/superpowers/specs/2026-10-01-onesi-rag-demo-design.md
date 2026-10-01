# Onesi RAG Demo — Design Spec

**Date:** 2026-10-01
**Status:** Draft for review
**Scope:** RAG backend, ingestion pipeline, gated demo page, and admin page for the Onesi AI maintenance companion, demonstrated on one equipment set (Ariel KBE).

---

## 1. Goal

A field technician asks a maintenance question about a piece of equipment and gets, in one three-pane screen:

- **Center:** a grounded, step-by-step procedure with page citations, plus follow-up chat.
- **Left:** the most relevant figure from the manual, or a CAD drawing.
- **Right:** a list of the tools needed for the job.

The answers come only from the OEM documentation connected to that equipment. The visual reference is `resources/WhatsApp Image 2026-09-19 at 23.39.50.jpeg`.

### Success criteria (demo)

- "How do I install the crosshead?" returns ordered steps citing manual p.87–88. The left pane shows `Figure: Crosshead Installation`, and the tools pane lists the crosshead installation/alignment tools from the manual.
- Every spec value (torque, clearance) in an answer is traceable to a cited page. If the documentation doesn't contain a spec, the answer says so instead of guessing.
- The demo is live on a public URL, gated behind admin-approved access, with a hard cap on API spend.
- Adding a second equipment set means adding a folder and running one command, with no code changes.

### Non-goals (v1)

- Table extraction. Tables stay as raw extracted text and are visible in the PDFs.
- SMS delivery of codes.
- Agentic "deep search" mode. It's planned as a later addition reusing the retrieval functions as tools (§6.6).
- Multi-tenant orgs, user-uploaded manuals, offline mode.

---

## 2. Source data findings

All findings come from inspecting `demo_data/` with PyMuPDF.

| File | What it is | Notes |
|---|---|---|
| `Ariel-KBE-Manual.pdf` | KBE Maintenance & Repair Manual, 154 pp | Clean TOC (Sections 1–5 + Appendix A). 85 captions, all unique. |
| `ER-063.pdf` | Fastener Torques for Reciprocating Compressors, 12 pp | Referenced by the manual. Contains figures (e.g. KBH crosshead torque sequence). |
| `ER-034.pdf` | VCI powder protection of non-lube cylinders for shipment, 2 pp | Applies to Ariel compressors generally. |
| `ER-129/140.pdf` | Frame preservation (mineral/PAO/ester; PAG oil) | Same structure, different oil compatibility. |
| `ER-130/141.pdf` | Cylinder & distance piece preservation (mineral/PAO/ester; PAG) | Same structure, different oil compatibility. |
| `cad/OL0511_S1.PDF` | Outline drawing: Guide Support Outline, H:E:ET class cylinders | Single vector sheet with a title block. |
| `cad/OL0782_S1.PDF` | Encoder Shaft Attachment Details (dimension table incl. KBE row) | Single vector sheet with a title block. |

**Figure conventions in the manual (they drive the extraction design):**

- Captions are written as `Figure: <Title>` and sit **above** the graphic. A numbered callout legend sits **below** it ("1 Crosshead Installation Tool  2 Metal Plate").
- Callout circles and leader lines are often **vector overlays** on top of the raster image, and some figures are vector-only (torque sequences, flowcharts). So we crop page regions, not embedded images.
- Body text cross-references figures by exact caption ("see Figure: Crosshead Alignment Tool").
- Edge cases seen:
  - two figures on one page (p.18);
  - two captions side by side on one text line (p.104);
  - a body-text sentence starting "Figure:" that is not a caption (p.55, long block).
- A throwaway spike confirmed that caption-anchored region cropping produces clean results on pp. 18, 88 and 127. Page 104 needs the split-caption and stop-at-body-text rules (§5.2).

---

## 3. Key decisions

| Decision | Choice | Why |
|---|---|---|
| Language | **Python 3.12 + FastAPI** | PDF processing (PyMuPDF) is the hard part and is best in Python. The site is static HTML/JS, so nothing favours TypeScript. |
| LLM | **Claude Haiku 4.5** (`claude-haiku-4-5`) for every call, no extended thinking | Cheapest current model. Supports vision, strict tools, streaming and web search. Model IDs are env-configurable per call. |
| Retrieval | **Hybrid:** SQLite FTS5 (BM25) + local embeddings, merged by reciprocal rank fusion | Exact part numbers ("A-18255", "VVCP") need lexical search, and loose phrasing needs semantic search. Scales to many manual sets. |
| Embeddings | **fastembed `BAAI/bge-small-en-v1.5`** (ONNX, 384-dim), stored in SQLite | No second vendor key, no torch, small Docker image. |
| Figure selection | The model picks from a **retrieved candidate list** via a strict tool with an enum of IDs. Fuzzy match is only a fallback. | The model cannot name a non-existent figure. |
| Storage | **SQLite**, split into `content.db` (built locally, shipped in the image) and `app.db` (runtime, on a volume) | Redeploys update content without touching user data. Ingestion (paid vision calls + human review) runs once locally. |
| Deploy | **Railway**, from the GitHub repo with a Dockerfile and one volume mounted at `/app/data` | Easiest git-push deploy with persistent disk. Portable to Render/Fly. |
| Email | **Resend**, with console fallback when no key is set | Simple API. Local testing needs no email setup. |
| Frontend | Vanilla HTML/CSS/JS, served by FastAPI alongside the existing Onesi site | Matches the existing site; no build step. |

---

## 4. Architecture

```
Browser (/demo, /admin, Onesi static pages)
        │  HTTPS (SSE for chat)
        ▼
FastAPI service (one container)
  ├── api/        routes: chat, assets, access, admin, static
  ├── auth/       access requests, codes, sessions, quotas
  ├── retrieval/  hybrid search + figure candidates   ──► content.db (read-only)
  ├── llm/        rewrite, chat, tools calls          ──► Anthropic API
  └── db.py       app.db (requests, sessions, usage, conversations)
                                                       ──► Resend (email)
Ingestion CLI (run locally) ──► content/ (content.db, figures/, cad/, review/)
```

### 4.1 Repo layout

```
AI_maintenance_Application/
├── corpus/
│   └── ariel-kbe/
│       ├── manifest.yaml
│       ├── docs/          Ariel-KBE-Manual.pdf, ER-034/063/129/130/140/141.pdf
│       ├── cad/           OL0511_S1.PDF, OL0782_S1.PDF
│       └── overrides.yaml # optional crop/rename/exclude fixes
├── backend/
│   ├── config.py          # env-driven settings
│   ├── ingest/            # __main__.py, manifest.py, chunker.py, figures.py, cad.py, describe.py, index.py, review.py
│   ├── retrieval/         # search.py (fts, vector, rrf), figures.py (candidates, fuzzy lookup)
│   ├── llm/               # client.py, rewrite.py, chat.py, tools.py, prompts/
│   ├── auth/              # requests.py, codes.py, sessions.py, quota.py, email.py
│   ├── api/               # app.py, chat.py, assets.py, access.py, admin.py
│   └── db.py              # schema + migrations for app.db
├── content/               # ingestion output, COMMITTED: content.db, figures/, cad/, review/
├── web/                   # Onesi site moved from resources/onesi-site (1)/, plus demo.html, admin.html, assets/demo.js, assets/admin.js, assets/icons/
├── data/                  # runtime volume (gitignored): app.db
├── tests/
├── Dockerfile
└── pyproject.toml
```

`demo_data/` is moved into `corpus/ariel-kbe/`. `resources/` keeps the mockup and pitch deck. The site code moves to `web/`.

### 4.2 Manual set manifest

```yaml
# corpus/ariel-kbe/manifest.yaml
id: ariel-kbe
equipment:
  name: Ariel KBE Reciprocating Compressor
  manufacturer: Ariel Corporation
  frame_class: KBE
documents:
  - file: docs/Ariel-KBE-Manual.pdf
    role: manual
    title: KBE Maintenance & Repair Manual
  - file: docs/ER-063.pdf
    role: engineering_reference
    applies_to: [ariel-*]        # shared across Ariel sets in future
  # ...ER-034/129/130/140/141
  - file: cad/OL0511_S1.PDF
    role: cad
  - file: cad/OL0782_S1.PDF
    role: cad
always_include_sections:          # injected into the tools call
  - doc: docs/Ariel-KBE-Manual.pdf
    section_prefix: "Section 1 - Tools"
```

Ingestion is idempotent. A document is re-processed only when its SHA-256 changes. Each document is stored once and linked to every set it applies to.

---

## 5. Ingestion pipeline

Command: `python -m backend.ingest ariel-kbe [--force] [--skip-vision]`. **Claude (the coding agent) runs this command, reviews the gallery and fixes problems via `overrides.yaml`.** It then reports what was flagged and what was changed.

### 5.1 Text chunking (`chunker.py`)

1. Strip the running header and footer by detecting lines repeated in the same position on ≥50% of pages (e.g. "KBE Maintenance & Repair Manual - Section 4…", "Page 88 of 154", "ARIEL CORPORATION | www.arielcorp.com"). Footer lines on ER documents are removed the same way.
2. Find the y-position of each TOC entry by searching its title on its target page. Split the document at the deepest TOC level into **section spans**.
3. For each span:
   - emit one chunk if it is ≤800 tokens;
   - otherwise split at paragraph boundaries into 300–800-token chunks with ~60 tokens of overlap;
   - merge spans under 80 tokens into the following sibling.
4. Text from figure regions (§5.2: caption + legend) is excluded from chunk text, because it is indexed with the figure.
5. Chunk record: `id, set_id, doc_id, section_path ("Section 4 - Part Replacement › Crosshead Installation"), page_start, page_end, text, token_count, has_warning (contains WARNING/CAUTION/DANGER), figure_refs[]`.
   - `figure_refs` combines (a) captions cross-referenced in the text via `Figure: <title>` hyperlinks/text, matched to figure IDs, and (b) figures located within the chunk's page/heading span.

### 5.2 Figure extraction (`figures.py`)

For each page, for each caption (a text line starting `Figure:` that is ≤2 lines tall):

1. **Split side-by-side captions.** If one text line contains multiple `Figure:` tokens, split it at each token using character positions into separate captions with their own x-ranges.
2. **Define the column:** x from the caption's left edge to the right edge of its column (the next caption to the right, or the page margin). y from the caption bottom down to the next caption in the same column, or the footer.
3. **Gather graphics** (embedded images plus vector drawings larger than 3pt) inside the column. Their union forms the figure box.
4. **Attach the legend:** text blocks directly below the box (within 60pt) that match the legend pattern (`^\d+\s+\S`, short lines, numbered items). **Stop at the first block that is body text**: longer than 25 words, or a NOTE/CAUTION box.
5. Final crop = caption ∪ graphics ∪ legend, with 6pt padding. Render at **200 dpi PNG** plus a **400px-wide thumbnail**.
6. Skip pseudo-captions: blocks starting `Figure:` that are longer than 2 lines and have no graphics below them (e.g. p.55).
7. Images with no caption on ER documents are kept and flagged `uncaptioned`. Their title comes from the vision description.

**Figure ID:** `<doc-slug>/<caption-slug>`, where the slug is lowercase ASCII, non-alphanumerics become `-`, and length is capped at 80 (e.g. `kbe-manual/crosshead-installation`). Duplicate slugs within a document get `-2`, `-3`.

**Figure record:** `id, set_id, doc_id, caption, caption_norm, page, bbox, legend_text, section_path, kind, description, components[], flags[], image_path, thumb_path, width, height`.

**`overrides.yaml`:**
```yaml
figures:
  kbe-manual/swabbing-oil-passages:
    bbox: [67, 58, 300, 300]     # PDF points
  kbe-manual/some-figure:
    exclude: true
  kbe-manual/typical-vvcp:
    caption: "Typical VVCP (Variable Volume Clearance Pocket)"
```

### 5.3 CAD (`cad.py`)

- Render each sheet as a full-page PNG at 150 dpi (the zoom viewer handles detail) plus a thumbnail. Keep the original PDF for download.
- Parse the title block for the drawing number (e.g. `OL0511`), title (e.g. "Guide Support Outline H:E:ET Class") and revision. The vision pass (§5.4) confirms or corrects these.
- Store in the same `assets` table with `asset_type = 'cad'`. Figures use `asset_type = 'figure'`.

### 5.4 Vision pass (`describe.py`)

For every figure crop and CAD sheet, one Haiku 4.5 call with the image plus caption and legend, through a strict tool `record_figure`:

```json
{ "kind": "photo|exploded_parts|line_drawing|schematic|chart|flowchart|cad",
  "complete": true, "has_body_text": false,
  "description": "1–2 sentences of what is shown",
  "components": ["crosshead", "installation tool", "metal plate"] }
```

- `complete=false` or `has_body_text=true` adds a flag for review.
- Results are cached in `content.db` by image SHA-256, so re-runs make no API calls.
- `--skip-vision` leaves descriptions empty, for offline development.
- The `kind` field provides the parts-diagram versus photo/schematic separation.

### 5.5 Indexing and review (`index.py`, `review.py`)

- **Chunks:** an FTS5 table (`text`, `section_path`) plus a 384-dim float32 embedding blob per chunk.
- **Figures and CAD:** an FTS5 table plus an embedding over `caption + legend + description + components + section_path`.
- Vector search is brute-force cosine in NumPy over the set's rows (~500 chunks), which takes under 5 ms. Swap to `sqlite-vec` if a set exceeds ~50k chunks.
- **Review gallery:** `content/review/<set>.html` is a static contact sheet of all figures and CAD sheets with caption, page, kind and flags. Flagged items are sorted first.
- **Ingestion summary** printed at the end:
  - number of chunks;
  - figures found vs. captions detected;
  - flagged count by flag;
  - vision API cost.

---

## 6. Request-time flow

### 6.1 Endpoint

`POST /api/chat`. The session cookie is required. Body:
```json
{ "set_id": "ariel-kbe", "conversation_id": "uuid|null", "message": "How do I install the crosshead?" }
```
The response is `text/event-stream` with these events:

| Event | Payload |
|---|---|
| `meta` | `{conversation_id, remaining_today}` |
| `token` | `{text}` (chat stream delta) |
| `assets` | `{figure_ids: [...], citations: [{page, doc_id, section_path}]}` |
| `tools` | `{groups: [...]}` or `{error: "..."}` |
| `done` | `{}` |
| `error` | `{message, code}` (e.g. `quota_exceeded`, `upstream_error`) |

`POST /api/tools/retry {conversation_id}` re-runs only the tools call for the last turn, and does not count toward quota.

### 6.2 Step 1 — Query rewrite (only when history exists)

Haiku, non-streaming, `max_tokens` 200, strict tool `rewrite → {query: string, new_task: boolean}`. The input is the last 6 turns plus the new message. On failure, it falls back to `query = message` and `new_task = false`.

### 6.3 Step 2 — Retrieval (`retrieval/search.py`)

1. FTS5 BM25 top 20 and vector top 20 over the set's chunks, merged by RRF (k = 60). Keep the **top 8**.
2. **Figure candidates (max 6, ordered):**
   - `figure_refs` of the top chunks, in chunk rank order;
   - then the RRF top hits from figure/CAD search;
   - CAD sheets are included only if their score passes a threshold;
   - duplicates removed.
3. Returned as structured objects; nothing is formatted as prompt text here.

### 6.4 Step 3a — Chat call (`llm/chat.py`)

- Haiku 4.5, streaming, `max_tokens` 2000, no extended thinking.
- **System prompt** (stable, cache-friendly):
  - role (field maintenance assistant for the given equipment);
  - grounding rules: use only the provided excerpts; cite `[p.N]`; never invent torque, clearance or pressure values, and if a spec is absent, say "not in the provided documentation";
  - format: safety warnings first, then numbered phases (Prepare → Remove → Inspect → Install → Complete, adapted to the task), then a short "Sources" line;
  - follow-ups answer directly without re-emitting the whole procedure.
- **User content:** retrieved chunks, each labelled with doc title, section path and pages; the candidate figure list (id, caption, kind, description); the conversation history; the question.
- **Tool `present_assets`** (strict). Its schema enum is built per request from the candidate IDs:
  ```json
  { "figure_ids": {"type":"array","items":{"enum":[...candidate ids]},"maxItems":3},
    "citations":  {"type":"array","items":{"type":"object","properties":{"page":{"type":"integer"},"doc_id":{"enum":[...]}}}} }
  ```
  The prompt instructs the model to call it once, after the answer text (`tool_choice: auto`). If it isn't called, the backend falls back to the top candidate figure. If an ID ever fails to resolve, the backend applies fuzzy lookup (normalized caption, trigram similarity ≥0.6) before dropping it.
- Text deltas are forwarded as `token` events. The tool input is emitted as an `assets` event.

### 6.5 Step 3b — Tools call (`llm/tools.py`)

- **Runs** on the first message of a conversation and whenever `new_task = true`. Otherwise the previous tools result is kept and no `tools` event is sent.
- It runs concurrently with the chat call (`asyncio.gather`), using the same retrieval result plus the set's `always_include_sections` chunks (manual Section 1 – Tools).
- Haiku 4.5, non-streaming, `max_tokens` 1500.
- Web search is **off by default**. `TOOLS_WEB_SEARCH=1` adds `web_search_20250305` with `max_uses: 1`.
- **Tool `submit_tools`** (strict):
  ```json
  { "groups": [{ "category": "oem|power|hand|measuring|consumables",
                 "tools": [{ "name": "...", "purpose": "...", "spec": "string|null",
                             "source": "manual|web|general",
                             "source_ref": "p.10 | url | null",
                             "icon": "<enum of icon keys>",
                             "figure_id": "<enum of candidate ids + always-include figure ids> | null" }] }] }
  ```
- Failure → a `tools` event with `error`. The chat is unaffected.

### 6.6 Future: agentic mode (not in v1)

`retrieval.search_chunks`, `retrieval.figure_candidates` and `read_section(doc_id, section_path)` are plain functions with typed inputs and outputs. That lets them be exposed later as Claude tools for a "deep search" mode without refactoring.

### 6.7 Cost and quota accounting

- Each Anthropic response's `usage` is logged to `app.db` (`llm_calls`: conversation, call type, model, input/output/cache tokens, web searches, estimated cost).
- Quota is incremented **only when the chat call completes successfully**.
- Expected cost: ~$0.02 per new task, ~$0.015 per follow-up.

### 6.8 Configuration (env)

| Var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | required |
| `CHAT_MODEL` / `TOOLS_MODEL` / `REWRITE_MODEL` / `VISION_MODEL` | `claude-haiku-4-5` | per-call model |
| `TOOLS_WEB_SEARCH` | `0` | enable 1 web search in the tools call |
| `DAILY_QUESTION_LIMIT` | `10` | per user per UTC day |
| `GLOBAL_DAILY_LIMIT` | `500` | all users per UTC day (spend kill switch) |
| `ADMIN_PASSWORD` | — | required |
| `ADMIN_EMAIL` | — | notification recipient |
| `SESSION_SECRET` | — | signs cookies; required |
| `RESEND_API_KEY` / `EMAIL_FROM` | unset → console | email delivery |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | links in emails |
| `DATA_DIR` / `CONTENT_DIR` | `./data` / `./content` | storage paths |

---

## 7. Access gating and admin

### 7.1 Data (`app.db`)

- `access_requests(id, email UNIQUE, name, company, note, status[pending|approved|rejected|revoked], daily_limit NULL, created_at, decided_at)`
- `access_codes(id, email, code_hash, expires_at, used_at, created_at)`
- `sessions(token_hash, email, created_at, expires_at, last_seen_at)`
- `redeem_attempts(email, ip, at, success)`
- `usage_daily(email, day, questions)`
- `conversations(id, email, set_id, created_at)`, `messages(id, conversation_id, role, content, assets_json, tools_json, created_at)`
- `llm_calls(...)` (§6.7)

### 7.2 Visitor flow

1. `POST /api/access/request {email, name, company, note, website(honeypot)}`.
   - Rate limit: 5 per IP per hour. A filled honeypot is silently dropped.
   - The reply is always a generic success.
   - A new request triggers an email to `ADMIN_EMAIL` with a link to `/admin`.
2. On admin approval, the system:
   - generates an 8-char code from the alphabet `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, displayed as `XXXX-XXXX`;
   - stores its SHA-256 hash with a 7-day expiry;
   - emails the code plus the link `/demo?email=…&code=…`.
3. `POST /api/access/redeem {email, code}`. The code must be valid, unused, unexpired and the status `approved`. Then:
   - the code is marked used;
   - a random 32-byte session token is created and stored hashed;
   - the cookie is set as `httpOnly; Secure; SameSite=Lax; Max-Age=30d`.

   After 5 failed attempts per email per hour, redemption is locked out with a generic error.
4. `POST /api/access/new-code {email}`. If the email is `approved`, a fresh code is emailed automatically. The response is always generic. Limited to 3 per email per hour.
5. Revoked users have their sessions deleted immediately, and new codes are refused.

### 7.3 Admin (`/admin`)

- `POST /api/admin/login {password}` checks against `ADMIN_PASSWORD` in constant time and sets a signed admin cookie (12h). Limited to 5 attempts per IP per 15 min.
- The page has three tabs:
  - **Pending:** Approve / Reject.
  - **Approved:** questions today and in total, last active, daily limit (editable), Revoke, Resend code, Reset today's quota, View conversations.
  - **Rejected/Revoked:** Restore.
- A header strip shows today's global usage and estimated spend from `llm_calls`.
- An admin session can use `/demo` with no quota.

### 7.4 Quotas

- Per user: `daily_limit` if set, else `DAILY_QUESTION_LIMIT` (10).
- Globally: `GLOBAL_DAILY_LIMIT` (500).
- Both are checked before any LLM call. Exceeding either returns the `error` event `quota_exceeded` with the reset time (midnight UTC).

### 7.5 Email

`auth/email.py` has `send(to, subject, html, text)`. It uses Resend when `RESEND_API_KEY` is set and otherwise logs the full message to the console. There are three templates: admin notification, access code, new code. **Deployment prerequisite:** a domain verified in Resend for `EMAIL_FROM`, because Resend's sandbox sender only delivers to the account owner.

---

## 8. Frontend

### 8.1 `/demo` (`web/demo.html`, `web/assets/demo.js`)

- Vanilla ES modules. `marked` + `DOMPurify` load from cdnjs for markdown rendering. Fonts and colour tokens come from the existing `web/assets/style.css`.
- **Gate states:** request form → "request received" → code entry (prefilled from URL params) → app. Also "Email me a new code" and quota exhausted.
- **Header:** brand, equipment selector (sets from `GET /api/sets`), questions remaining, user email, sign out.
- **Desktop (≥900px):** three columns, 30 / 40 / 30.
  - **Left:** Figures | Drawings tabs.
    - The viewer has click-to-fullscreen and wheel/pinch zoom and pan.
    - Below it: the caption, `Manual p.N ↗` (opens `/content-docs/<set>/<file>#page=N`), and a thumbnail strip of the current answer's figures.
    - The Drawings tab lists all CAD sheets for the set (`GET /api/sets/{id}/assets?type=cad`).
  - **Centre:** chat stream.
    - `[p.N]` citations become buttons that select a figure on that page in the left pane, if one exists, else open the PDF at that page.
    - Starter question chips appear when the conversation is empty.
    - A "New job" button starts a new conversation.
  - **Right:** tool groups as cards (icon or OEM figure thumbnail, name, purpose, spec, source chip). Skeletons show while loading; an error card has a Retry button.
- **Mobile (<900px):** tabs **Steps | Diagram | Tools**, with a dot on a tab when new content arrives. The Diagram tab opens full-width.
- **Icons:** about 30 inline SVG line icons in `web/assets/icons/` (torque-wrench, socket-set, allen-key, feeler-gauge, dial-indicator, micrometer, impact-wrench, drill, pry-bar, puller, hoist, screwdriver, pliers, hammer, multimeter, pressure-gauge, flashlight, rags, solvent, lubricant, sealant, gloves, safety-glasses, lockout-tag, ring-compressor, scraper, wire-brush, thermometer, jack, generic-tool). The icon key list is generated from the files and used as the enum in `submit_tools`.

### 8.2 Static assets served

- `/content/figures/...`, `/content/cad/...` are served from `CONTENT_DIR`.
- `/content-docs/<set>/<file>` serves the source PDFs (copied into `content/docs/` at ingestion) and needs a session.
- Figure and CAD images are public. They are manufacturer documentation already publicly distributed by Ariel. If that's undesirable, requiring a session is a one-line change.

### 8.3 Existing site

- `web/prototype.html`: the disabled "Prototype Coming Soon" button becomes an active "Try the demo" link to `/demo`.
- The early-access form posts to `/api/access/request`.

---

## 9. Deployment

- **Dockerfile:**
  - `python:3.12-slim`, installed with `uv`;
  - the fastembed model is downloaded at build time so cold start is fast;
  - copies `backend/`, `web/`, `content/`;
  - runs `uvicorn backend.api.app:app --host 0.0.0.0 --port $PORT`.
- **Railway:**
  - connect the GitHub repo; it auto-deploys on push to `main`;
  - add a volume mounted at `/app/data` (`DATA_DIR=/app/data`);
  - set the env vars from §6.8;
  - use the generated `*.up.railway.app` domain (a custom domain is optional).
- `app.db` migrations run automatically at startup.
- `GET /healthz` checks content.db readability and app.db writability.
- **Local:**
  - `uv run python -m backend.ingest ariel-kbe`;
  - `uv run uvicorn backend.api.app:app --reload`;
  - or `docker compose up` with a local `./data` volume.
- **Content update workflow:** ingest locally → review gallery → commit `content/` → push → Railway redeploys. User data on the volume is untouched.

---

## 10. Error handling

| Failure | Behaviour |
|---|---|
| Anthropic 429/5xx | SDK retries (2). Then: chat → `error` event "Assistant is busy, try again" (no quota charge); tools → tools error card with Retry. |
| Rewrite fails | Use the raw message as the query, with `new_task=false`. |
| `present_assets` not called / bad ID | Top candidate figure, after fuzzy lookup. |
| No chunks above threshold | The chat is told no relevant documentation was found and says so. Suggestions are shown. |
| Web search tool error (200 with error object) | Ignored. The tools list comes from manual + general knowledge. |
| Email send fails | Logged. Admin sees "email failed — Resend" on the user row. |
| Client disconnect mid-stream | Upstream calls are cancelled. The partial answer is not stored and quota is not charged. |

---

## 11. Testing

- **Unit (pytest, no network):**
  - chunker: header/footer removal, section paths for known pages, chunk size bounds;
  - figures: ≥83/85 captions cropped; p.88 box contains the legend; p.104 yields 2 separate figures without body text; p.18 yields 2; p.55 pseudo-caption skipped;
  - caption normalization and fuzzy lookup;
  - auth: code generation, expiry, single use, lockout, new-code flow, revoke kills sessions;
  - quota: per-user, global, admin bypass, no charge on failure;
  - API: chat SSE event order with a mocked Anthropic client; strict tool enum built from candidates.
- **Retrieval golden set** (`tests/golden/retrieval.yaml`, ~15 queries, offline): the expected section in the top 8 and the expected figure among the candidates. Examples:
  - "install crosshead" → `kbe-manual/crosshead-installation`;
  - "main bearing cap torque order" → `kbe-manual/main-bearing-cap-or-connecting-rod-cap-torque-sequence`;
  - "proximity switch wiring" → `kbe-manual/a-18255-proximity-switch-wiring-connections-for-unit-in-operation`;
  - "prime force feed lubricator pump" → `kbe-manual/pump-priming-and-adjustment`.
- **Answer eval** (`scripts/eval_answers.py`, real API, run manually, ~$0.30/run):
  - the same queries through the full pipeline;
  - checks: cited pages within expected ranges, expected figure in `figure_ids`, and every numeric spec with units in the answer appears in the retrieved chunk text;
  - used to decide whether `CHAT_MODEL` should move to Sonnet 5.5.
- **End-to-end:** local server, Playwright screenshots of desktop (1440×900) and mobile (390×844) for gate → code → question → figure/tools render.

---

## 12. Open items

- **Resend domain** for production email. Not needed for local testing.
- **ER document scoping.** ER-034/129/130/140/141 are generic Ariel documents. They are included in the `ariel-kbe` set for the demo and tagged `applies_to: [ariel-*]` for future sets.
- **Branding.** The site is "Onesi" and the mockup says "MaintIQ". The demo page uses Onesi unless told otherwise.
