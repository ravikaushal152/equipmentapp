"""Tests for equipment catalog filtering, detail views, and GenAI equipment discovery."""

from __future__ import annotations

from datetime import date, timedelta


def test_departments_and_equipment_catalog(client):
    dept_res = client.get("/api/departments")
    assert dept_res.status_code == 200
    depts = dept_res.json()["departments"]
    assert len(depts) == 5
    codes = {d["code"] for d in depts}
    assert codes == {"CSE", "ECE", "ROBO", "MECH", "BIO"}

    eq_res = client.get("/api/equipment")
    assert eq_res.status_code == 200
    body = eq_res.json()
    assert body["count"] == 14
    categories = {item["category"] for item in body["equipment"]}
    assert categories == {"laboratory_workstation", "development_kit", "specialized_equipment"}


def test_equipment_filters_department_category_search_and_instant(client):
    res = client.get("/api/equipment", params={"department": "ECE", "category": "development_kit"})
    assert res.status_code == 200
    items = res.json()["equipment"]
    assert len(items) >= 2
    assert all(i["department_code"] == "ECE" and i["category"] == "development_kit" for i in items)

    search_res = client.get("/api/equipment", params={"search": "Jetson"})
    assert search_res.status_code == 200
    assert all("jetson" in i["name"].lower() for i in search_res.json()["equipment"])

    instant_res = client.get("/api/equipment", params={"only_instant": "true"})
    assert instant_res.status_code == 200
    assert all(i["requires_faculty_approval"] is False for i in instant_res.json()["equipment"])


def test_equipment_detail_returns_equivalents_and_schedule(client):
    res = client.get("/api/equipment/1")
    assert res.status_code == 200
    data = res.json()
    assert data["equipment"]["id"] == 1
    assert len(data["hourly_slots"]) >= 10
    assert len(data["equivalent_resources"]) >= 1

    not_found = client.get("/api/equipment/9999")
    assert not_found.status_code == 404


def test_ai_discover_fallback_and_cache(client):
    deadline = (date.today() + timedelta(days=4)).isoformat()
    payload = {
        "query": "Need an edge AI GPU kit for real-time drone obstacle detection",
        "student_department": "MECH",
        "duration_hours": 2,
        "project_deadline": deadline,
    }
    res1 = client.post("/api/ai/discover", json=payload)
    assert res1.status_code == 200
    body1 = res1.json()
    assert body1["ai_mode"] == "deterministic_fallback"
    assert body1["cached"] is False
    assert len(body1["recommendations"]) == 3
    names = " ".join(r["name"].lower() for r in body1["recommendations"])
    assert "jetson" in names or "rtx" in names or "a100" in names

    # Second identical query should hit the TTL cache
    res2 = client.post("/api/ai/discover", json=payload)
    assert res2.status_code == 200
    assert res2.json()["cached"] is True

    # Multi-intent query (edge drone AI + carbon-fiber 3D printer) ranks diverse matching kits
    multi = client.post(
        "/api/ai/discover",
        json={
            "query": "Need edge AI hardware and a carbon-fiber 3D printer for an autonomous drone obstacle avoidance capstone",
            "student_department": "MECH",
        },
    ).json()
    top_names = " ".join(r["name"].lower() for r in multi["recommendations"])
    assert "jetson" in top_names
    assert "3d printer" in top_names


def test_ai_discover_with_mocked_gemini_response(client, monkeypatch):
    def fake_gemini_json(_prompt: str):
        return {
            "advisor_summary": "Use the Jetson AGX Orin for drone perception and Bambu X1E for chassis parts.",
            "recommendations": [
                {
                    "equipment_id": 4,
                    "match_score": 97,
                    "reasoning": "275 TOPS embedded compute fits multi-camera drone inference.",
                    "cross_dept_tip": "Borrow from Robotics Foundry via project access request.",
                }
            ],
        }

    monkeypatch.setattr(client.app.state.genai, "_call_gemini_json", fake_gemini_json)
    res = client.post(
        "/api/ai/discover",
        json={"query": "Autonomous quadcopter perception pipeline with TensorRT"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ai_mode"] == "gemini"
    assert body["recommendations"][0]["equipment_id"] == 4
    assert body["recommendations"][0]["match_score"] == 97.0


def test_gemini_model_fallback_chain(client):
    models = client.app.state.genai._candidate_models()
    assert len(models) >= 3
    assert "gemini-3.5-flash-lite" in models
