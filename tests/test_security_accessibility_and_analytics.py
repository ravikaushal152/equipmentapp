"""Tests for security headers, sanitization, SQLi safety, rate limiting, analytics, and accessibility."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path


def test_health_and_security_headers(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["resources_loaded"] == 14

    assert "default-src 'self'" in res.headers["content-security-policy"]
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_input_sanitization_and_unknown_field_rejection(client):
    day_out = (date.today() + timedelta(days=5)).isoformat()

    # Unknown extra fields rejected by Pydantic extra='forbid'
    bad_extra = client.post(
        "/api/ai/discover",
        json={"query": "Need GPU workstation", "is_admin": True},
    )
    assert bad_extra.status_code == 422

    # HTML / script injection tags stripped cleanly before storage
    xss_booking = client.post(
        "/api/bookings",
        json={
            "equipment_id": 12,
            "student_name": "<script>alert(1)</script>Riya Sharma",
            "student_id": "ROBO23B044",
            "student_department": "ROBO",
            "project_title": "<b>Precision Mount</b> Prototype",
            "project_track": "prototype",
            "project_deadline": day_out,
            "booking_date": day_out,
            "start_hour": 14,
            "end_hour": 16,
        },
    )
    assert xss_booking.status_code == 201
    booking = xss_booking.json()["booking"]
    assert "<script>" not in booking["student_name"]
    assert "<b>" not in booking["project_title"]
    assert "Riya Sharma" in booking["student_name"]


def test_sql_injection_payload_in_search_is_harmless(client):
    sqli = client.get("/api/equipment", params={"search": "' OR '1'='1'; DROP TABLE equipment; --"})
    assert sqli.status_code == 200
    assert sqli.json()["count"] == 0

    # Verify table intact afterwards
    after = client.get("/api/equipment")
    assert after.status_code == 200
    assert after.json()["count"] == 14


def test_rate_limiter_enforces_429(strict_rate_client):
    assert strict_rate_client.get("/api/departments").status_code == 200
    assert strict_rate_client.get("/api/departments").status_code == 200
    assert strict_rate_client.get("/api/departments").status_code == 200
    limited = strict_rate_client.get("/api/departments")
    assert limited.status_code == 429
    assert "retry-after" in limited.headers


def test_utilization_analytics_endpoint(client):
    res = client.get("/api/analytics/utilization")
    assert res.status_code == 200
    data = res.json()
    kpis = data["kpis"]
    assert kpis["total_resources"] == 14
    assert kpis["departments_connected"] == 5
    assert kpis["cross_department_share_pct"] > 0
    assert len(data["department_utilization"]) == 5
    assert len(data["peak_hours"]) == 12
    assert len(data["underutilized_opportunities"]) >= 1
    assert len(data["recent_audit_logs"]) >= 1


def test_frontend_accessibility_and_xss_safety(client):
    index_res = client.get("/")
    assert index_res.status_code == 200
    html = index_res.text

    # Core WCAG 2.1 AA structural landmarks and attributes
    assert '<html lang="en"' in html
    assert 'class="skip-link"' in html
    assert 'id="main-content"' in html
    assert 'role="banner"' in html
    assert 'role="tablist"' in html
    assert 'role="tabpanel"' in html
    assert 'aria-live="polite"' in html
    assert 'role="contentinfo"' in html

    # Confirm zero raw innerHTML usage across all frontend JavaScript modules
    js_dir = Path(__file__).resolve().parent.parent / "static" / "js"
    for js_file in js_dir.glob("*.js"):
        source = js_file.read_text(encoding="utf-8")
        assert "innerHTML" not in source, f"Unsafe innerHTML found in {js_file.name}"
        assert "document.write" not in source
