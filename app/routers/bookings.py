"""Slot booking, intelligent slot recommendations, and automatic conflict flagging."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import Database
from app.schemas import BookingCreateRequest, SmartSlotRequest
from app.security import sanitize_text
from app.services.scheduler import find_overlapping_bookings, recommend_smart_slots
from app.utils import get_db, parse_optional_iso_date, utc_now_iso

router = APIRouter(tags=["Bookings & Smart Scheduling"])


@router.get("/api/bookings")
def list_bookings(
    equipment_id: int | None = Query(default=None, ge=1),
    booking_date: str | None = Query(default=None, max_length=10),
    status: str | None = Query(default=None, max_length=24),
    department: str | None = Query(default=None, max_length=16),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """List lab bookings filtered by resource, date, status, or department."""
    parsed_date = parse_optional_iso_date(booking_date, field_name="booking_date")
    clauses: list[str] = []
    params: list[Any] = []

    if equipment_id is not None:
        clauses.append("b.equipment_id = ?")
        params.append(equipment_id)
    if parsed_date:
        clauses.append("b.booking_date = ?")
        params.append(parsed_date)
    if status:
        clauses.append("b.status = ?")
        params.append(sanitize_text(status).lower())
    if department:
        clauses.append("e.department_code = ?")
        params.append(sanitize_text(department).upper())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT b.*, e.name AS equipment_name, e.department_code AS equipment_department,
                   e.location
            FROM bookings b
            JOIN equipment e ON e.id = b.equipment_id
            {where_sql}
            ORDER BY b.booking_date ASC, b.start_hour ASC, b.id DESC
            """,
            params,
        ).fetchall()

    return {"count": len(rows), "bookings": [dict(r) for r in rows]}


@router.post("/api/bookings/smart-slots")
def get_smart_slots(
    payload: SmartSlotRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Recommend gap-packed, conflict-free, and load-balanced lab booking slots."""
    with db.connection() as conn:
        try:
            return recommend_smart_slots(
                conn,
                equipment_id=payload.equipment_id,
                preferred_date=payload.preferred_date,
                duration_hours=payload.duration_hours,
                project_deadline=payload.project_deadline,
                include_cross_department=payload.include_cross_department,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/bookings", status_code=201)
def create_booking(
    payload: BookingCreateRequest,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Book a lab slot; automatically detects overlaps and opens a resolvable conflict."""
    now = utc_now_iso()

    with db.transaction() as conn:
        eq = conn.execute(
            "SELECT * FROM equipment WHERE id = ?", (payload.equipment_id,)
        ).fetchone()
        if eq is None:
            raise HTTPException(status_code=404, detail="Equipment resource not found.")
        if eq["status"] == "maintenance":
            raise HTTPException(
                status_code=409,
                detail="This resource is currently under maintenance and cannot be booked.",
            )

        if payload.start_hour < eq["lab_hours_start"] or payload.end_hour > eq["lab_hours_end"]:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Booking hours must fall within lab operating hours "
                    f"({eq['lab_hours_start']:02d}:00–{eq['lab_hours_end']:02d}:00)."
                ),
            )

        duration = payload.end_hour - payload.start_hour
        if duration > eq["max_session_hours"]:
            raise HTTPException(
                status_code=422,
                detail=f"Maximum single session for this resource is {eq['max_session_hours']} hours.",
            )

        overlaps = find_overlapping_bookings(
            conn,
            payload.equipment_id,
            payload.booking_date,
            payload.start_hour,
            payload.end_hour,
        )

        booking_status = "conflict" if overlaps else "confirmed"
        cur = conn.execute(
            """
            INSERT INTO bookings (
                equipment_id, student_name, student_id, student_department,
                project_title, project_track, project_deadline, booking_date,
                start_hour, end_hour, team_size, flexibility,
                prior_hours_this_week, priority_credit, status, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                payload.equipment_id,
                payload.student_name,
                payload.student_id,
                payload.student_department,
                payload.project_title,
                payload.project_track,
                payload.project_deadline,
                payload.booking_date,
                payload.start_hour,
                payload.end_hour,
                payload.team_size,
                payload.flexibility,
                payload.prior_hours_this_week,
                booking_status,
                payload.notes,
                now,
            ),
        )
        new_booking_id = int(cur.lastrowid)

        conflict_id: int | None = None
        if overlaps:
            existing_first = overlaps[0]
            competing_ids = [int(existing_first["id"]), new_booking_id]
            overlap_start = max(int(existing_first["start_hour"]), payload.start_hour)
            overlap_end = min(int(existing_first["end_hour"]), payload.end_hour)

            conn.execute(
                "UPDATE bookings SET status = 'conflict' WHERE id = ?",
                (existing_first["id"],),
            )
            c_cur = conn.execute(
                """
                INSERT INTO conflicts (
                    equipment_id, conflict_date, overlap_start_hour, overlap_end_hour,
                    competing_booking_ids_json, severity, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'high', 'open', ?)
                """,
                (
                    payload.equipment_id,
                    payload.booking_date,
                    overlap_start,
                    overlap_end,
                    json.dumps(competing_ids),
                    now,
                ),
            )
            conflict_id = int(c_cur.lastrowid)

        conn.execute(
            """
            INSERT INTO audit_logs (actor, actor_role, action, entity_type, entity_id, details_json, created_at)
            VALUES (?, 'student', ?, 'booking', ?, ?, ?)
            """,
            (
                payload.student_name,
                "booking_conflict_flagged" if overlaps else "booking_confirmed",
                new_booking_id,
                json.dumps(
                    {
                        "equipment": eq["name"],
                        "date": payload.booking_date,
                        "hours": f"{payload.start_hour:02d}:00-{payload.end_hour:02d}:00",
                        "conflict_id": conflict_id,
                    }
                ),
                now,
            ),
        )

        created = dict(
            conn.execute(
                """
                SELECT b.*, e.name AS equipment_name, e.department_code AS equipment_department
                FROM bookings b JOIN equipment e ON e.id = b.equipment_id
                WHERE b.id = ?
                """,
                (new_booking_id,),
            ).fetchone()
        )

    return {
        "booking": created,
        "conflict_detected": bool(overlaps),
        "conflict_id": conflict_id,
        "message": (
            "Scheduling conflict detected! Open the Conflict Resolver tab for AI-recommended fair allocations."
            if overlaps
            else "Slot confirmed! Your laboratory session is booked."
        ),
    }


@router.patch("/api/bookings/{booking_id}/cancel")
def cancel_booking(
    booking_id: int,
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    """Cancel an active booking and release its laboratory slot."""
    now = utc_now_iso()

    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Booking not found.")
        if row["status"] == "cancelled":
            raise HTTPException(status_code=400, detail="Booking is already cancelled.")

        conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        conn.execute(
            """
            INSERT INTO audit_logs (actor, actor_role, action, entity_type, entity_id, details_json, created_at)
            VALUES (?, 'student', 'booking_cancelled', 'booking', ?, ?, ?)
            """,
            (
                row["student_name"],
                booking_id,
                json.dumps({"project_title": row["project_title"], "date": row["booking_date"]}),
                now,
            ),
        )

    return {"booking_id": booking_id, "status": "cancelled"}
