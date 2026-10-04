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
uv run python scripts/eval_answers.py  # answer quality against the real API (~$0.15 measured)
```

## Add or update a manual set

1. Create `corpus/<set-id>/manifest.yaml` (copy `corpus/ariel-kbe/manifest.yaml`) and put PDFs under `docs/` and `cad/`.
2. `uv run python -m backend.ingest <set-id>` (add `--force` to re-process unchanged files, `--skip-vision` to skip figure descriptions).
3. Open `content/review/<set-id>.html`; fix any flagged crop in `corpus/<set-id>/overrides.yaml` and re-run.
4. Commit `content/` and the corpus, then push. The deploy picks it up.

## Deploy (Railway)

1. Push this repo to GitHub. In Railway: **New Project → Deploy from GitHub repo**. It builds the `Dockerfile`.
2. **Add a Volume** to the service, mounted at `/app/data` (holds `app.db`: access requests, sessions, usage).
3. **Variables:** `ANTHROPIC_API_KEY`, `ADMIN_PASSWORD`, `ADMIN_EMAIL`, `SESSION_SECRET` (random), `PUBLIC_BASE_URL` (the Railway URL, `https://…`), and optionally `RESEND_API_KEY` and `EMAIL_FROM` (needs a domain verified in Resend).
4. **Settings → Networking → Generate Domain.** Set `PUBLIC_BASE_URL` to it and redeploy.
5. Check `https://<domain>/healthz` returns `{"ok": true, ...}` (HTTP 503 if content or the app DB is missing, which Railway's healthcheck treats as a failed deploy). Then sign in at `/admin`.

The app refuses to start over https if `SESSION_SECRET`, `ADMIN_PASSWORD` or `ANTHROPIC_API_KEY` is missing.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_MODEL` / `TOOLS_MODEL` / `REWRITE_MODEL` / `VISION_MODEL` | `claude-haiku-4-5` | model per call |
| `TOOLS_WEB_SEARCH` | `0` | allow one web search in the tools call |
| `DAILY_QUESTION_LIMIT` | `10` | per user, per UTC day |
| `GLOBAL_DAILY_LIMIT` | `500` | all users, per UTC day (spend cap) |
| `DATA_DIR` / `CONTENT_DIR` | `data` / `content` | runtime DB / ingested content |
