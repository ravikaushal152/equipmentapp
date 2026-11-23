"""Resource utilization and campus lab accessibility analytics endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services.analytics import compute_utilization_analytics

router = APIRouter(tags=["Utilization Analytics"])


@router.get("/api/analytics/utilization")
def get_utilization_analytics(request: Request) -> dict[str, Any]:
    """Return campus-wide utilization KPIs, department stats, peak hours, and idle opportunities."""
    db = request.app.state.db
    with db.connection() as conn:
        return compute_utilization_analytics(conn)
