"""uma.moe circle (club) client. The only module that knows uma.moe's API.

PROVENANCE: the endpoint, header and field names below come from the notes of a
third-party project that inspected the live API
(sukuna-shinmyomaru/umamusume-quota-dashboard, docs/uma-api.md). They do NOT
come from uma.moe's own OpenAPI file, which could not be fetched when this was
written. Check them against https://uma.moe/api/docs. See docs/UMAMOE.md.

Request:   GET https://uma.moe/api/v4/circles?circle_id=<id>&year=<Y>&month=<M>
           header  X-API-Key: <key>
Response:  {"circle": {...}, "members": [{"viewer_id", "trainer_name", "daily_fans": [32 slots]}]}

`daily_fans[i]` is a member's lifetime cumulative fan total at the end of
competition day i (slot 0 is the previous month's closing total). Slots that
have not happened yet are 0, and so are the slots after a member left.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import requests

from fantracker.models import ClubSnapshot, SourceMember
from fantracker.sources.base import SourceError

logger = logging.getLogger(__name__)

BASE_URL = "https://uma.moe"
CIRCLES_PATH = "/api/v4/circles"
JST = timezone(timedelta(hours=9))  # uma.moe's competition months follow Japan time


class _MonthRejected(Exception):
    """uma.moe answered 400, which it does for a month it does not have yet."""


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


class UmaMoeClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 20.0,
        clock: Callable[[], datetime] = _now_utc,
    ) -> None:
        if not api_key:
            raise SourceError("UMAMOE_API_KEY is not set.")
        self._url = base_url.rstrip("/") + CIRCLES_PATH
        self._headers = {"X-API-Key": api_key, "Accept": "application/json", "User-Agent": "lilyai-fan-tracker"}
        self._session = session or requests.Session()
        self._timeout = timeout
        self._clock = clock

    def fetch_club(self, club_id: str) -> ClubSnapshot:
        local = self._clock().astimezone(JST)
        year, month = local.year, local.month
        try:
            payload = self._get(club_id, year, month)
        except _MonthRejected:
            # Right after a month change uma.moe may not have the new month yet.
            year, month = _previous_month(year, month)
            logger.info("uma.moe has no data for the current month yet; using %04d-%02d", year, month)
            try:
                payload = self._get(club_id, year, month)
            except _MonthRejected:
                raise SourceError("uma.moe rejected the request (HTTP 400) for this and the previous month") from None
        return parse_circle(payload, club_id)

    def _get(self, club_id: str, year: int, month: int) -> Any:
        params = {"circle_id": club_id, "year": year, "month": month}
        try:
            response = self._session.get(self._url, params=params, headers=self._headers, timeout=self._timeout)
        except requests.RequestException as exc:
            # Class name only: the exception text can contain the URL or request details.
            raise SourceError(f"uma.moe request failed ({type(exc).__name__})") from None

        status = response.status_code
        if status == 200:
            try:
                return response.json()
            except ValueError:
                raise SourceError("uma.moe returned an unreadable response") from None
        if status == 400:
            raise _MonthRejected
        if status == 401:
            raise SourceError("uma.moe rejected the API key (HTTP 401). Check UMAMOE_API_KEY.")
        if status == 403:
            raise SourceError("uma.moe refused the request (HTTP 403).")
        if status == 404:
            raise SourceError("uma.moe does not know this club (HTTP 404).")
        if status == 429:
            retry = getattr(response, "headers", {}).get("Retry-After")
            raise SourceError("uma.moe is rate limiting us (HTTP 429)" + (f"; retry after {retry}s." if retry else "."))
        raise SourceError(f"uma.moe returned HTTP {status}")


def _latest_total(daily_fans: list[Any]) -> tuple[int, int] | None:
    """(slot index, fan total) of the newest positive entry.

    Negative entries are transfer markers from a previous club and zeros are
    missed scrapes or days that have not happened, so both are skipped.
    """
    for index in range(len(daily_fans) - 1, -1, -1):
        value = daily_fans[index]
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        if value > 0:
            return index, int(value)
    return None


def _parse_member(raw: Any) -> tuple[int, SourceMember] | None:
    if not isinstance(raw, dict):
        return None
    viewer_id = raw.get("viewer_id")
    if isinstance(viewer_id, bool) or not isinstance(viewer_id, int | str) or not str(viewer_id).strip():
        return None
    daily_fans = raw.get("daily_fans")
    if not isinstance(daily_fans, list):
        return None
    latest = _latest_total(daily_fans)
    if latest is None:
        return None
    index, total = latest
    trainer_id = str(viewer_id).strip()
    name = raw.get("trainer_name")
    name = name.strip() if isinstance(name, str) and name.strip() else f"Trainer {trainer_id}"
    return index, SourceMember(trainer_id, name, total)


def _select_active(candidates: list[tuple[int, SourceMember]], member_count: Any, club_id: str) -> list[SourceMember]:
    """Drop members who left mid-month.

    uma.moe keeps them in `members` with zeros after their last day, so a member
    whose newest data is older than everyone else's has left. One missed scrape
    looks the same, so when `circle.member_count` is known we keep that many of
    the freshest members instead of only those on the very newest day.
    """
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda item: item[0], reverse=True)
    expected = member_count if isinstance(member_count, int) and not isinstance(member_count, bool) else None
    if expected is not None and expected > 0:
        cutoff = ordered[min(expected, len(ordered)) - 1][0]
    else:
        expected = None
        cutoff = ordered[0][0]
    active = [member for index, member in ordered if index >= cutoff]
    if expected is not None and len(active) != expected:
        logger.warning("Club %s: uma.moe reports %s members but %s look active", club_id, expected, len(active))
    return active


def parse_circle(payload: Any, club_id: str) -> ClubSnapshot:
    """Turn one /api/v4/circles response into a roster. Raises SourceError on a shape we don't know."""
    if not isinstance(payload, dict):
        raise SourceError("uma.moe returned an unexpected response shape")
    circle = payload.get("circle")
    members_raw = payload.get("members")
    if not isinstance(circle, dict) or not isinstance(members_raw, list):
        raise SourceError("uma.moe response is missing 'circle' or 'members'")
    circle_id = circle.get("circle_id")
    if circle_id is None:
        raise SourceError("uma.moe response is missing circle.circle_id")

    candidates = []
    skipped = 0
    for raw in members_raw:
        parsed = _parse_member(raw)
        if parsed is None:
            skipped += 1
        else:
            candidates.append(parsed)
    if skipped:
        logger.warning("Club %s: skipped %s member entries uma.moe sent without usable data", club_id, skipped)

    name = circle.get("name")
    return ClubSnapshot(
        club_id=str(circle_id),
        members=_select_active(candidates, circle.get("member_count"), club_id),
        club_name=name.strip() if isinstance(name, str) and name.strip() else None,
    )
