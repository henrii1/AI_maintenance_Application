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
