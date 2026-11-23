"""Tests for project-based access requests, AI briefs, and faculty approval workflows."""

from __future__ import annotations

from datetime import date, timedelta


def test_project_brief_preview_and_request_submission(client):
    req_date = (date.today() + timedelta(days=3)).isoformat()
    deadline = (date.today() + timedelta(days=6)).isoformat()

    brief_res = client.post(
        "/api/ai/project-brief",
        json={
            "equipment_id": 13,
            "student_department": "ECE",
            "project_title": "Tactile Sensor Pick-and-Place Testbed",
            "project_track": "capstone",
            "project_deadline": deadline,
            "team_size": 3,
            "justification": "Testing custom capacitive fingertip sensors mounted on the UR3e gripper for our ECE-Robotics capstone demo.",
            "safety_acknowledged": True,
        },
    )
    assert brief_res.status_code == 200
    brief = brief_res.json()["brief"]
    assert 45 <= brief["readiness_score"] <= 99
    assert brief["recommended_action"] == "Approve"

    submit_res = client.post(
        "/api/requests",
        json={
            "student_name": "Nikhil Joshi",
            "student_id": "ECE22B073",
            "student_email": "nikhil.joshi@campus.edu",
            "student_department": "ECE",
            "project_title": "Tactile Sensor Pick-and-Place Testbed",
            "project_track": "capstone",
            "project_deadline": deadline,
            "team_size": 3,
            "equipment_id": 13,
            "requested_date": req_date,
            "start_hour": 10,
            "end_hour": 12,
            "justification": "Testing custom capacitive fingertip sensors mounted on the UR3e gripper for our ECE-Robotics capstone demo.",
            "safety_acknowledged": True,
            "flexibility": "flexible_time",
        },
    )
    assert submit_res.status_code == 201
    req = submit_res.json()["request"]
    assert req["status"] == "pending"
    assert req["ai_brief"]["summary"]

    # Faculty approves request -> booking created cleanly
    decision = client.post(
        f"/api/requests/{req['id']}/decision",
        json={
            "decision": "approved",
            "reviewer_name": "Dr. Meera Nair",
            "reviewer_notes": "Cleared for Innovation Foundry Cell B; bring safety card.",
        },
    )
    assert decision.status_code == 200
    dec_body = decision.json()
    assert dec_body["status"] == "approved"
    assert dec_body["booking_id"] is not None
    assert dec_body["conflict_id"] is None

    # Attempting to modify an already-approved request returns 409
    repeat = client.post(
        f"/api/requests/{req['id']}/decision",
        json={"decision": "rejected", "reviewer_name": "Dr. Meera Nair"},
    )
    assert repeat.status_code == 409


def test_faculty_approval_on_overlapping_slot_opens_conflict(client):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    deadline = (date.today() + timedelta(days=4)).isoformat()

    # Request UR3e tomorrow 09:00-11:00 which is already booked by Ishaan Verma in seed data
    create = client.post(
        "/api/requests",
        json={
            "student_name": "Aditi Menon",
            "student_id": "CSE22B034",
            "student_email": "aditi.m@campus.edu",
            "student_department": "CSE",
            "project_title": "Vision-Based Sorting Demo",
            "project_track": "competition",
            "project_deadline": deadline,
            "team_size": 2,
            "equipment_id": 13,
            "requested_date": tomorrow,
            "start_hour": 9,
            "end_hour": 11,
            "justification": "Running visual servoing test sequence ahead of campus robotics showcase.",
            "safety_acknowledged": True,
            "flexibility": "flexible_time",
        },
    )
    assert create.status_code == 201
    assert create.json()["has_slot_conflict"] is True
    req_id = create.json()["request"]["id"]

    approve = client.post(
        f"/api/requests/{req_id}/decision",
        json={"decision": "approved", "reviewer_name": "Dr. Meera Nair"},
    )
    assert approve.status_code == 200
    assert approve.json()["conflict_id"] is not None


def test_faculty_revision_then_rejection_flow(client):
    pending = client.get("/api/requests", params={"status": "pending"}).json()["requests"]
    assert len(pending) >= 1
    req_id = pending[0]["id"]

    rev = client.post(
        f"/api/requests/{req_id}/decision",
        json={
            "decision": "revision_requested",
            "reviewer_name": "Prof. Rajesh Kulkarni",
            "reviewer_notes": "Please upload power budget schematic.",
        },
    )
    assert rev.status_code == 200
    assert rev.json()["status"] == "revision_requested"

    rej = client.post(
        f"/api/requests/{req_id}/decision",
        json={
            "decision": "rejected",
            "reviewer_name": "Prof. Rajesh Kulkarni",
            "reviewer_notes": "Out of scope for this semester.",
        },
    )
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"
