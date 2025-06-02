import os
import time
from io import BytesIO

import discord
import requests
from PIL import Image, ImageFont, ImageDraw
from discord.ext import commands
from discord.commands import slash_command
from discord.ui import Select, View

from Helpers.classes import PlayerStats
from Helpers.functions import getPlayerData, fix_progressbar, getPlayerUUID, generate_rank_badge, create_progress_bar
from Helpers.variables import class_map
from StringProgressBar import progressBar
import re
import math


class Progress(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Displays player\'s Wynncraft progress')
    async def progress(self, message, name: discord.Option(str, require=True)):
        playerdata = PlayerStats(name, 7)

        max_lvl = 1690
        max_combat = 106
        max_prof = 132
        max_discovery = 601
        max_quests = 262
        max_dungeons = 18
        max_raids = 4
        max_overall = max_lvl + max_discovery + max_quests + max_dungeons + max_raids
        await message.defer()
        if not playerdata:
            embed = discord.Embed(title=':no_entry: Something went wrong',
                                  description=f'Could not find a player with name **{name}**!', color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return
        all_classes = []
        options = []
        for pclass in playerdata.characters:
            class_score = (playerdata.characters[pclass]['level'] * 1.1) + (
                len(playerdata.characters[pclass]['quests'])) + (playerdata.characters[pclass]['discoveries'] * 0.1)
            all_classes.append({'score': class_score, 'data': playerdata.characters[pclass]})
        all_classes.sort(key=lambda x: x['score'], reverse=True)
        i = 0
        for pclass in all_classes:
            if pclass['data']['nickname']:
                label = pclass['data']['nickname']
            else:
                label = pclass['data']['type']
            options.append(discord.SelectOption(label=label, value=str(i),
                                                description=f'Total Level: {pclass["data"]["totalLevel"] + 12}',
                                                emoji=class_map[re.sub('\\d', '', pclass["data"]["type"].lower())]))
            i += 1

        async def callback(interaction):
            selected = int(select.values[0])
            bg = Image.open('images/profile/bg_progress.png')
            background = Image.open(f"images/profile_backgrounds/{playerdata.background}.png")
            bg.paste(background, (0, 0), background)
            draw = ImageDraw.Draw(bg)

            color = playerdata.tag_color

            # skin
            try:
                headers = {'User-Agent': os.getenv("visage_UA")}
                url = f"https://visage.surgeplay.com/bust/500/{playerdata.UUID}"
                response = requests.get(url, headers=headers)
                skin = Image.open(BytesIO(response.content))
            except Exception as e:
                print(e)
                skin = Image.open('images/profile/x-steve500.png')
            bg.paste(skin, (150, 26), skin)

            rank = generate_rank_badge(playerdata.tag_display, playerdata.tag_color)
            rank_w, rank_h = rank.size
            bg.paste(rank, (400 - int(rank_w / 2), 483 - int(rank_h / 2)), rank)

            name_font = ImageFont.truetype('images/profile/minecraft_font.ttf', 64)
            _, _, w, h = draw.textbbox((0, 0), playerdata.username, font=name_font)
            draw.text(((800 - w) / 2, 520), playerdata.username, font=name_font, fill=playerdata.tag_color)

            title_font = ImageFont.truetype('images/profile/5x5.ttf', 35)
            data_font = ImageFont.truetype('images/profile/minecraft_font.ttf', 25)

            if all_classes[selected]['data']['nickname']:
                label = all_classes[selected]['data']['nickname']
            else:
                label = all_classes[selected]['data']['type']

            _, _, w, h = draw.textbbox((0, 0), label, font=title_font)
            draw.text(((800 - w) / 2, 585), label, font=title_font, fill=playerdata.tag_color)

            lvl = all_classes[selected]['data']['totalLevel']
            combat = all_classes[selected]['data']['level']
            if "Lost Sanctuary" in all_classes[selected]['data']['dungeons']['list']:
                del all_classes[selected]['data']['dungeons']['list']['Lost Sanctuary']
            dungeons = len(all_classes[selected]['data']['dungeons']['list'])
            if dungeons > max_dungeons:
                dungeons = max_dungeons
            raids = len(all_classes[selected]['data']['raids']['list'])
            profs = [{'prof': 'Farming',
                      'level': all_classes[selected]['data']['professions']['farming']['level']},
                     {'prof': 'Fishing',
                      'level': all_classes[selected]['data']['professions']['fishing']['level']},
                     {'prof': 'Mining',
                      'level': all_classes[selected]['data']['professions']['mining']['level']},
                     {'prof': 'Woodcutting',
                      'level': all_classes[selected]['data']['professions']['woodcutting']['level']},
                     {'prof': 'Alchemism',
                      'level': all_classes[selected]['data']['professions']['alchemism']['level']},
                     {'prof': 'Armouring',
                      'level': all_classes[selected]['data']['professions']['armouring']['level']},
                     {'prof': 'Cooking',
                      'level': all_classes[selected]['data']['professions']['cooking']['level']},
                     {'prof': 'Jeweling',
                      'level': all_classes[selected]['data']['professions']['jeweling']['level']},
                     {'prof': 'Scribing',
                      'level': all_classes[selected]['data']['professions']['scribing']['level']},
                     {'prof': 'Tailoring',
                      'level': all_classes[selected]['data']['professions']['tailoring']['level']},
                     {'prof': 'Weaponsmithing',
                      'level': all_classes[selected]['data']['professions']['weaponsmithing']['level']},
                     {'prof': 'Woodworking',
                      'level': all_classes[selected]['data']['professions']['woodworking']['level']}]
            discovery = all_classes[selected]['data']['discoveries']
            quests = len(all_classes[selected]['data']['quests'])

            if discovery > max_discovery:
                discovery = max_discovery
            overall = lvl + discovery + quests + dungeons + raids

            draw.text((50, 630), 'Overall Progress', font=title_font, fill='#fad51e')
            overall_prgbar = create_progress_bar(300, math.floor(100 * (overall/max_overall)), color='#2dde1f', scale=2)
            bg.paste(overall_prgbar, (50, 670), overall_prgbar)
            draw.text((660, 661), f'{math.floor(100 * (overall/max_overall))}%', font=data_font, fill='#ffffff')

            pos = 720
            draw.text((50, pos), 'Combat Level', font=title_font, fill='#fad51e')
            draw.text((50, pos + 33), f'{combat}/{max_combat}', font=data_font, fill='#ffffff')
            combat_prgbar = create_progress_bar(250, 100 / max_combat * combat, color='#2dde1f', scale=1)
            bg.paste(combat_prgbar, (50, pos + 70), combat_prgbar)
            draw.text((310, pos + 53), f'{math.floor(100 / max_combat * combat)}%', font=data_font, fill='#ffffff')

            draw.text((400, pos), 'Professions Level', font=title_font, fill='#fad51e')
            draw.text((400, pos + 33), f'{lvl - combat}/{max_prof * 12}', font=data_font, fill='#ffffff')
            prof_prgbar = create_progress_bar(250, 100 / (max_prof * 12) * (lvl - combat), color='#2dde1f', scale=1)
            bg.paste(prof_prgbar, (400, pos + 70), prof_prgbar)
            draw.text((660, pos + 53), f'{math.floor(100 / (max_prof * 12) * (lvl - combat))}%', font=data_font,
                      fill='#ffffff')

            pos = 820
            draw.text((50, pos), 'Quests', font=title_font, fill='#fad51e')
            draw.text((50, pos + 33), f'{quests}/{max_quests}', font=data_font, fill='#ffffff')
            quests_prgbar = create_progress_bar(250, 100 / max_quests * quests, color='#2dde1f', scale=1)
            bg.paste(quests_prgbar, (50, pos + 70), quests_prgbar)
            draw.text((310, pos + 53), f'{math.floor(100 / max_quests * quests)}%', font=data_font, fill='#ffffff')

            draw.text((400, pos), 'Discoveries', font=title_font, fill='#fad51e')
            draw.text((400, pos + 33), f'{discovery}/{max_discovery}', font=data_font, fill='#ffffff')
            discoveries_prgbar = create_progress_bar(250, 100 / max_discovery * discovery, color='#2dde1f', scale=1)
            bg.paste(discoveries_prgbar, (400, pos + 70), discoveries_prgbar)
            draw.text((660, pos + 53), f'{math.floor(100 / max_discovery * discovery)}%', font=data_font,
                      fill='#ffffff')

            pos = 920
            draw.text((50, pos), 'Dungeons', font=title_font, fill='#fad51e')
            draw.text((50, pos + 33), f'{dungeons}/{max_dungeons}', font=data_font, fill='#ffffff')
            dungeons_prgbar = create_progress_bar(250, 100 / max_dungeons * dungeons, color='#2dde1f', scale=1)
            bg.paste(dungeons_prgbar, (50, pos + 70), dungeons_prgbar)
            draw.text((310, pos + 53), f'{math.floor(100 / max_dungeons * dungeons)}%', font=data_font, fill='#ffffff')

            draw.text((400, pos), 'Raids', font=title_font, fill='#fad51e')
            draw.text((400, pos + 33), f'{raids}/{max_raids}', font=data_font, fill='#ffffff')
            raids_prgbar = create_progress_bar(250, 100 / max_raids * raids, color='#2dde1f', scale=1)
            bg.paste(raids_prgbar, (400, pos + 70), raids_prgbar)
            draw.text((660, pos + 53), f'{math.floor(100 / max_raids * raids)}%', font=data_font,
                      fill='#ffffff')

            pos = 1020
            profs_font = ImageFont.truetype('images/profile/minecraft_font.ttf', 20)
            for i, prof in enumerate(profs):
                column = math.floor(i/4)
                if i % 4 == 0:
                    height = 0
                else:
                    height += 1

                draw.text((50 + column * 245, pos + height * 30), f'{prof["prof"]} [{prof["level"]}]', font=profs_font,
                      fill='#91e896' if prof['level'] == max_prof else '#ffffff')

            possessive_noun = '\'s' if playerdata.username[-1] != 's' else '\''
            embed = discord.Embed(title=playerdata.username.replace("_", "\\_") + f'{possessive_noun} progress card',
                                  description=f'<:discordlinked:1023567645817188402> <@{playerdata.discord}>' if playerdata.linked else '',
                                  color=int(color[1:], 16))
            with BytesIO() as file:
                bg.save(file, format="PNG")
                file.seek(0)
                t = int(time.time())
                progress_card = discord.File(file, filename=f"progress{t}.png")
                embed.set_image(url=f"attachment://progress{t}.png")

            await interaction.message.edit(embed=embed, file=progress_card, view=None)

            if playerdata.linked:
                # 1 Year background unlock
                if str(interaction.user.id) == playerdata.discord and overall >= max_overall and 7 not in playerdata.backgrounds_owned:
                    embed = discord.Embed(title=':tada: New background unlocked!',
                                          description=f'<@{playerdata.discord}> unlocked the **Completitionist** background!',
                                          color=0x34eb40)
                    bg_file = discord.File(f'./images/profile_backgrounds/7.png', filename=f"7.png")
                    embed.set_thumbnail(url=f"attachment://7.png")

                    unlock = playerdata.unlock_background('Completitionist')
                    if unlock:
                        await message.channel.send(embed=embed, file=bg_file)

        select = Select(options=options, placeholder='Select a class')
        select.callback = callback
        view = View()
        view.add_item(select)

        await message.respond(view=view)

    @commands.Cog.listener()
    async def on_ready(self):
        print('Progress command loaded')


def setup(client):
    client.add_cog(Progress(client))
