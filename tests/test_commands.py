"""Slash command replies and table formatting (mocked data source, no Discord)."""

from __future__ import annotations

from fantracker import commands as cmd
from fantracker.formatting import display_width, fit, leaderboard_table
from tests.conftest import CLUB1


def seeded(world):
    world.at("2026-11-04 00:05").roster(
        {"111": ("Alice", 100), "222": ("Bob", 100), "333": ("Cara", 100)}
    ).sync()
    world.at("2026-11-04 12:00").roster(
        {"111": ("Alice", 130), "222": ("Bob", 190), "333": ("Cara", 110)}
    ).sync()
    return world


def test_link_success_then_relink_then_unlink(world):
    seeded(world)
    first = cmd.cmd_link(world.service, "42", "111")
    assert "Linked you to **Alice**" in first.content and "Club 1" in first.content and first.ephemeral
    assert "Updated your link" in cmd.cmd_link(world.service, "42", "222").content
    assert "unlinked" in cmd.cmd_unlink(world.service, "42").content
    assert "don't have" in cmd.cmd_unlink(world.service, "42").content


def test_link_accepts_ids_typed_with_spaces_or_dashes(world):
    seeded(world)
    assert "Linked you" in cmd.cmd_link(world.service, "42", "1 1-1").content


def test_link_rejects_bad_and_unknown_ids(world):
    seeded(world)
    assert "doesn't look like a trainer ID" in cmd.cmd_link(world.service, "42", "not valid!!").content
    assert "couldn't find" in cmd.cmd_link(world.service, "42", "999").content
    assert world.service.linked_trainer("42") is None


def test_link_before_any_sync_says_data_is_not_loaded(world):
    assert "hasn't loaded" in cmd.cmd_link(world.service, "42", "111").content


def test_leaderboard_single_club(world):
    seeded(world)
    reply = cmd.cmd_leaderboard(world.service, "club1", "daily", 10)
    assert len(reply.embeds) == 1
    embed = reply.embeds[0]
    assert embed.title == "Club 1 · Daily top 10"
    names = [line.split()[1] for line in embed.description.splitlines() if line[:2].strip().isdigit()]
    assert names == ["Bob", "Alice", "Cara"]
    assert "Resets" in embed.description and "00:00 UTC" in embed.description


def test_leaderboard_both_clubs_gives_one_embed_each(world):
    seeded(world)
    reply = cmd.cmd_leaderboard(world.service, "both", "weekly", 15)
    assert [e.title for e in reply.embeds] == ["Club 1 · Weekly top 15", "Club 2 · Weekly top 15"]


def test_leaderboard_rejects_unknown_input(world):
    assert cmd.cmd_leaderboard(world.service, "club9", "daily", 10).ephemeral
    assert cmd.cmd_leaderboard(world.service, "club1", "hourly", 10).ephemeral


def test_leaderboard_never_exceeds_thirty_rows(world):
    members = {str(i): (f"P{i}", 100) for i in range(40)}
    world.at("2026-11-04 00:05").roster(members).sync()
    reply = cmd.cmd_leaderboard(world.service, "club1", "daily", 999)
    rows = [ln for ln in reply.embeds[0].description.splitlines()[2:] if ln[:2].strip().isdigit()]
    assert len(rows) == 30
    assert len(reply.embeds[0].description) < 4096  # Discord's embed description limit


def test_fan_gain_for_yourself_and_for_someone_else(world):
    seeded(world)
    cmd.cmd_link(world.service, "42", "222")
    mine = cmd.cmd_fan_gain(world.service, "42", "daily")
    assert "**Gain:** +90" in mine.embeds[0].description
    assert "#1 of 3 in Club 1" in mine.embeds[0].description
    other = cmd.cmd_fan_gain(world.service, "7", "daily", target_discord_id="42")
    assert "Bob" in other.embeds[0].title


def test_fan_gain_requires_a_link(world):
    seeded(world)
    assert "haven't linked" in cmd.cmd_fan_gain(world.service, "42", "daily").content
    assert "hasn't linked" in cmd.cmd_fan_gain(world.service, "42", "daily", target_discord_id="7").content


def test_fan_gain_explains_join_day_zero(world):
    seeded(world)
    world.at("2026-11-04 15:00").roster(
        {"111": ("Alice", 130), "222": ("Bob", 190), "333": ("Cara", 110), "444": ("Newbie", 5000)}
    ).sync()
    cmd.cmd_link(world.service, "42", "444")
    text = cmd.cmd_fan_gain(world.service, "42", "daily").embeds[0].description
    assert "+0" in text and "Joined during this period" in text


def test_fan_gain_after_leaving_the_club(world):
    seeded(world)
    cmd.cmd_link(world.service, "42", "333")
    world.at("2026-11-04 15:00").roster({"111": ("Alice", 130), "222": ("Bob", 190)}).sync()
    assert "isn't on either club roster" in cmd.cmd_fan_gain(world.service, "42", "daily").content


def test_table_truncates_long_and_wide_names_and_strips_backticks():
    from fantracker.models import StandingRow

    rows = [
        StandingRow(1, "a", "A very long trainer name indeed", 10, 5, True),
        StandingRow(2, "b", "ウマ娘ウマ娘ウマ娘ウマ娘", 10, 4, True),
        StandingRow(3, "c", "evil```name", 10, 3, True),
    ]
    table = leaderboard_table(rows)
    body = table.strip("`\n").splitlines()
    assert table.count("```") == 2  # a name cannot close the code block early
    assert all(display_width(line) <= display_width(body[0]) + 0 for line in body)
    assert "…" in body[1] and "…" in body[2]


def test_fit_respects_display_width():
    assert display_width("ウマ") == 4
    assert display_width(fit("ウマ娘ウマ娘ウマ娘", 8)) <= 8
    assert fit("short", 14) == "short"


def test_empty_table_message():
    assert leaderboard_table([]) == "No members to show yet."


def test_club_label_comes_from_config():
    assert CLUB1.label == "Club 1"
