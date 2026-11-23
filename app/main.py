"""FastAPI application factory and static SPA entrypoint for LabLink."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings, load_settings
from app.database import Database
from app.routers import analytics, bookings, conflicts, equipment, requests
from app.security import RateLimitMiddleware, SecurityHeadersMiddleware
from app.seed import seed_database
from app.services.genai_service import GenAIService

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure a LabLink FastAPI application instance."""
    cfg = settings or load_settings()
    db = Database(cfg.db_path)
    genai_service = GenAIService(cfg)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        db.init_schema()
        seed_database(db)
        yield

    app = FastAPI(
        title="LabLink — Campus Laboratory Resource-Sharing Platform",
        description=(
            "GenAI-powered cross-department campus laboratory discovery, intelligent slot booking, "
            "project-based access requests, faculty approvals, and fair scheduling conflict resolution."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    app.state.settings = cfg
    app.state.db = db
    app.state.genai = genai_service

    # Security headers & lightweight sliding-window rate limiting
    app.add_middleware(
        RateLimitMiddleware,
        general_rpm=cfg.rate_limit_general_rpm,
        ai_rpm=cfg.rate_limit_ai_rpm,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    # Register modular API routers
    app.include_router(equipment.router)
    app.include_router(bookings.router)
    app.include_router(requests.router)
    app.include_router(conflicts.router)
    app.include_router(analytics.router)

    @app.get("/health", tags=["System"])
    def health_check() -> dict[str, Any]:
        """Lightweight liveness & readiness probe for Google Cloud Run."""
        with db.connection() as conn:
            resource_count = conn.execute("SELECT COUNT(*) FROM equipment").fetchone()[0]
        return {
            "status": "ok",
            "service": "lablink",
            "version": __version__,
            "ai_mode": "gemini" if cfg.has_gemini_key else "deterministic_fallback",
            "gemini_model": cfg.gemini_model,
            "resources_loaded": int(resource_count),
        }

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled server error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "An unexpected internal error occurred. Please try again."},
        )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def serve_index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
