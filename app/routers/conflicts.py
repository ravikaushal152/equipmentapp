"""Scheduling conflict listing, fairness analysis, Gemini recommendations, and atomic resolution."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ConflictApplyRequest
from app.services.conflict_resolver import apply_resolution_plan, build_conflict_analysis

router = APIRouter(tags=["Scheduling Conflict Resolution"])


@router.get("/api/conflicts")
def list_conflicts(
    request: Request,
    status: str = Query(default="all", pattern="^(open|resolved|all)$"),
) -> dict[str, Any]:
    """List scheduling conflicts with fairness-scored competing bookings."""
    db = request.app.state.db
    where_sql = "" if status == "all" else "WHERE status = ?"
    params = () if status == "all" else (status,)

    with db.connection() as conn:
        ids = [
            int(r["id"])
            for r in conn.execute(
                f"""
                SELECT id FROM conflicts
                {where_sql}
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, conflict_date ASC, id DESC
                """,
                params,
            ).fetchall()
        ]
        items = [build_conflict_analysis(conn, cid) for cid in ids]

    return {"count": len(items), "conflicts": items}


@router.get("/api/conflicts/{conflict_id}/analyze")
def analyze_conflict(request: Request, conflict_id: int) -> dict[str, Any]:
    """Run deterministic fairness scoring + feasible plan generation + Gemini advisor commentary."""
    db = request.app.state.db
    genai_service = request.app.state.genai

    with db.connection() as conn:
        try:
            analysis = build_conflict_analysis(conn, conflict_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    ai_advice = genai_service.advise_on_conflict(analysis)
    return {**analysis, "ai_advice": ai_advice}


@router.post("/api/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    request: Request, conflict_id: int, payload: ConflictApplyRequest
) -> dict[str, Any]:
    """Atomically apply a verified conflict resolution plan to resolve overlaps cleanly."""
    db = request.app.state.db
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
