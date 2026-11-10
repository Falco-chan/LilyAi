"""Discord front end, built on discord.py.

Deliberately no `from __future__ import annotations` here: discord.py reads
the command parameter annotations at runtime.

The wording of every reply lives in commands.py. This module only connects
Discord interactions to it. Needs no privileged intents.
"""

import asyncio
import logging
import math
import time
from collections.abc import Callable

import discord
from discord import app_commands

from fantracker import commands as cmd
from fantracker.config import Settings
from fantracker.services.tracker import TrackerService
from fantracker.timeutil import PERIODS

logger = logging.getLogger(__name__)

FATAL = (discord.LoginFailure, discord.PrivilegedIntentsRequired)


def _embeds(reply: cmd.Reply) -> list:
    colour = discord.Colour.blurple()
    return [discord.Embed(title=e.title, description=e.description, colour=colour) for e in reply.embeds]


async def _run(interaction: discord.Interaction, handler, *args, ephemeral: bool) -> None:
    """Acknowledge within Discord's 3 seconds, do the work off the event loop, then answer."""
    await interaction.response.defer(ephemeral=ephemeral)
    reply = await asyncio.to_thread(handler, *args)
    await interaction.followup.send(
        content=reply.content,
        embeds=_embeds(reply),
        ephemeral=ephemeral,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def register_commands(tree: app_commands.CommandTree, service: TrackerService) -> None:
    club_choices = [app_commands.Choice(name=c.label, value=c.key) for c in service.clubs]
    club_choices.append(app_commands.Choice(name="Both clubs", value="both"))
    period_choices = [app_commands.Choice(name=cmd.PERIOD_LABELS[p], value=p) for p in PERIODS]
    top_choices = [app_commands.Choice(name=f"Top {n}", value=n) for n in cmd.TOP_CHOICES]

    @tree.command(name="link", description="Link your Discord account to your trainer ID")
    @app_commands.describe(trainer_id="Your in-game trainer ID")
    async def link(interaction: discord.Interaction, trainer_id: str):
        await _run(interaction, cmd.cmd_link, service, str(interaction.user.id), trainer_id, ephemeral=True)

    @tree.command(name="unlink", description="Remove the trainer ID linked to your Discord account")
    async def unlink(interaction: discord.Interaction):
        await _run(interaction, cmd.cmd_unlink, service, str(interaction.user.id), ephemeral=True)

    @tree.command(name="leaderboard", description="Top fan gains in a club")
    @app_commands.describe(club="Which club", period="Daily, weekly or monthly", top="How many members to show")
    @app_commands.choices(club=club_choices, period=period_choices, top=top_choices)
    async def leaderboard(
        interaction: discord.Interaction,
        club: app_commands.Choice[str],
        period: app_commands.Choice[str],
        top: app_commands.Choice[int] | None = None,
    ):
        await _run(
            interaction,
            cmd.cmd_leaderboard,
            service,
            club.value,
            period.value,
            top.value if top else None,
            ephemeral=False,
        )

    @tree.command(name="fan_gain", description="Your fan gain for a period")
    @app_commands.describe(period="Daily, weekly or monthly", user="Look up someone else (optional)")
    @app_commands.choices(period=period_choices)
    async def fan_gain(
        interaction: discord.Interaction,
        period: app_commands.Choice[str],
        user: discord.User | None = None,
    ):
        await _run(
            interaction,
            cmd.cmd_fan_gain,
            service,
            str(interaction.user.id),
            period.value,
            str(user.id) if user else None,
            ephemeral=False,
        )

    @tree.error
    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error("Slash command failed", exc_info=error)
        message = "Something went wrong. Please try again in a moment."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning("Could not tell the user about a failed command")


class FanBot(discord.Client):
    def __init__(self, service: TrackerService, settings: Settings, on_state: Callable[[str], None]) -> None:
        super().__init__(intents=discord.Intents.default(), allowed_mentions=discord.AllowedMentions.none())
        self._settings = settings
        self._on_state = on_state
        self.tree = app_commands.CommandTree(self)
        register_commands(self.tree, service)

    async def setup_hook(self) -> None:
        try:
            if self._settings.discord_guild_id:
                guild = discord.Object(id=self._settings.discord_guild_id)
                self.tree.copy_global_to(guild=guild)  # guild commands appear instantly
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
            logger.info("Slash commands registered")
        except discord.HTTPException:
            logger.exception("Could not register slash commands; the bot is online but has no commands")

    async def on_ready(self) -> None:
        logger.info("Discord connected as %s", self.user)
        self._on_state("ready")

    async def on_resumed(self) -> None:
        self._on_state("ready")

    async def on_disconnect(self) -> None:
        self._on_state("reconnecting")


class DiscordRunner:
    """Keeps the bot connected. discord.py reconnects on network loss by itself;
    this adds a restart-with-backoff for crashes, and stops for good on a bad token."""

    def __init__(self, token: str, service: TrackerService, settings: Settings) -> None:
        self._token = token
        self._service = service
        self._settings = settings
        self._bot: FanBot | None = None
        self._stopping = False
        self.state = "starting"
        self.error: str | None = None

    def _set_state(self, state: str) -> None:
        self.state = state
        if state == "ready":
            self.error = None

    def status(self) -> dict:
        latency = None
        if self._bot is not None and self.state == "ready" and math.isfinite(self._bot.latency):
            latency = round(self._bot.latency * 1000)
        return {"state": self.state, "latency_ms": latency, "error": self.error}

    async def run(self) -> None:
        backoff = 5
        while not self._stopping:
            bot = FanBot(self._service, self._settings, self._set_state)
            self._bot = bot
            self.state = "starting"
            started = time.monotonic()
            try:
                await bot.start(self._token, reconnect=True)
            except FATAL as exc:
                self.state = "error"
                if isinstance(exc, discord.LoginFailure):
                    self.error = "Discord rejected the login: check DISCORD_TOKEN"
                else:
                    self.error = str(exc)
                logger.error("Discord bot stopped: %s", self.error)
                return
            except asyncio.CancelledError:
                await bot.close()
                raise
            except Exception:
                logger.exception("Discord client crashed; restarting in %ss", backoff)
                self.state = "reconnecting"
                self.error = "client crashed; restarting"
            finally:
                if not bot.is_closed():
                    await bot.close()
            if self._stopping:
                return
            if time.monotonic() - started > 300:
                backoff = 5  # it ran fine for a while, so start the backoff over
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    async def close(self) -> None:
        self._stopping = True
        if self._bot is not None:
            await self._bot.close()
