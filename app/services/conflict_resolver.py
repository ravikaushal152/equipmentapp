"""Deterministic fairness scoring and provably feasible scheduling conflict resolution."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.scheduler import _find_free_windows, format_hour

TRACK_WEIGHTS: dict[str, float] = {
    "capstone": 86.0,
    "competition": 82.0,
    "research": 74.0,
    "course_lab": 68.0,
    "prototype": 56.0,
}

FLEXIBILITY_SPECIFICITY: dict[str, float] = {
    "strict": 95.0,
    "flexible_time": 66.0,
    "flexible_equipment": 56.0,
    "both": 40.0,
}


def compute_fairness_score(
    booking: dict[str, Any], *, reference_date: date | None = None
) -> dict[str, Any]:
    """Compute a transparent 0-100 allocation priority & fairness score for a booking."""
    ref = reference_date or date.today()
    deadline = date.fromisoformat(booking["project_deadline"])
    days_left = (deadline - ref).days

    # 1. Deadline Urgency (35%)
    if days_left <= 1:
        deadline_score = 100.0
    elif days_left <= 3:
        deadline_score = 92.0
    elif days_left <= 5:
        deadline_score = 80.0
    elif days_left <= 8:
        deadline_score = 66.0
    elif days_left <= 14:
        deadline_score = 46.0
    else:
        deadline_score = 26.0

    # 2. Usage Fairness / Anti-Starvation (25%)
    prior_hours = float(booking.get("prior_hours_this_week", 0.0))
    priority_credit = int(booking.get("priority_credit", 0))
    usage_score = max(15.0, 100.0 - prior_hours * 7.5) + min(18.0, priority_credit * 9.0)
    usage_score = min(100.0, usage_score)

    # 3. Requirement Specificity (20%)
    flex = str(booking.get("flexibility", "flexible_time"))
    specificity_score = FLEXIBILITY_SPECIFICITY.get(flex, 60.0)

    # 4. Project Impact & Team Size (20%)
    track = str(booking.get("project_track", "prototype"))
    team_size = int(booking.get("team_size", 1))
    impact_score = min(100.0, TRACK_WEIGHTS.get(track, 60.0) + min(14.0, (team_size - 1) * 4.0))

    total = round(
        0.35 * deadline_score
        + 0.25 * usage_score
        + 0.20 * specificity_score
        + 0.20 * impact_score,
        1,
    )

    return {
        "booking_id": booking["id"],
        "student_name": booking["student_name"],
        "student_department": booking["student_department"],
        "project_title": booking["project_title"],
        "project_track": track,
        "project_deadline": booking["project_deadline"],
        "days_until_deadline": days_left,
        "team_size": team_size,
        "flexibility": flex,
        "prior_hours_this_week": prior_hours,
        "priority_credit": priority_credit,
        "start_hour": booking["start_hour"],
        "end_hour": booking["end_hour"],
        "total_score": total,
        "breakdown": {
            "deadline_urgency": round(deadline_score, 1),
            "usage_fairness": round(usage_score, 1),
            "equipment_specificity": round(specificity_score, 1),
            "project_impact": round(impact_score, 1),
        },
    }


def _nearest_window(
    windows: list[tuple[int, int]], preferred_start: int
) -> tuple[int, int] | None:
    if not windows:
        return None
    return min(windows, key=lambda w: (abs(w[0] - preferred_start), w[0]))


def build_conflict_analysis(
    conn: sqlite3.Connection, conflict_id: int
) -> dict[str, Any]:
    """Analyze a conflict, score competing bookings, and generate provably feasible plans."""
    conflict_row = conn.execute(
        """
        SELECT c.*, e.name AS equipment_name, e.department_code AS equipment_department,
               e.compatibility_group, e.location, e.lab_hours_start, e.lab_hours_end,
               e.max_session_hours
        FROM conflicts c
        JOIN equipment e ON e.id = c.equipment_id
        WHERE c.id = ?
        """,
        (conflict_id,),
    ).fetchone()
    if conflict_row is None:
        raise ValueError(f"Conflict {conflict_id} not found.")

    conflict = dict(conflict_row)
    competing_ids: list[int] = json.loads(conflict["competing_booking_ids_json"])
    if not competing_ids:
        return {"conflict": conflict, "scored_bookings": [], "feasible_plans": []}

    placeholders = ",".join("?" for _ in competing_ids)
    bookings = [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM bookings WHERE id IN ({placeholders})",
            competing_ids,
        ).fetchall()
    ]

    ref_date = date.today()
    scored = [compute_fairness_score(b, reference_date=ref_date) for b in bookings]
    scored.sort(key=lambda s: (s["total_score"], -s["days_until_deadline"]), reverse=True)

    if conflict["status"] == "resolved" or len(scored) < 2:
        return {
            "conflict": {
                "id": conflict["id"],
                "equipment_id": conflict["equipment_id"],
                "equipment_name": conflict["equipment_name"],
                "equipment_department": conflict["equipment_department"],
                "location": conflict["location"],
                "conflict_date": conflict["conflict_date"],
                "overlap_start_hour": conflict["overlap_start_hour"],
                "overlap_end_hour": conflict["overlap_end_hour"],
                "overlap_label": (
                    f"{format_hour(conflict['overlap_start_hour'])}–"
                    f"{format_hour(conflict['overlap_end_hour'])}"
                ),
                "severity": conflict["severity"],
                "status": conflict["status"],
                "resolution_strategy": conflict["resolution_strategy"],
                "resolution_summary": conflict["resolution_summary"],
                "resolved_by": conflict["resolved_by"],
                "resolved_at": conflict["resolved_at"],
            },
            "scored_bookings": scored,
            "feasible_plans": [],
        }

    booking_by_id = {b["id"]: b for b in bookings}
    winner_score = scored[0]
    secondary_score = scored[1]
    winner = booking_by_id[winner_score["booking_id"]]
    secondary = booking_by_id[secondary_score["booking_id"]]
    secondary_duration = int(secondary["end_hour"]) - int(secondary["start_hour"])
    contested_date = date.fromisoformat(conflict["conflict_date"])
    secondary_deadline = date.fromisoformat(secondary["project_deadline"])

    primary_eq = {
        "id": conflict["equipment_id"],
        "name": conflict["equipment_name"],
        "department_code": conflict["equipment_department"],
        "lab_hours_start": conflict["lab_hours_start"],
        "lab_hours_end": conflict["lab_hours_end"],
    }

    feasible_plans: list[dict[str, Any]] = []
    excluded_ids = {winner["id"], secondary["id"]}

    # --- PLAN 1: Cross-Department Equivalent Reallocation (zero time disruption!) ---
    equivalents = [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM equipment
            WHERE compatibility_group = ?
              AND id != ?
              AND status = 'available'
            ORDER BY CASE WHEN department_code != ? THEN 0 ELSE 1 END, id ASC
            """,
            (conflict["compatibility_group"], conflict["equipment_id"], conflict["equipment_department"]),
        ).fetchall()
    ]

    best_cross_plan = None
    for eq in equivalents:
        for day_offset in (0, 1):
            candidate_date = contested_date + timedelta(days=day_offset)
            if candidate_date > secondary_deadline:
                continue
            windows, util_pct, _ = _find_free_windows(
                conn, eq, candidate_date.isoformat(), secondary_duration
            )
            exact_match = (int(secondary["start_hour"]), int(secondary["end_hour"])) in windows
            chosen = (
                (int(secondary["start_hour"]), int(secondary["end_hour"]))
                if exact_match
                else _nearest_window(windows, int(secondary["start_hour"]))
            )
            if chosen:
                same_time = exact_match and day_offset == 0
                flex_bonus = 10.0 if secondary["flexibility"] in ("flexible_equipment", "both") else 0.0
                feasibility = round(min(99.0, (93.0 if same_time else 84.0) + flex_bonus * 0.5), 1)
                fairness_idx = round(min(99.0, 88.0 + (8.0 if same_time else 3.0)), 1)
                best_cross_plan = {
                    "plan_id": f"cross_dept_{eq['id']}_{candidate_date.isoformat()}_{chosen[0]}",
                    "strategy": "cross_dept_reallocation",
                    "title": (
                        f"Cross-Dept Reallocation → {eq['name']} ({eq['department_code']})"
                    ),
                    "feasibility_score": feasibility,
                    "fairness_index": fairness_idx,
                    "utilization_gain_note": (
                        f"Shifts load to underutilized {eq['department_code']} lab ({util_pct:.0f}% booked) "
                        "while keeping both teams on schedule."
                    ),
                    "summary": (
                        f"Keep {winner['student_name']} ({winner['student_department']}) on "
                        f"{conflict['equipment_name']} at "
                        f"{format_hour(winner['start_hour'])}–{format_hour(winner['end_hour'])}; "
                        f"reallocate {secondary['student_name']} to compatible {eq['name']} "
                        f"({eq['department_code']}) on {candidate_date.isoformat()} "
                        f"{format_hour(chosen[0])}–{format_hour(chosen[1])}."
                    ),
                    "actions": [
                        {
                            "booking_id": winner["id"],
                            "student_name": winner["student_name"],
                            "equipment_id": conflict["equipment_id"],
                            "equipment_name": conflict["equipment_name"],
                            "booking_date": winner["booking_date"],
                            "start_hour": int(winner["start_hour"]),
                            "end_hour": int(winner["end_hour"]),
                            "new_status": "confirmed",
                            "priority_credit_delta": 0,
                            "change_label": "Retains original slot (higher deadline & fairness priority)",
                        },
                        {
                            "booking_id": secondary["id"],
                            "student_name": secondary["student_name"],
                            "equipment_id": eq["id"],
                            "equipment_name": eq["name"],
                            "booking_date": candidate_date.isoformat(),
                            "start_hour": chosen[0],
                            "end_hour": chosen[1],
                            "new_status": "confirmed",
                            "priority_credit_delta": 1,
                            "change_label": (
                                f"Reallocated to {eq['department_code']} equivalent "
                                f"({'exact same time!' if same_time else format_hour(chosen[0]) + '–' + format_hour(chosen[1])}) +1 priority credit"
                            ),
                        },
                    ],
                }
                break
        if best_cross_plan:
            break

    if best_cross_plan:
        feasible_plans.append(best_cross_plan)

    # --- PLAN 2: Priority Allocation + Same-Resource Shift + Priority Credit ---
    shift_plan = None
    for day_offset in (0, 1, 2):
        candidate_date = contested_date + timedelta(days=day_offset)
        if candidate_date > secondary_deadline:
            continue
        # When shifting on the contested date, reserve winner's slot so we never overlap winner
        windows, _, occupied = _find_free_windows(
            conn,
            primary_eq,
            candidate_date.isoformat(),
            secondary_duration,
            exclude_booking_ids=excluded_ids,
        )
        if day_offset == 0:
            winner_hours = set(range(int(winner["start_hour"]), int(winner["end_hour"])))
            windows = [
                (w_s, w_e)
                for (w_s, w_e) in windows
                if all(h not in winner_hours for h in range(w_s, w_e))
            ]

        chosen = _nearest_window(windows, int(secondary["start_hour"]))
        if chosen:
            same_day = day_offset == 0
            feasibility = 91.0 if same_day else 82.0
            fairness_idx = 90.0 if same_day else 83.0
            shift_plan = {
                "plan_id": f"shift_{candidate_date.isoformat()}_{chosen[0]}",
                "strategy": "same_resource_shift",
                "title": (
                    f"{'Same-Day' if same_day else 'Next-Day'} Priority Shift + Priority Credit"
                ),
                "feasibility_score": feasibility,
                "fairness_index": fairness_idx,
                "utilization_gain_note": (
                    "Packs sessions back-to-back on the same resource to avoid idle gaps while rewarding the flexible team."
                ),
                "summary": (
                    f"Confirm {winner['student_name']} ({winner['project_track']}, due in "
                    f"{winner_score['days_until_deadline']}d) in the contested slot; reschedule "
                    f"{secondary['student_name']} on {conflict['equipment_name']} to "
                    f"{candidate_date.isoformat()} {format_hour(chosen[0])}–{format_hour(chosen[1])} "
                    f"with +1 priority credit for future bookings."
                ),
                "actions": [
                    {
                        "booking_id": winner["id"],
                        "student_name": winner["student_name"],
                        "equipment_id": conflict["equipment_id"],
                        "equipment_name": conflict["equipment_name"],
                        "booking_date": winner["booking_date"],
                        "start_hour": int(winner["start_hour"]),
                        "end_hour": int(winner["end_hour"]),
                        "new_status": "confirmed",
                        "priority_credit_delta": 0,
                        "change_label": "Confirmed in contested slot",
                    },
                    {
                        "booking_id": secondary["id"],
                        "student_name": secondary["student_name"],
                        "equipment_id": conflict["equipment_id"],
                        "equipment_name": conflict["equipment_name"],
                        "booking_date": candidate_date.isoformat(),
                        "start_hour": chosen[0],
                        "end_hour": chosen[1],
                        "new_status": "confirmed",
                        "priority_credit_delta": 1,
                        "change_label": (
                            f"Shifted to {candidate_date.isoformat()} "
                            f"{format_hour(chosen[0])}–{format_hour(chosen[1])} +1 priority credit"
                        ),
                    },
                ],
            }
            break

    if shift_plan:
        feasible_plans.append(shift_plan)

    # --- PLAN 3: Balanced Split-Shift Allocation ---
    overlap_start = min(int(winner["start_hour"]), int(secondary["start_hour"]))
    overlap_end = max(int(winner["end_hour"]), int(secondary["end_hour"]))
    span = overlap_end - overlap_start
    if span >= 2:
        # Give slightly larger share to higher-priority booking if odd span
        winner_hours_alloc = (span + 1) // 2
        split_point = overlap_start + winner_hours_alloc
        if split_point < overlap_end:
            feasible_plans.append(
                {
                    "plan_id": f"split_{contested_date.isoformat()}_{overlap_start}_{split_point}",
                    "strategy": "split_shift",
                    "title": "Balanced Split-Shift Within Contested Window",
                    "feasibility_score": 86.0,
                    "fairness_index": 87.0,
                    "utilization_gain_note": (
                        "Maintains 100% continuous utilization of the contested window so both teams unblock immediately."
                    ),
                    "summary": (
                        f"Split {format_hour(overlap_start)}–{format_hour(overlap_end)} into consecutive blocks: "
                        f"{winner['student_name']} takes {format_hour(overlap_start)}–{format_hour(split_point)} "
                        f"and {secondary['student_name']} takes {format_hour(split_point)}–{format_hour(overlap_end)}."
                    ),
                    "actions": [
                        {
                            "booking_id": winner["id"],
                            "student_name": winner["student_name"],
                            "equipment_id": conflict["equipment_id"],
                            "equipment_name": conflict["equipment_name"],
                            "booking_date": conflict["conflict_date"],
                            "start_hour": overlap_start,
                            "end_hour": split_point,
                            "new_status": "confirmed",
                            "priority_credit_delta": 0,
                            "change_label": (
                                f"Allocated first block ({format_hour(overlap_start)}–{format_hour(split_point)})"
                            ),
                        },
                        {
                            "booking_id": secondary["id"],
                            "student_name": secondary["student_name"],
                            "equipment_id": conflict["equipment_id"],
                            "equipment_name": conflict["equipment_name"],
                            "booking_date": conflict["conflict_date"],
                            "start_hour": split_point,
                            "end_hour": overlap_end,
                            "new_status": "confirmed",
                            "priority_credit_delta": 1,
                            "change_label": (
                                f"Allocated second block ({format_hour(split_point)}–{format_hour(overlap_end)}) +1 priority credit"
                            ),
                        },
                    ],
                }
            )

    feasible_plans.sort(
        key=lambda p: (p["feasibility_score"] * 0.5 + p["fairness_index"] * 0.5),
        reverse=True,
    )

    return {
        "conflict": {
            "id": conflict["id"],
            "equipment_id": conflict["equipment_id"],
            "equipment_name": conflict["equipment_name"],
            "equipment_department": conflict["equipment_department"],
            "location": conflict["location"],
            "conflict_date": conflict["conflict_date"],
            "overlap_start_hour": conflict["overlap_start_hour"],
            "overlap_end_hour": conflict["overlap_end_hour"],
            "overlap_label": (
                f"{format_hour(conflict['overlap_start_hour'])}–"
                f"{format_hour(conflict['overlap_end_hour'])}"
            ),
            "severity": conflict["severity"],
            "status": conflict["status"],
            "resolution_strategy": conflict["resolution_strategy"],
            "resolution_summary": conflict["resolution_summary"],
            "resolved_by": conflict["resolved_by"],
            "resolved_at": conflict["resolved_at"],
        },
        "scored_bookings": scored,
        "feasible_plans": feasible_plans,
    }


