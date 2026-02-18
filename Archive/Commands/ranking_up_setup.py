import json
import time

import discord
from discord import option, default_permissions, slash_command
from discord.ext import commands
from discord.ui import View

from Helpers.variables import IS_TEST_MODE, ALL_GUILD_IDS, RANK_UP_CHANNEL_ID


class buttonView(View):
    def __init__(self, client):
        super().__init__(timeout=None)
        self.client = client

    @discord.ui.button(label='I want to rank up!', custom_id='call-button', style=discord.ButtonStyle.green, emoji='🛎️')
    async def call_big_brain(self, button, ctx: discord.ApplicationContext):
        await ctx.response.defer(ephemeral=True)
        with open('button_cd.json', 'r') as f:
            cd = json.load(f)
            f.close()

        user_id = str(ctx.user.id)

        t = int(time.time())

        if user_id in cd:
            if t - 604800 > cd[user_id]:
                await self.client.get_channel(RANK_UP_CHANNEL_ID).send(
                    f'**{ctx.user.name}** is interested in ranking up!')
                cd[ctx.user.id] = t
                with open('button_cd.json', 'w') as f:
                    json.dump(cd, f)
                    f.close()
                embed = discord.Embed(title='',
                                      description=':white_check_mark: Our staff will get in touch with you soon',
                                      color=0x28bf50)
                await ctx.followup.send(embed=embed, ephemeral=True)
            else:
                embed = discord.Embed(title='',
                                      description=':no_entry: Nuh uh. You already pressed this button this week.',
                                      color=0xbf2828)
                await ctx.followup.send(embed=embed, delete_after=5, ephemeral=True)
        else:
            await self.client.get_channel(RANK_UP_CHANNEL_ID).send(
               f'**{ctx.user.name}** is interested in ranking up!')
            cd[user_id] = t
            with open('button_cd.json', 'w') as f:
                json.dump(cd, f)
                f.close()
            embed = discord.Embed(title='',
                                  description=':white_check_mark: Our staff will get in touch with you soon',
                                  color=0x28bf50)
            await ctx.followup.send(embed=embed, ephemeral=True)


class RankingUpSetup(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description="ADMIN: Set up the ranking up channel", default_member_permissions=discord.Permissions(administrator=True), guild_ids=ALL_GUILD_IDS)
    async def ranking_up_setup(self, message, json_data: discord.Option(str, description='JSON data from Discohook website'), button: discord.Option(bool, default=True, description='Include the ping button')):
        await message.defer(ephemeral=True)
        embed_data = json.loads(json_data)
        embedList = []
        incl_button = buttonView(self.client) if button else None
        for e in embed_data["embeds"]:
            title = e["title"] if "title" in e else ""
            description = e["description"] if "description" in e else ""
            color = e["color"] if "color" in e else 0x000000
            footer = e["footer"]["text"] if "footer" in e else None
            embed = discord.Embed(title=title, description=description, color=color)
            if footer:
                embed.set_footer(text=footer)
            if "fields" in e:
                for field in e["fields"]:
                    name = field["name"] if "name" in field else ""
                    value = field["value"] if "value" in field else ""
                    inline = True if "inline" in field else False
                    embed.add_field(name=name, value=value, inline=inline)
            embedList.append(embed)

        # embed = discord.Embed(title='', description='Promotions rely on your role in the guild and how you help it! We value all sorts of contributions, trying our best to give everyone a chance to rank up regardless of their interests and skills.\n\n🕗‎ ‎ ‎  **Passive contributions**\n> - Being active in guild chat and/or on Discord\n> - Joining voice calls\n> - Playing with other guild members\n> - Helping out a fellow guild member\n> - Giving [recommendations, advice and feedback](https://docs.google.com/forms/d/10JrNE3WqX_xSo0wEX8_PEcy9zX5LrWivYBmz_FHttKg/).\n\nWe are thankful for any positive effort that contributes to making TAq a friendly and welcoming community! ♡\n\n🔧‎ ‎ ‎  **Active contributions**\n> - Joining the [war](https://discord.com/channels/729147655875199017/1152966582834827344) effort\n> - Joining our event team (DM <@316548101202640897>)\n> - Starting up giveaways (DM any chief)\n> - Refilling teleport scrolls\n> - Recruiting new guild members (you can even earn [5LE per recruit](https://discord.com/channels/729147655875199017/729162124223447040/1212068441029345381))\n> - Donating [ingredients or materials](https://discord.com/channels/729147655875199017/1135510651981287424)\n> - Grinding XP during guild leveling events\n\nThe first rank-ups are easy to achieve. To get promoted to Manatee (recruiter), something as easy as regularly chatting with the guild is enough!\nAfter reaching Angler, an [application](https://discord.com/channels/729147655875199017/887769052142002206/1203799031638270014) is required in order to rank up and become a part of our HR team.', color=0xa397e4)
        # embedList.append(embed)
        # embed = discord.Embed(title='⚔️ Ranking up through warring', description='Jumping into guild wars is like hitting the fast lane to rank up in no time! It\'s an absolute blast and a great way to dive into exciting end-game content. You get to team up with fellow guild members, form strategies, and kick some towers!\n\n> Being active in wars is super important for our guild because it keeps us strong and competitive. Plus, it\'s not just about the thrill – having war power means we get to hold territories and generate sweet emeralds, which we can then spend on guild events and community giveaways. So, if you\'re up for some action-packed fun and want to help our guild thrive, join the war efforts today!\n\nThe amount of wars you participate in will always be taken into account for promotion waves. Bonus points if you help with starting rounds of FFA, teaching other members, pinging when we get attacked, etc!\n\n\n⏩ **Rank up shortcuts**\n```- Starfish/Manatee → Piranha = learn how to queue\n- Piranha → Barracuda = learning about defending our claim\n- Barracuda → Angler = learn how to eco```\nWe are always looking for new warrers, so do not hesitate to ask for information!', color=0xa397e4)
        # embedList.append(embed)
        # embed = discord.Embed(title='', description='', color=0xa397e4)
        # embed.set_footer(text='Pressing the button below will notify a chief about your interest in ranking up. You will soon be contacted and helped, ensuring a quick and easy promotion. What are you waiting for?')
        # embedList.append(embed)

        file = discord.File('images/profile/How_to_rank_up.png')

        await message.channel.send(embeds=embedList, view=incl_button, file=file)
        await message.respond(f'Button created', ephemeral=True, delete_after=5)

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(buttonView(self.client))
        pass


def setup(client):
    client.add_cog(RankingUpSetup(client))
