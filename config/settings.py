"""
settings.py - Central configuration for the Multi-Agent Productivity System.
Loads all values from environment variables (.env file).
"""

from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field
from enum import Enum
from functools import lru_cache

# settings.py lives at:  <root>/backend/config/settings.py
# .env lives at:         <root>/.env
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class AutonomyLevel(str, Enum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    AUTONOMOUS = "autonomous"



class Settings(BaseSettings):
    # ── Groq ────────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = Field(default="", description="Groq API key")
    GROQ_MODEL: str = Field("llama-3.3-70b-versatile", description="Groq model to use")
    GROQ_TEMPERATURE: float = Field(0.2, description="LLM temperature")

    # ── Google OAuth ─────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = Field(default="", description="Google OAuth client ID")
    GOOGLE_CLIENT_SECRET: str = Field(default="", description="Google OAuth client secret")
    GOOGLE_REDIRECT_URI: str = Field(
        "http://localhost:8000/auth/callback",
        description="OAuth redirect URI",
    )
    GOOGLE_SCOPES: list[str] = Field(
        default=[
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.readonly",
            "openid",
            "email",
            "profile",
        ],
        description="Required Google OAuth scopes",
    )

    # ── Token storage ────────────────────────────────────────────────────────
    TOKEN_FILE: str = Field("token.json", description="Path to stored OAuth token")

    # ── FAISS / Vector Memory ────────────────────────────────────────────────
    FAISS_INDEX_PATH: str = Field("memory/faiss_index", description="FAISS index directory")
    EMBEDDING_MODEL: str = Field("all-MiniLM-L6-v2", description="SentenceTransformers model for local embeddings")
    FAISS_TOP_K: int = Field(5, description="Number of top results to retrieve from FAISS")

    # ── Application ──────────────────────────────────────────────────────────
    APP_TITLE: str = "Multi-Agent Productivity System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = Field(False, description="Enable debug mode")
    LOG_LEVEL: str = Field("INFO", description="Logging level")
    LOG_FILE: str = Field("logs/system.log", description="Path to log file")

    # ── Autonomy ─────────────────────────────────────────────────────────────
    AUTONOMY_LEVEL: AutonomyLevel = Field(
        AutonomyLevel.AUTONOMOUS,
        description="System autonomy level: manual | assisted | autonomous",
    )

    # ── Task / Planning ──────────────────────────────────────────────────────
    MAX_SUBTASKS: int = Field(5, description="Maximum subtasks per goal")
    RETRY_ATTEMPTS: int = Field(3, description="Number of retry attempts for failed actions")
    RETRY_DELAY_SECONDS: int = Field(5, description="Seconds between retries")

    # ── Scheduler ────────────────────────────────────────────────────────────
    MONITOR_INTERVAL_HOURS: int = Field(24, description="How often the monitor agent runs (hours)")
    REFLECTION_INTERVAL_DAYS: int = Field(7, description="How often the reflection agent runs (days)")

    # ── CORS ─────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173", "http://localhost:8000"],
        description="CORS allowed origins",
    )

    model_config = {
        "env_file": str(_ENV_FILE),   # absolute path — works from any CWD
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
