"""The contract between the tracker and whatever supplies club rosters."""

from __future__ import annotations

from typing import Protocol

from fantracker.models import ClubSnapshot


class SourceError(RuntimeError):
    """The data source could not supply a usable roster. Messages must not contain secrets."""


class FanSource(Protocol):
    def fetch_club(self, club_id: str) -> ClubSnapshot:
        """Blocking call. Raise SourceError on any failure."""
        ...
