"""The fan-gain rules the project asked for, one scenario per rule.

Calendar used: 2026-11-02 is a Monday, 2026-11-04 a Wednesday,
2026-11-08 a Sunday, 2026-11-09 the next Monday.
"""

from __future__ import annotations

from fantracker.sources.base import SourceError
from tests.conftest import CLUB1, CLUB2


def test_gain_accumulates_across_periods(world):
    world.at("2026-11-04 00:05").roster({"A": ("Alice", 100)}).sync()
    world.at("2026-11-04 12:00").roster({"A": ("Alice", 150)}).sync()
    assert [world.gain("A", p) for p in ("daily", "weekly", "monthly")] == [(50, True)] * 3

    world.at("2026-11-05 00:05").roster({"A": ("Alice", 150)}).sync()
    world.at("2026-11-05 12:00").roster({"A": ("Alice", 200)}).sync()
    assert world.gain("A", "daily") == (50, True)
    assert world.gain("A", "weekly") == (100, True)
    assert world.gain("A", "monthly") == (100, True)


def test_weekly_resets_on_monday_utc(world):
    world.at("2026-11-04 00:05").roster({"A": ("Alice", 100)}).sync()
    world.at("2026-11-08 00:05").roster({"A": ("Alice", 300)}).sync()  # Sunday
    world.at("2026-11-08 23:55").roster({"A": ("Alice", 500)}).sync()
    assert world.gain("A", "weekly") == (400, True)  # still the week that began Monday 11-02
    assert world.gain("A", "daily") == (200, True)

    world.at("2026-11-09 00:05").roster({"A": ("Alice", 500)}).sync()  # Monday
    assert world.gain("A", "weekly") == (0, True)
    assert world.gain("A", "daily") == (0, True)

    world.at("2026-11-09 12:00").roster({"A": ("Alice", 550)}).sync()
    assert world.gain("A", "weekly") == (50, True)
    assert world.gain("A", "monthly") == (450, True)  # the month did not reset


def test_monthly_resets_on_the_first(world):
    world.at("2026-10-31 00:05").roster({"A": ("Alice", 1000)}).sync()
    world.at("2026-10-31 23:50").roster({"A": ("Alice", 1400)}).sync()
    assert world.gain("A", "monthly") == (400, True)

    world.at("2026-11-01 00:05").roster({"A": ("Alice", 1400)}).sync()
    world.at("2026-11-01 12:00").roster({"A": ("Alice", 1500)}).sync()
    assert world.gain("A", "monthly") == (100, True)  # October's gain is gone


def test_total_below_baseline_means_the_tally_was_reset(world):
    world.at("2026-11-04 00:05").roster({"A": ("Alice", 5000)}).sync()
    world.at("2026-11-04 12:00").roster({"A": ("Alice", 300)}).sync()
    assert world.gain("A", "daily") == (300, True)


def test_join_day_is_zero_then_counts_from_the_next_day(world):
    world.at("2026-11-04 00:05").roster({"A": ("Alice", 100)}).sync()
    world.at("2026-11-04 12:00").roster({"A": ("Alice", 150), "B": ("Bob", 1000)}).sync()
    world.at("2026-11-04 18:00").roster({"A": ("Alice", 160), "B": ("Bob", 1100)}).sync()
    for period in ("daily", "weekly", "monthly"):
        assert world.gain("B", period) == (0, False)  # join day: zero, however many fans were earned

    world.at("2026-11-05 00:05").roster({"A": ("Alice", 160), "B": ("Bob", 1100)}).sync()
    world.at("2026-11-05 12:00").roster({"A": ("Alice", 170), "B": ("Bob", 1150)}).sync()
    for period in ("daily", "weekly", "monthly"):
        assert world.gain("B", period) == (50, True)


def test_member_who_leaves_is_removed_but_keeps_their_link(world):
    world.at("2026-11-04 00:05").roster({"A": ("Alice", 100), "B": ("Bob", 200)}).sync()
    assert world.service.link("42", "B").status == "linked"

    world.at("2026-11-04 12:00").roster({"A": ("Alice", 110)}).sync()
    assert world.on_roster() == ["A"]
    assert world.store.find_active_member("B") is None
    assert world.service.linked_trainer("42") == "B"


def test_rejoin_in_the_same_month_restores_the_original_baseline(world):
    both = {"A": ("Alice", 100), "B": ("Bob", 1000)}
    world.at("2026-11-04 00:05").roster(both).sync()
    world.at("2026-11-05 12:00").roster({"A": ("Alice", 120)}).sync()  # B leaves
    assert world.on_roster() == ["A"]

    world.at("2026-11-07 00:05").roster({"A": ("Alice", 125)}).sync()  # Saturday's first sync: B still away
    world.at("2026-11-07 12:00").roster({"A": ("Alice", 130), "B": ("Bob", 1400)})
    world.sync()
    assert world.gain("B", "daily") == (0, False)  # rejoin day counts as zero
    assert world.gain("B", "weekly") == (400, True)  # as if they never left
    assert world.gain("B", "monthly") == (400, True)


