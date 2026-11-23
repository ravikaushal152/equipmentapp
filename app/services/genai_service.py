"""Google Gemini GenAI service with hybrid retrieval, TTL caching, and deterministic fallback."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import date
from typing import Any

from app.config import Settings

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "day", "days",
        "due", "for", "from", "hardware", "have", "i", "in", "is", "it", "lab",
        "my", "need", "needing", "of", "on", "or", "our", "project", "that",
        "the", "this", "to", "use", "using", "want", "we", "week", "with",
    }
)

# Lightweight domain synonyms to boost semantic retrieval even offline
_DOMAIN_SYNONYMS: dict[str, set[str]] = {
    "drone": {"jetson", "orin", "embedded", "autonomous", "perception", "robotics"},
    "edge": {"jetson", "orin", "hailo", "raspberry", "embedded", "tensorrt", "iot"},
    "ai": {"jetson", "gpu", "a100", "rtx", "learning", "tensorrt", "npu"},
    "ml": {"gpu", "a100", "rtx", "deep", "learning", "pytorch", "cuda", "simulation"},
    "llm": {"a100", "80gb", "vram", "gpu", "pytorch", "cuda", "rtx"},
    "vram": {"a100", "80gb", "48gb", "rtx", "gpu", "pytorch"},
    "training": {"a100", "rtx", "gpu", "pytorch", "cuda", "deep"},
    "vision": {"yolo", "camera", "perception", "jetson", "orin", "rtx", "kria"},
    "fpga": {"xilinx", "kria", "verilog", "vhdl", "rtl", "vivado", "artix", "risc"},
    "motor": {"oscilloscope", "pwm", "inverter", "power", "signal", "rigol", "tektronix"},
    "pwm": {"oscilloscope", "rigol", "tektronix", "motor", "signal"},
    "pcb": {"oscilloscope", "spi", "i2c", "signal", "cobot", "esd"},
    "robot": {"ur3e", "cobot", "ros", "jetson", "arm", "chassis", "prusa"},
    "3d": {"printer", "bambu", "prusa", "enclosure", "cad", "carbon", "chassis"},
    "printer": {"bambu", "prusa", "carbon", "chassis", "additive", "filament"},
    "chassis": {"bambu", "prusa", "printer", "carbon", "3d", "frame"},
    "bio": {"spectrophotometer", "uv", "nanoparticle", "biosensor", "assay", "curie"},
    "water": {"spectrophotometer", "uv", "biosensor", "sensor", "optical"},
}


class GenAIService:
    """Facade around the Google GenAI SDK with strict JSON outputs and safe fallback."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._client: Any | None = None

    FALLBACK_MODELS: tuple[str, ...] = (
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.5-flash",
    )

    @staticmethod
    def _should_use_vertex() -> bool:
        """Use Gemini Developer API by default; opt into Vertex AI only via env flag."""
        return os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in ("true", "1")

    def _get_client(self) -> Any | None:
        if not self.settings.has_gemini_key:
            return None
        if self._client is None:
            try:
                from google import genai

                self._client = genai.Client(
                    api_key=self.settings.gemini_api_key,
                    vertexai=self._should_use_vertex(),
                )
            except Exception as exc:  # pragma: no cover - defensive import guard
                logger.warning("Unable to initialize Google GenAI client: %s", exc)
                return None
        return self._client

    def _candidate_models(self) -> list[str]:
        """Return ordered unique list starting with configured model then fast fallbacks."""
        seen: set[str] = set()
        models: list[str] = []
        for name in (self.settings.gemini_model, *self.FALLBACK_MODELS):
            if name and name not in seen:
                seen.add(name)
                models.append(name)
        return models

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        entry = self._cache.get(key)
        if not entry:
            return None
        expires_at, payload = entry
        if time.monotonic() > expires_at:
            self._cache.pop(key, None)
            return None
        return payload

    def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        if len(self._cache) >= 128:
            oldest_key = min(self._cache, key=lambda k: self._cache[k][0])
            self._cache.pop(oldest_key, None)
        self._cache[key] = (time.monotonic() + self.settings.ai_cache_ttl_seconds, payload)

    def _call_gemini_json(self, prompt: str) -> dict[str, Any] | None:
        """Invoke Gemini expecting a JSON object, trying resilient fallback models on 503/404."""
        client = self._get_client()
        if client is None:
            return None

        from google.genai import types

        for model_name in self._candidate_models():
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.25,
                    ),
                )
                raw_text = (response.text or "").strip()
                if not raw_text:
                    continue
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception as exc:
                logger.warning(
                    "Gemini model '%s' call failed; trying next fallback: %s",
                    model_name,
                    exc,
                )
        return None

    @staticmethod
    def _rank_equipment_locally(
        query: str,
        equipment_items: list[dict[str, Any]],
        student_department: str | None = None,
    ) -> list[tuple[dict[str, Any], float, list[str]]]:
        """Hybrid lexical + synonym capability scorer over campus equipment."""
        tokens = {t for t in _TOKEN_RE.findall(query.lower()) if t not in _STOPWORDS and len(t) >= 2}
        expanded = set(tokens)
        for token in tokens:
            expanded.update(_DOMAIN_SYNONYMS.get(token, set()))

        raw_scored: list[tuple[dict[str, Any], float, list[str]]] = []
        for item in equipment_items:
            if item["status"] == "maintenance":
                continue
            title_and_specs = " ".join([item["name"], " ".join(item.get("specs", []))]).lower()
            desc_text = " ".join(
                [
                    item["description"],
                    item["category"].replace("_", " "),
                    item["compatibility_group"].replace("_", " "),
                    item["department_code"],
                ]
            ).lower()

            title_tokens = set(_TOKEN_RE.findall(title_and_specs)) - _STOPWORDS
            all_tokens = (title_tokens | set(_TOKEN_RE.findall(desc_text))) - _STOPWORDS

            title_direct = sorted(tokens & title_tokens)
            desc_direct = sorted((tokens & all_tokens) - set(title_direct))
            semantic_hits = sorted((expanded - tokens) & all_tokens)

            raw = (
                len(title_direct) * 20.0
                + len(desc_direct) * 12.0
                + min(4, len(semantic_hits)) * 5.0
            )
            if item["status"] == "available":
                raw += 4.0
            if student_department and item["department_code"] != student_department and (
                title_direct or desc_direct
            ):
                raw += 5.0

            matched_terms = (title_direct + desc_direct + semantic_hits)[:5]
            raw_scored.append((item, raw, matched_terms))

        raw_scored.sort(key=lambda pair: pair[1], reverse=True)
        max_raw = max((r for _, r, _ in raw_scored), default=1.0) or 1.0

        # Scale into readable 52..97 match scores while promoting compatibility-group diversity
        normalized_list: list[tuple[dict[str, Any], float, list[str]]] = []
        seen_groups: dict[str, int] = {}
        for item, raw, terms in raw_scored:
            group = item["compatibility_group"]
            group_penalty = seen_groups.get(group, 0) * 6.5
            seen_groups[group] = seen_groups.get(group, 0) + 1
            scaled = 52.0 + (raw / max_raw) * 45.0 - group_penalty
            normalized_list.append((item, round(min(97.0, max(45.0, scaled)), 1), terms))

        normalized_list.sort(key=lambda pair: pair[1], reverse=True)
        return normalized_list

    def discover_equipment(
        self,
        *,
        query: str,
        equipment_items: list[dict[str, Any]],
        availability_by_id: dict[int, dict[str, Any]],
        student_department: str | None = None,
        duration_hours: int = 2,
        project_deadline: str | None = None,
    ) -> dict[str, Any]:
        """Recommend cross-department campus lab equipment from natural-language needs."""
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "op": "discover",
                    "q": query.lower(),
                    "dept": student_department,
                    "dur": duration_hours,
                    "dl": project_deadline,
                    "has_key": self.settings.has_gemini_key,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

        cached = self._cache_get(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

        ranked = self._rank_equipment_locally(query, equipment_items, student_department)
        top_candidates = ranked[:5]

        # Build structured candidate context for Gemini
        candidate_context = [
            {
                "equipment_id": item["id"],
                "name": item["name"],
                "department_code": item["department_code"],
                "category": item["category"],
                "specs": item["specs"],
                "requires_faculty_approval": bool(item["requires_faculty_approval"]),
                "safety_cert_required": item["safety_cert_required"],
                "utilization_today_pct": availability_by_id.get(item["id"], {}).get(
                    "utilization_pct", 0.0
                ),
                "open_hours_today": availability_by_id.get(item["id"], {}).get("open_hours", 8),
                "local_match_score": score,
            }
            for item, score, _ in top_candidates
        ]

        prompt = (
            "You are LabLink, a GenAI campus laboratory resource advisor helping college students "
            "discover specialized equipment, development kits, and workstations across departments.\n"
            f"Student Query: {query}\n"
            f"Student Department: {student_department or 'Unspecified'}\n"
            f"Desired Session Duration: {duration_hours} hours\n"
            f"Project Deadline: {project_deadline or 'Not specified'}\n"
            f"Verified Campus Equipment Candidates (JSON):\n{json.dumps(candidate_context)}\n\n"
            "Return ONLY a JSON object with keys:\n"
            "- 'advisor_summary': concise 2-sentence overview of the best equipment strategy.\n"
            "- 'recommendations': array of top 3 items from the candidates above, each with:\n"
            "  - 'equipment_id' (int from candidates)\n"
            "  - 'match_score' (number 50-99)\n"
            "  - 'reasoning' (1-2 clear sentences explaining fit for the student's project)\n"
            "  - 'cross_dept_tip' (short practical tip on cross-department access or alternatives)\n"
        )

        ai_result = self._call_gemini_json(prompt)
        by_id = {item["id"]: (item, score, hits) for item, score, hits in top_candidates}

        if ai_result and isinstance(ai_result.get("recommendations"), list):
            recommendations: list[dict[str, Any]] = []
            for rec in ai_result["recommendations"][:4]:
                eq_id = rec.get("equipment_id")
                if eq_id not in by_id:
                    continue
                item, fallback_score, _ = by_id[eq_id]
                avail = availability_by_id.get(eq_id, {})
                recommendations.append(
                    {
                        "equipment_id": eq_id,
                        "name": item["name"],
                        "department_code": item["department_code"],
                        "category": item["category"],
                        "location": item["location"],
                        "specs": item["specs"],
                        "requires_faculty_approval": bool(item["requires_faculty_approval"]),
                        "safety_cert_required": item["safety_cert_required"],
                        "is_cross_department": bool(
                            student_department and item["department_code"] != student_department
                        ),
                        "match_score": float(rec.get("match_score", fallback_score)),
                        "reasoning": str(rec.get("reasoning", item["description"])),
                        "cross_dept_tip": str(
                            rec.get(
                                "cross_dept_tip",
                                f"Located in {item['location']} ({item['department_code']}).",
                            )
                        ),
                        "utilization_pct": avail.get("utilization_pct", 0.0),
                        "open_hours": avail.get("open_hours", 8),
                    }
                )
            if recommendations:
                result = {
                    "ai_mode": "gemini",
                    "model": self.settings.gemini_model,
                    "advisor_summary": str(
                        ai_result.get(
                            "advisor_summary",
                            "Matched top cross-department campus resources based on your technical requirements.",
                        )
                    ),
                    "recommendations": recommendations,
                    "cached": False,
                }
                self._cache_set(cache_key, result)
                return result

        # Deterministic hybrid fallback (guaranteed reliable offline & in automated tests)
        fallback_recs: list[dict[str, Any]] = []
        for item, score, hits in top_candidates[:3]:
            avail = availability_by_id.get(item["id"], {})
            is_cross = bool(student_department and item["department_code"] != student_department)
            hit_phrase = (
                f"Matches key requirements ({', '.join(hits)})"
                if hits
                else "Strong capability match for general lab prototyping"
            )
            fallback_recs.append(
                {
                    "equipment_id": item["id"],
                    "name": item["name"],
                    "department_code": item["department_code"],
                    "category": item["category"],
                    "location": item["location"],
                    "specs": item["specs"],
                    "requires_faculty_approval": bool(item["requires_faculty_approval"]),
                    "safety_cert_required": item["safety_cert_required"],
                    "is_cross_department": is_cross,
                    "match_score": score,
                    "reasoning": f"{hit_phrase}. {item['description']}",
                    "cross_dept_tip": (
                        f"Cross-department resource in {item['department_code']} ({item['location']}) — "
                        f"{'submit a quick project request for faculty sign-off.' if item['requires_faculty_approval'] else 'available for instant slot booking.'}"
                    ),
                    "utilization_pct": avail.get("utilization_pct", 0.0),
                    "open_hours": avail.get("open_hours", 8),
                }
            )

        lead = fallback_recs[0]["name"] if fallback_recs else "campus equipment"
        result = {
            "ai_mode": "deterministic_fallback",
            "model": "hybrid-semantic-matcher",
            "advisor_summary": (
                f"Top recommendation is {lead}, selected by capability alignment, "
                f"real-time lab availability, and cross-department accessibility."
            ),
            "recommendations": fallback_recs,
            "cached": False,
        }
        self._cache_set(cache_key, result)
        return result

    def generate_project_brief(
        self,
        *,
        project_title: str,
        project_track: str,
        project_deadline: str,
        student_department: str,
        team_size: int,
        justification: str,
        safety_acknowledged: bool,
        equipment: dict[str, Any],
        has_slot_conflict: bool = False,
    ) -> dict[str, Any]:
        """Generate a faculty-ready feasibility & safety readiness brief for a project request."""
        days_left = (date.fromisoformat(project_deadline) - date.today()).days
        is_cross_dept = student_department != equipment["department_code"]
        urgency_tier = "high" if days_left <= 5 else ("medium" if days_left <= 12 else "standard")

        prompt = (
            "You are LabLink's Faculty Review Assistant. Summarize a student's campus lab "
            "project-based access request concisely for a faculty approver.\n"
            f"Project: {project_title} ({project_track}, team of {team_size})\n"
            f"Deadline: {project_deadline} ({days_left} days left)\n"
            f"Student Dept: {student_department} -> Equipment Dept: {equipment['department_code']}\n"
            f"Equipment: {equipment['name']} (Safety cert: {equipment['safety_cert_required']})\n"
            f"Safety Acknowledged: {safety_acknowledged}\n"
            f"Slot Conflict Detected: {has_slot_conflict}\n"
            f"Student Justification: {justification}\n\n"
            "Return ONLY a JSON object with keys:\n"
            "- 'readiness_score' (integer 45-98)\n"
            "- 'urgency_tier' ('high' | 'medium' | 'standard')\n"
            "- 'summary' (1-2 concise sentences for faculty)\n"
            "- 'strengths' (array of 2-3 short bullet strings)\n"
            "- 'prerequisites_check' (1 short sentence checking safety/prereqs)\n"
            "- 'recommended_action' ('Approve' | 'Approve & Resolve Conflict' | 'Request Revision')\n"
        )

        ai_result = self._call_gemini_json(prompt)
        if ai_result and "summary" in ai_result:
            return {
                "readiness_score": int(ai_result.get("readiness_score", 85)),
                "urgency_tier": str(ai_result.get("urgency_tier", urgency_tier)),
                "summary": str(ai_result["summary"]),
                "strengths": [str(s) for s in ai_result.get("strengths", [])[:3]],
                "prerequisites_check": str(
                    ai_result.get(
                        "prerequisites_check",
                        f"Verify {equipment['safety_cert_required']} prior to session.",
                    )
                ),
                "recommended_action": str(
                    ai_result.get(
                        "recommended_action",
                        "Approve & Resolve Conflict" if has_slot_conflict else "Approve",
                    )
                ),
                "ai_mode": "gemini",
            }

        # Deterministic brief fallback
        score = 72
        strengths: list[str] = []
        if len(justification) >= 45:
            score += 10
            strengths.append("Detailed technical justification tied to project deliverables")
        if safety_acknowledged:
            score += 8
            strengths.append(f"Safety requirement acknowledged ({equipment['safety_cert_required']})")
        if is_cross_dept:
            score += 4
            strengths.append(
                f"Promotes cross-department lab sharing ({student_department} → {equipment['department_code']})"
            )
        if team_size >= 2:
            strengths.append(f"High educational leverage supporting a {team_size}-student team")

        if not safety_acknowledged:
            recommended = "Request Revision"
            score = max(48, score - 18)
        elif has_slot_conflict:
            recommended = "Approve & Resolve Conflict"
        else:
            recommended = "Approve"

        return {
            "readiness_score": min(96, score),
            "urgency_tier": urgency_tier,
            "summary": (
                f"{project_track.replace('_', ' ').title()} project '{project_title}' "
                f"({days_left} days to deadline) requests {equipment['name']}. "
                f"{'Overlapping slot detected—route through Conflict Resolver after sign-off.' if has_slot_conflict else 'Requested slot is open and aligned with lab hours.'}"
            ),
            "strengths": strengths[:3] or ["Clear equipment request aligned with lab capabilities"],
            "prerequisites_check": (
                f"Confirm '{equipment['safety_cert_required']}' at {equipment['location']}."
                if safety_acknowledged
                else f"Student must confirm '{equipment['safety_cert_required']}' before approval."
            ),
            "recommended_action": recommended,
            "ai_mode": "deterministic_fallback",
        }

    def advise_on_conflict(self, analysis: dict[str, Any]) -> dict[str, Any]:
        """Use Gemini to explain the fairest mathematically verified conflict-resolution plan."""
        plans = analysis.get("feasible_plans", [])
        scored = analysis.get("scored_bookings", [])
        conflict = analysis.get("conflict", {})

        if not plans:
            return {
                "ai_mode": "deterministic_fallback",
                "recommended_plan_id": "",
                "executive_summary": "No open plans require action for this conflict.",
                "fairness_rationale": "Conflict is already resolved or has fewer than two active bookings.",
                "student_notification_draft": "",
            }

        top_plan = plans[0]
        prompt = (
            "You are LabLink's Fair Campus Resource Allocation Advisor.\n"
            "Below is a real scheduling conflict with mathematically verified, zero-overlap feasible plans.\n"
            f"Conflict: {json.dumps(conflict)}\n"
            f"Fairness-Scored Competing Bookings: {json.dumps(scored)}\n"
            f"Verified Feasible Plans (choose ONLY from these plan_ids): {json.dumps(plans)}\n\n"
            "Return ONLY a JSON object with keys:\n"
            "- 'recommended_plan_id': exact plan_id from Verified Feasible Plans\n"
            "- 'executive_summary': 2 clear sentences explaining the fairest feasible resolution\n"
            "- 'fairness_rationale': 2 sentences referencing project deadlines, prior usage hours, and equipment availability\n"
            "- 'student_notification_draft': short, polite 2-sentence message to send the affected students\n"
        )

        ai_result = self._call_gemini_json(prompt)
        valid_ids = {p["plan_id"] for p in plans}
        if ai_result and ai_result.get("recommended_plan_id") in valid_ids:
            return {
                "ai_mode": "gemini",
                "model": self.settings.gemini_model,
                "recommended_plan_id": str(ai_result["recommended_plan_id"]),
                "executive_summary": str(
                    ai_result.get("executive_summary", top_plan["summary"])
                ),
                "fairness_rationale": str(
                    ai_result.get("fairness_rationale", top_plan["utilization_gain_note"])
                ),
                "student_notification_draft": str(
                    ai_result.get("student_notification_draft", "")
                ),
            }

        winner = scored[0]
        secondary = scored[1] if len(scored) > 1 else scored[0]
        return {
            "ai_mode": "deterministic_fallback",
            "model": "fairness-constraint-advisor",
            "recommended_plan_id": top_plan["plan_id"],
            "executive_summary": top_plan["summary"],
            "fairness_rationale": (
                f"{winner['student_name']} scores {winner['total_score']}/100 "
                f"(deadline in {winner['days_until_deadline']}d, {winner['prior_hours_this_week']}h prior usage) "
                f"vs {secondary['student_name']} at {secondary['total_score']}/100 "
                f"(deadline in {secondary['days_until_deadline']}d, {secondary['prior_hours_this_week']}h prior usage). "
                f"{top_plan['utilization_gain_note']}"
            ),
            "student_notification_draft": (
                f"Hi {secondary['student_name']}, to unblock an urgent campus capstone deadline on "
                f"{conflict['equipment_name']}, LabLink reserved a verified alternative slot for your "
                f"project and added +1 Priority Credit to your account."
            ),
        }
