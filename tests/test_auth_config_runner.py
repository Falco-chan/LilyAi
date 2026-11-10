"""Auth rules, settings parsing and the background loops (all mocked)."""

from __future__ import annotations

import asyncio

import pytest

from fantracker.auth import ApiError, RateLimiter, authorize
from fantracker.config import ConfigError, load_settings, validate_for_runtime
from fantracker.runner import SyncRunner, SyncTooSoon, run_self_ping

# ---- auth -------------------------------------------------------------


def test_read_key_can_read_but_not_write():
    assert authorize("r", "w", "Bearer r", need_write=False) == "read"
    with pytest.raises(ApiError) as info:
        authorize("r", "w", "Bearer r", need_write=True)
    assert info.value.status == 403


def test_write_key_can_do_both():
    assert authorize("r", "w", "Bearer w", need_write=False) == "write"
    assert authorize("r", "w", "Bearer w", need_write=True) == "write"


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer wrong", "Basic r", "r"])
def test_bad_credentials_are_401(header):
    with pytest.raises(ApiError) as info:
        authorize("r", "w", header, need_write=False)
    assert info.value.status == 401 and info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_no_configured_keys_fails_closed():
    with pytest.raises(ApiError) as info:
        authorize(None, None, "Bearer anything", need_write=False)
    assert info.value.status == 503


def test_only_a_write_key_configured():
    assert authorize(None, "w", "Bearer w", need_write=False) == "write"
    with pytest.raises(ApiError):
        authorize(None, "w", "Bearer ", need_write=False)


def test_rate_limiter_window():
    now = [0.0]
    limiter = RateLimiter(limit=2, window=10, clock=lambda: now[0])
    assert limiter.check("k") == 0 and limiter.check("k") == 0
    assert limiter.check("k") == pytest.approx(10)
    now[0] = 4
    assert limiter.check("k") == pytest.approx(6)
    assert limiter.check("other") == 0  # separate key
    now[0] = 10
    assert limiter.check("k") == 0  # first hit has aged out


# ---- config -----------------------------------------------------------


def test_defaults_use_the_two_clubs():
    s = load_settings({})
    assert [c.club_id for c in s.clubs] == ["974470619", "325938032"]
    assert s.sync_interval_seconds == 300 and s.self_ping_interval_seconds == 300
    assert s.self_ping_url is None and s.discord_token is None


def test_render_external_url_becomes_the_ping_target():
    s = load_settings({"RENDER_EXTERNAL_URL": "https://bot.onrender.com/"})
    assert s.self_ping_url == "https://bot.onrender.com/health"
    explicit = load_settings({"RENDER_EXTERNAL_URL": "https://a", "SELF_PING_URL": "https://b/health"})
    assert explicit.self_ping_url == "https://b/health"


def test_blank_values_count_as_unset():
    s = load_settings({"DISCORD_TOKEN": "   ", "CLUB1_ID": ""})
    assert s.discord_token is None and s.clubs[0].club_id == "974470619"


@pytest.mark.parametrize(
    "env",
    [
        {"SYNC_INTERVAL_SECONDS": "abc"},
        {"SYNC_INTERVAL_SECONDS": "5"},  # below the 60s floor
        {"DISCORD_GUILD_ID": "not-a-number"},
        {"CLUB1_ID": "1", "CLUB2_ID": "1"},
    ],
)
def test_invalid_settings_are_rejected(env):
    with pytest.raises(ConfigError):
        load_settings(env)


def test_render_without_turso_is_a_fatal_error():
    with pytest.raises(ConfigError, match="TURSO_DATABASE_URL"):
        validate_for_runtime(load_settings({"RENDER": "true"}))


def test_half_configured_turso_is_a_fatal_error():
    with pytest.raises(ConfigError):
        validate_for_runtime(load_settings({"TURSO_DATABASE_URL": "libsql://x"}))


def test_missing_optional_settings_only_warn():
    warnings = validate_for_runtime(load_settings({}))
    assert any("DISCORD_TOKEN" in w for w in warnings) and any("API_" in w for w in warnings)


# ---- background loops -------------------------------------------------


def test_sync_runner_syncs_on_start_and_repeats(world):
    world.roster({"A": ("Alice", 100)})

    async def scenario():
        runner = SyncRunner(world.service, interval_seconds=0.05)
        stop = asyncio.Event()
        task = asyncio.create_task(runner.run(stop))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        return runner

    runner = asyncio.run(scenario())
    assert runner.last_outcomes and all(o.ok for o in runner.last_outcomes)
    assert world.store.club_info("974470619").member_count == 1


def test_manual_sync_is_rate_limited(world):
    world.roster({"A": ("Alice", 100)})
    clock = [100.0]

    async def scenario():
        runner = SyncRunner(world.service, 300, min_manual_gap_seconds=30, monotonic=lambda: clock[0])
        await runner.sync_now()
        clock[0] += 10
        with pytest.raises(SyncTooSoon) as info:
            await runner.sync_now()
        assert info.value.retry_after == pytest.approx(20)
        clock[0] += 25
        return await runner.sync_now()

    assert asyncio.run(scenario())


def test_self_ping_requests_the_url_and_survives_failures():
    calls: list[str] = []

    def flaky_fetch(url: str) -> int:
        calls.append(url)
        if len(calls) == 1:
            raise ConnectionError("network down")
        return 200 if len(calls) < 4 else 500

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(run_self_ping("https://x.test/health", 0.02, stop, fetch=flaky_fetch))
        await asyncio.sleep(0.3)
        assert not task.done()  # still alive after an exception and a 500
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert len(calls) >= 4 and set(calls) == {"https://x.test/health"}


def test_self_ping_stops_immediately_when_asked():
    async def scenario():
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(run_self_ping("https://x.test/health", 300, stop, fetch=lambda _: 200), timeout=1)

    asyncio.run(scenario())
