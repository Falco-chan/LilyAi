"""API authentication and rate limiting. No web-framework imports, so it is testable alone.

Two bearer keys: a read key (GET endpoints) and a write key (also allowed to
trigger a sync). The write key implies read access. With no keys configured
every private endpoint answers 503, so a forgotten setting fails closed.
"""

from __future__ import annotations

import hmac
import time
from collections import deque
from collections.abc import Callable


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def _matches(secret: str | None, candidate: str) -> bool:
    return bool(secret) and hmac.compare_digest(secret.encode(), candidate.encode())


def _bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def authorize(
    read_key: str | None,
    write_key: str | None,
    authorization_header: str | None,
    *,
    need_write: bool,
) -> str:
    """Return the caller's role ("read" or "write") or raise ApiError."""
    if not (read_key or write_key):
        raise ApiError(503, "api_disabled", "The API has no keys configured on the server.")
    unauthorized = ApiError(
        401, "unauthorized", "Missing or invalid API key.", {"WWW-Authenticate": "Bearer"}
    )
    token = _bearer(authorization_header)
    if token is None:
        raise unauthorized
    is_write = _matches(write_key, token)
    is_read = _matches(read_key, token)
    if not (is_write or is_read):
        raise unauthorized
    if need_write and not is_write:
        raise ApiError(403, "forbidden", "This key can read data but not change it.")
    return "write" if is_write else "read"


class RateLimiter:
    """Sliding window: at most `limit` calls per `window` seconds per key."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str) -> float:
        """0.0 if the call is allowed (and counted); otherwise seconds to wait."""
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return max(0.001, self._window - (now - hits[0]))
        hits.append(now)
        return 0.0
