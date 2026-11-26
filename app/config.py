import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'kpi.db'}")
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    COOKIE_SECURE: bool = _bool("COOKIE_SECURE", False)
    SESSION_MAX_AGE: int = int(os.getenv("SESSION_MAX_AGE", str(14 * 24 * 3600)))

    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "").strip().lower()
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
    ALLOWED_EMAIL_DOMAINS: list[str] = [
        d.strip().lower().lstrip("@") for d in os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",") if d.strip()
    ]

    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "KPI <kpi@example.com>")

    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    LLM_MODEL_TEXT: str = os.getenv("LLM_MODEL_TEXT", "z-ai/glm-5.3")

    TIMEZONE: str = "Europe/Moscow"


settings = Settings()

if len(settings.SECRET_KEY) < 32:
    raise RuntimeError("SECRET_KEY в .env не задан или короче 32 символов.")
