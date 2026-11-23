"""Strict Pydantic v2 request and response schemas for LabLink."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.security import sanitize_text, validate_iso_date

CategoryType = Literal["specialized_equipment", "development_kit", "laboratory_workstation"]
TrackType = Literal["capstone", "research", "course_lab", "competition", "prototype"]
FlexibilityType = Literal["strict", "flexible_time", "flexible_equipment", "both"]
DecisionType = Literal["approved", "rejected", "revision_requested"]


class StrictInputModel(BaseModel):
    """Base model rejecting unknown fields and stripping surrounding whitespace."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class AIDiscoveryRequest(StrictInputModel):
    """Natural-language request for GenAI equipment recommendations."""

    query: str = Field(..., min_length=4, max_length=600)
    student_department: str | None = Field(default=None, max_length=16)
    preferred_date: str | None = Field(default=None, max_length=10)
    duration_hours: int = Field(default=2, ge=1, le=6)
    project_deadline: str | None = Field(default=None, max_length=10)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        cleaned = sanitize_text(value)
        if len(cleaned) < 4:
            raise ValueError("Please describe your project requirements in at least 4 characters.")
        return cleaned

    @field_validator("student_department")
    @classmethod
    def clean_dept(cls, value: str | None) -> str | None:
        return sanitize_text(value).upper() if value else None

    @field_validator("preferred_date", "project_deadline")
    @classmethod
    def check_dates(cls, value: str | None) -> str | None:
        return validate_iso_date(value) if value else None


class SmartSlotRequest(StrictInputModel):
    """Input for intelligent slot recommendations and load-balancing."""

    equipment_id: int = Field(..., ge=1)
    preferred_date: str = Field(..., max_length=10)
    duration_hours: int = Field(default=2, ge=1, le=6)
    project_deadline: str | None = Field(default=None, max_length=10)
    include_cross_department: bool = True

    @field_validator("preferred_date", "project_deadline")
    @classmethod
    def check_dates(cls, value: str | None) -> str | None:
        return validate_iso_date(value) if value else None


class BookingCreateRequest(StrictInputModel):
    """Create a lab slot booking (automatically flags conflicts on overlap)."""

    equipment_id: int = Field(..., ge=1)
    student_name: str = Field(..., min_length=2, max_length=80)
    student_id: str = Field(..., min_length=2, max_length=32)
    student_department: str = Field(..., min_length=2, max_length=16)
    project_title: str = Field(..., min_length=3, max_length=120)
    project_track: TrackType = "capstone"
    project_deadline: str = Field(..., max_length=10)
    booking_date: str = Field(..., max_length=10)
    start_hour: int = Field(..., ge=6, le=21)
    end_hour: int = Field(..., ge=7, le=22)
    team_size: int = Field(default=2, ge=1, le=12)
    flexibility: FlexibilityType = "flexible_time"
    prior_hours_this_week: float = Field(default=0.0, ge=0.0, le=40.0)
    notes: str = Field(default="", max_length=500)

    @field_validator("student_name", "student_id", "project_title", "notes")
    @classmethod
    def clean_strings(cls, value: str) -> str:
        return sanitize_text(value)

    @field_validator("student_department")
    @classmethod
    def clean_dept(cls, value: str) -> str:
        return sanitize_text(value).upper()

    @field_validator("project_deadline", "booking_date")
    @classmethod
    def check_dates(cls, value: str) -> str:
        return validate_iso_date(value)

    @model_validator(mode="after")
    def check_hours(self) -> BookingCreateRequest:
        if self.end_hour <= self.start_hour:
            raise ValueError("end_hour must be greater than start_hour.")
        if (self.end_hour - self.start_hour) > 6:
            raise ValueError("Single booking duration cannot exceed 6 hours.")
        return self