def apply_resolution_plan(
    conn: sqlite3.Connection,
    conflict_id: int,
    plan_id: str,
    resolved_by: str,
) -> dict[str, Any]:
    """Atomically apply a verified conflict resolution plan inside an open DB transaction."""
    analysis = build_conflict_analysis(conn, conflict_id)
    conflict = analysis["conflict"]
    if conflict["status"] == "resolved":
        raise ValueError("This scheduling conflict has already been resolved.")

    selected = next(
        (plan for plan in analysis["feasible_plans"] if plan["plan_id"] == plan_id),
        None,
    )
    if selected is None:
        raise ValueError(f"Resolution plan '{plan_id}' is no longer available or invalid.")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Apply each allocation update atomically
    for action in selected["actions"]:
        conn.execute(
            """
            UPDATE bookings
            SET equipment_id = ?,
                booking_date = ?,
                start_hour = ?,
                end_hour = ?,
                status = ?,
                priority_credit = priority_credit + ?,
                notes = CASE
                    WHEN notes = '' THEN ?
                    ELSE notes || ' | ' || ?
                END
            WHERE id = ?
            """,
            (
                action["equipment_id"],
                action["booking_date"],
                action["start_hour"],
                action["end_hour"],
                action["new_status"],
                action["priority_credit_delta"],
                f"Resolved via LabLink ({selected['strategy']}): {action['change_label']}",
                f"Resolved via LabLink ({selected['strategy']}): {action['change_label']}",
                action["booking_id"],
            ),
        )

    conn.execute(
        """
        UPDATE conflicts
        SET status = 'resolved',
            resolution_strategy = ?,
            resolution_summary = ?,
            resolved_by = ?,
            resolved_at = ?
        WHERE id = ?
        """,
        (selected["strategy"], selected["summary"], resolved_by, now, conflict_id),
    )

    conn.execute(
        """
        INSERT INTO audit_logs (actor, actor_role, action, entity_type, entity_id, details_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resolved_by,
            "faculty",
            "conflict_resolved",
            "conflict",
            conflict_id,
            json.dumps(
                {
                    "plan_id": selected["plan_id"],
                    "strategy": selected["strategy"],
                    "summary": selected["summary"],
                    "actions": selected["actions"],
                }
            ),
            now,
        ),
    )

    return {
        "conflict_id": conflict_id,
        "status": "resolved",
        "applied_plan": selected,
        "resolved_by": resolved_by,
        "resolved_at": now,
    }
