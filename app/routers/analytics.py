"""Resource utilization and campus lab accessibility analytics endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.database import Database
from app.services.analytics import compute_utilization_analytics
from app.utils import get_db

router = APIRouter(tags=["Utilization Analytics"])


@router.get("/api/analytics/utilization")
def get_utilization_analytics(db: Database = Depends(get_db)) -> dict[str, Any]:
    """Return campus-wide utilization KPIs, department stats, peak hours, and idle opportunities."""
    with db.connection() as conn:
        return compute_utilization_analytics(conn)
