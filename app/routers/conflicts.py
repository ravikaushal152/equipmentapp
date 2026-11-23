"""Scheduling conflict listing, fairness analysis, Gemini recommendations, and atomic resolution."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import Database
from app.schemas import ConflictApplyRequest
from app.services.conflict_resolver import (
    apply_resolution_plan,
    build_conflict_analysis,
    list_conflict_summaries,
)
from app.services.genai_service import GenAIService
from app.utils import get_db, get_genai

router = APIRouter(tags=["Scheduling Conflict Resolution"])


@router.get("/api/conflicts")
def list_conflicts(
    status: str = Query(default="all", pattern="^(open|resolved|all)$"),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """List scheduling conflicts with fairness-scored competing bookings in bulk."""
    with db.connection() as conn:
        items = list_conflict_summaries(conn, status=status)
    return {"count": len(items), "conflicts": items}


@router.get("/api/conflicts/{conflict_id}/analyze")
def analyze_conflict(
    conflict_id: int,
    db: Database = Depends(get_db),
    genai_service: GenAIService = Depends(get_genai),
) -> dict[str, Any]:
    """Run deterministic fairness scoring + feasible plan generation + Gemini advisor commentary."""
    with db.connection() as conn:
        try:
            analysis = build_conflict_analysis(conn, conflict_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    ai_advice = genai_service.advise_on_conflict(analysis)
    return {**analysis, "ai_advice": ai_advice}


@router.post("/api/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: int,
    payload: ConflictApplyRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Atomically apply a verified conflict resolution plan to resolve overlaps cleanly."""
    with db.transaction() as conn:
        try:
            return apply_resolution_plan(
                conn,
                conflict_id=conflict_id,
                plan_id=payload.plan_id,
                resolved_by=payload.resolved_by,
            )
        except ValueError as exc:
            message = str(exc)
            code = 404 if "not found" in message.lower() else 409
            raise HTTPException(status_code=code, detail=message) from exc
