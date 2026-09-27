# Commands/graidlog.py
import asyncio

import discord
from discord.commands import SlashCommandGroup, AutocompleteContext
from discord import Option
from discord.ext import commands

from Helpers.database import DB, get_current_guild_data
from Helpers.graid_stats import RAID_SHORT_TO_FULL
from Helpers.variables import HOME_GUILD_IDS, RAID_LOG_CHANNEL_ID, NOTG_EMOJI, TCC_EMOJI, TNA_EMOJI, NOL_EMOJI, TWP_EMOJI

RAID_EMOJIS = {
    "Nest of the Grootslangs": NOTG_EMOJI,
    "The Canyon Colossus": TCC_EMOJI,
    "The Nameless Anomaly": TNA_EMOJI,
    "Orphion's Nexus of Light": NOL_EMOJI,
    "The Wartorn Palace": TWP_EMOJI,
}


def _db():
    db = DB(); db.connect(); return db


async def _member_autocomplete(ctx: AutocompleteContext):
    """Autocomplete from current guild members."""
    prefix = (ctx.value or "").strip().lower()
    data = await asyncio.to_thread(get_current_guild_data)
    members = data.get('members', []) if isinstance(data, dict) else []
    names = sorted(set(
        m.get('name') or m.get('username') or ''
        for m in members
        if m.get('name') or m.get('username')
    ))
    if prefix:
        names = [n for n in names if n.lower().startswith(prefix)]
    return names[:25]


class GraidCommands(commands.Cog):
    def __init__(self, client):
        self.client = client

    graid = SlashCommandGroup(
        "graid",
        "Manual guild raid logging",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True),
    )

    # --- /graid log (ADMIN only) ---

    @graid.command(name="log", description="ADMIN: Manually log a guild raid")
    async def log_raid(
        self,
        ctx: discord.ApplicationContext,
        raid_type: Option(str, "Raid type", choices=["NOTG", "TCC", "TNA", "NOL", "WTP"], required=True),
        player1: Option(str, "First participant", autocomplete=_member_autocomplete, required=True),
        player2: Option(str, "Second participant", autocomplete=_member_autocomplete, required=True),
        player3: Option(str, "Third participant", autocomplete=_member_autocomplete, required=True),
        player4: Option(str, "Fourth participant", autocomplete=_member_autocomplete, required=True),
    ):
        await ctx.defer(ephemeral=True)

        # Validate all 4 are current guild members
        current_data = await asyncio.to_thread(get_current_guild_data)
        current_members = current_data.get('members', []) if isinstance(current_data, dict) else []
        current_names = {
            (m.get('name') or m.get('username') or '').casefold(): (m.get('name') or m.get('username') or '')
            for m in current_members
            if m.get('name') or m.get('username')
        }
        uuid_by_name = {
            (m.get('name') or m.get('username') or '').casefold(): m.get('uuid')
            for m in current_members
            if m.get('name') or m.get('username')
        }

        if not current_names:
            await ctx.followup.send(':no_entry: Guild member data unavailable. Try again later.', ephemeral=True)
            return

        players = [player1, player2, player3, player4]
        non_members = [p for p in players if p.casefold() not in current_names]
        if non_members:
            listed = ', '.join(f'`{n}`' for n in non_members)
            await ctx.followup.send(f':no_entry: Not current guild members: {listed}', ephemeral=True)
            return

        # Normalize casing to match guild data
        players = [current_names.get(p.casefold(), p) for p in players]

        # Check for duplicates
        if len(set(p.casefold() for p in players)) < 4:
            await ctx.followup.send(':no_entry: All 4 participants must be different players.', ephemeral=True)
            return

        full_raid_name = RAID_SHORT_TO_FULL.get(raid_type)

        # Insert into database
        db = _db()
        try:
            cur = db.cursor

            # Check for active event
            cur.execute("SELECT id FROM graid_events WHERE active = TRUE LIMIT 1")
            row = cur.fetchone()
            event_id = row[0] if row else None

            cur.execute(
                "INSERT INTO graid_logs (event_id, raid_type) VALUES (%s, %s) RETURNING id",
                (event_id, full_raid_name)
            )
            log_id = cur.fetchone()[0]

            # Identity comes from the live guild data the players were just
            # validated against — discord_links.ign can lag a rename, and a
            # missed uuid here silently costs the player event points and payout.
            uuids = {}
            for ign in players:
                uuid_val = uuid_by_name.get(ign.casefold())
                if not uuid_val:
                    cur.execute("SELECT uuid FROM discord_links WHERE LOWER(ign) = LOWER(%s)", (ign,))
                    uuid_row = cur.fetchone()
                    uuid_val = uuid_row[0] if uuid_row else None
                uuids[ign] = uuid_val

            for ign in players:
                cur.execute(
                    "INSERT INTO graid_log_participants (log_id, uuid, ign) VALUES (%s, %s, %s)",
                    (log_id, uuids[ign], ign)
                )

            # Upsert totals if event is active
            if event_id is not None:
                for ign in players:
                    if uuids[ign]:
                        cur.execute("""
                            INSERT INTO graid_event_totals (event_id, uuid, total)
                            VALUES (%s, %s, 1)
                            ON CONFLICT (event_id, uuid) DO UPDATE
                              SET total = graid_event_totals.total + 1,
                                  last_updated = NOW()
                        """, (event_id, uuids[ign]))

            db.connection.commit()
        finally:
            db.close()

        # Post to raid-log channel
        channel = self.client.get_channel(RAID_LOG_CHANNEL_ID)
        if channel:
            bolded = [f"**{discord.utils.escape_markdown(n)}**" for n in players]
            names_str = ", ".join(bolded[:-1]) + ", and " + bolded[-1]
            emoji = RAID_EMOJIS.get(full_raid_name, "")
            embed = discord.Embed(
                title=f"{emoji} {full_raid_name} Completed!",
                description=names_str,
                color=0x00FF00,
            )
            await channel.send(embed=embed)

        unresolved = [p for p in players if not uuids.get(p)]
        warning = (
            f"\n:warning: No uuid resolved for {', '.join(f'`{p}`' for p in unresolved)}. "
            f"Logged without event credit; link the account and re-check."
        ) if unresolved else ""
        await ctx.followup.send(
            f":white_check_mark: Logged **{raid_type}** raid with {', '.join(discord.utils.escape_markdown(p) for p in players)}{warning}",
            ephemeral=True,
        )


def setup(client):
    client.add_cog(GraidCommands(client))
