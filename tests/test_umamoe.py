"""uma.moe client against a MOCK of the response shape described in docs/UMAMOE.md.

The fixture below follows a third-party project's notes on the live API, not
uma.moe's own OpenAPI file. These tests prove the parsing and error handling,
not that a real request with a real key succeeds.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import requests

from fantracker.config import load_settings
from fantracker.sources import UmaMoeClient, build_source
from fantracker.sources.base import SourceError
from fantracker.sources.umamoe import parse_circle

KEY = "uma_k_test_secret_value"
CLUB = "974470619"


def slots(*values: int) -> list[int]:
    """A daily_fans array: 32 slots, zero after the given values."""
    return [*values, *([0] * (32 - len(values)))]


def member(viewer_id: int, name: str, daily_fans: list[int]) -> dict:
    return {"viewer_id": viewer_id, "trainer_name": name, "shame_score": 0, "daily_fans": daily_fans}


def payload(members: list[dict], *, circle_id: int = int(CLUB), member_count: int | None = None) -> dict:
    circle = {"circle_id": circle_id, "name": "Dynasty"}
    if member_count is not None:
        circle["member_count"] = member_count
    return {"circle": circle, "members": members}


class FakeResponse:
    def __init__(self, body=None, status=200, headers=None):
        self._body, self.status_code, self.headers = body, status, headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, *responses, error=None):
        self._responses = list(responses)
        self._error = error
        self.calls: list[dict] = []

    def get(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        if self._error:
            raise self._error
        return self._responses.pop(0)


def client(session, when="2026-11-10 06:00"):
    moment = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    return UmaMoeClient(KEY, session=session, clock=lambda: moment)


def test_request_uses_the_documented_endpoint_header_and_params():
    session = FakeSession(FakeResponse(payload([member(1, "A", slots(10, 20))])))
    client(session).fetch_club(CLUB)
    call = session.calls[0]
    assert call["url"] == "https://uma.moe/api/v4/circles"
    assert call["params"] == {"circle_id": CLUB, "year": 2026, "month": 11}
    assert call["headers"]["X-API-Key"] == KEY
    assert call["timeout"] == 20.0


def test_month_follows_japan_time_not_utc():
    # 2026-10-31 16:00 UTC is already 01:00 on Nov 1 in Japan.
    session = FakeSession(FakeResponse(payload([member(1, "A", slots(10))])))
    client(session, when="2026-10-31 16:00").fetch_club(CLUB)
    assert session.calls[0]["params"]["month"] == 11


def test_future_month_400_falls_back_to_the_previous_month():
    session = FakeSession(FakeResponse({}, status=400), FakeResponse(payload([member(1, "A", slots(10))])))
    snapshot = client(session, when="2026-12-01 03:00").fetch_club(CLUB)
    assert [(c["params"]["year"], c["params"]["month"]) for c in session.calls] == [(2026, 12), (2026, 11)]
    assert [m.trainer_id for m in snapshot.members] == ["1"]


def test_fallback_crosses_the_year_boundary():
    session = FakeSession(FakeResponse({}, status=400), FakeResponse(payload([member(1, "A", slots(10))])))
    client(session, when="2026-12-31 16:00").fetch_club(CLUB)  # 2027-01-01 in Japan
    assert [(c["params"]["year"], c["params"]["month"]) for c in session.calls] == [(2027, 1), (2026, 12)]


def test_two_400s_is_an_error():
    session = FakeSession(FakeResponse({}, status=400), FakeResponse({}, status=400))
    with pytest.raises(SourceError, match="HTTP 400"):
        client(session).fetch_club(CLUB)


def test_members_get_their_latest_total_and_names():
    body = payload(
        [
            member(111, "Alice", slots(1000, 1100, 1200)),
            member(222, "ボブ", slots(5000, 5100, 5300)),
        ]
    )
    snapshot = parse_circle(body, CLUB)
    assert snapshot.club_id == CLUB and snapshot.club_name == "Dynasty"
    assert [(m.trainer_id, m.name, m.fans) for m in snapshot.members] == [("111", "Alice", 1200), ("222", "ボブ", 5300)]


def test_member_who_left_is_dropped():
    body = payload(
        [
            member(1, "Stays", slots(100, 200, 300, 400)),
            member(2, "Also stays", slots(100, 150, 300, 450)),
            member(3, "Left", slots(100, 150, 0, 0)),
        ]
    )
    assert [m.trainer_id for m in parse_circle(body, CLUB).members] == ["1", "2"]


def test_transfer_markers_and_zero_gaps_are_skipped():
    body = payload(
        [
            member(1, "Transfer", slots(-1, -1, 300, 350)),
            member(2, "Gap", slots(100, 0, 300, 400)),
        ]
    )
    assert [(m.trainer_id, m.fans) for m in parse_circle(body, CLUB).members] == [("1", 350), ("2", 400)]


def test_member_with_no_positive_total_is_ignored():
    body = payload([member(1, "Real", slots(100, 200)), member(2, "Empty", slots(0, 0))])
    assert [m.trainer_id for m in parse_circle(body, CLUB).members] == ["1"]


def test_member_count_keeps_someone_whose_scrape_was_missed_today():
    body = payload(
        [
            member(1, "A", slots(100, 200, 300, 400)),
            member(2, "B", slots(100, 200, 300, 400)),
            member(3, "Missed", slots(100, 200, 300, 0)),  # no data for the newest day
            member(4, "Left", slots(100, 150, 0, 0)),
        ],
        member_count=3,
    )
    assert [m.trainer_id for m in parse_circle(body, CLUB).members] == ["1", "2", "3"]


def test_without_member_count_only_the_newest_day_counts():
    body = payload(
        [
            member(1, "A", slots(100, 200, 300)),
            member(2, "Behind", slots(100, 200, 0)),
        ]
    )
    assert [m.trainer_id for m in parse_circle(body, CLUB).members] == ["1"]


def test_unreadable_member_entries_are_skipped_not_fatal():
    body = payload(
        [
            "not a dict",
            {"viewer_id": None, "daily_fans": slots(1)},
            {"viewer_id": 5, "daily_fans": "nope"},
            member(6, "Fine", slots(10)),
        ]
    )
    assert [m.trainer_id for m in parse_circle(body, CLUB).members] == ["6"]


def test_missing_name_gets_a_placeholder():
    body = payload([{"viewer_id": 9, "daily_fans": slots(10)}])
    assert parse_circle(body, CLUB).members[0].name == "Trainer 9"


@pytest.mark.parametrize("body", [None, [], {}, {"circle": {}, "members": []}, {"circle": {"circle_id": 1}}])
def test_unknown_shapes_raise_a_source_error(body):
    with pytest.raises(SourceError):
        parse_circle(body, CLUB)


def test_a_different_club_in_the_answer_is_reported_to_the_caller():
    # The service compares snapshot.club_id with the requested club and rejects a mismatch.
    assert parse_circle(payload([member(1, "A", slots(10))], circle_id=42), CLUB).club_id == "42"


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (401, "API key"),
        (403, "refused"),
        (404, "does not know"),
        (429, "rate limiting"),
        (500, "HTTP 500"),
        (503, "HTTP 503"),
    ],
)
def test_http_errors_become_source_errors_without_the_key(status, text):
    session = FakeSession(FakeResponse({"error": "x"}, status=status))
    with pytest.raises(SourceError, match=text) as info:
        client(session).fetch_club(CLUB)
    assert KEY not in str(info.value)


def test_rate_limit_message_includes_retry_after():
    session = FakeSession(FakeResponse({}, status=429, headers={"Retry-After": "30"}))
    with pytest.raises(SourceError, match="30s"):
        client(session).fetch_club(CLUB)


def test_network_error_does_not_leak_the_key_or_url():
    session = FakeSession(error=requests.ConnectionError(f"failed https://uma.moe/api with {KEY}"))
    with pytest.raises(SourceError) as info:
        client(session).fetch_club(CLUB)
    assert "ConnectionError" in str(info.value)
    assert KEY not in str(info.value) and "https://" not in str(info.value)


def test_unreadable_json_is_a_source_error():
    session = FakeSession(FakeResponse(ValueError("not json")))
    with pytest.raises(SourceError, match="unreadable"):
        client(session).fetch_club(CLUB)


def test_client_requires_a_key():
    with pytest.raises(SourceError):
        UmaMoeClient("")


def test_build_source_needs_the_api_key():
    with pytest.raises(SourceError, match="UMAMOE_API_KEY"):
        build_source(load_settings({}))
    assert isinstance(build_source(load_settings({"UMAMOE_API_KEY": KEY})), UmaMoeClient)
