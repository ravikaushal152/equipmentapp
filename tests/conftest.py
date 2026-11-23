"""Shared pytest fixtures providing isolated SQLite databases per test."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path):
    """Create a fresh LabLink FastAPI TestClient backed by a temporary SQLite DB."""
    settings = Settings(
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        db_path=tmp_path / "test_lablink.db",
        env="test",
        port=8080,
        rate_limit_general_rpm=500,
        rate_limit_ai_rpm=100,
        ai_cache_ttl_seconds=60,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def strict_rate_client(tmp_path: Path):
    """TestClient configured with tight rate limits to verify 429 protection."""
    settings = Settings(
        gemini_api_key="",
        gemini_model="gemini-2.5-flash",
        db_path=tmp_path / "test_rate.db",
        env="test",
        port=8080,
        rate_limit_general_rpm=3,
        rate_limit_ai_rpm=2,
        ai_cache_ttl_seconds=60,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
