"""SQLite database initialization, schema management, and transaction helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS departments (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    building TEXT NOT NULL,
    coordinator_name TEXT NOT NULL,
    coordinator_email TEXT NOT NULL,
    lab_hours_start INTEGER NOT NULL CHECK (lab_hours_start BETWEEN 6 AND 12),
    lab_hours_end INTEGER NOT NULL CHECK (lab_hours_end BETWEEN 16 AND 23),
    cross_dept_policy TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equipment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department_code TEXT NOT NULL REFERENCES departments(code) ON DELETE RESTRICT,
    category TEXT NOT NULL CHECK (
        category IN ('specialized_equipment', 'development_kit', 'laboratory_workstation')
    ),
    compatibility_group TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT NOT NULL,
    specs_json TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    safety_cert_required TEXT NOT NULL,
    requires_faculty_approval INTEGER NOT NULL DEFAULT 0 CHECK (requires_faculty_approval IN (0, 1)),
    max_session_hours INTEGER NOT NULL DEFAULT 4 CHECK (max_session_hours BETWEEN 1 AND 8),
    lab_hours_start INTEGER NOT NULL DEFAULT 8 CHECK (lab_hours_start BETWEEN 6 AND 12),
    lab_hours_end INTEGER NOT NULL DEFAULT 20 CHECK (lab_hours_end BETWEEN 16 AND 23),
    status TEXT NOT NULL DEFAULT 'available' CHECK (
        status IN ('available', 'in_use', 'maintenance')
    ),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    student_name TEXT NOT NULL,
    student_id TEXT NOT NULL,
    student_department TEXT NOT NULL,
    project_title TEXT NOT NULL,
    project_track TEXT NOT NULL CHECK (
        project_track IN ('capstone', 'research', 'course_lab', 'competition', 'prototype')
    ),
    project_deadline TEXT NOT NULL,
    booking_date TEXT NOT NULL,
    start_hour INTEGER NOT NULL CHECK (start_hour BETWEEN 6 AND 22),
    end_hour INTEGER NOT NULL CHECK (end_hour BETWEEN 7 AND 23 AND end_hour > start_hour),
    team_size INTEGER NOT NULL DEFAULT 1 CHECK (team_size BETWEEN 1 AND 12),
    flexibility TEXT NOT NULL DEFAULT 'flexible_time' CHECK (
        flexibility IN ('strict', 'flexible_time', 'flexible_equipment', 'both')
    ),
    prior_hours_this_week REAL NOT NULL DEFAULT 0.0 CHECK (prior_hours_this_week >= 0),
    priority_credit INTEGER NOT NULL DEFAULT 0 CHECK (priority_credit >= 0),
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (
        status IN ('confirmed', 'pending_approval', 'conflict', 'rescheduled', 'cancelled')
    ),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_name TEXT NOT NULL,
    student_id TEXT NOT NULL,
    student_email TEXT NOT NULL,
    student_department TEXT NOT NULL,
    project_title TEXT NOT NULL,
    project_track TEXT NOT NULL CHECK (
        project_track IN ('capstone', 'research', 'course_lab', 'competition', 'prototype')
    ),
    project_deadline TEXT NOT NULL,
    team_size INTEGER NOT NULL DEFAULT 1 CHECK (team_size BETWEEN 1 AND 12),
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    requested_date TEXT NOT NULL,
    start_hour INTEGER NOT NULL CHECK (start_hour BETWEEN 6 AND 22),
    end_hour INTEGER NOT NULL CHECK (end_hour BETWEEN 7 AND 23 AND end_hour > start_hour),
    justification TEXT NOT NULL,
    safety_acknowledged INTEGER NOT NULL DEFAULT 1 CHECK (safety_acknowledged IN (0, 1)),
    flexibility TEXT NOT NULL DEFAULT 'flexible_time' CHECK (
        flexibility IN ('strict', 'flexible_time', 'flexible_equipment', 'both')
    ),
    prior_hours_this_week REAL NOT NULL DEFAULT 0.0 CHECK (prior_hours_this_week >= 0),
    ai_brief_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'rejected', 'revision_requested')
    ),
    reviewer_name TEXT NOT NULL DEFAULT '',
    reviewer_notes TEXT NOT NULL DEFAULT '',
    booking_id INTEGER REFERENCES bookings(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    conflict_date TEXT NOT NULL,
    overlap_start_hour INTEGER NOT NULL,
    overlap_end_hour INTEGER NOT NULL,
    competing_booking_ids_json TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'high' CHECK (severity IN ('high', 'medium', 'low')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolution_strategy TEXT NOT NULL DEFAULT '',
    resolution_summary TEXT NOT NULL DEFAULT '',
    resolved_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_equipment_dept_cat
    ON equipment(department_code, category, status);
CREATE INDEX IF NOT EXISTS idx_equipment_compat
    ON equipment(compatibility_group);
CREATE INDEX IF NOT EXISTS idx_bookings_equip_date_status
    ON bookings(equipment_id, booking_date, status);
CREATE INDEX IF NOT EXISTS idx_requests_status
    ON project_requests(status, created_at);
CREATE INDEX IF NOT EXISTS idx_conflicts_status
    ON conflicts(status, conflict_date);
"""


class Database:
    """Lightweight, thread-safe SQLite database wrapper."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        """Open a configured SQLite connection."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self.transaction() as conn:
            conn.executescript(SCHEMA_SQL)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a read-ready connection and close cleanly on exit."""
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection wrapped in an atomic transaction."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
