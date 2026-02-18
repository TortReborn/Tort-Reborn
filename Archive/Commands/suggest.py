import discord
from discord.ext import commands
from discord.commands import slash_command
from discord.ui import InputText, Modal

from Helpers.variables import ALL_GUILD_IDS


class SuggestionModal(Modal):
    def __init__(self, client, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.add_item(InputText(label="Title", placeholder="Suggestion title"))
        self.client = client

        self.add_item(
            InputText(
                label="Description",
                value="",
                style=discord.InputTextStyle.long,
            )
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.nick is None:
            name = interaction.user.name
        else:
            name = interaction.user.nick
        embed = discord.Embed(title=self.children[0].value, description=self.children[1].value, color=0x34eb5b)
        embed.set_author(name=name, icon_url=interaction.user.avatar)
        channel = self.client.get_channel(947937511441850420)
        msg = await channel.send(embeds=[embed])
        await msg.add_reaction('👍')
        await msg.add_reaction('👎')
        await interaction.response.send_message("Thank you for your suggestion! ;)", ephemeral=True)


class Suggest(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(default_permission=False, description='Opens a suggestion form', guild_ids=ALL_GUILD_IDS)
    async def suggest(self, message):
        modal = SuggestionModal(self.client, title="Creating new suggestion")
        await message.interaction.response.send_modal(modal)

    @commands.Cog.listener()
    async def on_ready(self):
        print('Suggest command loaded')


def setup(client):
    client.add_cog(Suggest(client))
