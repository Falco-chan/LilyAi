"""Background loops: the 5-minute data refresh and the Render self-ping."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import requests

from fantracker.models import SyncOutcome
from fantracker.services.tracker import TrackerService

logger = logging.getLogger(__name__)


class SyncTooSoon(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__("a sync ran very recently")
        self.retry_after = retry_after


class SyncRunner:
    def __init__(
        self,
        service: TrackerService,
        interval_seconds: float,
        *,
        min_manual_gap_seconds: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._interval = interval_seconds
        self._gap = min_manual_gap_seconds
        self._monotonic = monotonic
        self._lock = asyncio.Lock()
        self._last_run: float | None = None
        self.last_outcomes: list[SyncOutcome] = []

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self._cycle()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    async def sync_now(self) -> list[SyncOutcome]:
        """Manual refresh (used by the API). Rate limited."""
        if self._last_run is not None:
            elapsed = self._monotonic() - self._last_run
            if elapsed < self._gap:
                raise SyncTooSoon(self._gap - elapsed)
        return await self._cycle()

    async def _cycle(self) -> list[SyncOutcome]:
        async with self._lock:
            outcomes = await asyncio.to_thread(self._service.sync_all)
            self._last_run = self._monotonic()
            self.last_outcomes = outcomes
            return outcomes


def _http_get(url: str) -> int:
    return requests.get(url, timeout=20, headers={"User-Agent": "lilyai-fan-tracker-self-ping"}).status_code


async def run_self_ping(
    url: str,
    interval_seconds: float,
    stop: asyncio.Event,
    *,
    fetch: Callable[[str], int] = _http_get,
) -> None:
    """Request our own /health on a timer. A failed ping is logged, never fatal."""
    reported_ok = False
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            return  # asked to stop
        except TimeoutError:
            pass
        try:
            status = await asyncio.to_thread(fetch, url)
        except Exception as exc:  # noqa: BLE001 - must never kill the loop
            logger.warning("Self-ping failed (%s)", type(exc).__name__)
            continue
        if status >= 400:
            logger.warning("Self-ping got HTTP %s", status)
        elif not reported_ok:
            logger.info("Self-ping is working (HTTP %s); further pings are logged at debug level", status)
            reported_ok = True
        else:
            logger.debug("Self-ping OK (HTTP %s)", status)