class ProjectRequestCreate(StrictInputModel):
    """Submit a structured project-based cross-department access request."""

    student_name: str = Field(..., min_length=2, max_length=80)
    student_id: str = Field(..., min_length=2, max_length=32)
    student_email: str = Field(..., min_length=5, max_length=120)
    student_department: str = Field(..., min_length=2, max_length=16)
    project_title: str = Field(..., min_length=3, max_length=120)
    project_track: TrackType = "capstone"
    project_deadline: str = Field(..., max_length=10)
    team_size: int = Field(default=2, ge=1, le=12)
    equipment_id: int = Field(..., ge=1)
    requested_date: str = Field(..., max_length=10)
    start_hour: int = Field(..., ge=6, le=21)
    end_hour: int = Field(..., ge=7, le=22)
    justification: str = Field(..., min_length=12, max_length=1200)
    safety_acknowledged: bool = True
    flexibility: FlexibilityType = "flexible_time"
    prior_hours_this_week: float = Field(default=0.0, ge=0.0, le=40.0)

    @field_validator("student_name", "student_id", "project_title")
    @classmethod
    def clean_short(cls, value: str) -> str:
        return sanitize_text(value)

    @field_validator("justification")
    @classmethod
    def clean_justification(cls, value: str) -> str:
        cleaned = sanitize_text(value, allow_newlines=True)
        if len(cleaned) < 12:
            raise ValueError("Project justification must be at least 12 characters.")
        return cleaned

    @field_validator("student_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        cleaned = sanitize_text(value).lower()
        if "@" not in cleaned or "." not in cleaned.split("@")[-1]:
            raise ValueError("Please enter a valid campus email address.")
        return cleaned

    @field_validator("student_department")
    @classmethod
    def clean_dept(cls, value: str) -> str:
        return sanitize_text(value).upper()

    @field_validator("project_deadline", "requested_date")
    @classmethod
    def check_dates(cls, value: str) -> str:
        return validate_iso_date(value)

    @model_validator(mode="after")
    def check_hours(self) -> ProjectRequestCreate:
        if self.end_hour <= self.start_hour:
            raise ValueError("end_hour must be greater than start_hour.")
        if (self.end_hour - self.start_hour) > 6:
            raise ValueError("Requested session duration cannot exceed 6 hours.")
        return self


class ProjectBriefPreviewRequest(StrictInputModel):
    """Preview an AI feasibility & readiness brief before submitting a project request."""

    equipment_id: int = Field(..., ge=1)
    student_department: str = Field(..., min_length=2, max_length=16)
    project_title: str = Field(..., min_length=3, max_length=120)
    project_track: TrackType = "capstone"
    project_deadline: str = Field(..., max_length=10)
    team_size: int = Field(default=2, ge=1, le=12)
    justification: str = Field(..., min_length=10, max_length=1200)
    safety_acknowledged: bool = True

    @field_validator("project_title", "justification")
    @classmethod
    def clean_text_fields(cls, value: str) -> str:
        return sanitize_text(value, allow_newlines=True)

    @field_validator("student_department")
    @classmethod
    def clean_dept(cls, value: str) -> str:
        return sanitize_text(value).upper()

    @field_validator("project_deadline")
    @classmethod
    def check_deadline(cls, value: str) -> str:
        return validate_iso_date(value)


class FacultyDecisionRequest(StrictInputModel):
    """Faculty approval, rejection, or revision request decision."""

    decision: DecisionType
    reviewer_name: str = Field(..., min_length=2, max_length=80)
    reviewer_notes: str = Field(default="", max_length=600)

    @field_validator("reviewer_name", "reviewer_notes")
    @classmethod
    def clean_fields(cls, value: str) -> str:
        return sanitize_text(value, allow_newlines=True)


class ConflictApplyRequest(StrictInputModel):
    """Apply a recommended conflict resolution plan atomically."""

    plan_id: str = Field(..., min_length=2, max_length=64)
    resolved_by: str = Field(default="LabLink Faculty Coordinator", min_length=2, max_length=80)

    @field_validator("plan_id", "resolved_by")
    @classmethod
    def clean_fields(cls, value: str) -> str:
        return sanitize_text(value)
