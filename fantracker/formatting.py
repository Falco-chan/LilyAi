"""Text rendering for Discord. Pure functions, no Discord imports."""

from __future__ import annotations

import unicodedata
from datetime import datetime

from fantracker.models import StandingRow

NAME_WIDTH = 14


def format_int(value: int) -> str:
    return f"{value:,}"


def discord_ts(moment: datetime, style: str = "R") -> str:
    """Discord renders this in each viewer's own language and timezone."""
    return f"<t:{int(moment.timestamp())}:{style}>"


def _char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    return sum(_char_width(ch) for ch in text)


def clean_name(text: str) -> str:
    """Names go inside a code block: remove anything that could end it."""
    return " ".join(text.replace("`", "'").split())


def fit(text: str, width: int) -> str:
    """Truncate to `width` display columns, ending with an ellipsis."""
    if display_width(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = _char_width(ch)
        if used + w > width - 1:
            break
        out, used = out + ch, used + w
    return out + "…"


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def leaderboard_table(rows: list[StandingRow], value_header: str = "Gain") -> str:
    if not rows:
        return "No members to show yet."
    values = [format_int(r.gain) for r in rows]
    rank_w = max(2, len(str(len(rows))))
    value_w = max(len(value_header), *(len(v) for v in values))
    lines = [f"{'#':>{rank_w}}  {pad('Name', NAME_WIDTH)}  {value_header:>{value_w}}"]
    for row, value in zip(rows, values, strict=True):
        name = pad(fit(clean_name(row.name), NAME_WIDTH), NAME_WIDTH)
        lines.append(f"{row.rank:>{rank_w}}  {name}  {value:>{value_w}}")
    return "```\n" + "\n".join(lines) + "\n```"


def freshness_line(last_synced_at: datetime | None, resets_at: datetime) -> str:
    updated = f"Updated {discord_ts(last_synced_at)}" if last_synced_at else "Data not loaded yet"
    return f"{updated} · Resets {discord_ts(resets_at)} (00:00 UTC)"
