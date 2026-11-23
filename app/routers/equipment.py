"""Equipment catalog, department directory, schedule timeline, and GenAI discovery endpoints."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import Database
from app.schemas import AIDiscoveryRequest
from app.security import sanitize_text
from app.services.genai_service import GenAIService
from app.services.scheduler import get_equipment_schedule
from app.utils import get_db, get_genai, parse_optional_iso_date

router = APIRouter(tags=["Equipment & Discovery"])


def _enrich_equipment_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["specs"] = json.loads(item.pop("specs_json"))
    item["requires_faculty_approval"] = bool(item["requires_faculty_approval"])
    return item


def _load_daily_availability_map(
    conn: sqlite3.Connection,
    equipment_items: list[dict[str, Any]],
    day_iso: str,
) -> dict[int, dict[str, Any]]:
    """Compute daily utilization and open hours for all resources in a single query."""
    booking_rows = conn.execute(
        """
        SELECT equipment_id, start_hour, end_hour
        FROM bookings
        WHERE booking_date = ?
          AND status IN ('confirmed', 'conflict')
        """,
        (day_iso,),
    ).fetchall()

    booked_hours_by_eq: dict[int, set[int]] = {}
    for b in booking_rows:
        hours_set = booked_hours_by_eq.setdefault(int(b["equipment_id"]), set())
        hours_set.update(range(int(b["start_hour"]), int(b["end_hour"])))

    availability: dict[int, dict[str, Any]] = {}
    for item in equipment_items:
        total_h = max(1, int(item["lab_hours_end"]) - int(item["lab_hours_start"]))
        occ = len(booked_hours_by_eq.get(int(item["id"]), set()))
        availability[int(item["id"])] = {
            "utilization_pct": round((occ / total_h) * 100, 1),
            "open_hours": max(0, total_h - occ),
        }
    return availability


@router.get("/api/departments")
def list_departments(db: Database = Depends(get_db)) -> dict[str, Any]:
    """List all participating campus departments and cross-department policies."""
    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM departments ORDER BY code ASC").fetchall()
    return {"departments": [dict(r) for r in rows]}


@router.get("/api/equipment")
def list_equipment(
    department: str | None = Query(default=None, max_length=16),
    category: str | None = Query(default=None, max_length=32),
    status: str | None = Query(default=None, max_length=24),
    search: str | None = Query(default=None, max_length=120),
    only_instant: bool = Query(default=False),
    target_date: str | None = Query(default=None, max_length=10),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Browse and filter campus lab equipment with live daily availability summaries."""
    day_iso = parse_optional_iso_date(target_date, field_name="target_date") or date.today().isoformat()

    clauses: list[str] = []
    params: list[Any] = []

    if department:
        clauses.append("e.department_code = ?")
        params.append(sanitize_text(department).upper())
    if category:
        clauses.append("e.category = ?")
        params.append(sanitize_text(category).lower())
    if status:
        clauses.append("e.status = ?")
        params.append(sanitize_text(status).lower())
    if only_instant:
        clauses.append("e.requires_faculty_approval = 0")
    if search:
        term = f"%{sanitize_text(search).lower()}%"
        clauses.append(
            "(LOWER(e.name) LIKE ? OR LOWER(e.description) LIKE ? "
            "OR LOWER(e.specs_json) LIKE ? OR LOWER(e.location) LIKE ?)"
        )
        params.extend([term, term, term, term])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT e.*, d.name AS department_name, d.building, d.coordinator_name
            FROM equipment e
            JOIN departments d ON d.code = e.department_code
            {where_sql}
            ORDER BY e.department_code ASC, e.name ASC
            """,
            params,
        ).fetchall()
        items = [_enrich_equipment_row(dict(row)) for row in rows]
        availability_map = _load_daily_availability_map(conn, items, day_iso)

    for item in items:
        avail = availability_map.get(item["id"], {"utilization_pct": 0.0, "open_hours": 0})
        item["utilization_today_pct"] = avail["utilization_pct"]
        item["open_hours_today"] = avail["open_hours"]

    return {"date": day_iso, "count": len(items), "equipment": items}


@router.get("/api/equipment/{equipment_id}")
def get_equipment_detail(
    equipment_id: int,
    target_date: str | None = Query(default=None, max_length=10),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Return full specs, cross-department equivalents, and schedule for a single resource."""
    day_iso = parse_optional_iso_date(target_date, field_name="target_date") or date.today().isoformat()

    with db.connection() as conn:
        schedule = get_equipment_schedule(conn, equipment_id, day_iso)
        if schedule is None:
            raise HTTPException(status_code=404, detail="Equipment resource not found.")

        equivalents = [
            _enrich_equipment_row(dict(r))
            for r in conn.execute(
                """
                SELECT e.*, d.name AS department_name
                FROM equipment e
                JOIN departments d ON d.code = e.department_code
                WHERE e.compatibility_group = ? AND e.id != ?
                ORDER BY e.department_code ASC
                """,
                (schedule["equipment"]["compatibility_group"], equipment_id),
            ).fetchall()
        ]

    return {**schedule, "equivalent_resources": equivalents}


@router.get("/api/equipment/{equipment_id}/schedule")
def get_schedule(
    equipment_id: int,
    target_date: str | None = Query(default=None, max_length=10),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Return the hourly booking grid for an equipment item on target_date."""
    day_iso = parse_optional_iso_date(target_date, field_name="target_date") or date.today().isoformat()

    with db.connection() as conn:
        schedule = get_equipment_schedule(conn, equipment_id, day_iso)
        if schedule is None:
            raise HTTPException(status_code=404, detail="Equipment resource not found.")
    return schedule


@router.post("/api/ai/discover")
def ai_discover_equipment(
    payload: AIDiscoveryRequest,
    db: Database = Depends(get_db),
    genai_service: GenAIService = Depends(get_genai),
) -> dict[str, Any]:
    """GenAI-powered natural-language equipment discovery across departments (zero N+1 queries)."""
    day_iso = payload.preferred_date or date.today().isoformat()

    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT e.*, d.name AS department_name
            FROM equipment e
            JOIN departments d ON d.code = e.department_code
            ORDER BY e.id ASC
            """
        ).fetchall()
        equipment_items = [_enrich_equipment_row(dict(r)) for r in rows]
        availability_by_id = _load_daily_availability_map(conn, equipment_items, day_iso)

    return genai_service.discover_equipment(
        query=payload.query,
        equipment_items=equipment_items,
        availability_by_id=availability_by_id,
        student_department=payload.student_department,
        duration_hours=payload.duration_hours,
        project_deadline=payload.project_deadline,
    )
