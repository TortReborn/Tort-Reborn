import asyncio

import discord
from discord.ext import commands

from Helpers.database import DB
from Helpers.variables import error_channel


class OnMemberUpdate(commands.Cog):
    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Only care about role changes
        if before.roles == after.roles:
            return

        added_roles = set(after.roles) - set(before.roles)
        if not added_roles:
            return

        added_names = {r.name for r in added_roles}

        promo = None
        if "Piranha" in added_names:
            promo = "piranhaPromo"
        elif "Manatee" in added_names:
            promo = "manateePromo"

        if promo is None:
            return

        try:
            from Helpers.sheets import find_by_ign, update_promo

            # Look up IGN from discord_links
            db = DB()
            db.connect()
            try:
                db.cursor.execute(
                    "SELECT uuid FROM discord_links WHERE discord_id = %s",
                    (after.id,)
                )
                row = db.cursor.fetchone()
            finally:
                db.close()

            if not row:
                return

            uuid = row[0]
            from Helpers.functions import getUsernameFromUUID
            name_result = await asyncio.to_thread(getUsernameFromUUID, uuid)
            if not name_result:
                return
            ign = name_result

            # Check if already marked to avoid double-updates from rank_promote
            sheet_row = await asyncio.to_thread(find_by_ign, ign)
            if not sheet_row.get("success") or not sheet_row.get("data"):
                return

            already_done = sheet_row["data"].get(promo, False)
            if already_done:
                return

            # Also mark manateePromo if this is a piranha promo
            if promo == "piranhaPromo":
                if not sheet_row["data"].get("manateePromo", False):
                    await asyncio.to_thread(update_promo, ign, "manateePromo")
            await asyncio.to_thread(update_promo, ign, promo)

        except Exception as e:
            err_ch = self.client.get_channel(error_channel)
            if err_ch:
                await err_ch.send(
                    f"## Recruiter Tracker - Role Promo Fallback Error\n"
                    f"**User:** <@{after.id}> | **Promo:** `{promo}`\n"
                    f"```\n{str(e)[:500]}\n```"
                )


def setup(client):
    client.add_cog(OnMemberUpdate(client))
