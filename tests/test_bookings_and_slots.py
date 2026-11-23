"""Tests for smart slot recommendations, slot bookings, overlap detection, and cancellation."""

from __future__ import annotations

from datetime import date, timedelta


def test_smart_slots_returns_conflict_free_optimized_windows(client):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    deadline = (date.today() + timedelta(days=5)).isoformat()

    res = client.post(
        "/api/bookings/smart-slots",
        json={
            "equipment_id": 1,
            "preferred_date": tomorrow,
            "duration_hours": 2,
            "project_deadline": deadline,
            "include_cross_department": True,
        },
    )
    assert res.status_code == 200
    data = res.json()
    recs = data["recommendations"]
    assert len(recs) >= 2
    assert any(r["is_cross_department_alternative"] for r in recs)

    # Verify the top recommended slot is genuinely conflict-free in the live schedule
    top = recs[0]
    sched = client.get(
        f"/api/equipment/{top['equipment_id']}/schedule",
        params={"target_date": top["date"]},
    ).json()
    slot_states = {s["hour"]: s["state"] for s in sched["hourly_slots"]}
    for h in range(top["start_hour"], top["end_hour"]):
        assert slot_states[h] == "open"


def test_create_booking_confirmed_and_overlap_triggers_conflict(client):
    day_out = (date.today() + timedelta(days=3)).isoformat()
    deadline = (date.today() + timedelta(days=6)).isoformat()

    # First booking on a clean slot -> confirmed
    res1 = client.post(
        "/api/bookings",
        json={
            "equipment_id": 5,
            "student_name": "Anjali Rao",
            "student_id": "ECE23B011",
            "student_department": "ECE",
            "project_title": "Smart Campus Air Quality Edge Node",
            "project_track": "capstone",
            "project_deadline": deadline,
            "booking_date": day_out,
            "start_hour": 10,
            "end_hour": 12,
            "team_size": 3,
            "flexibility": "strict",
            "prior_hours_this_week": 0.0,
        },
    )
    assert res1.status_code == 201
    body1 = res1.json()
    assert body1["conflict_detected"] is False
    assert body1["booking"]["status"] == "confirmed"

    # Overlapping second booking -> flags a live conflict automatically
    res2 = client.post(
        "/api/bookings",
        json={
            "equipment_id": 5,
            "student_name": "Karan Mehta",
            "student_id": "CSE22B090",
            "student_department": "CSE",
            "project_title": "Gesture Classification Benchmark",
            "project_track": "prototype",
            "project_deadline": (date.today() + timedelta(days=14)).isoformat(),
            "booking_date": day_out,
            "start_hour": 11,
            "end_hour": 13,
            "team_size": 1,
            "flexibility": "both",
            "prior_hours_this_week": 7.0,
        },
    )
    assert res2.status_code == 201
    body2 = res2.json()
    assert body2["conflict_detected"] is True
    assert body2["conflict_id"] is not None
    assert body2["booking"]["status"] == "conflict"


def test_booking_validation_and_cancellation(client):
    day_out = (date.today() + timedelta(days=4)).isoformat()

    # Invalid: exceeds lab operating hours
    bad = client.post(
        "/api/bookings",
        json={
            "equipment_id": 14,  # BIO closes at 19:00
            "student_name": "Test Student",
            "student_id": "BIO23B001",
            "student_department": "BIO",
            "project_title": "Late Night Assay",
            "project_track": "research",
            "project_deadline": day_out,
            "booking_date": day_out,
            "start_hour": 18,
            "end_hour": 20,
        },
    )
    assert bad.status_code == 422

    # Create valid booking and cancel it
    ok = client.post(
        "/api/bookings",
        json={
            "equipment_id": 11,
            "student_name": "Rohan Das",
            "student_id": "MECH23B010",
            "student_department": "MECH",
            "project_title": "Sensor Bracket Print",
            "project_track": "course_lab",
            "project_deadline": day_out,
            "booking_date": day_out,
            "start_hour": 9,
            "end_hour": 11,
        },
    )
    assert ok.status_code == 201
    booking_id = ok.json()["booking"]["id"]

    cancel_res = client.patch(f"/api/bookings/{booking_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"

    # Second cancel fails cleanly
    again = client.patch(f"/api/bookings/{booking_id}/cancel")
    assert again.status_code == 400

    # Non-existent resources & bookings return clean 404s
    assert client.patch("/api/bookings/9999/cancel").status_code == 404
    assert (
        client.post(
            "/api/bookings/smart-slots",
            json={"equipment_id": 9999, "preferred_date": day_out, "duration_hours": 2},
        ).status_code
        == 404
    )


def test_list_bookings_and_cross_dept_analytics_update(client):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    before = client.get("/api/analytics/utilization").json()["kpis"]["active_bookings"]

    res = client.post(
        "/api/bookings",
        json={
            "equipment_id": 12,  # ROBO Prusa XL
            "student_name": "Meera Iyer",
            "student_id": "BIO23B022",
            "student_department": "BIO",
            "project_title": "Microfluidic Chip Holder Enclosure",
            "project_track": "research",
            "project_deadline": (date.today() + timedelta(days=5)).isoformat(),
            "booking_date": tomorrow,
            "start_hour": 15,
            "end_hour": 17,
        },
    )
    assert res.status_code == 201

    listed = client.get("/api/bookings", params={"department": "ROBO", "booking_date": tomorrow})
    assert listed.status_code == 200
    assert any(b["student_name"] == "Meera Iyer" for b in listed.json()["bookings"])

    after = client.get("/api/analytics/utilization").json()["kpis"]["active_bookings"]
    assert after == before + 1