def test_rejoin_in_a_later_month_is_treated_as_a_new_player(world):
    world.at("2026-10-28 00:05").roster({"A": ("Alice", 100), "B": ("Bob", 500)}).sync()
    world.at("2026-10-29 12:00").roster({"A": ("Alice", 110)}).sync()  # B leaves in October

    world.at("2026-11-02 00:05").roster({"A": ("Alice", 115)}).sync()  # the day's first sync: B not back yet
    world.at("2026-11-02 12:00").roster({"A": ("Alice", 120), "B": ("Bob", 900)}).sync()
    assert world.gain("B", "monthly") == (0, False)  # October history does not count

    world.at("2026-11-03 00:05").roster({"A": ("Alice", 120), "B": ("Bob", 900)}).sync()
    world.at("2026-11-03 12:00").roster({"A": ("Alice", 125), "B": ("Bob", 950)}).sync()
    assert world.gain("B", "monthly") == (50, True)


def test_snapshot_is_taken_only_on_the_first_sync_of_the_day(world):
    world.at("2026-11-04 00:05").roster({"A": ("Alice", 100)})
    first = world.sync()
    world.at("2026-11-04 12:00").roster({"A": ("Alice", 150)})
    second = world.sync()
    assert first[0].result.took_day_snapshot is True
    assert second[0].result.took_day_snapshot is False
    assert world.db.execute("SELECT fans FROM day_snapshots WHERE club_id = ?", (CLUB1.club_id,)) == [(100,)]


def test_empty_roster_is_refused_and_nobody_is_marked_as_left(world):
    world.at("2026-11-04 00:05").roster({"A": ("Alice", 100)}).sync()
    world.at("2026-11-04 12:00").roster({})
    outcomes = world.sync()
    assert outcomes[0].ok is False
    assert world.on_roster() == ["A"]
    assert "empty roster" in world.store.club_info(CLUB1.club_id).last_error


def test_one_club_failing_does_not_stop_the_other(world):
    world.roster({"A": ("Alice", 100)})
    world.source.errors[CLUB1.club_id] = SourceError("boom")
    outcomes = world.sync()
    assert [(o.club_key, o.ok) for o in outcomes] == [("club1", False), ("club2", True)]
    assert world.store.club_info(CLUB1.club_id).last_error == "boom"
    assert world.store.club_info(CLUB2.club_id).member_count == 1


def test_unexpected_error_is_reported_without_detail(world):
    world.roster({"A": ("Alice", 100)})
    world.source.errors[CLUB1.club_id] = RuntimeError("secret-looking detail")
    outcome = world.sync()[0]
    assert outcome.ok is False
    assert "secret-looking" not in outcome.error
    assert "RuntimeError" in outcome.error


def test_successful_sync_clears_the_recorded_error(world):
    world.roster({"A": ("Alice", 100)})
    world.source.errors[CLUB1.club_id] = SourceError("boom")
    world.sync()
    del world.source.errors[CLUB1.club_id]
    world.sync()
    assert world.store.club_info(CLUB1.club_id).last_error is None


def test_ranking_order_and_top_limit(world):
    world.at("2026-11-04 00:05").roster(
        {"A": ("Alice", 100), "B": ("Bob", 100), "C": ("Cara", 100), "D": ("Dan", 100)}
    ).sync()
    world.at("2026-11-04 12:00").roster(
        {"A": ("Alice", 130), "B": ("Bob", 190), "C": ("Cara", 130), "D": ("Dan", 110)}
    ).sync()
    board = world.service.leaderboard(CLUB1, "daily", 3)
    assert [(r.rank, r.trainer_id, r.gain) for r in board.rows] == [(1, "B", 90), (2, "A", 30), (3, "C", 30)]
    assert world.service.leaderboard(CLUB1, "daily", 999).top == 30  # capped at a full club


def test_sync_reports_who_joined_and_left(world):
    world.roster({"A": ("Alice", 100), "B": ("Bob", 100)})
    first = world.sync()[0].result
    assert sorted(first.joined) == ["A", "B"]
    world.at("2026-11-04 12:00").roster({"B": ("Bob", 110), "C": ("Cara", 5)})
    second = world.sync()[0].result
    assert second.joined == ["C"] and second.left == ["A"]


def test_member_moving_between_clubs(world):
    world.roster({"T": ("Tina", 100), "A": ("Alice", 100)}).sync()
    assert world.store.find_active_member("T")[0] == CLUB1.club_id
    world.at("2026-11-04 12:00").roster({"A": ("Alice", 110)})
    world.roster({"T": ("Tina", 120)}, club=CLUB2).sync()
    assert world.store.find_active_member("T")[0] == CLUB2.club_id
    assert world.on_roster(CLUB1) == ["A"]


def test_standing_includes_rank_and_roster_size(world):
    world.at("2026-11-04 00:05").roster({"A": ("Alice", 100), "B": ("Bob", 100)}).sync()
    world.at("2026-11-04 12:00").roster({"A": ("Alice", 110), "B": ("Bob", 150)}).sync()
    standing = world.service.standing_for_trainer("A", "daily")
    assert (standing.row.rank, standing.roster_size, standing.club.key) == (2, 2, "club1")
    assert world.service.standing_for_trainer("nobody", "daily") is None
