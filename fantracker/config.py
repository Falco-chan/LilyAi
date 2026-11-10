"""Settings, read once from environment variables.

No secret has a default. Club IDs are not secret, so the two clubs this
project was built for are the defaults.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_CLUB1_ID = "974470619"
DEFAULT_CLUB2_ID = "325938032"


class ConfigError(RuntimeError):
    """The environment cannot produce a usable configuration."""


@dataclass(frozen=True)
class ClubConfig:
    key: str  # stable name used by slash commands and the API: "club1" / "club2"
    club_id: str  # uma.moe circle id
    label: str  # display name until the data source supplies the real one


@dataclass(frozen=True)
class Settings:
    clubs: tuple[ClubConfig, ...]
    discord_token: str | None
    discord_guild_id: int | None
    turso_url: str | None
    turso_token: str | None
    sqlite_path: str
    umamoe_api_key: str | None
    sync_interval_seconds: int
    self_ping_url: str | None
    self_ping_interval_seconds: int
    api_read_key: str | None
    api_write_key: str | None
    cors_origins: tuple[str, ...]
    port: int
    on_render: bool


def _opt(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    value = value.strip() if value else ""
    return value or None


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int) -> int:
    raw = _opt(env, name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    clubs = (
        ClubConfig("club1", _opt(env, "CLUB1_ID") or DEFAULT_CLUB1_ID, _opt(env, "CLUB1_LABEL") or "Club 1"),
        ClubConfig("club2", _opt(env, "CLUB2_ID") or DEFAULT_CLUB2_ID, _opt(env, "CLUB2_LABEL") or "Club 2"),
    )
    if clubs[0].club_id == clubs[1].club_id:
        raise ConfigError("CLUB1_ID and CLUB2_ID must be different")

    guild_raw = _opt(env, "DISCORD_GUILD_ID")
    if guild_raw is not None and not guild_raw.isdigit():
        raise ConfigError("DISCORD_GUILD_ID must be a numeric Discord server id")

    ping_url = _opt(env, "SELF_PING_URL")
    if ping_url is None:
        external = _opt(env, "RENDER_EXTERNAL_URL")  # set automatically by Render
        if external:
            ping_url = external.rstrip("/") + "/health"

    cors = _opt(env, "CORS_ORIGINS") or ""

    return Settings(
        clubs=clubs,
        discord_token=_opt(env, "DISCORD_TOKEN"),
        discord_guild_id=int(guild_raw) if guild_raw else None,
        turso_url=_opt(env, "TURSO_DATABASE_URL"),
        turso_token=_opt(env, "TURSO_AUTH_TOKEN"),
        sqlite_path=_opt(env, "SQLITE_PATH") or "fantracker.db",
        umamoe_api_key=_opt(env, "UMAMOE_API_KEY"),
        # 60s floor protects uma.moe from an accidental tight loop.
        sync_interval_seconds=_int(env, "SYNC_INTERVAL_SECONDS", 300, minimum=60),
        self_ping_url=ping_url,
        self_ping_interval_seconds=_int(env, "SELF_PING_INTERVAL_SECONDS", 300, minimum=30),
        api_read_key=_opt(env, "API_READ_KEY"),
        api_write_key=_opt(env, "API_WRITE_KEY"),
        cors_origins=tuple(o.strip() for o in cors.split(",") if o.strip()),
        port=_int(env, "PORT", 8000, minimum=1),
        on_render=_opt(env, "RENDER") is not None,
    )


def validate_for_runtime(settings: Settings) -> list[str]:
    """Raise ConfigError for fatal problems; return warnings for the rest."""
    if bool(settings.turso_url) != bool(settings.turso_token):
        raise ConfigError("Set both TURSO_DATABASE_URL and TURSO_AUTH_TOKEN, or neither")
    if settings.on_render and not settings.turso_url:
        raise ConfigError(
            "Render's filesystem is wiped on every restart, so a local SQLite file would "
            "lose all data. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN."
        )

    warnings: list[str] = []
    if not settings.discord_token:
        warnings.append("DISCORD_TOKEN is not set: the Discord bot will not start.")
    if not (settings.api_read_key or settings.api_write_key):
        warnings.append("API_READ_KEY / API_WRITE_KEY are not set: private API endpoints will answer 503.")
    if not settings.umamoe_api_key:
        warnings.append("UMAMOE_API_KEY is not set.")
    return warnings
