import json

from numpy import random
import re
import time
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

import discord
import requests
from discord import option
from discord.ext import commands
from discord.commands import slash_command

from Helpers.functions import addLine, get_transition_color, split_sentence, expand_image

base_stats = {'weapon': ['damage', 'earthDamage', 'thunderDamage', 'waterDamage', 'fireDamage', 'airDamage'],
              'armor': ['health', 'earthDefense', 'thunderDefense', 'waterDefense', 'fireDefense', 'airDefense'],
              'accessory': ['health', 'earthDefense', 'thunderDefense', 'waterDefense', 'fireDefense', 'airDefense']}

requirements_list = ['quest', 'level', 'classRequirement', 'strength', 'dexterity', 'intelligence', 'defense',
                     'agility']

bonus_stats = ['strengthPoints', 'dexterityPoints', 'intelligencePoints', 'defensePoints', 'agilityPoints']

damage_stats = ['earthDamageBonus', 'thunderDamageBonus', 'waterDamageBonus', 'fireDamageBonus', 'airDamageBonus',
                'mainAttackDamageBonus', 'mainAttackDamageBonusRaw', 'spellDamageBonus', 'spellDamageBonusRaw',
                'spellElementalDamageBonusRaw', 'spellEarthDamageBonusRaw', 'spellThunderDamageBonusRaw',
                'spellWaterDamageBonusRaw', 'spellFirerDamageBonusRaw', 'spellAirDamageBonusRaw'
                ]

defense_stats = ['bonusEarthDefense', 'bonusThunderDefense', 'bonusWaterDefense', 'bonusFireDefense', 'bonusAirDefense']

spell_cost_stats = ['spellCostPct1', 'spellCostRaw1', 'spellCostPct2', 'spellCostRaw2', 'spellCostPct3',
                    'spellCostRaw3', 'spellCostPct4', 'spellCostRaw4']

base = {
    'damage': '&6Neutral Damage',
    'earthDamage': '&2Earth &7Damage',
    'thunderDamage': '&eThunder &7Damage',
    'waterDamage': '&bWater &7Damage',
    'fireDamage': '&cFire &7Damage',
    'airDamage': '&fAir &7Damage',
    'health': '&4Health',
    'earthDefense': '&2Earth &7Defence',
    'thunderDefense': '&eThunder &7Defence',
    'waterDefense': '&bWater &7Defence',
    'fireDefense': '&cFire &7Defence',
    'airDefense': '&fAir &7Defence',
    'averageDPS': '&8Average DPS'
}

symbols = {
    'damage': '&6✤',
    'earthDamage': '&2✤',
    'thunderDamage': '&e✦',
    'waterDamage': '&b❉',
    'fireDamage': '&c✹',
    'airDamage': '&f❋',
    'health': '&4❤',
    'earthDefense': '&2✤',
    'thunderDefense': '&e✦',
    'waterDefense': '&b❉',
    'fireDefense': '&c✹',
    'airDefense': '&f❋',
}

attackSpeed = {
    'SUPER_SLOW': 'Super Slow',
    'VERY_SLOW': 'Very Slow',
    'SLOW': 'Slow',
    'NORMAL': 'Normal',
    'FAST': 'Fast',
    'VERY_FAST': 'Very Fast',
    'SUPER_FAST': 'Super Fast'
}

requirements = {
    'level': 'Combat Lv. Min',
    'quest': 'Quest Req',
    'strength': 'Strength Min',
    'dexterity': 'Dexterity Min',
    'intelligence': 'Intelligence Min',
    'defense': 'Defence Min',
    'agility': 'Agility Min',
    'classRequirement': 'Class Requirement'
}

