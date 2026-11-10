"""TursoDatabase against a MOCK of Turso's documented HTTP API.

The request and response shapes follow https://docs.turso.tech/sdk/http/reference.
These tests do not talk to a real Turso database.
"""

from __future__ import annotations

import base64

import pytest
import requests

from fantracker.storage import DatabaseError, TursoDatabase

TOKEN = "super-secret-token"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def post(self, url, json, headers, timeout):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.error:
            raise self.error
        return self.response

    def close(self):
        pass


def ok_result(rows=(), cols=("c",)):
    return {
        "type": "ok",
        "response": {
            "type": "execute",
            "result": {"cols": [{"name": c} for c in cols], "rows": [list(r) for r in rows]},
        },
    }


CLOSE = {"type": "ok", "response": {"type": "close"}}


def make(response=None, error=None, url="libsql://my-db-org.turso.io"):
    session = FakeSession(response, error)
    return TursoDatabase(url, TOKEN, session=session), session


def test_request_format_matches_the_documented_pipeline():
    db, session = make(FakeResponse({"baton": None, "results": [ok_result(), CLOSE]}))
    db.execute("SELECT ?", [1])
    call = session.calls[0]
    assert call["url"] == "https://my-db-org.turso.io/v2/pipeline"  # libsql:// became https://
    assert call["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert call["json"] == {
        "requests": [
            {"type": "execute", "stmt": {"sql": "SELECT ?", "args": [{"type": "integer", "value": "1"}]}},
            {"type": "close"},
        ]
    }


def test_arguments_are_encoded_by_type():
    db, session = make(FakeResponse({"results": [ok_result(), CLOSE]}))
    db.execute("x", [None, True, 7, 1.5, "text", b"\x00\x01"])
    args = session.calls[0]["json"]["requests"][0]["stmt"]["args"]
    assert args == [
        {"type": "null"},
        {"type": "integer", "value": "1"},
        {"type": "integer", "value": "7"},
        {"type": "float", "value": 1.5},
        {"type": "text", "value": "text"},
        {"type": "blob", "base64": base64.b64encode(b"\x00\x01").decode()},
    ]


def test_rows_are_decoded_into_python_values():
    rows = [
        [{"type": "text", "value": "Alice"}, {"type": "integer", "value": "123456789012"}, {"type": "null"}],
    ]
    db, _ = make(FakeResponse({"results": [ok_result(rows), CLOSE]}))
    assert db.execute("SELECT ...") == [("Alice", 123456789012, None)]


def test_many_statements_travel_in_one_request_and_results_line_up():
    one = [[{"type": "integer", "value": "1"}]]
    two = [[{"type": "text", "value": "two"}]]
    db, session = make(FakeResponse({"results": [ok_result(one), ok_result(two), CLOSE]}))
    assert db.execute_many([("a", ()), ("b", ())]) == [[(1,)], [("two",)]]
    assert len(session.calls) == 1


def test_a_failed_statement_raises_with_its_message():
    failing = {"type": "error", "error": {"message": "no such table: nope", "code": "SQL_INPUT_ERROR"}}
    db, _ = make(FakeResponse({"results": [ok_result(), failing, CLOSE]}))
    with pytest.raises(DatabaseError, match="statement 2 failed: no such table: nope"):
        db.execute_many([("a", ()), ("b", ())])


def test_http_error_does_not_leak_the_token():
    db, _ = make(FakeResponse({"error": "unauthorized"}, status=401))
    with pytest.raises(DatabaseError) as info:
        db.execute("x")
    assert "401" in str(info.value) and TOKEN not in str(info.value)


def test_network_error_does_not_leak_the_url_or_token():
    db, _ = make(error=requests.ConnectionError(f"failed to reach https://my-db-org.turso.io with {TOKEN}"))
    with pytest.raises(DatabaseError) as info:
        db.execute("x")
    assert "ConnectionError" in str(info.value)
    assert TOKEN not in str(info.value) and "turso.io" not in str(info.value)


def test_unreadable_response_raises():
    db, _ = make(FakeResponse(ValueError("not json")))
    with pytest.raises(DatabaseError, match="unreadable"):
        db.execute("x")


def test_missing_result_entries_raise_instead_of_returning_partial_data():
    db, _ = make(FakeResponse({"results": [ok_result()]}))
    with pytest.raises(DatabaseError):
        db.execute_many([("a", ()), ("b", ())])


def test_bad_url_is_rejected():
    with pytest.raises(DatabaseError):
        TursoDatabase("my-db.turso.io", TOKEN)


def test_https_url_is_used_as_is():
    db, session = make(FakeResponse({"results": [ok_result(), CLOSE]}), url="https://db.example.test/")
    db.execute("x")
    assert session.calls[0]["url"] == "https://db.example.test/v2/pipeline"
