import discord
from discord.ext import commands
from discord.commands import user_command

from Helpers.classes import LinkAccount, NewMember
from Helpers.database import DB
from Helpers.functions import getPlayerUUID
from Helpers.variables import guilds, discord_ranks, discord_rank_roles


class RankPromote(commands.Cog):
    def __init__(self, client):
        self.client = client

    @user_command(name='Rank | Promote', default_member_permissions=discord.Permissions(manage_roles=True), guild_ids=guilds)
    async def promote_member(self, interaction: discord.Interaction, user: discord.Member):
        if interaction.user.guild_permissions.manage_roles:
            await interaction.defer(ephemeral=True)
            db = DB()
            db.connect()
            db.cursor.execute(f'SELECT rank, uuid FROM discord_links WHERE discord_id = {user.id}')
            row = db.cursor.fetchone()
            # Check if user is linked
            if row:
                username, UUID = getPlayerUUID(row[1])
                current_rank = row[0]
                current_rank_index = list(discord_ranks).index(current_rank)
                # check if promoted user is already Narwhal
                if current_rank_index >= 8:
                    embed = discord.Embed(title=':warning: Oops!',
                                          description=f'Cannot promote past Narwhal.',
                                          color=0xebdb34)
                    await interaction.respond(embed=embed, ephemeral=True)
                    db.close()
                    return
                next_rank = discord_ranks[list(discord_ranks)[current_rank_index+1]]
                all_roles = interaction.guild.roles
                roles_to_add = []
                roles_to_remove = []
                for add_role in next_rank['roles']:
                    role = discord.utils.find(lambda r: r.name == add_role, all_roles)
                    if role not in user.roles:
                        roles_to_add.append(role)

                await user.add_roles(*roles_to_add, reason=f'Promotion (ran by {interaction.author.name})', atomic=True)

                remove_roles = [x for x in discord_rank_roles if x not in next_rank['roles']]
                for remove_role in remove_roles:
                    role = discord.utils.find(lambda r: r.name == remove_role, all_roles)
                    if role in user.roles:
                        roles_to_remove.append(role)

                await user.remove_roles(*roles_to_remove, reason=f'Promotion (ran by {interaction.author.name})', atomic=True)

                try:
                    parts = user.nick.split(' ', 1)
                    base = parts[1] if len(parts) > 1 else parts[0]
                    new_rank = list(discord_ranks)[current_rank_index+1]
                    await user.edit(nick=f'{new_rank} {base}')
                except Exception:
                    pass

                db.cursor.execute(f'UPDATE discord_links SET rank = \'{list(discord_ranks)[current_rank_index+1]}\' WHERE discord_id = {user.id}')
                db.connection.commit()
                db.close()

                embed = discord.Embed(title=':white_check_mark: Promotion successful',
                                      description=f'<@{user.id}> promoted to **{list(discord_ranks)[current_rank_index+1]}**', color=0x3ed63e)
                await interaction.respond(embed=embed)
            else:
                embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                      description=f'<@{user.id}> does not have a linked Minecraft account.\nPlease use the `/manage link` command first.',
                                      color=0xe33232)
                await interaction.respond(embed=embed, ephemeral=True)
                db.close()
                return
        else:
            await interaction.response.send_message(
                'You are missing Manage Roles permission(s) to run this command.', ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        print('RankPromote user command loaded')


def setup(client):
    client.add_cog(RankPromote(client))
