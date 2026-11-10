"""Plain data types shared between the data source, storage, and front ends."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SourceMember:
    trainer_id: str
    name: str
    fans: int


@dataclass(frozen=True)
class ClubSnapshot:
    """One club roster as returned by a data source at one moment."""

    club_id: str
    members: list[SourceMember]
    club_name: str | None = None


@dataclass(frozen=True)
class SyncResult:
    club_id: str
    member_count: int
    joined: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    took_day_snapshot: bool = False


@dataclass(frozen=True)
class SyncOutcome:
    club_key: str
    ok: bool
    error: str | None = None
    result: SyncResult | None = None


@dataclass(frozen=True)
class StandingRow:
    rank: int
    trainer_id: str
    name: str
    fans: int  # current total as reported by the source
    gain: int  # gain over the requested period
    has_baseline: bool  # False on a member's join day: gain is shown as 0


@dataclass(frozen=True)
class ClubInfo:
    club_id: str
    name: str | None
    member_count: int
    last_synced_at: datetime | None
    last_error: str | None
