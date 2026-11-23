"""Tests for deterministic fairness scoring and atomic scheduling conflict resolution."""

from __future__ import annotations

from datetime import date, timedelta

from app.services.conflict_resolver import compute_fairness_score


def test_fairness_scoring_prioritizes_urgent_low_usage_and_priority_credit():
    today = date.today()
    urgent_newcomer = {
        "id": 1,
        "student_name": "Aarav",
        "student_department": "ECE",
        "project_title": "Capstone Flood Model",
        "project_track": "capstone",
        "project_deadline": (today + timedelta(days=2)).isoformat(),
        "team_size": 4,
        "flexibility": "strict",
        "prior_hours_this_week": 0.0,
        "priority_credit": 0,
        "start_hour": 10,
        "end_hour": 14,
    }
    heavy_flexible_user = {
        "id": 2,
        "student_name": "Rohan",
        "student_department": "CSE",
        "project_title": "Exploratory Sweep",
        "project_track": "prototype",
        "project_deadline": (today + timedelta(days=18)).isoformat(),
        "team_size": 1,
        "flexibility": "both",
        "prior_hours_this_week": 12.0,
        "priority_credit": 0,
        "start_hour": 10,
        "end_hour": 14,
    }
    score_a = compute_fairness_score(urgent_newcomer, reference_date=today)
    score_b = compute_fairness_score(heavy_flexible_user, reference_date=today)

    assert score_a["total_score"] > score_b["total_score"] + 25
    assert score_a["breakdown"]["deadline_urgency"] > score_b["breakdown"]["deadline_urgency"]
    assert score_a["breakdown"]["usage_fairness"] > score_b["breakdown"]["usage_fairness"]

    # Priority credit increases usage fairness score for students who yielded earlier
    credited = {**heavy_flexible_user, "priority_credit": 2}
    score_credited = compute_fairness_score(credited, reference_date=today)
    assert score_credited["breakdown"]["usage_fairness"] > score_b["breakdown"]["usage_fairness"]


def test_analyze_and_apply_conflict_resolution_eliminates_all_overlaps(client):
    list_res = client.get("/api/conflicts", params={"status": "open"})
    assert list_res.status_code == 200
    open_conflicts = list_res.json()["conflicts"]
    assert len(open_conflicts) == 3

    conflict_id = open_conflicts[0]["conflict"]["id"]
    analysis_res = client.get(f"/api/conflicts/{conflict_id}/analyze")
    assert analysis_res.status_code == 200
    analysis = analysis_res.json()

    assert len(analysis["scored_bookings"]) == 2
    assert len(analysis["feasible_plans"]) >= 2
    recommended_id = analysis["ai_advice"]["recommended_plan_id"]
    assert recommended_id in {p["plan_id"] for p in analysis["feasible_plans"]}

    # Apply the recommended plan atomically
    apply_res = client.post(
        f"/api/conflicts/{conflict_id}/resolve",
        json={"plan_id": recommended_id, "resolved_by": "Dr. Ananya Deshmukh"},
    )
    assert apply_res.status_code == 200
    applied = apply_res.json()
    assert applied["status"] == "resolved"

    # Re-applying the same conflict must fail cleanly with 409
    dup = client.post(
        f"/api/conflicts/{conflict_id}/resolve",
        json={"plan_id": recommended_id, "resolved_by": "Dr. Ananya Deshmukh"},
    )
    assert dup.status_code == 409

    # Verify zero overlapping bookings exist across affected equipment/dates after resolution
    actions = applied["applied_plan"]["actions"]
    for action in actions:
        sched = client.get(
            f"/api/equipment/{action['equipment_id']}/schedule",
            params={"target_date": action["booking_date"]},
        ).json()
        for slot in sched["hourly_slots"]:
            if action["start_hour"] <= slot["hour"] < action["end_hour"]:
                assert slot["state"] == "booked"
                assert len(slot["bookings"]) == 1


def test_resolve_all_seeded_conflicts_updates_analytics_cleanly(client):
    open_conflicts = client.get("/api/conflicts", params={"status": "open"}).json()["conflicts"]
    assert len(open_conflicts) == 3

    for entry in open_conflicts:
        cid = entry["conflict"]["id"]
        analysis = client.get(f"/api/conflicts/{cid}/analyze").json()
        rec_plan_id = analysis["ai_advice"]["recommended_plan_id"]
        res = client.post(
            f"/api/conflicts/{cid}/resolve",
            json={"plan_id": rec_plan_id, "resolved_by": "Campus Lab Committee"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "resolved"

    analytics = client.get("/api/analytics/utilization").json()["kpis"]
    assert analytics["open_conflicts"] == 0
    assert analytics["resolved_conflicts"] == 4
