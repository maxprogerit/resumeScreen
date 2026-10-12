"""Authentication (Bearer key or signed session cookie), rate limiting, body-size limit and
security headers."""

import hashlib
import hmac
import secrets
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import MutableHeaders

from app.errors import ApiError, error_response
from app.models import RateLimitHit

SESSION_COOKIE = "rs_session"
SESSION_TTL_S = 8 * 3600
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "resume-screener"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# ------------------------------------------------------------------ sessions


def _session_key(api_key: str) -> bytes:
    return hashlib.sha256(b"resume-screener-session:" + api_key.encode()).digest()


def make_session_token(api_key: str, now: float | None = None) -> str:
    """`<expiry>.<hmac>`. Rotating API_KEY_ADMIN invalidates every session."""
    expiry = int((time.time() if now is None else now) + SESSION_TTL_S)
    sig = hmac.new(_session_key(api_key), str(expiry).encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"


def verify_session_token(token: str, api_key: str, now: float | None = None) -> bool:
    expiry_s, _, sig = token.partition(".")
    if not expiry_s.isdigit():
        return False
    expected = hmac.new(_session_key(api_key), expiry_s.encode(), hashlib.sha256).hexdigest()
    current = time.time() if now is None else now
    return hmac.compare_digest(sig, expected) and int(expiry_s) > current


def key_matches(candidate: str, api_key: str) -> bool:
    return secrets.compare_digest(candidate.encode(), api_key.encode())


def require_api_key(request: Request) -> None:
    configured = request.app.state.settings.api_key_admin
    if configured is None:
        raise ApiError(503, "auth_not_configured", "API_KEY_ADMIN is not configured on the server")
    api_key = configured.get_secret_value()

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and key_matches(token, api_key):
        return

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie and verify_session_token(cookie, api_key):
        # Cookie auth is ambient, so state-changing requests must prove they come from our
        # own page: a custom header cannot be sent cross-site without a CORS preflight
        # (which this server never allows). SameSite=Strict is the second layer.
        if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise ApiError(403, "csrf_failed", "Missing X-Requested-With header")
        return

    raise ApiError(
        401, "unauthorized", "Missing or invalid credentials", {"WWW-Authenticate": "Bearer"}
    )


# ------------------------------------------------------------------ rate limiting


class DbRateLimiter:
    """Sliding-window limiter backed by the database, so the limit holds across worker
    processes. Check-then-insert is not atomic: under heavy concurrency a few extra
    requests can slip through, which is fine for abuse protection."""

    def __init__(self, session_factory: sessionmaker[Session], limit: int, window_s: float = 60.0):
        self.session_factory = session_factory
        self.limit = limit
        self.window_s = window_s

    def hit(self, key: str, now: float | None = None, *, limit: int | None = None) -> None:
        t = time.time() if now is None else now
        cap = self.limit if limit is None else limit
        with self.session_factory() as db:
            db.execute(delete(RateLimitHit).where(RateLimitHit.ts <= t - self.window_s))
            count, oldest = db.execute(
                select(func.count(), func.min(RateLimitHit.ts)).where(RateLimitHit.key == key)
            ).one()
            if count >= cap:
                db.commit()
                retry = max(1, int(self.window_s - (t - (oldest or t))))
                raise ApiError(
                    429, "rate_limited", "Too many requests, slow down", {"Retry-After": str(retry)}
                )
            db.add(RateLimitHit(key=key, ts=t))
            db.commit()


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def enforce_upload_rate_limit(request: Request) -> None:
    request.app.state.rate_limiter.hit(f"upload:{_client(request)}")


def enforce_login_rate_limit(request: Request) -> None:
    # Brute-force protection for the key check: 10 attempts per minute per client.
    request.app.state.rate_limiter.hit(f"login:{_client(request)}", limit=10)


# ------------------------------------------------------------------ request body limit

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BodyLimitMiddleware:
    """Rejects request bodies above `max_bytes`, counting the bytes actually received, so
    chunked uploads (no Content-Length) cannot bypass the limit."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.max_bytes:
            response = error_response(
                413, "payload_too_large", "Request body too large", scope["path"]
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> MutableMapping[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise ApiError(413, "payload_too_large", "Request body too large")
            return message

        await self.app(scope, limited_receive, send)


# ------------------------------------------------------------------ headers

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


class SecurityHeadersMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so exceptions raised while the body is being
    received reach FastAPI's handlers unchanged."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]

        async def send_with_headers(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Frame-Options", "DENY")
                if not path.startswith(("/docs", "/redoc")):
                    headers.setdefault("Content-Security-Policy", _CSP)
                if path.startswith("/api"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)