identifications = {
    'healthRegen': {'name': 'Health Regen', 'suffix': '%', 'priority': '+'},
    'manaRegen': {'name': 'Mana Regen', 'suffix': '/5s', 'priority': '+'},
    'spellDamageBonus': {'name': 'Spell Damage', 'suffix': '%', 'priority': '+'},
    'mainAttackDamageBonus': {'name': 'Main Attack Damage', 'suffix': '%', 'priority': '+'},
    'lifeSteal': {'name': 'Life Steal', 'suffix': '/3s', 'priority': '+'},
    'manaSteal': {'name': 'Mana Steal', 'suffix': '/3s', 'priority': '+'},
    'xpBonus': {'name': 'Xp Bonus', 'suffix': '%', 'priority': '+'},
    'lootBonus': {'name': 'Loot Bonus', 'suffix': '%', 'priority': '+'},
    'attackSpeedBonus': {'name': 'Attack Speed', 'suffix': '', 'priority': '+'},
    'reflection': {'name': 'Reflection', 'suffix': '%', 'priority': '+'},
    'strengthPoints': {'name': 'Strength', 'suffix': '', 'priority': '+'},
    'dexterityPoints': {'name': 'Dexterity', 'suffix': '', 'priority': '+'},
    'intelligencePoints': {'name': 'Intelligence', 'suffix': '', 'priority': '+'},
    'defensePoints': {'name': 'Defence', 'suffix': '', 'priority': '+'},
    'agilityPoints': {'name': 'Agility', 'suffix': '', 'priority': '+'},
    'spellDamageBonusRaw': {'name': 'Spell Damage', 'suffix': '', 'priority': '+'},
    'mainAttackDamageBonusRaw': {'name': 'Main Attack Damage', 'suffix': '', 'priority': '+'},
    'earthDamageBonus': {'name': 'Earth Damage', 'suffix': '%', 'priority': '+'},
    'thunderDamageBonus': {'name': 'Thunder Damage', 'suffix': '%', 'priority': '+'},
    'waterDamageBonus': {'name': 'Water Damage', 'suffix': '%', 'priority': '+'},
    'fireDamageBonus': {'name': 'Fire Damage', 'suffix': '%', 'priority': '+'},
    'airDamageBonus': {'name': 'Air Damage', 'suffix': '%', 'priority': '+'},
    'bonusEarthDefense': {'name': 'Earth Defence', 'suffix': '%', 'priority': '+'},
    'bonusThunderDefense': {'name': 'Thunder Defence', 'suffix': '%', 'priority': '+'},
    'bonusWaterDefense': {'name': 'Water Defence', 'suffix': '%', 'priority': '+'},
    'bonusFireDefense': {'name': 'Fire Defence', 'suffix': '%', 'priority': '+'},
    'bonusAirDefense': {'name': 'Air Defence', 'suffix': '%', 'priority': '+'},
    'speed': {'name': 'Walk Speed', 'suffix': '%', 'priority': '+'},
    'healthBonus': {'name': 'Health', 'suffix': '', 'priority': '+'},
    'healthRegenRaw': {'name': 'Health Regen', 'suffix': '', 'priority': '+'},
    'emeraldStealing': {'name': 'Stealing', 'suffix': '%', 'priority': '+'},
    'spellCostRaw1': {'name': '1st Spell Cost', 'suffix': '', 'priority': '-'},
    'spellCostRaw2': {'name': '2nd Spell Cost', 'suffix': '', 'priority': '-'},
    'spellCostRaw3': {'name': '3rd Spell Cost', 'suffix': '', 'priority': '-'},
    'spellCostRaw4': {'name': '4th Spell Cost', 'suffix': '', 'priority': '-'},
    'spellCostPct1': {'name': '1st Spell Cost', 'suffix': '%', 'priority': '-'},
    'spellCostPct2': {'name': '2nd Spell Cost', 'suffix': '%', 'priority': '-'},
    'spellCostPct3': {'name': '3rd Spell Cost', 'suffix': '%', 'priority': '-'},
    'spellCostPct4': {'name': '4th Spell Cost', 'suffix': '%', 'priority': '-'},
    'sprintRegen': {'name': 'Sprint Regen', 'suffix': '%', 'priority': '+'},
    'jumpHeight': {'name': 'Jump Height', 'suffix': '', 'priority': '+'},
    'sprint': {'name': 'Sprint', 'suffix': '%', 'priority': '+'},
    'spellEarthDamageBonusRaw': {'name': 'Earth Spell Damage', 'suffix': '', 'priority': '+'},
    'spellThunderDamageBonusRaw': {'name': 'Thunder Spell Damage', 'suffix': '', 'priority': '+'},
    'spellWaterDamageBonusRaw': {'name': 'Water Spell Damage', 'suffix': '', 'priority': '+'},
    'spellFireDamageBonusRaw': {'name': 'Fire Spell Damage', 'suffix': '', 'priority': '+'},
    'spellAirDamageBonusRaw': {'name': 'Air Spell Damage', 'suffix': '', 'priority': '+'},
    'spellNeutralDamageBonusRaw': {'name': 'Neutral Spell Damage', 'suffix': '', 'priority': '+'},
    'EarthSpellDamage': {'name': 'Earth Spell Damage', 'suffix': '%', 'priority': '+'},
    'ThunderSpellDamage': {'name': 'Thunder Spell Damage', 'suffix': '%', 'priority': '+'},
    'WaterSpellDamage': {'name': 'Water Spell Damage', 'suffix': '%', 'priority': '+'},
    'FireSpellDamage': {'name': 'Fire Spell Damage', 'suffix': '%', 'priority': '+'},
    'AirSpellDamage': {'name': 'Air Spell Damage', 'suffix': '%', 'priority': '+'},
    'rawEarthMainDamage': {'name': 'Earth Main Damage', 'suffix': '', 'priority': '+'},
    'rawThunderMainDamage': {'name': 'Thunder Main Damage', 'suffix': '', 'priority': '+'},
    'rawWaterMainDamage': {'name': 'Water Main Damage', 'suffix': '', 'priority': '+'},
    'rawFireMainDamage': {'name': 'Fire Main Damage', 'suffix': '', 'priority': '+'},
    'rawAirMainDamage': {'name': 'Air Main Damage', 'suffix': '', 'priority': '+'},
    'EarthMainDamage': {'name': 'Earth Main Damage', 'suffix': '%', 'priority': '+'},
    'ThunderMainDamage': {'name': 'Thunder Main Damage', 'suffix': '%', 'priority': '+'},
    'WaterMainDamage': {'name': 'Water Main Damage', 'suffix': '%', 'priority': '+'},
    'FireMainDamage': {'name': 'Fire Main Damage', 'suffix': '%', 'priority': '+'},
    'AirMainDamage': {'name': 'Air Main Damage', 'suffix': '%', 'priority': '+'},
    'spellElementalDamageBonus': {'name': 'Elemental Spell Damage', 'suffix': '%', 'priority': '+'},
    'spellElementalDamageBonusRaw': {'name': 'Elemental Damage Bonus', 'suffix': '', 'priority': '+'},
    'elementalDamageBonus': {'name': 'Elemental Damage', 'suffix': '%', 'priority': '+'},
    'elementalDamageBonusRaw': {'name': 'Elemental Damage', 'suffix': '', 'priority': '+'},
    'rawElementalMainAttackDamage': {'name': 'Elemental Main Attack Damage', 'suffix': '', 'priority': '+'},
    'lootQuality': {'name': 'Loot Quality', 'suffix': '%', 'priority': '+'},
    'gatherXpBonus': {'name': 'Gather Xp Bonus', 'suffix': '%', 'priority': '+'},
    'gatherSpeed': {'name': 'Gather Speed', 'suffix': '%', 'priority': '+'},
    'soulPoints': {'name': 'Soul Point Regen', 'suffix': '%', 'priority': '+'},
    'thorns': {'name': 'Thorns', 'suffix': '%', 'priority': '+'},
    'poison': {'name': 'Poison', 'suffix': '/3s', 'priority': '+'},
    'exploding': {'name': 'Exploding', 'suffix': '%', 'priority': '+'}
}

