"""Shared utility helpers and FastAPI dependency providers for LabLink."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app.database import Database
from app.security import validate_iso_date
from app.services.genai_service import GenAIService


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_hour(hour: int) -> str:
    """Format an integer hour (0-23) into HH:00 24-hour notation."""
    return f"{hour:02d}:00"


def parse_optional_iso_date(value: str | None, *, field_name: str = "date") -> str | None:
    """Validate an optional YYYY-MM-DD query parameter or raise HTTP 422."""
    if not value:
        return None
    try:
        return validate_iso_date(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be a valid YYYY-MM-DD calendar date.",
        ) from exc


def get_db(request: Request) -> Database:
    """FastAPI dependency returning the application Database instance."""
    return request.app.state.db


def get_genai(request: Request) -> GenAIService:
    """FastAPI dependency returning the application GenAIService instance."""
    return request.app.state.genai
