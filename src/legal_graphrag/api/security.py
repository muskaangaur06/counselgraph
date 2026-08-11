"""API-layer guardrails: authentication and rate limiting, both as FastAPI dependencies.

Auth is a small fixed reviewer roster (no user database, no signup): each
session token carries a username and role. The roster is read from either the
REVIEWERS json env var, e.g.:

    REVIEWERS=[{"username":"admin","password":"...","role":"admin"},
               {"username":"reviewer","password":"...","role":"reviewer"},
               {"username":"senior_counsel","password":"...","role":"senior_counsel"}]

or from REVIEWER_1_USERNAME/REVIEWER_1_PASSWORD/REVIEWER_1_ROLE style vars
(REVIEWER_2_*, REVIEWER_3_*, ...) if REVIEWERS isn't set. Falls back to the old
single hardcoded ADMIN_USERNAME/ADMIN_PASSWORD account (role "admin") if
neither is configured, so existing deployments keep working unchanged.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Cookie, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin@321")

SESSION_COOKIE_NAME = "lg_session"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60  # 12 hours

_SESSION_SECRET = os.getenv("SESSION_SECRET") or os.getenv("API_KEY") or "dev-only-insecure-secret"
_serializer = URLSafeTimedSerializer(_SESSION_SECRET, salt="lg-session")

RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "30"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

VALID_ROLES = ("admin", "reviewer", "senior_counsel")


@dataclass(frozen=True)
class Reviewer:
    username: str
    password: str
    role: str


def _load_roster_from_json() -> list[Reviewer] | None:
    raw = os.getenv("REVIEWERS")
    if not raw:
        return None
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        raise EnvironmentError("REVIEWERS env var is not valid JSON.")
    return [Reviewer(e["username"], e["password"], e.get("role", "reviewer")) for e in entries]


def _load_roster_from_numbered_vars() -> list[Reviewer] | None:
    roster: list[Reviewer] = []
    i = 1
    while True:
        username = os.getenv(f"REVIEWER_{i}_USERNAME")
        if not username:
            break
        password = os.getenv(f"REVIEWER_{i}_PASSWORD", "")
        role = os.getenv(f"REVIEWER_{i}_ROLE", "reviewer")
        roster.append(Reviewer(username, password, role))
        i += 1
    return roster or None


def load_reviewer_roster() -> list[Reviewer]:
    roster = _load_roster_from_json() or _load_roster_from_numbered_vars()
    if roster:
        return roster
    # backward-compatible fallback: the original single hardcoded admin account
    return [Reviewer(ADMIN_USERNAME, ADMIN_PASSWORD, "admin")]


def verify_credentials(username: str, password: str) -> Reviewer | None:
    """Constant-time-ish lookup against the fixed reviewer roster. Returns the
    matched Reviewer (with its role) or None."""
    for reviewer in load_reviewer_roster():
        if reviewer.username == username and reviewer.password == password:
            return reviewer
    return None


def verify_admin_credentials(username: str, password: str) -> bool:
    """Kept for backward compatibility with existing callers/tests: true for ANY
    valid roster account, not just one named "admin"."""
    return verify_credentials(username, password) is not None


def create_session_token(username: str = ADMIN_USERNAME, role: str = "admin") -> str:
    return _serializer.dumps({"user": username, "role": role})


def _decode_session_token(token: str) -> dict | None:
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None


def _verify_session_token(token: str) -> bool:
    return _decode_session_token(token) is not None


async def require_session(lg_session: str | None = Cookie(default=None)) -> None:
    """Requires a valid, non-expired session cookie from POST /api/auth/login."""
    if not lg_session or not _verify_session_token(lg_session):
        raise HTTPException(status_code=401, detail="Not logged in, or session expired. Please log in again.")


async def get_current_reviewer(lg_session: str | None = Cookie(default=None)) -> dict:
    """Like require_session, but also returns {"username", "role"} for the caller
    to use (e.g. stamping the real reviewer identity/role onto a review_action
    row instead of always "admin")."""
    if not lg_session:
        raise HTTPException(status_code=401, detail="Not logged in, or session expired. Please log in again.")
    payload = _decode_session_token(lg_session)
    if payload is None:
        raise HTTPException(status_code=401, detail="Not logged in, or session expired. Please log in again.")
    return {"username": payload.get("user", "unknown"), "role": payload.get("role", "reviewer")}


class _SlidingWindowRateLimiter:
    """In-memory, per-process rate limiter. A multi-worker deployment would need Redis instead."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > self.window_seconds:
            window.popleft()
        if len(window) >= self.max_requests:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {self.max_requests} requests per "
                       f"{self.window_seconds}s. Try again shortly.",
            )
        window.append(now)


_limiter = _SlidingWindowRateLimiter(RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


async def rate_limit(request: Request) -> None:
    client_key = request.cookies.get(SESSION_COOKIE_NAME) or (request.client.host if request.client else "unknown")
    _limiter.check(client_key)


# separate, tighter limiter for login attempts specifically, keyed by source IP
_login_limiter = _SlidingWindowRateLimiter(max_requests=10, window_seconds=60)


async def login_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    _login_limiter.check(client_ip)
