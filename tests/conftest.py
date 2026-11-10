"""Shared test helpers.

Everything here is a MOCK: the data source is a fake and the database is
in-memory SQLite. Passing tests prove the logic, not that uma.moe, Discord
or Turso work from a real deployment.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fantracker.config import ClubConfig
from fantracker.models import ClubSnapshot, SourceMember
from fantracker.services.tracker import TrackerService
from fantracker.sources.base import SourceError
from fantracker.storage import SqliteDatabase, Store

CLUB1 = ClubConfig("club1", "974470619", "Club 1")
CLUB2 = ClubConfig("club2", "325938032", "Club 2")


def utc(text: str) -> datetime:
    """utc('2026-11-04 12:00') -> aware datetime."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


class FakeSource:
    def __init__(self) -> None:
        self.rosters: dict[str, dict[str, tuple[str, int]]] = {}
        self.errors: dict[str, Exception] = {}

    def set(self, club_id: str, members: dict[str, tuple[str, int]]) -> None:
        self.rosters[club_id] = dict(members)

    def fetch_club(self, club_id: str) -> ClubSnapshot:
        if club_id in self.errors:
            raise self.errors[club_id]
        if club_id not in self.rosters:
            raise SourceError("no such club in the fake source")
        members = [SourceMember(t, name, fans) for t, (name, fans) in self.rosters[club_id].items()]
        return ClubSnapshot(club_id, members, club_name=f"Real name of {club_id}")


class World:
    """A service wired to a fake source, an in-memory database and a settable clock."""

    def __init__(self) -> None:
        self.now = utc("2026-11-04 00:05")  # a Wednesday
        self.source = FakeSource()
        self.db = SqliteDatabase(":memory:")
        self.store = Store(self.db)
        self.store.init_schema()
        self.service = TrackerService(self.store, self.source, [CLUB1, CLUB2], clock=lambda: self.now)

    def at(self, text: str) -> World:
        self.now = utc(text)
        return self

    def roster(self, members: dict[str, tuple[str, int]], club: ClubConfig = CLUB1) -> World:
        self.source.set(club.club_id, members)
        return self

    def sync(self):
        return self.service.sync_all()

    def gain(self, trainer_id: str, period: str, club: ClubConfig = CLUB1) -> tuple[int, bool]:
        rows = self.store.standings(club.club_id, period, self.now)
        row = next(r for r in rows if r.trainer_id == trainer_id)
        return row.gain, row.has_baseline

    def on_roster(self, club: ClubConfig = CLUB1) -> list[str]:
        return [r.trainer_id for r in self.store.standings(club.club_id, "daily", self.now)]


@pytest.fixture
def world() -> World:
    w = World()
    # Club 2 just needs to exist and stay quiet unless a test uses it.
    w.source.set(CLUB2.club_id, {"900": ("Other", 10)})
    return w
