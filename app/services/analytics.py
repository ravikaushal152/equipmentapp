"""Campus laboratory utilization, cross-department access, and fairness analytics."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from app.services.scheduler import format_hour


def compute_utilization_analytics(conn: sqlite3.Connection) -> dict[str, Any]:
    """Compute real-time campus-wide and department-level utilization metrics."""
    today = date.today()
    window_dates = [today.isoformat(), (today + timedelta(days=1)).isoformat()]

    departments = [
        dict(r) for r in conn.execute("SELECT * FROM departments ORDER BY code ASC").fetchall()
    ]
    equipment_rows = [
        dict(r) for r in conn.execute("SELECT * FROM equipment ORDER BY id ASC").fetchall()
    ]
    bookings = [
        dict(r)
        for r in conn.execute(
            """
            SELECT b.*, e.department_code AS equipment_department, e.name AS equipment_name
            FROM bookings b
            JOIN equipment e ON e.id = b.equipment_id
            WHERE b.status IN ('confirmed', 'conflict')
            """
        ).fetchall()
    ]

    window_bookings = [b for b in bookings if b["booking_date"] in window_dates]

    # Department-level aggregation over today + tomorrow capacity
    dept_stats: list[dict[str, Any]] = []
    total_capacity = 0
    total_booked = 0

    for dept in departments:
        code = dept["code"]
        dept_eq = [e for e in equipment_rows if e["department_code"] == code]
        cap_hours = sum(
            (int(e["lab_hours_end"]) - int(e["lab_hours_start"])) * len(window_dates)
            for e in dept_eq
        )
        dept_bookings = [b for b in window_bookings if b["equipment_department"] == code]
        booked_hours = sum(int(b["end_hour"]) - int(b["start_hour"]) for b in dept_bookings)
        cross_inbound = sum(1 for b in dept_bookings if b["student_department"] != code)

        total_capacity += cap_hours
        total_booked += booked_hours
        util_pct = round((booked_hours / max(1, cap_hours)) * 100, 1)

        dept_stats.append(
            {
                "department_code": code,
                "department_name": dept["name"],
                "building": dept["building"],
                "equipment_count": len(dept_eq),
                "booked_hours": booked_hours,
                "capacity_hours": cap_hours,
                "utilization_pct": util_pct,
                "cross_dept_inbound_bookings": cross_inbound,
            }
        )

    # Cross-department sharing ratio across all active bookings
    total_active_bookings = len(bookings)
    cross_dept_bookings = sum(
        1 for b in bookings if b["student_department"] != b["equipment_department"]
    )
    cross_dept_share_pct = round(
        (cross_dept_bookings / max(1, total_active_bookings)) * 100, 1
    )

    # Peak-hour demand curve (08:00 to 19:00)
    peak_hours: list[dict[str, Any]] = []
    for hour in range(8, 20):
        count = sum(
            1 for b in window_bookings if int(b["start_hour"]) <= hour < int(b["end_hour"])
        )
        peak_hours.append(
            {
                "hour": hour,
                "label": format_hour(hour),
                "active_sessions": count,
            }
        )

    # Identify underutilized resources (< 25% booked across 2-day window) to boost accessibility
    underutilized: list[dict[str, Any]] = []
    for eq in equipment_rows:
        cap = (int(eq["lab_hours_end"]) - int(eq["lab_hours_start"])) * len(window_dates)
        eq_booked = sum(
            int(b["end_hour"]) - int(b["start_hour"])
            for b in window_bookings
            if b["equipment_id"] == eq["id"]
        )
        util = round((eq_booked / max(1, cap)) * 100, 1)
        if util < 25.0 and eq["status"] == "available":
            underutilized.append(
                {
                    "equipment_id": eq["id"],
                    "name": eq["name"],
                    "department_code": eq["department_code"],
                    "category": eq["category"],
                    "location": eq["location"],
                    "utilization_pct": util,
                    "free_hours_next_48h": cap - eq_booked,
                }
            )
    underutilized.sort(key=lambda item: item["utilization_pct"])

    open_conflicts = conn.execute(
        "SELECT COUNT(*) FROM conflicts WHERE status = 'open'"
    ).fetchone()[0]
    resolved_conflicts = conn.execute(
        "SELECT COUNT(*) FROM conflicts WHERE status = 'resolved'"
    ).fetchone()[0]
    pending_requests = conn.execute(
        "SELECT COUNT(*) FROM project_requests WHERE status = 'pending'"
    ).fetchone()[0]

    recent_audit = [
        {
            "id": r["id"],
            "actor": r["actor"],
            "actor_role": r["actor_role"],
            "action": r["action"],
            "entity_type": r["entity_type"],
            "entity_id": r["entity_id"],
            "details": json.loads(r["details_json"]),
            "created_at": r["created_at"],
        }
        for r in conn.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT 12"
        ).fetchall()
    ]

    return {
        "kpis": {
            "total_resources": len(equipment_rows),
            "departments_connected": len(departments),
            "campus_utilization_pct": round((total_booked / max(1, total_capacity)) * 100, 1),
            "cross_department_share_pct": cross_dept_share_pct,
            "active_bookings": total_active_bookings,
            "open_conflicts": int(open_conflicts),
            "resolved_conflicts": int(resolved_conflicts),
            "pending_approvals": int(pending_requests),
        },
        "department_utilization": dept_stats,
        "peak_hours": peak_hours,
        "underutilized_opportunities": underutilized[:6],
        "recent_audit_logs": recent_audit,
    }
