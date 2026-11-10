"""What each slash command says. No Discord imports, so it is testable alone.

The Discord layer turns a `Reply` into real messages and embeds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fantracker.formatting import clean_name, format_int, freshness_line, leaderboard_table
from fantracker.services.tracker import MAX_TOP, TrackerService
from fantracker.timeutil import PERIODS

PERIOD_LABELS = {"daily": "Daily", "weekly": "Weekly", "monthly": "Monthly"}
TOP_CHOICES = (10, 15, 30)


@dataclass(frozen=True)
class EmbedData:
    title: str
    description: str


@dataclass(frozen=True)
class Reply:
    content: str | None = None
    embeds: list[EmbedData] = field(default_factory=list)
    ephemeral: bool = False


def _error(text: str) -> Reply:
    return Reply(content=text, ephemeral=True)


def cmd_link(service: TrackerService, discord_id: str, trainer_id: str) -> Reply:
    result = service.link(discord_id, trainer_id)
    if result.status == "invalid":
        return _error(
            "That doesn't look like a trainer ID. Use letters and digits only, for example `/link 123456789012`."
        )
    if result.status == "not_synced":
        return _error("Club data hasn't loaded yet. Try again in a few minutes.")
    if result.status == "not_found":
        return _error(
            "I couldn't find that trainer ID in either club. Check the number, "
            "or wait for the next update (every 5 minutes) if you just joined."
        )
    where = f" in {result.club_label}" if result.club_label else ""
    verb = "Updated your link to" if result.status == "relinked" else "Linked you to"
    return Reply(content=f"{verb} **{clean_name(result.trainer_name or '')}**{where}.", ephemeral=True)


def cmd_unlink(service: TrackerService, discord_id: str) -> Reply:
    if service.unlink(discord_id):
        return Reply(content="Your trainer ID has been unlinked.", ephemeral=True)
    return _error("You don't have a trainer ID linked.")


def cmd_leaderboard(service: TrackerService, club_choice: str, period: str, top: int | None) -> Reply:
    if period not in PERIODS:
        return _error("Pick daily, weekly or monthly.")
    top = min(top or TOP_CHOICES[0], MAX_TOP)
    if club_choice == "both":
        clubs = list(service.clubs)
    else:
        club = service.club(club_choice)
        if club is None:
            return _error("Unknown club.")
        clubs = [club]

    embeds = []
    for club in clubs:
        board = service.leaderboard(club, period, top)
        footer = freshness_line(board.info.last_synced_at, board.resets_at)
        embeds.append(
            EmbedData(
                title=f"{club.label} · {PERIOD_LABELS[period]} top {board.top}",
                description=leaderboard_table(board.rows) + "\n" + footer,
            )
        )
    return Reply(embeds=embeds)


def cmd_fan_gain(
    service: TrackerService, discord_id: str, period: str, target_discord_id: str | None = None
) -> Reply:
    if period not in PERIODS:
        return _error("Pick daily, weekly or monthly.")
    looking_up_other = target_discord_id is not None and target_discord_id != discord_id
    trainer_id = service.linked_trainer(target_discord_id or discord_id)
    if trainer_id is None:
        if looking_up_other:
            return _error("That user hasn't linked a trainer ID.")
        return _error("You haven't linked a trainer ID yet. Use `/link` first.")
    standing = service.standing_for_trainer(trainer_id, period)
    if standing is None:
        return _error(
            "That trainer isn't on either club roster right now. If they just joined, "
            "wait for the next update (every 5 minutes)."
        )

    row = standing.row
    lines = [
        f"**Gain:** +{format_int(row.gain)}",
        f"**Rank:** #{row.rank} of {standing.roster_size} in {standing.club.label}",
        f"**Fans:** {format_int(row.fans)}",
    ]
    if not row.has_baseline:
        lines.append("_Joined during this period, so gains start counting from the next UTC day._")
    lines.append(freshness_line(standing.info.last_synced_at, standing.resets_at))
    embed = EmbedData(
        title=f"{clean_name(row.name)} · {PERIOD_LABELS[period]} fan gain",
        description="\n".join(lines),
    )
    return Reply(embeds=[embed])
