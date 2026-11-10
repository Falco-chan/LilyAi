"""Database backends behind one tiny interface.

`Store` only ever calls `execute` / `execute_many`, so the backend is
replaceable. SQLite (stdlib) is used for tests and local development.
Turso is used in production through its documented HTTP API
(`POST /v2/pipeline`), which needs nothing beyond `requests`.
"""

from __future__ import annotations

import base64
import sqlite3
import threading
from collections.abc import Sequence
from typing import Any, Protocol

import requests

Params = Sequence[Any]
Statement = tuple[str, Params]
Rows = list[tuple]


class DatabaseError(RuntimeError):
    """A database call failed. Messages never contain credentials."""


class Database(Protocol):
    def execute(self, sql: str, params: Params = ()) -> Rows: ...

    def execute_many(self, statements: Sequence[Statement]) -> list[Rows]: ...

    def close(self) -> None: ...


class SqliteDatabase:
    """Thread-safe wrapper over one sqlite3 connection."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()

    def execute(self, sql: str, params: Params = ()) -> Rows:
        return self.execute_many([(sql, params)])[0]

    def execute_many(self, statements: Sequence[Statement]) -> list[Rows]:
        """Run statements in order inside one transaction."""
        with self._lock:
            try:
                with self._conn:  # commits on success, rolls back on error
                    return [self._conn.execute(sql, tuple(params)).fetchall() for sql, params in statements]
            except sqlite3.Error as exc:
                raise DatabaseError(f"SQLite error: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _encode(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, bytes):
        return {"type": "blob", "base64": base64.b64encode(value).decode("ascii")}
    return {"type": "text", "value": str(value)}


def _decode(cell: dict[str, Any]) -> Any:
    kind = cell.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(cell["value"])
    if kind == "float":
        return float(cell["value"])
    if kind == "blob":
        return base64.b64decode(cell["base64"])
    return cell["value"]  # text


class TursoDatabase:
    """Turso over HTTP.

    Every `execute_many` call is one pipeline request, so statements run in
    order on one connection. The pipeline is NOT atomic: if statement N fails,
    statements before it have already been applied. `Store` is written so
    that a failed sync is simply repeated by the next one.
    """

    def __init__(
        self,
        url: str,
        auth_token: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 20.0,
    ) -> None:
        base = url.strip().rstrip("/")
        if base.startswith("libsql://"):
            base = "https://" + base[len("libsql://") :]
        if not base.startswith(("https://", "http://")):
            raise DatabaseError("TURSO_DATABASE_URL must start with libsql:// or https://")
        self._endpoint = base + "/v2/pipeline"
        self._headers = {"Authorization": f"Bearer {auth_token}"}
        self._session = session or requests.Session()
        self._timeout = timeout
        self._lock = threading.Lock()

    def execute(self, sql: str, params: Params = ()) -> Rows:
        return self.execute_many([(sql, params)])[0]

    def execute_many(self, statements: Sequence[Statement]) -> list[Rows]:
        if not statements:
            return []
        body = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": [_encode(p) for p in params]}}
                for sql, params in statements
            ]
            + [{"type": "close"}]
        }
        try:
            with self._lock:
                response = self._session.post(
                    self._endpoint, json=body, headers=self._headers, timeout=self._timeout
                )
        except requests.RequestException as exc:
            # Class name only: the exception text can contain the URL.
            raise DatabaseError(f"Turso request failed ({type(exc).__name__})") from None
        if response.status_code != 200:
            raise DatabaseError(f"Turso returned HTTP {response.status_code}")
        try:
            results = response.json()["results"]
        except (ValueError, KeyError, TypeError):
            raise DatabaseError("Turso returned an unreadable response") from None

        out: list[Rows] = []
        for index in range(len(statements)):
            entry = results[index] if index < len(results) else {"type": "error"}
            if entry.get("type") != "ok":
                message = (entry.get("error") or {}).get("message", "unknown error")
                raise DatabaseError(f"Turso statement {index + 1} failed: {message}")
            result = entry["response"]["result"]
            out.append([tuple(_decode(cell) for cell in row) for row in result.get("rows", [])])
        return out

    def close(self) -> None:
        self._session.close()
