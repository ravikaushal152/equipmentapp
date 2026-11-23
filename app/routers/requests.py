"""Project-based cross-department access requests and faculty approval workflows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import (
    FacultyDecisionRequest,
    ProjectBriefPreviewRequest,
    ProjectRequestCreate,
)
from app.security import sanitize_text
from app.services.scheduler import find_overlapping_bookings

router = APIRouter(tags=["Project Requests & Faculty Approvals"])


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/api/requests")
def list_project_requests(
    request: Request,
    status: str | None = Query(default=None, max_length=24),
) -> dict[str, Any]:
    """List project-based access requests with AI briefs and live overlap checks."""
    db = request.app.state.db
    clauses: list[str] = []
    params: list[Any] = []
    if status and status != "all":
        clauses.append("pr.status = ?")
        params.append(sanitize_text(status).lower())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT pr.*, e.name AS equipment_name, e.department_code AS equipment_department,
                   e.location, e.safety_cert_required, d.coordinator_name
            FROM project_requests pr
            JOIN equipment e ON e.id = pr.equipment_id
            JOIN departments d ON d.code = e.department_code
            {where_sql}
            ORDER BY CASE pr.status WHEN 'pending' THEN 0 ELSE 1 END, pr.id DESC
            """,
            params,
        ).fetchall()

        results: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["ai_brief"] = json.loads(item.pop("ai_brief_json") or "{}")
            item["safety_acknowledged"] = bool(item["safety_acknowledged"])
            item["is_cross_department"] = (
                item["student_department"] != item["equipment_department"]
            )
            overlaps = find_overlapping_bookings(
                conn,
                int(item["equipment_id"]),
                item["requested_date"],
                int(item["start_hour"]),
                int(item["end_hour"]),
            )
            item["has_slot_conflict"] = bool(overlaps) and item["status"] == "pending"
            results.append(item)

    return {"count": len(results), "requests": results}


@router.post("/api/ai/project-brief")
def preview_project_brief(
    request: Request, payload: ProjectBriefPreviewRequest
) -> dict[str, Any]:
    """Generate an AI feasibility and safety readiness brief for a draft project request."""
    db = request.app.state.db
    genai_service = request.app.state.genai

    with db.connection() as conn:
        eq_row = conn.execute(
            "SELECT * FROM equipment WHERE id = ?", (payload.equipment_id,)
        ).fetchone()
        if eq_row is None:
            raise HTTPException(status_code=404, detail="Equipment resource not found.")
        equipment = dict(eq_row)

    brief = genai_service.generate_project_brief(
        project_title=payload.project_title,
        project_track=payload.project_track,
        project_deadline=payload.project_deadline,
        student_department=payload.student_department,
        team_size=payload.team_size,
        justification=payload.justification,
        safety_acknowledged=payload.safety_acknowledged,
        equipment=equipment,
    )
    return {"brief": brief}