rarity_colours = {'Normal': "&f",
                  'Unique': "&e",
                  'Rare': "&d",
                  'Legendary': "&b",
                  'Fabled': "&c",
                  'Crafted': "&3",
                  'Set': "&a",
                  'Mythic': "&5"}

classWeapons = {'Dagger': 'Assassin/Ninja',
                'Bow': 'Archer/Hunter',
                'Spear': 'Warrior/Knight',
                'Relik': 'Shaman/Skyseer',
                'Wand': 'Mage/Dark Wizard'}


async def get_items(message: discord.AutocompleteContext):
    with open('items.json', 'r') as f:
        ITEMS = json.load(f)['items']
        f.close()
    return [item for item in ITEMS if message.value.lower() in item.lower()]


class Id(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Identifies a chosen item!')
    @option("item", description="Pick an item!", autocomplete=get_items)
    async def id(self, message, item: str, corkian_amplifier: discord.Option(int, min_value=1, max_value=3, default=0,
                                                                             description="Corkian Amplifier tier ranging from 1 to 3")):

        await message.defer()
        longest_lineWidth = 0
        percentage_bonus = corkian_amplifier * 5

        item = item.title().replace('\'S', '\'s')

        with open('items.json', 'r') as f:
            ITEMS = json.load(f)['items']
            f.close()

        if item not in ITEMS:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not find item named `{item}`.\nPlease check your spelling or try again later.',
                                  color=0xe33232)
            await message.respond(embed=embed, ephemeral=True)
            return

        img = Image.new('RGBA', (700, 21), color='#100010e2')
        d = ImageDraw.Draw(img)
        d.fontmode = '1'
        gameFont = ImageFont.truetype('images/profile/game.ttf', 19)
        symbolFont = ImageFont.truetype('images/profile/game.ttf', 16)

        with open('custom_items.json', 'r') as f:
            custom_items = json.load(f)
            f.close()

        data = None
        custom = False
        for custom_item in custom_items:
            if custom_item['name'] == item:
                data = custom_item
                custom = True
                break

        if data is None:
            with open('items_data.json', 'r') as f:
                items = json.load(f)
                f.close()

            for datum in items['items']:
                if datum['name'] == item:
                    data = datum
                    break

        if data['category'] == 'weapon' or 'majorIds' in data:
            if not custom:
                req = requests.post('https://web-api.wynncraft.com/api/v3/item/search',
                                    json={"query": item, "type": ["weapons", "armour", "accessories"]})
                items = req.json()['results']
                if data['category'] == 'weapon':
                    averageDPS = items[item]['base']['averageDPS']

                if 'majorIds' in items[item]:
                    majorId = items[item]['majorIds']['description']
            else:
                majorId = data['majorIds']

        img, d = expand_image(img)

        if 'attackSpeed' in data:
            lineWidth = addLine(f'&7{attackSpeed[data["attackSpeed"]]} Attack Speed', d, gameFont, 0, img.height - 21)
            longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
            img, d = expand_image(img)

        for stat in base_stats[data['category']]:
            if stat not in data:
                continue
            elif data[stat] == 0 or data[stat] == "0-0":
                continue
            img, d = expand_image(img)
            addLine(f'{symbols[stat]} ', d, symbolFont, 0, img.height - 21)
            lineWidth = addLine(f'{base[stat]}: {"+" if data["category"] != "weapon" and data[stat] > 0 else ""}{data[stat]}', d,
                                gameFont,
                                23,
                                img.height - 21)
            longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        if data['category'] == 'weapon':
            img, d = expand_image(img)
            lineWidth = addLine(
                f'{base["averageDPS"]}: &7{averageDPS}', d, gameFont, 23, img.height - 21)
            longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        hasRequirement = False
        if 'type' in data:
            if data['type'] in classWeapons:
                if not hasRequirement:
                    img, d = expand_image(img)
                    hasRequirement = True
                img, d = expand_image(img)
                addLine('&a✔', d, symbolFont, 0, img.height - 21)
                lineWidth = addLine(f'&7Class Req: {classWeapons[data["type"]]}', d, gameFont, 23, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        for requirement in requirements_list:
            if requirement not in data:
                continue
            elif data[requirement] == 0 or data[requirement] is None:
                continue
            if not hasRequirement:
                img, d = expand_image(img)
                hasRequirement = True
            img, d = expand_image(img)
            addLine('&a✔', d, symbolFont, 0, img.height - 21)
            lineWidth = addLine(
                f'&7{requirements[requirement]}: {str(data[requirement]).title()}\n', d, gameFont,
                23,
                img.height - 21)
            longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        hasBonus = False
        for bonus in bonus_stats:
            if bonus not in data:
                continue
            elif data[bonus] == 0 or data[bonus] is None:
                continue
            if not hasBonus:
                img, d = expand_image(img)
                hasBonus = True
            img, d = expand_image(img)
            idValue = f'+{data[bonus]}' if data[bonus] > 0 else str(data[bonus])
            idColour = '&a' if idValue[0] == identifications[bonus]['priority'] else '&c'
            lineWidth = addLine(
                f'{idColour}{idValue}{identifications[bonus]["suffix"]} &7{identifications[bonus]["name"]}',
                d, gameFont, 0, img.height - 21)
            longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        totalPercentage = 0
        idCount = 0
        hasIdentification = False
        for identification in data:
            if identification not in identifications:
                continue
            elif identification in bonus_stats:
                continue
            elif identification in damage_stats:
                continue
            elif identification in defense_stats:
                continue
            elif identification in spell_cost_stats:
                continue
            elif data[identification] == 0 or data[identification] is None:
                continue
            if not hasIdentification:
                img, d = expand_image(img)
                hasIdentification = True
            img, d = expand_image(img)
            if 'identified' in data or data[identification] == 1 or data[identification] == -1:
                idValue = f'+{data[identification]}' if data[identification] > 0 else str(data[identification])
                idColour = '&a' if idValue[0] == identifications[identification]['priority'] else '&c'
                lineWidth = addLine(
                    f'{idColour}{idValue}{identifications[identification]["suffix"]} &7{identifications[identification]["name"]}',
                    d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
            else:
                id_min = round(data[identification] * 0.3) if (data[identification] > 0 and
                                                               identifications[identification]['priority'] == '+') or (
                                                                          data[identification] < 0 and
                                                                          identifications[identification][
                                                                              'priority'] == '-') else round(
                    data[identification] * 1.3)
                id_max = round(data[identification] * 1.3) if (data[identification] > 0 and
                                                               identifications[identification]['priority'] == '+') or (
                                                                          data[identification] < 0 and
                                                                          identifications[identification][
                                                                              'priority'] == '-') else round(
                    data[identification] * 0.7)
                roll = random.randint(30, 131) / 100 if data[identification] > 0 else random.randint(70, 131) / 100
                idValue = round(data[identification] * roll)
                if idValue == 0:
                    idValue = 1 if data[identification] > 0 else -1
                if (idValue > 0 and identifications[identification]['priority'] == '+') or (
                        idValue < 0 and identifications[identification]['priority'] == '-'):
                    failedRoll = (id_max - idValue)
                    idValue += int(failedRoll / 100 * percentage_bonus)
                percentage = 100 / (id_max - id_min) * (idValue - id_min)
                idValue = f'+{idValue}' if idValue > 0 else str(idValue)
                idColour = '&a' if idValue[0] == identifications[identification]['priority'] else '&c'
                lineWidth = addLine(
                    f'{idColour}{idValue}{identifications[identification]["suffix"]} &7{identifications[identification]["name"]} &{get_transition_color(int(percentage))}[{int(percentage)}%]',
                    d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
                idCount += 1
                totalPercentage += percentage

        hasDefenseStat = False
        for identification in defense_stats:
            if identification not in data:
                continue
            elif data[identification] == 0 or data[identification] is None:
                continue
            if not hasDefenseStat:
                img, d = expand_image(img)
                hasDefenseStat = True
            img, d = expand_image(img)
            if 'identified' in data or data[identification] == 1 or data[identification] == -1:
                idValue = f'+{data[identification]}' if data[identification] > 0 else str(data[identification])
                idColour = '&a' if idValue[0] == identifications[identification]['priority'] else '&c'
                lineWidth = addLine(
                    f'{idColour}{idValue}{identifications[identification]["suffix"]} &7{identifications[identification]["name"]}',
                    d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
            else:
                id_min = round(data[identification] * 0.3) if (data[identification] > 0 and
                                                               identifications[identification]['priority'] == '+') or (
                                                                          data[identification] < 0 and
                                                                          identifications[identification][
                                                                              'priority'] == '-') else round(
                    data[identification] * 1.3)
                id_max = round(data[identification] * 1.3) if (data[identification] > 0 and
                                                               identifications[identification]['priority'] == '+') or (
                                                                          data[identification] < 0 and
                                                                          identifications[identification][
                                                                              'priority'] == '-') else round(
                    data[identification] * 0.7)
                roll = random.randint(30, 131) / 100 if data[identification] > 0 else random.randint(70, 131) / 100
                idValue = round(data[identification] * roll)
                if idValue == 0:
                    idValue = 1 if data[identification] > 0 else -1
                if (idValue > 0 and identifications[identification]['priority'] == '+') or (
                        idValue < 0 and identifications[identification]['priority'] == '-'):
                    failedRoll = id_max - idValue
                    idValue += int(failedRoll / 100 * percentage_bonus)
                percentage = 100 / (id_max - id_min) * (idValue - id_min)
                idValue = f'+{idValue}' if idValue > 0 else str(idValue)
                idColour = '&a' if idValue[0] == identifications[identification]['priority'] else '&c'
                lineWidth = addLine(
                    f'{idColour}{idValue}{identifications[identification]["suffix"]} &7{identifications[identification]["name"]} &{get_transition_color(int(percentage))}[{int(percentage)}%]',
                    d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
                idCount += 1
                totalPercentage += percentage

        hasDamageStat = False
        for identification in damage_stats:
            if identification not in data:
                continue
            elif data[identification] == 0 or data[identification] is None:
                continue
            if not hasDamageStat:
                img, d = expand_image(img)
                hasDamageStat = True
            img, d = expand_image(img)
            if 'identified' in data or data[identification] == 1 or data[identification] == -1:
                idValue = f'+{data[identification]}' if data[identification] > 0 else str(data[identification])
                idColour = '&a' if idValue[0] == identifications[identification]['priority'] else '&c'
                lineWidth = addLine(
                    f'{idColour}{idValue}{identifications[identification]["suffix"]} &7{identifications[identification]["name"]}',
                    d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
            else:
                id_min = round(data[identification] * 0.3) if (data[identification] > 0 and
                                                               identifications[identification]['priority'] == '+') or (
                                                                          data[identification] < 0 and
                                                                          identifications[identification][
                                                                              'priority'] == '-') else round(
                    data[identification] * 1.3)
                id_max = round(data[identification] * 1.3) if (data[identification] > 0 and
                                                               identifications[identification]['priority'] == '+') or (
                                                                          data[identification] < 0 and
                                                                          identifications[identification][
                                                                              'priority'] == '-') else round(
                    data[identification] * 0.7)
                roll = random.randint(30, 131) / 100 if data[identification] > 0 else random.randint(70, 131) / 100
                idValue = round(data[identification] * roll)
                if idValue == 0:
                    idValue = 1 if data[identification] > 0 else -1
                if (idValue > 0 and identifications[identification]['priority'] == '+') or (
                        idValue < 0 and identifications[identification]['priority'] == '-'):
                    failedRoll = id_max - idValue
                    idValue += int(failedRoll / 100 * percentage_bonus)
                percentage = 100 / (id_max - id_min) * (idValue - id_min)
                idValue = f'+{idValue}' if idValue > 0 else str(idValue)
                idColour = '&a' if idValue[0] == identifications[identification]['priority'] else '&c'
                lineWidth = addLine(
                    f'{idColour}{idValue}{identifications[identification]["suffix"]} &7{identifications[identification]["name"]} &{get_transition_color(int(percentage))}[{int(percentage)}%]',
                    d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
                idCount += 1
                totalPercentage += percentage

        hasSpellCostStat = False
        for identification in spell_cost_stats:
            if identification not in data:
                continue
            elif data[identification] == 0 or data[identification] is None:
                continue
            if not hasSpellCostStat:
                img, d = expand_image(img)
                hasSpellCostStat = True
            img, d = expand_image(img)
            if 'identified' in data or data[identification] == 1 or data[identification] == -1:
                idValue = f'+{data[identification]}' if data[identification] > 0 else str(data[identification])
                idColour = '&a' if idValue[0] == identifications[identification]['priority'] else '&c'
                lineWidth = addLine(
                    f'{idColour}{idValue}{identifications[identification]["suffix"]} &7{identifications[identification]["name"]}',
                    d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
            else:
                id_min = round(data[identification] * 0.3) if (data[identification] > 0 and
                                                               identifications[identification]['priority'] == '+') or (
                                                                          data[identification] < 0 and
                                                                          identifications[identification][
                                                                              'priority'] == '-') else round(
                    data[identification] * 1.3)
                id_max = round(data[identification] * 1.3) if (data[identification] > 0 and
                                                               identifications[identification]['priority'] == '+') or (
                                                                          data[identification] < 0 and
                                                                          identifications[identification][
                                                                              'priority'] == '-') else round(
                    data[identification] * 0.3)
                roll = random.randint(30, 131) / 100 if data[identification] > 0 else random.randint(30, 131) / 100
                idValue = round(data[identification] * roll)
                if idValue == 0:
                    idValue = 1 if data[identification] > 0 else -1
                if (idValue > 0 and identifications[identification]['priority'] == '+') or (
                        idValue < 0 and identifications[identification]['priority'] == '-'):
                    failedRoll = id_max - idValue
                    idValue += int(failedRoll / 100 * percentage_bonus)
                percentage = 100 / (id_max - id_min) * (idValue - id_min)
                idValue = f'+{idValue}' if idValue > 0 else str(idValue)
                idColour = '&a' if idValue[0] == identifications[identification]['priority'] else '&c'
                lineWidth = addLine(
                    f'{idColour}{idValue}{identifications[identification]["suffix"]} &7{identifications[identification]["name"]} &{get_transition_color(int(percentage))}[{int(percentage)}%]',
                    d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth
                idCount += 1
                totalPercentage += percentage

        if 'majorIds' in data:
            if data['majorIds'] is not None:
                majorLines = split_sentence(majorId)
                for line in majorLines:
                    img, d = expand_image(img)
                    lineWidth = addLine(f'&3{line}', d, gameFont, 0, img.height - 21)
                    longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        if idCount > 0:
            lineWidth = addLine(
                f'{rarity_colours[data["tier"]]}{item} &{get_transition_color(int(totalPercentage / idCount))}[{int(totalPercentage / idCount)}%]',
                d, gameFont, 0, 0)
        else:
            lineWidth = addLine(f'{rarity_colours[data["tier"]]}{item}', d, gameFont, 0, 0)
        longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        img, d = expand_image(img)

        if 'sockets' in data:
            if data['sockets'] != 0:
                img, d = expand_image(img)
                lineWidth = addLine(f'&7[{data["sockets"]}] Powder Slots', d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        img, d = expand_image(img)

        lineWidth = addLine(f'{rarity_colours[data["tier"]]}{data["tier"].capitalize()} Item', d, gameFont, 0,
                            img.height - 21)
        longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        if 'restrictions' in data:
            if data['restrictions']:
                img, d = expand_image(img)
                lineWidth = addLine(f'&c{data["restrictions"].title()} Item', d, gameFont, 0, img.height - 21)
                longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        if 'addedLore' in data:
            if data['addedLore'] is not None:
                loreLines = split_sentence(data['addedLore'].replace('\\', ''))
                for line in loreLines:
                    img, d = expand_image(img)
                    lineWidth = addLine(f'&8{line}', d, gameFont, 0, img.height - 21)
                    longest_lineWidth = lineWidth if lineWidth > longest_lineWidth else longest_lineWidth

        img = img.crop((0, 0, longest_lineWidth + 2, img.height))
        img = ImageOps.expand(img, border=(8, 8), fill='#100010e2')
        d = ImageDraw.Draw(img)

        d.rectangle((2, 2, img.width - 3, img.height - 3), outline='#240059', width=2)
        d.rectangle((0, 0, 1, 1), fill='#00000000')
        d.rectangle((img.width - 2, 0, img.width, 1), fill='#00000000')
        d.rectangle((0, img.height - 2, 1, img.height), fill='#00000000')
        d.rectangle((img.width - 2, img.height - 2, img.width, img.height), fill='#00000000')

        with BytesIO() as file:
            img.save(file, format="PNG")
            file.seek(0)
            t = int(time.time())
            ided_item = discord.File(file, filename=f"{item}{t}.png")
        await message.respond(file=ided_item)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Id(client))
