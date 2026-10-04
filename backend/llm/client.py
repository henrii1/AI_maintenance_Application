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
