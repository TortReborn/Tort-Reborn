import discord
from discord import Embed
from discord.ext import commands
from discord.commands import slash_command

import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from Helpers.variables import ALL_GUILD_IDS


class Build(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Search for a build specific to your requirements', guild_ids=ALL_GUILD_IDS)
    async def build(self, message,
                    wynn_class: discord.Option(str, name='class', choices=['Archer/Hunter', 'Warrior/Knight',
                                                                           'Assassin/Ninja', 'Mage/Dark Wizard',
                                                                           'Shaman/Skyseer', 'All'], require=True),
                    use_case: discord.Option(str, name='use-case', choices=['general', 'lootrun', 'loot bonus', 'gxp', 'raids'],
                                             require=True)):

        await message.defer()
        fitting_builds = []
        embed = Embed(title=f'{wynn_class} {use_case} builds', description='Here\'s a list of builds that fit your requirements', color=0x42f551)

        SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

        # The ID and range of a sample spreadsheet.
        SAMPLE_SPREADSHEET_ID = "1mn0Ix-eY-bm9HKGkmx-B5ClkXpGj9jXZZJShOk0JOYk"
        SAMPLE_RANGE_NAME = "Builds!A:E"

        creds = None
        # The file token.json stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the first
        # time.
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    "credentials.json", SCOPES
                )
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open("token.json", "w") as token:
                token.write(creds.to_json())

        try:
            service = build("sheets", "v4", credentials=creds)

            # Call the Sheets API
            sheet = service.spreadsheets()
            result = (
                sheet.values()
                .get(spreadsheetId=SAMPLE_SPREADSHEET_ID, range=SAMPLE_RANGE_NAME)
                .execute()
            )
            values = result.get("values", [])

            if not values:
                print("No data found.")
                return

            for row in values:
                b_class = row[0]
                b_name = row[1]
                b_description = row[2]
                b_usecase = row[3]
                b_link = row[4]
                if (wynn_class == 'All' or wynn_class == b_class) and use_case == b_usecase:
                    fitting_builds.append(row)
                    formatted_link = f':link: **[Wynnbuilder Link]({b_link})**'
                    embed.add_field(name=b_name, value=f'**Class**: {b_class}\n{b_description}\n{formatted_link}' if wynn_class == 'All' else f'{b_description}\n{formatted_link}', inline=False)

            if len(fitting_builds) == 0:
                embed.add_field(name=':( No builds found', value=' ')

            await message.respond(embed=embed)

        except HttpError as err:
            print(err)
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'There was an error connection to builds spreadsheet.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Build(client))
