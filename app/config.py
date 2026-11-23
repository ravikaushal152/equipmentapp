"""Application configuration loaded cleanly from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for LabLink."""

    gemini_api_key: str
    gemini_model: str
    db_path: Path
    env: str
    port: int
    rate_limit_general_rpm: int
    rate_limit_ai_rpm: int
    ai_cache_ttl_seconds: int

    @property
    def has_gemini_key(self) -> bool:
        """Return True when a non-empty Gemini API key is configured."""
        return bool(self.gemini_api_key and self.gemini_api_key.strip())


def load_settings() -> Settings:
    """Build settings from process environment with safe defaults."""
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        or "gemini-3.5-flash-lite",
        db_path=Path(os.getenv("LABLINK_DB_PATH", "data/lablink.db")),
        env=os.getenv("LABLINK_ENV", "development").strip().lower(),
        port=int(os.getenv("PORT", "8080")),
        rate_limit_general_rpm=int(os.getenv("LABLINK_RATE_LIMIT_RPM", "180")),
        rate_limit_ai_rpm=int(os.getenv("LABLINK_AI_RATE_LIMIT_RPM", "30")),
        ai_cache_ttl_seconds=int(os.getenv("LABLINK_AI_CACHE_TTL", "300")),
    )
