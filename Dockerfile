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
