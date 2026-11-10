"""HTTP API for the LilyAi app, plus the health check Render and the self-pinger use.

Routes are thin: they call TrackerService and shape the answer. Every error
uses one JSON shape: {"error": {"code": "...", "message": "..."}}.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from fantracker import __version__
from fantracker.auth import ApiError, RateLimiter, authorize
from fantracker.config import ClubConfig, Settings
from fantracker.models import ClubInfo
from fantracker.runner import SyncRunner, SyncTooSoon
from fantracker.services.tracker import MAX_TOP, TrackerService
from fantracker.timeutil import PERIODS, utc_now

logger = logging.getLogger(__name__)

Period = Literal["daily", "weekly", "monthly"]


class ClubStatus(BaseModel):
    key: str
    club_id: str
    label: str
    name: str | None
    member_count: int
    last_synced_at: datetime | None
    last_error: str | None


class DiscordStatus(BaseModel):
    state: str  # disabled | starting | ready | reconnecting | error
    latency_ms: int | None
    error: str | None


class StatusResponse(BaseModel):
    server: str
    version: str
    time: datetime
    discord: DiscordStatus
    clubs: list[ClubStatus]


class LeaderboardEntry(BaseModel):
    rank: int
    trainer_id: str
    name: str
    fans: int
    gain: int
    has_baseline: bool


class LeaderboardResponse(BaseModel):
    club: ClubStatus
    period: Period
    top: int
    resets_at: datetime
    entries: list[LeaderboardEntry]


class PeriodStanding(BaseModel):
    gain: int
    rank: int
    has_baseline: bool


class MemberResponse(BaseModel):
    trainer_id: str
    name: str
    fans: int
    club: ClubStatus
    roster_size: int
    periods: dict[str, PeriodStanding]


class SyncClubResult(BaseModel):
    club: str
    ok: bool
    member_count: int | None
    error: str | None


class SyncResponse(BaseModel):
    results: list[SyncClubResult]


def _club_status(club: ClubConfig, info: ClubInfo) -> ClubStatus:
    return ClubStatus(
        key=club.key,
        club_id=club.club_id,
        label=club.label,
        name=info.name,
        member_count=info.member_count,
        last_synced_at=info.last_synced_at,
        last_error=info.last_error,
    )


def create_app(
    *,
    settings: Settings,
    service: TrackerService,
    sync_runner: SyncRunner,
    discord_status: Callable[[], dict],
) -> FastAPI:
    app = FastAPI(
        title="LilyAi Fan Tracker API",
        version=__version__,
        docs_url="/api/v1/docs",
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )
    limiter = RateLimiter(limit=120, window=60)

    def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return error_response(422, "invalid_request", detail or "Invalid request.")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error in an API request", exc_info=exc)
        return error_response(500, "internal_error", "Internal server error.")

    def require(need_write: bool):
        def dependency(authorization: str | None = Header(default=None)) -> str:
            role = authorize(settings.api_read_key, settings.api_write_key, authorization, need_write=need_write)
            wait = limiter.check(role)
            if wait:
                raise ApiError(
                    429, "rate_limited", "Too many requests.", {"Retry-After": str(math.ceil(wait))}
                )
            return role

        return dependency

    read_access = Depends(require(need_write=False))
    write_access = Depends(require(need_write=True))

    def club_or_404(key: str) -> ClubConfig:
        club = service.club(key)
        if club is None:
            raise ApiError(404, "club_not_found", f"Unknown club {key!r}. Use club1, club2 or a configured club id.")
        return club

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        """Cheap liveness check: no database or network access, so it never flaps."""
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/status", response_model=StatusResponse, tags=["status"], dependencies=[read_access])
    def status() -> StatusResponse:
        return StatusResponse(
            server="ok",
            version=__version__,
            time=utc_now(),
            discord=DiscordStatus(**discord_status()),
            clubs=[_club_status(club, info) for club, info in service.club_infos()],
        )

    @app.get(
        "/api/v1/clubs/{club}/leaderboard",
        response_model=LeaderboardResponse,
        tags=["fans"],
        dependencies=[read_access],
    )
    def leaderboard(
        club: str,
        period: Period = "daily",
        top: int = Query(MAX_TOP, ge=1, le=MAX_TOP),
    ) -> LeaderboardResponse:
        config = club_or_404(club)
        board = service.leaderboard(config, period, top)
        return LeaderboardResponse(
            club=_club_status(config, board.info),
            period=period,
            top=board.top,
            resets_at=board.resets_at,
            entries=[LeaderboardEntry(**vars(row)) for row in board.rows],
        )

    @app.get(
        "/api/v1/members/{trainer_id}",
        response_model=MemberResponse,
        tags=["fans"],
        dependencies=[read_access],
    )
    def member(trainer_id: str) -> MemberResponse:
        standings = {p: service.standing_for_trainer(trainer_id, p) for p in PERIODS}
        first = next((s for s in standings.values() if s is not None), None)
        if first is None:
            raise ApiError(404, "member_not_found", "That trainer is not on a club roster right now.")
        return MemberResponse(
            trainer_id=first.row.trainer_id,
            name=first.row.name,
            fans=first.row.fans,
            club=_club_status(first.club, first.info),
            roster_size=first.roster_size,
            periods={
                p: PeriodStanding(gain=s.row.gain, rank=s.row.rank, has_baseline=s.row.has_baseline)
                for p, s in standings.items()
                if s is not None
            },
        )

    @app.post("/api/v1/sync", response_model=SyncResponse, tags=["sync"], dependencies=[write_access])
    async def sync() -> SyncResponse:
        """Refresh both clubs now instead of waiting for the next 5-minute cycle."""
        try:
            outcomes = await sync_runner.sync_now()
        except SyncTooSoon as exc:
            raise ApiError(
                429,
                "rate_limited",
                "A sync ran moments ago; try again shortly.",
                {"Retry-After": str(math.ceil(exc.retry_after))},
            ) from None
        return SyncResponse(
            results=[
                SyncClubResult(
                    club=o.club_key,
                    ok=o.ok,
                    member_count=o.result.member_count if o.result else None,
                    error=o.error,
                )
                for o in outcomes
            ]
        )

    return app
