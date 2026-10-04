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