@router.post("/api/requests", status_code=201)
def create_project_request(
    request: Request, payload: ProjectRequestCreate
) -> dict[str, Any]:
    """Submit a structured project-based access request with an AI faculty review brief."""
    db = request.app.state.db
    genai_service = request.app.state.genai
    now = _utc_now()

    with db.connection() as conn:
        eq_row = conn.execute(
            "SELECT * FROM equipment WHERE id = ?", (payload.equipment_id,)
        ).fetchone()
        if eq_row is None:
            raise HTTPException(status_code=404, detail="Equipment resource not found.")
        equipment = dict(eq_row)

        if (
            payload.start_hour < equipment["lab_hours_start"]
            or payload.end_hour > equipment["lab_hours_end"]
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Requested hours must fall within lab hours "
                    f"({equipment['lab_hours_start']:02d}:00–{equipment['lab_hours_end']:02d}:00)."
                ),
            )
        if (payload.end_hour - payload.start_hour) > equipment["max_session_hours"]:
            raise HTTPException(
                status_code=422,
                detail=f"Maximum session length for this resource is {equipment['max_session_hours']} hours.",
            )

        overlaps = find_overlapping_bookings(
            conn,
            payload.equipment_id,
            payload.requested_date,
            payload.start_hour,
            payload.end_hour,
        )

    brief = genai_service.generate_project_brief(
        project_title=payload.project_title,
        project_track=payload.project_track,
        project_deadline=payload.project_deadline,
        student_department=payload.student_department,
        team_size=payload.team_size,
        justification=payload.justification,
        safety_acknowledged=payload.safety_acknowledged,
        equipment=equipment,
        has_slot_conflict=bool(overlaps),
    )

    with db.transaction() as conn:
        cur = conn.execute(
            """
            INSERT INTO project_requests (
                student_name, student_id, student_email, student_department,
                project_title, project_track, project_deadline, team_size,
                equipment_id, requested_date, start_hour, end_hour,
                justification, safety_acknowledged, flexibility,
                prior_hours_this_week, ai_brief_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                payload.student_name,
                payload.student_id,
                payload.student_email,
                payload.student_department,
                payload.project_title,
                payload.project_track,
                payload.project_deadline,
                payload.team_size,
                payload.equipment_id,
                payload.requested_date,
                payload.start_hour,
                payload.end_hour,
                payload.justification,
                1 if payload.safety_acknowledged else 0,
                payload.flexibility,
                payload.prior_hours_this_week,
                json.dumps(brief),
                now,
            ),
        )
        req_id = int(cur.lastrowid)

        conn.execute(
            """
            INSERT INTO audit_logs (actor, actor_role, action, entity_type, entity_id, details_json, created_at)
            VALUES (?, 'student', 'project_request_submitted', 'project_request', ?, ?, ?)
            """,
            (
                payload.student_name,
                req_id,
                json.dumps(
                    {
                        "project_title": payload.project_title,
                        "equipment": equipment["name"],
                        "cross_department": payload.student_department
                        != equipment["department_code"],
                    }
                ),
                now,
            ),
        )

        row = dict(
            conn.execute(
                """
                SELECT pr.*, e.name AS equipment_name, e.department_code AS equipment_department
                FROM project_requests pr JOIN equipment e ON e.id = pr.equipment_id
                WHERE pr.id = ?
                """,
                (req_id,),
            ).fetchone()
        )
        row["ai_brief"] = json.loads(row.pop("ai_brief_json"))
        row["safety_acknowledged"] = bool(row["safety_acknowledged"])

    return {"request": row, "has_slot_conflict": bool(overlaps)}


@router.post("/api/requests/{request_id}/decision")
def review_project_request(
    request: Request, request_id: int, payload: FacultyDecisionRequest
) -> dict[str, Any]:
    """Faculty approval, rejection, or revision decision with automatic booking/conflict linking."""
    db = request.app.state.db
    now = _utc_now()

    with db.transaction() as conn:
        req_row = conn.execute(
            """
            SELECT pr.*, e.name AS equipment_name, e.department_code AS equipment_department
            FROM project_requests pr
            JOIN equipment e ON e.id = pr.equipment_id
            WHERE pr.id = ?
            """,
            (request_id,),
        ).fetchone()
        if req_row is None:
            raise HTTPException(status_code=404, detail="Project request not found.")

        if req_row["status"] not in ("pending", "revision_requested"):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot modify request already finalized as '{req_row['status']}'.",
            )

        booking_id: int | None = None
        conflict_id: int | None = None

        if payload.decision == "approved":
            overlaps = find_overlapping_bookings(
                conn,
                int(req_row["equipment_id"]),
                req_row["requested_date"],
                int(req_row["start_hour"]),
                int(req_row["end_hour"]),
            )
            booking_status = "conflict" if overlaps else "confirmed"

            b_cur = conn.execute(
                """
                INSERT INTO bookings (
                    equipment_id, student_name, student_id, student_department,
                    project_title, project_track, project_deadline, booking_date,
                    start_hour, end_hour, team_size, flexibility,
                    prior_hours_this_week, priority_credit, status, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    req_row["equipment_id"],
                    req_row["student_name"],
                    req_row["student_id"],
                    req_row["student_department"],
                    req_row["project_title"],
                    req_row["project_track"],
                    req_row["project_deadline"],
                    req_row["requested_date"],
                    req_row["start_hour"],
                    req_row["end_hour"],
                    req_row["team_size"],
                    req_row["flexibility"],
                    req_row["prior_hours_this_week"],
                    booking_status,
                    f"Approved by {payload.reviewer_name}: {payload.reviewer_notes or 'Cleared for lab access.'}",
                    now,
                ),
            )
            booking_id = int(b_cur.lastrowid)

            if overlaps:
                first_existing = overlaps[0]
                conn.execute(
                    "UPDATE bookings SET status = 'conflict' WHERE id = ?",
                    (first_existing["id"],),
                )
                c_cur = conn.execute(
                    """
                    INSERT INTO conflicts (
                        equipment_id, conflict_date, overlap_start_hour, overlap_end_hour,
                        competing_booking_ids_json, severity, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'high', 'open', ?)
                    """,
                    (
                        req_row["equipment_id"],
                        req_row["requested_date"],
                        max(int(first_existing["start_hour"]), int(req_row["start_hour"])),
                        min(int(first_existing["end_hour"]), int(req_row["end_hour"])),
                        json.dumps([int(first_existing["id"]), booking_id]),
                        now,
                    ),
                )
                conflict_id = int(c_cur.lastrowid)

        conn.execute(
            """
            UPDATE project_requests
            SET status = ?,
                reviewer_name = ?,
                reviewer_notes = ?,
                booking_id = ?,
                reviewed_at = ?
            WHERE id = ?
            """,
            (
                payload.decision,
                payload.reviewer_name,
                payload.reviewer_notes,
                booking_id,
                now,
                request_id,
            ),
        )

        conn.execute(
            """
            INSERT INTO audit_logs (actor, actor_role, action, entity_type, entity_id, details_json, created_at)
            VALUES (?, 'faculty', ?, 'project_request', ?, ?, ?)
            """,
            (
                payload.reviewer_name,
                f"request_{payload.decision}",
                request_id,
                json.dumps(
                    {
                        "project_title": req_row["project_title"],
                        "booking_id": booking_id,
                        "conflict_id": conflict_id,
                        "notes": payload.reviewer_notes,
                    }
                ),
                now,
            ),
        )

    return {
        "request_id": request_id,
        "status": payload.decision,
        "reviewer_name": payload.reviewer_name,
        "booking_id": booking_id,
        "conflict_id": conflict_id,
        "message": (
            "Approved! Because this slot overlapped an existing session, a conflict has been opened in the Conflict Resolver."
            if conflict_id
            else f"Project request marked as '{payload.decision.replace('_', ' ')}'."
        ),
    }
