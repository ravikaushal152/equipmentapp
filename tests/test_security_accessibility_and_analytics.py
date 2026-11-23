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

    csp = res.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "object-src 'none'" in csp
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_invalid_date_query_params_return_422_not_500(client):
    assert client.get("/api/equipment", params={"target_date": "not-a-date"}).status_code == 422
    assert client.get("/api/equipment/1", params={"target_date": "2026-99-99"}).status_code == 422
    assert client.get("/api/equipment/1/schedule", params={"target_date": "bad"}).status_code == 422
    assert client.get("/api/bookings", params={"booking_date": "invalid"}).status_code == 422


def test_oversized_payload_rejected_with_413(client):
    huge_query = "a" * 70_000
    res = client.post("/api/ai/discover", json={"query": huge_query})
    assert res.status_code == 413


def test_production_hsts_header_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    prod_settings = Settings(
        gemini_api_key="",
        gemini_model="gemini-3.5-flash-lite",
        db_path=tmp_path / "prod.db",
        env="production",
        port=8080,
        rate_limit_general_rpm=100,
        rate_limit_ai_rpm=30,
        ai_cache_ttl_seconds=60,
    )
    with TestClient(create_app(prod_settings)) as prod_client:
        res = prod_client.get("/health")
        assert res.status_code == 200
        assert "max-age=31536000" in res.headers["strict-transport-security"]


def test_static_asset_caching_and_api_no_store(client):
    static_res = client.get("/static/css/styles.css")
    assert static_res.status_code == 200
    assert "max-age=300" in static_res.headers["cache-control"]

    api_res = client.get("/api/departments")
    assert api_res.status_code == 200
    assert api_res.headers["cache-control"] == "no-store"


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


def test_rate_limiter_prunes_stale_keys_bounded_memory():
    from collections import deque

    from app.security import RateLimitMiddleware

    mw = RateLimitMiddleware(app=None, general_rpm=10, ai_rpm=5)
    mw._hits[("10.0.0.1", "general")] = deque([1.0])
    mw._hits[("10.0.0.2", "general")] = deque([200.0])
    mw._prune_stale(window_start=100.0)
    assert ("10.0.0.1", "general") not in mw._hits
    assert ("10.0.0.2", "general") in mw._hits


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
    import re

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
    assert 'class="sr-only"' in html

    # Zero inline event handlers or inline style attributes in HTML (strict CSP compliance)
    assert "onclick=" not in html.lower()
    assert "style=" not in html.lower()

    # Every form control with an id has a corresponding label[for] or explicit aria-label
    control_ids = re.findall(r'<(?:input|select|textarea)[^>]*\bid="([^"]+)"', html)
    label_fors = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', html))
    for cid in control_ids:
        assert cid in label_fors, f"Form control #{cid} is missing a matching <label for='{cid}'>"

    # Confirm zero unsafe DOM sinks or inline style mutations across all frontend JS modules
    js_dir = Path(__file__).resolve().parent.parent / "static" / "js"
    for js_file in js_dir.glob("*.js"):
        source = js_file.read_text(encoding="utf-8")
        assert "innerHTML" not in source, f"Unsafe innerHTML found in {js_file.name}"
        assert "document.write" not in source
        assert ".style." not in source, f"Inline .style mutation found in {js_file.name}"
