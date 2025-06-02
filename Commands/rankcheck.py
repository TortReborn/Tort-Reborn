import discord
import traceback
from discord import Embed, ButtonStyle
from discord.commands import slash_command
from discord.ext import commands

from Helpers.classes import Guild
from Helpers.database import DB
from Helpers.functions import getNameFromUUID
from Helpers.variables import test, discord_ranks, guilds, te


class ReportPaginator(discord.ui.View):
    def __init__(self, embed_mismatch: Embed, embed_linkage: Embed, embed_usernames: Embed):
        super().__init__(timeout=None)
        self.embeds = {
            "mismatch": embed_mismatch,
            "linkage": embed_linkage,
            "usernames": embed_usernames
        }

    @discord.ui.button(label="Mismatch Issues", style=ButtonStyle.primary)
    async def show_mismatch(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.embeds["mismatch"], view=self)

    @discord.ui.button(label="Linkage Issues", style=ButtonStyle.secondary)
    async def show_linkage(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.embeds["linkage"], view=self)

    @discord.ui.button(label="Username Mismatches", style=ButtonStyle.success)
    async def show_usernames(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.embeds["usernames"], view=self)


class RankCheck(commands.Cog):
    def __init__(self, client):
        self.client = client

    if test:
        guild_ids = [guilds[1]]
    else:
        guild_ids = [te, guilds[1]]

    @slash_command(
        description='Check for game/discord rank & nickname consistency',
        guild_ids=guild_ids
    )
    async def rankcheck(self, interaction):
        await interaction.response.defer()

        try:
            data = Guild('The%20Aquarium').all_members
            guild_uuids = {m['uuid'] for m in data}

            discord_members = {m.id: m for m in interaction.guild.members}

            db = DB(); db.connect()
            db.cursor.execute("SELECT uuid, discord_id, rank FROM discord_links")
            all_links = db.cursor.fetchall()
            db.close()

            links_map = {row[0]: (row[1], row[2]) for row in all_links}
            linked_uuids = set(links_map)

            hdr = (
                '```ansi\n'
                ' [1;37m{:^16s}   {:^12s}   {:^23s}\n'
                '╘═════════════════╪══════════════╪════════════════════════╛\n'
            ).format('Player', 'In-Game Rank', 'Discord Rank')

            mismatch, linkage, usernames = [], [], []

            for member in data:
                uuid = member['uuid']
                stale_api = member.get('name', '')

                raw = getNameFromUUID(uuid)
                ign = raw[0] if isinstance(raw, list) and raw else str(raw)

                if stale_api and stale_api != ign:
                    usernames.append(f'[0;36m {ign:16} → {stale_api}')

                linked = links_map.get(uuid)
                if linked and linked[1] != 'None':
                    discord_id, role = linked

                    try:
                        expected = discord_ranks[role]['in_game_rank']
                    except KeyError:
                        mismatch.append(
                            f'[0;31m ERROR: {ign:16} no mapping for role "{role}"'
                        )
                        continue

                    if member['rank'].upper() != expected:
                        dr = f'{role} ({expected})'
                        mismatch.append(
                            f'[0;0m {ign:16} [1;37m│ [0;0m'
                            f'{member["rank"].upper():12} [1;37m│ [0;0m{dr:23}'
                        )

                    disc_mem = discord_members.get(discord_id)
                    if disc_mem:
                        nick = disc_mem.nick or disc_mem.name
                        parts = nick.split()
                        prefix = parts[0]
                        second = parts[1] if len(parts) > 1 else None

                        if prefix.lower() != role.lower():
                            mismatch.append(
                                f'[0;33m PREFIX MISMATCH: "{prefix}" ≠ "{role}" for {ign}'
                            )
                        if second and second != ign:
                            mismatch.append(
                                f'[0;33m NICKNAME MISMATCH: "{second}" ≠ "{ign}"'
                            )
                else:
                    linkage.append(
                        f'[0;0m {ign:16} [1;37m│ [0;0m'
                        f'{member["rank"].upper():12} [1;37m│ [0;31mNOT LINKED'
                    )

            orphans = linked_uuids - guild_uuids
            if orphans:
                linkage.append('')
                linkage.append('[0;35mLinked but not in guild:')
                for uuid in orphans:
                    raw = getNameFromUUID(uuid)
                    ign = raw[0] if isinstance(raw, list) and raw else str(raw)
                    linkage.append(f'  {ign}')

            embed_mismatch = Embed(
                title='Mismatch Issues',
                description=hdr + '\n'.join(mismatch) + '```'
            )
            embed_linkage = Embed(
                title='Linkage Issues',
                description=hdr + '\n'.join(linkage) + '```'
            )

            hdr3 = (
                '```ansi\n'
                ' [1;37m{:^16s} → {:^16s}\n'
                '╘═════════════════╪════════════════════╛\n'
            ).format('Official IGN', 'Guild API Name')
            embed_usernames = Embed(
                title='Username Mismatches',
                description=hdr3 + '\n'.join(usernames) + '```'
            )

            view = ReportPaginator(embed_mismatch, embed_linkage, embed_usernames)
            await interaction.followup.send(embed=embed_mismatch, view=view)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Something blew up: ```{e}```", ephemeral=True)
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_ready(self):
        print('RankCheck command loaded')


def setup(client):
    client.add_cog(RankCheck(client))
