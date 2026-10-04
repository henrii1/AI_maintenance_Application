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
