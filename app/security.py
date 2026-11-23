"""Security middleware, input sanitization, payload-size limits, and bounded rate limiting."""

from __future__ import annotations

import re
import time
from collections import deque
from datetime import date

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def sanitize_text(value: str, *, allow_newlines: bool = False) -> str:
    """Strip unsafe control characters and HTML tags from plain text inputs."""
    cleaned = _CONTROL_CHARS_RE.sub("", value)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    if not allow_newlines:
        cleaned = " ".join(cleaned.split())
    else:
        lines = [" ".join(line.split()) for line in cleaned.splitlines()]
        cleaned = "\n".join(line for line in lines if line)
    return cleaned.strip()


def validate_iso_date(value: str) -> str:
    """Ensure a string is a valid YYYY-MM-DD calendar date."""
    parsed = date.fromisoformat(value)
    return parsed.isoformat()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach hardened HTTP security headers including strict CSP without unsafe-inline."""

    CSP_POLICY = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "manifest-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    def __init__(self, app, *, is_production: bool = False) -> None:
        super().__init__(app)
        self.is_production = is_production

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = self.CSP_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if self.is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=300"
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject excessively large JSON payloads early to guard against memory exhaustion."""

    def __init__(self, app, *, max_bytes: int = 65_536) -> None:
        super().__init__(app)
        self.max_bytes = max(1024, max_bytes)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method in ("POST", "PUT", "PATCH") and request.url.path.startswith("/api/"):
            content_length = request.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request payload exceeds maximum allowed size (64 KB)."},
                )
            body = await request.body()
            if len(body) > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request payload exceeds maximum allowed size (64 KB)."},
                )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Bounded sliding-window rate limiter protecting API and GenAI routes."""

    MAX_TRACKED_KEYS = 2048

    def __init__(
        self,
        app,
        *,
        general_rpm: int = 180,
        ai_rpm: int = 30,
    ) -> None:
        super().__init__(app)
        self.general_rpm = max(1, general_rpm)
        self.ai_rpm = max(1, ai_rpm)
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def reset(self) -> None:
        """Clear in-memory rate-limit counters."""
        self._hits.clear()

    def _prune_stale(self, window_start: float) -> None:
        """Remove inactive keys so memory stays strictly bounded under high traffic."""
        stale_keys = [
            key
            for key, queue in self._hits.items()
            if not queue or queue[-1] < window_start
        ]
        for key in stale_keys:
            self._hits.pop(key, None)

        if len(self._hits) >= self.MAX_TRACKED_KEYS:
            # Evict oldest least-recently-seen entry
            oldest_key = min(
                self._hits,
                key=lambda k: self._hits[k][-1] if self._hits[k] else 0.0,
            )
            self._hits.pop(oldest_key, None)

    @staticmethod
    def _client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        is_ai = path.startswith("/api/ai/")
        bucket = "ai" if is_ai else "general"
        limit = self.ai_rpm if is_ai else self.general_rpm
        now = time.monotonic()
        window_start = now - 60.0

        if len(self._hits) >= self.MAX_TRACKED_KEYS:
            self._prune_stale(window_start)

        key = (self._client_key(request), bucket)
        timestamps = self._hits.setdefault(key, deque())
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()

        if len(timestamps) >= limit:
            retry_after = max(1, int(60 - (now - timestamps[0])))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        "Rate limit exceeded. Please wait briefly before making more requests."
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - len(timestamps)))
        return response
