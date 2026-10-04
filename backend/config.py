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
