"""Intelligent slot booking, overlap detection, and utilization-optimizing recommendations."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

ACTIVE_BOOKING_STATUSES = ("confirmed", "conflict")


def format_hour(hour: int) -> str:
    """Format an integer hour (0-23) into HH:00 24-hour notation."""
    return f"{hour:02d}:00"


def find_overlapping_bookings(
    conn: sqlite3.Connection,
    equipment_id: int,
    booking_date: str,
    start_hour: int,
    end_hour: int,
    *,
    exclude_booking_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Return all active bookings overlapping [start_hour, end_hour) on equipment_id/date."""
    rows = conn.execute(
        """
        SELECT b.*, e.name AS equipment_name, e.department_code AS equipment_department
        FROM bookings b
        JOIN equipment e ON e.id = b.equipment_id
        WHERE b.equipment_id = ?
          AND b.booking_date = ?
          AND b.status IN ('confirmed', 'conflict')
          AND b.start_hour < ?
          AND b.end_hour > ?
        ORDER BY b.start_hour ASC, b.id ASC
        """,
        (equipment_id, booking_date, end_hour, start_hour),
    ).fetchall()

    excluded = exclude_booking_ids or set()
    return [dict(row) for row in rows if row["id"] not in excluded]


def get_equipment_schedule(
    conn: sqlite3.Connection, equipment_id: int, target_date: str
) -> dict[str, Any] | None:
    """Build an hourly timeline and utilization summary for one resource on target_date."""
    eq_row = conn.execute(
        """
        SELECT e.*, d.name AS department_name, d.building
        FROM equipment e
        JOIN departments d ON d.code = e.department_code
        WHERE e.id = ?
        """,
        (equipment_id,),
    ).fetchone()
    if eq_row is None:
        return None

    equipment = dict(eq_row)
    equipment["specs"] = json.loads(equipment.pop("specs_json"))

    bookings = [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM bookings
            WHERE equipment_id = ?
              AND booking_date = ?
              AND status IN ('confirmed', 'conflict')
            ORDER BY start_hour ASC, id ASC
            """,
            (equipment_id, target_date),
        ).fetchall()
    ]

    start_h = int(equipment["lab_hours_start"])
    end_h = int(equipment["lab_hours_end"])
    total_hours = max(1, end_h - start_h)

    hourly_slots: list[dict[str, Any]] = []
    occupied_hours = 0
    for hour in range(start_h, end_h):
        matching = [b for b in bookings if b["start_hour"] <= hour < b["end_hour"]]
        if len(matching) > 1 or any(b["status"] == "conflict" for b in matching):
            slot_state = "conflict"
            occupied_hours += 1
        elif len(matching) == 1:
            slot_state = "booked"
            occupied_hours += 1
        else:
            slot_state = "open"

        hourly_slots.append(
            {
                "hour": hour,
                "label": f"{format_hour(hour)}–{format_hour(hour + 1)}",
                "state": slot_state,
                "bookings": [
                    {
                        "id": b["id"],
                        "student_name": b["student_name"],
                        "student_department": b["student_department"],
                        "project_title": b["project_title"],
                        "status": b["status"],
                        "start_hour": b["start_hour"],
                        "end_hour": b["end_hour"],
                    }
                    for b in matching
                ],
            }
        )

    utilization_pct = round((occupied_hours / total_hours) * 100, 1)
    return {
        "equipment": equipment,
        "date": target_date,
        "lab_hours": {"start": start_h, "end": end_h},
        "utilization_pct": utilization_pct,
        "occupied_hours": occupied_hours,
        "open_hours": total_hours - occupied_hours,
        "hourly_slots": hourly_slots,
        "bookings": bookings,
    }


def _find_free_windows(
    conn: sqlite3.Connection,
    equipment_row: dict[str, Any],
    target_date: str,
    duration_hours: int,
    *,
    exclude_booking_ids: set[int] | None = None,
) -> tuple[list[tuple[int, int]], float, set[int]]:
    """Return (free_windows, current_utilization_pct, occupied_hour_set) for equipment/date."""
    start_h = int(equipment_row["lab_hours_start"])
    end_h = int(equipment_row["lab_hours_end"])
    excluded = exclude_booking_ids or set()

    rows = conn.execute(
        """
        SELECT id, start_hour, end_hour
        FROM bookings
        WHERE equipment_id = ?
          AND booking_date = ?
          AND status IN ('confirmed', 'conflict')
        """,
        (equipment_row["id"], target_date),
    ).fetchall()

    occupied: set[int] = set()
    for row in rows:
        if row["id"] in excluded:
            continue
        for hour in range(int(row["start_hour"]), int(row["end_hour"])):
            occupied.add(hour)

    total_hours = max(1, end_h - start_h)
    util_pct = round((len(occupied) / total_hours) * 100, 1)

    windows: list[tuple[int, int]] = []
    for candidate_start in range(start_h, end_h - duration_hours + 1):
        candidate_end = candidate_start + duration_hours
        if all(h not in occupied for h in range(candidate_start, candidate_end)):
            windows.append((candidate_start, candidate_end))

    return windows, util_pct, occupied


def recommend_smart_slots(
    conn: sqlite3.Connection,
    equipment_id: int,
    preferred_date: str,
    duration_hours: int,
    project_deadline: str | None = None,
    *,
    include_cross_department: bool = True,
) -> dict[str, Any]:
    """Recommend conflict-free, utilization-optimized slots across primary & equivalent resources."""
    primary_row = conn.execute(
        """
        SELECT e.*, d.name AS department_name
        FROM equipment e
        JOIN departments d ON d.code = e.department_code
        WHERE e.id = ?
        """,
        (equipment_id,),
    ).fetchone()
    if primary_row is None:
        raise ValueError(f"Equipment {equipment_id} not found.")

    primary = dict(primary_row)
    effective_duration = min(duration_hours, int(primary["max_session_hours"]))

    candidates: list[dict[str, Any]] = [primary]
    if include_cross_department:
        equivalents = conn.execute(
            """
            SELECT e.*, d.name AS department_name
            FROM equipment e
            JOIN departments d ON d.code = e.department_code
            WHERE e.compatibility_group = ?
              AND e.id != ?
              AND e.status = 'available'
            ORDER BY e.requires_faculty_approval ASC, e.id ASC
            """,
            (primary["compatibility_group"], equipment_id),
        ).fetchall()
        candidates.extend(dict(r) for r in equivalents)

    base_date = date.fromisoformat(preferred_date)
    deadline_date = date.fromisoformat(project_deadline) if project_deadline else None
    search_dates = [base_date + timedelta(days=offset) for offset in range(3)]

    recommendations: list[dict[str, Any]] = []
    primary_util_on_date = 0.0

    for eq in candidates:
        is_primary = eq["id"] == primary["id"]
        is_cross_dept = eq["department_code"] != primary["department_code"]

        for day_offset, target_d in enumerate(search_dates):
            if deadline_date and target_d > deadline_date:
                continue
            target_iso = target_d.isoformat()
            windows, util_pct, occupied = _find_free_windows(
                conn, eq, target_iso, effective_duration
            )
            if is_primary and day_offset == 0:
                primary_util_on_date = util_pct

            if not windows:
                continue

            # Score each open window for utilization optimization & convenience
            best_window = None
            best_score = -1.0
            best_reasons: list[str] = []

            for w_start, w_end in windows:
                score = 62.0
                reasons: list[str] = []

                if is_primary:
                    score += 14.0
                elif is_cross_dept:
                    score += 8.0
                    if primary_util_on_date >= 45.0 and util_pct < primary_util_on_date:
                        score += 10.0
                        reasons.append(
                            f"Balances load to {eq['department_code']} ({util_pct:.0f}% booked vs {primary_util_on_date:.0f}%)"
                        )
                    else:
                        reasons.append(
                            f"Cross-department equivalent in {eq['department_code']} lab"
                        )

                # Date proximity
                score -= day_offset * 9.0
                if day_offset == 0:
                    reasons.append("Matches preferred date")
                else:
                    reasons.append(f"+{day_offset} day(s) out, safely before deadline")

                # Gap-packing bonus: adjacent to existing session reduces dead idle slivers
                adjacent_before = (w_start - 1) in occupied
                adjacent_after = w_end in occupied
                if adjacent_before or adjacent_after:
                    score += 12.0
                    reasons.append("Packs adjacent to existing session (minimizes idle lab gap)")
                elif not occupied:
                    # Prefer clean morning or early afternoon starts on open days
                    if w_start in (9, 10, 14):
                        score += 5.0
                    reasons.append("Open lab block with high availability")

                # Off-peak bonus (early morning or late afternoon)
                if w_start <= 9 or w_start >= 16:
                    score += 4.0
                    reasons.append("Off-peak window improves campus throughput")

                # Instant-booking bonus when no faculty approval bottleneck
                if not eq["requires_faculty_approval"]:
                    score += 4.0
                    reasons.append("Instant booking (no faculty approval wait)")

                score = round(min(99.0, max(35.0, score)), 1)
                if score > best_score:
                    best_score = score
                    best_window = (w_start, w_end)
                    best_reasons = reasons[:3]

            if best_window:
                recommendations.append(
                    {
                        "equipment_id": eq["id"],
                        "equipment_name": eq["name"],
                        "department_code": eq["department_code"],
                        "location": eq["location"],
                        "is_primary_resource": is_primary,
                        "is_cross_department_alternative": is_cross_dept,
                        "requires_faculty_approval": bool(eq["requires_faculty_approval"]),
                        "date": target_iso,
                        "start_hour": best_window[0],
                        "end_hour": best_window[1],
                        "time_label": f"{format_hour(best_window[0])}–{format_hour(best_window[1])}",
                        "current_utilization_pct": util_pct,
                        "optimization_score": best_score,
                        "reasons": best_reasons,
                    }
                )

    recommendations.sort(key=lambda item: item["optimization_score"], reverse=True)
    return {
        "primary_equipment": {
            "id": primary["id"],
            "name": primary["name"],
            "department_code": primary["department_code"],
            "compatibility_group": primary["compatibility_group"],
            "utilization_on_date_pct": primary_util_on_date,
        },
        "requested_duration_hours": effective_duration,
        "recommendations": recommendations[:6],
    }
