import colorsys
import datetime
import math
import os
import json
import re
from io import BytesIO
from uuid import UUID

import requests
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageFont, ImageOps, ImageColor

from Helpers.variables import minecraft_colors, minecraft_banner_colors, colours, shadows, test


def isInCurrDay(data, uuid):
    for member in data:
        if member['uuid'] == uuid:
            return True
    return False


def date_diff(time=False):
    now = datetime.datetime.now()
    if type(time) is int:
        diff = now - datetime.datetime.fromtimestamp(time)
    elif isinstance(time, datetime.datetime):
        diff = now - time.replace(tzinfo=None)
    elif not time:
        diff = 0
    return diff.days


def getPlayerData(name):
    if name.lower() == 'woodcreature':
        name = 'aa7402cc-bf1c-4aed-838b-fd8897d38836'
    url = f'https://api.wynncraft.com/v2/player/{name}/stats'
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return False


def getPlayerDatav3(uuid):
    url = f'https://api.wynncraft.com/v3/player/{uuid}?fullResult'
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return False


def getGuildFromShort(short):
    url = ('https://api.wynncraft.com/public_api.php'
           '?action=statsLeaderboard&type=guild&timeframe=alltime')
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return False

    for guild in data.get('data', []):
        if guild['prefix'].lower() == short.lower():
            return guild['name']

    # fallback to local cache
    with open('guild_prefix.json', 'r') as f:
        guilds = json.load(f)
    return guilds.get(short.lower(), False)



def search(item, type):
    try_prefix = getGuildFromShort(item)
    if try_prefix:
        return [True, try_prefix]

    searchurl = (
      'https://api.wynncraft.com/public_api.php'
      f'?action=statsSearch&search={urlify(item)}'
    )
    try:
        resp = requests.get(searchurl, timeout=10)
        resp.raise_for_status()
        jsondata = resp.json()
    except requests.RequestException:
        return [False]

    for datum in jsondata.get(type, []):
        if item.lower() == datum.lower():
            return [True, datum]
    return [False]


def getData(guild):
    url = f"https://api.wynncraft.com/v3/guild/{urlify(guild)}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return False


def urlify(in_string):
    return in_string.replace(' ', '%20')


def pretty_date(time=False):
    now = datetime.datetime.now(datetime.timezone.utc)
    if isinstance(time, int):
        diff = now - datetime.datetime.fromtimestamp(time, tz=datetime.timezone.utc)
    elif isinstance(time, datetime.datetime):
        if time.tzinfo is None:
            time = time.replace(tzinfo=datetime.timezone.utc)
        diff = now - time
    elif not time:
        diff = 0
    second_diff = diff.seconds
    day_diff = diff.days

    if day_diff < 0:
        return ''

    if day_diff == 0:
        if second_diff < 10:
            return "just now"
        if second_diff < 60:
            return str(second_diff) + " seconds ago"
        if second_diff < 120:
            return "a minute ago"
        if second_diff < 3600:
            return str(second_diff // 60) + " mins ago"
        if second_diff < 7200:
            return "an hour ago"
        if second_diff < 86400:
            return str(second_diff // 3600) + " hrs ago"
    if day_diff == 1:
        return "Yesterday"
    if day_diff < 7:
        return str(day_diff) + " days ago"
    if day_diff < 31:
        return str(day_diff // 7) + " weeks ago"
    if day_diff < 365:
        return str(day_diff // 30) + " months ago"
    return str(day_diff // 365) + " years ago"


def fix_progressbar(progress):
    temp_progress = progress[0].split('><')
    if temp_progress[0] == '<:a_e:1001612221069131866':
        temp_progress[0] = '<:a_start_e:1001612227926827101'
    else:
        temp_progress[0] = '<:a_start:1001612226337177761'
    if progress[1] < 100 and temp_progress[-1] == ':a_:1001612219810857091>':
        temp_progress[-1] = ':a_end_half:1001612224953057330>'
    elif temp_progress[-1] == ':a_:1001612219810857091>':
        temp_progress[-1] = ':a_end:1001612222096740545>'
    else:
        temp_progress[-1] = ':a_end_e:1001612223740923994>'

    progress[0] = '><'.join(temp_progress)

    return progress


def calcPercentage(p, m):
    return math.floor((100 / m) * p)


def getGuildMembers(guild):
    url = (
      'https://api.wynncraft.com/public_api.php'
      f'?action=guildStats&command={urlify(guild)}'
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json().get('members', [])
    except requests.RequestException:
        return []


def savePlayers(data):
    if test:
        guild_log = 'lunarity.json'
    else:
        guild_log = 'theaquarium.json'

    with open(guild_log, 'w') as f:
        f.write(json.dumps(data))
        f.close()


def dropShadow(image):
    new_image = Image.new("RGBA", (350, 350), (0, 0, 0, 0))
    image = image.resize((310, 310))
    new_image.paste(image, (20, 50), image)
    enhancer = ImageEnhance.Brightness(new_image)
    output = enhancer.enhance(0)
    image = output.filter(ImageFilter.GaussianBlur(radius=5))

    return image


def getPlayerUUID(player):
    try:
        req = requests.get(f"https://api.mojang.com/users/profiles/minecraft/{player}")
        username = req.json()['name']
        player_uuid = UUID(req.json()['id'])
        return [username, str(player_uuid)]
    except:
        try:
            req = requests.get(f"https://api.wynncraft.com/v3/player/{player}")
            username = req.json()['username']
            player_uuid = UUID(req.json()['uuid'])
            return [username, str(player_uuid)]
        except:
            return False


def getNameFromUUID(uuid):
    try:
        req = requests.get(f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}")
        username = req.json()['name']
        player_uuid = UUID(req.json()['id'])
        return [username, str(player_uuid)]
    except:
        return False


class Color:
    def __init__(self, color):
        self.hex = color
        self.rgb = tuple(int(color.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
        self.r, self.g, self.b = self.rgb[0] / 255, self.rgb[1] / 255, self.rgb[2] / 255
        self.hsv = colorsys.rgb_to_hsv(self.r, self.g, self.b)
        self.h, self.s, self.v = self.hsv

        shadow_h = (self.h - 0.03) % 1
        light_h = (self.h + 0.03) % 1
        shadow_v = max(self.v - 0.1, 0)
        light_v = min(self.v + 0.15, 1)

        self.shadow = '#%02x%02x%02x' % self.normalize_rgb(
            colorsys.hsv_to_rgb(shadow_h, self.s, shadow_v))
        self.light = '#%02x%02x%02x' % self.normalize_rgb(
            colorsys.hsv_to_rgb(light_h, self.s, light_v))

    def normalize_rgb(self, rgb):
        return tuple(int(c * 255) for c in rgb)


def generate_rank_old_badge(text, colour, scale=4):
    if colour[0] != '#':
        colour = '#' + colour
    match = re.search(r'^#(?:[0-9a-fA-F]{3}){1,2}$', colour)
    if not match:
        return False
    color = Color(colour)
    img_width = len(text) * 12 + 8
    img = Image.new('RGBA', (img_width, 18), (255, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font = ImageFont.truetype('images/profile/5x5.ttf', 20)

    draw.rectangle([(2, 2), (img.width - 3, img.height - 3)], fill=color.hex)
    draw.rectangle([(2, img.height - 2), (img.width - 3, img.height)], fill=color.shadow)
    draw.rectangle([(img.width - 2, 2), (img.width, img.height - 3)], fill=color.shadow)
    draw.rectangle([(2, 0), (img.width - 3, 1)], fill=color.light)
    draw.rectangle([(0, 2), (1, img.height - 3)], fill=color.light)
    draw.text((img.width / 2 + 2, img.height / 2 - 2), text, font=font, anchor="mm", fill=color.text_shadow)
    draw.text((img.width / 2, img.height / 2 - 2), text, font=font, anchor="mm")

    textshade = Image.new("RGBA", (img.width, 4), (255, 0, 0, 0))
    shadedraw = ImageDraw.Draw(textshade)
    shadedraw.text((img.width / 2, textshade.height), text, font=font, anchor="mb", fill=color.shade)

    img.paste(textshade, (0, 10), textshade)

    img = img.resize((img.width * scale, img.height * scale), resample=Image.Resampling.NEAREST)

    return img


def generate_rank_badge(text, colour, scale=4):
    if colour[0] != '#':
        colour = '#' + colour
    match = re.search(r'^#(?:[0-9a-fA-F]{3}){1,2}$', colour)
    if not match:
        return False
    color = Color(colour)
    img_width = len(text) * 12 + 12
    img = Image.new('RGBA', (img_width, 18), (255, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font = ImageFont.truetype('images/profile/5x5.ttf', 20)

    draw.rectangle([(0, 1), (img.width, 14)], fill=color.light)
    draw.rectangle([(2, 3), (img.width - 3, 12)], fill=color.hex)
    draw.rectangle([(0, 11), (3, 14)], fill=color.shadow)
    draw.rectangle([(img.width - 4, 11), (img.width, 14)], fill=color.shadow)
    draw.rectangle([(2, 15), (img.width - 3, 16)], fill=color.shadow)
    draw.rectangle([(2, 11), (3, 12)], fill=color.light)
    draw.rectangle([(img.width - 4, 11), (img.width - 3, 12)], fill=color.light)
    draw.text((img.width / 2 + 2, img.height / 2 - 3), text, font=font, anchor="mm", fill=color.shadow)
    draw.text((img.width / 2, img.height / 2 - 3), text, font=font, anchor="mm")

    # textshade = Image.new("RGBA", (img.width, 4), (255, 0, 0, 0))
    # shadedraw = ImageDraw.Draw(textshade)
    # shadedraw.text((img.width / 2, textshade.height), text, font=font, anchor="mb", fill=color.shade)

    # img.paste(textshade, (0, 10), textshade)

    img = img.resize((img.width * scale, img.height * scale), resample=Image.Resampling.NEAREST)

    return img


def get_guild_badge_colors_with_text(colour):
    if colour[0] != '#':
        colour = '#' + colour
    match = re.search(r'^#(?:[0-9a-fA-F]{3}){1,2}$', colour)
    if not match:
        return False
    r, g, b = [int(colour[i:i+2], 16)/255 for i in (1, 3, 5)]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)

    # light and shadow tones
    light_s = max(s - 0.20, 0)
    light_v = min(v + 0.09, 1)
    shadow_s = min(s + 0.15, 1)
    shadow_v = max(v - 0.15, 0)

    light_rgb = colorsys.hsv_to_rgb(h, light_s, light_v)
    shadow_rgb = colorsys.hsv_to_rgb(h, shadow_s, shadow_v)

    light_hex = '#%02x%02x%02x' % tuple(int(c * 255) for c in light_rgb)
    shadow_hex = '#%02x%02x%02x' % tuple(int(c * 255) for c in shadow_rgb)

    # calculate brightness of base color to decide text color
    brightness = 0.299 * r + 0.587 * g + 0.114 * b
    text_color = '#000000' if brightness > 0.5 else '#ffffff'

    return light_hex, shadow_hex, text_color


def generate_badge(text, base_color, scale=3):
    if not base_color.startswith("#"):
        base_color = "#" + base_color

    if not re.fullmatch(r'^#(?:[0-9a-fA-F]{3}){1,2}$', base_color):
        return False

    light, shadow, text_color = get_guild_badge_colors_with_text(base_color)

    font = ImageFont.truetype('images/profile/5x5.ttf', 20)

    img_height = 18
    text_bbox = font.getbbox(text)
    text_width = text_bbox[2] - text_bbox[0]
    img_width = text_width + 24

    img = Image.new('RGBA', (img_width, img_height), (255, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 1), (img.width, 14)], fill=light)
    draw.rectangle([(2, 3), (img.width - 3, 12)], fill=base_color)
    draw.rectangle([(0, 11), (3, 14)], fill=shadow)
    draw.rectangle([(img.width - 4, 11), (img.width, 14)], fill=shadow)
    draw.rectangle([(2, 15), (img.width - 3, 16)], fill=shadow)
    draw.rectangle([(2, 11), (3, 12)], fill=light)
    draw.rectangle([(img.width - 4, 11), (img.width - 3, 12)], fill=light)

    left_margin = 14
    text_y = img.height / 2 - 3
    draw.text((left_margin + 2, text_y), text, font=font, anchor="lm", fill=shadow)
    draw.text((left_margin, text_y), text, font=font, anchor="lm", fill=text_color)

    img = img.resize((img.width * scale, img.height * scale), resample=Image.Resampling.NEAREST)
    return img


def vertical_gradient(width=900, height=1180, main_color='#66ccff', secondary_color=False, reverse=False):
    if main_color[0] != '#':
        main_color = '#' + main_color
    match = re.search(r'^#(?:[0-9a-fA-F]{3}){1,2}$', main_color)
    if not match:
        return False
    if secondary_color is not False:
        if secondary_color[0] != '#':
            secondary_color = '#' + secondary_color
        match = re.search(r'^#(?:[0-9a-fA-F]{3}){1,2}$', secondary_color)
        if not match:
            return False
        top_color = ImageColor.getrgb(main_color)
        bottom_color = ImageColor.getrgb(secondary_color)
    else:
        color = Color(main_color)
        if not reverse:
            top_color = ImageColor.getrgb(color.light)
            bottom_color = ImageColor.getrgb(color.shadow)
        else:
            top_color = ImageColor.getrgb(color.shadow)
            bottom_color = ImageColor.getrgb(color.light)

    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    for y in range(height):
        ratio = y / height
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        for x in range(width):
            img.putpixel((x, y), (r, g, b))
    return img


def round_corners(img, radius=25):
    img = img.convert("RGBA")
    rounded_mask = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(rounded_mask)
    draw.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)
    img.putalpha(rounded_mask)
    return img


def generate_banner(guild, scale, style=''):
    url = f"https://api.wynncraft.com/v3/guild/{urlify(guild)}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return Image.open(f'images/banner{style}/base.png')

    if data['banner']:
        banner = data['banner']

        bg = Image.open(f'images/banner{style}/base.png')

        w, h = bg.size

        crop_x1 = w / 64
        crop_y1 = h / 64
        crop_x2 = (w / 2) - (11 * crop_x1)
        crop_y2 = h - (24 * crop_y1)

        bg = bg.crop((crop_x1, crop_y1, crop_x2, crop_y2))

        bg = bg.convert("L")

        bg = ImageOps.colorize(bg, "black", minecraft_banner_colors[banner['base']])

        for layer in banner['layers']:
            if w == 64:
                mask = Image.open(f'images/banner/{layer["pattern"].lower()}.png')
                mask = mask.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            pattern = Image.open(f'images/banner{style}/{layer["pattern"].lower()}.png')
            pattern = pattern.crop((crop_x1, crop_y1, crop_x2, crop_y2))
            pattern = pattern.convert("RGBA")

            pattern2 = pattern.convert("L")

            pattern2 = ImageOps.colorize(pattern2, "black", minecraft_banner_colors[layer['colour']])

            bg.paste(pattern2, (0, 0), mask)

    else:
        bg = Image.open(f'images/banner{style}/base.png')

        w, h = bg.size

        crop_x1 = w / 64
        crop_y1 = h / 64
        crop_x2 = (w / 2) - (11 * crop_x1)
        crop_y2 = h - (24 * crop_y1)

        bg = bg.crop((crop_x1, crop_y1, crop_x2, crop_y2))

    bg = bg.resize((bg.width * scale, bg.height * scale), Image.NEAREST)

    return bg


def update_items():
    ITEMS = []

    req = requests.get('https://api.wynncraft.com/v3/item/database?fullResult')
    items = req.json()

    for item in items:
        if item != 'default':
            ITEMS.append(item)

    with open('custom_items.json', 'r') as f:
        custom_items = json.load(f)
        for custom_item in custom_items:
            ITEMS.append(custom_item['name'])
            # items.append(custom_item)
        f.close()

    with open('items.json', 'w') as f:
        json.dump({"items": ITEMS}, f)
        f.close()
    with open('items_data.json', 'w') as f:
        json.dump({"items": items}, f)
        f.close()


def addLine(text, draw, font, x, y, drop_x=2, drop_y=2, anchor=None):
    if text[0] != '&':
        text = f'&f{text}'

    strlist = re.findall('&[^&]+', text)

    for word in strlist:
        if word[1] == '#':
            _, _, w, h = draw.textbbox((0, 0), word[8::], font=font)
            draw.text((x + drop_x, y + drop_y), word[8::], font=font, fill=darken_color(word[1:8], 0.72), anchor=anchor)
            draw.text((x, y), word[8::], font=font, fill=word[1:8], anchor=anchor)
        else:
            _, _, w, h = draw.textbbox((0, 0), word[2::], font=font)
            draw.text((x + drop_x, y + drop_y), word[2::], font=font, fill=shadows[word[1]], anchor=anchor)
            draw.text((x, y), word[2::], font=font, fill=colours[word[1]], anchor=anchor)
        x += w
    return x


def interpolate_color(start_color, end_color, percentage):
    start_r, start_g, start_b = start_color
    end_r, end_g, end_b = end_color

    r = int(start_r + (end_r - start_r) * percentage)
    g = int(start_g + (end_g - start_g) * percentage)
    b = int(start_b + (end_b - start_b) * percentage)

    return '#{:02x}{:02x}{:02x}'.format(r, g, b)


def get_transition_color(percentage):
    if percentage <= 0:
        return '#FF5555'
    elif percentage < 70:
        start_color = (255, 85, 85)  # #FF5555
        end_color = (255, 255, 85)  # #FFFF55
        normalized_percentage = (percentage - 0) / (70 - 0)
    elif percentage < 90:
        start_color = (255, 255, 85)  # #FFFF55
        end_color = (85, 255, 85)  # #55FF55
        normalized_percentage = (percentage - 70) / (90 - 70)
    elif percentage < 100:
        start_color = (85, 255, 85)  # #55FF55
        end_color = (85, 255, 255)  # #55FFFF
        normalized_percentage = (percentage - 90) / (100 - 90)
    else:
        return '#55FFFF'

    return interpolate_color(start_color, end_color, normalized_percentage)


def darken_color(color, factor):
    r, g, b = int(color[1:3], 21), int(color[3:5], 21), int(color[5:7], 21)

    r = max(0, int(r * (1 - factor)))
    g = max(0, int(g * (1 - factor)))
    b = max(0, int(b * (1 - factor)))

    hex_color = '#{:02x}{:02x}{:02x}'.format(r, g, b)

    return hex_color


def split_sentence(sentence, length=28):
    words = sentence.split()
    chunks = []
    current_chunk = ""

    for word in words:
        if len(current_chunk) + len(word) <= length:
            current_chunk += " " + word if current_chunk else word
        else:
            chunks.append(current_chunk)
            current_chunk = word

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def expand_image(image, border=(0, 0, 0, 21), fill='#100010e2'):
    img = ImageOps.expand(image, border=border, fill=fill)
    d = ImageDraw.Draw(img)
    d.fontmode = '1'

    return img, d


def format_number(num):
    num = float('{:.3g}'.format(num))
    magnitude = 0
    while abs(num) >= 1000:
        magnitude += 1
        num /= 1000.0
    return '{}{}'.format('{:f}'.format(num).rstrip('0').rstrip('.'), ['', 'K', 'M', 'B', 'T'][magnitude])


def generate_applicant_info(pdata):
    img = Image.new('RGBA', (750, 230), color='#100010e2')
    d = ImageDraw.Draw(img)
    d.fontmode = '1'
    titleFont = ImageFont.truetype('images/profile/5x5.ttf', 30)
    gameFont = ImageFont.truetype('images/profile/game.ttf', 38)

    # skin
    try:
        headers = {'User-Agent': os.getenv("visage_UA")}
        url = f"https://visage.surgeplay.com/bust/190/{pdata.UUID}"
        response = requests.get(url, headers=headers)
        skin = Image.open(BytesIO(response.content))
    except:
        skin = Image.open('images/profile/X-Steve.webp')
    img.paste(skin, (20, 20), skin)

    addLine(f'&{pdata.tag_color}{pdata.username}', d, gameFont, 220, 30)
    d.text((220, 70), 'Playtime', font=titleFont, fill='#fad51e')
    addLine(f'&f{int(pdata.playtime)} hrs', d, gameFont, 220, 100)

    d.text((500, 70), 'Total level', font=titleFont, fill='#fad51e')
    addLine(f'&f{int(pdata.total_level)}', d, gameFont, 500, 100)

    d.text((220, 140), 'Total Wars', font=titleFont, fill='#fad51e')
    addLine(f'&f{int(pdata.wars)}', d, gameFont, 220, 170)

    d.text((500, 140), 'Quests done', font=titleFont, fill='#fad51e')
    addLine(f'&f{int(pdata.completed_quests)}', d, gameFont, 500, 170)

    d.rectangle((4, 4, img.width - 6, img.height - 6), outline='#240059', width=4)
    d.rectangle((0, 0, 2, 2), fill='#00000000')
    d.rectangle((img.width - 4, 0, img.width, 1), fill='#00000000')
    d.rectangle((0, img.height - 4, 2, img.height), fill='#00000000')
    d.rectangle((img.width - 4, img.height - 4, img.width, img.height), fill='#00000000')

    return img


def create_progress_bar(width, percentage, color='#2167dd', scale=1):
    img = Image.new('RGBA', (400, 10), color='#00000000')
    progbar = colorize(Image.open('images/profile/progressbar.png'), color)

    pbar_bg = progbar.crop((0, 0, 400, 10))
    img.paste(pbar_bg, (0, 0), pbar_bg)

    pbar = progbar.crop((0, 10, math.floor(4 * percentage), 20))
    img.paste(pbar, (0,0), pbar)

    img = img.resize((int(img.width * (width/img.width)), img.height), resample=Image.Resampling.NEAREST)
    img = img.resize((img.width * scale, img.height * scale), resample=Image.Resampling.NEAREST)

    return img


def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + (" " if current_line else "") + word
        if draw.textlength(test_line, font=font) <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return "\n".join(lines)


def get_multiline_text_size(text, font, spacing=0):
    lines = text.split('\n')
    widths = [font.getlength(line) for line in lines]
    max_width = max(widths) if widths else 0
    line_height = font.getbbox("Hg")[3] + 2
    total_height = len(lines) * line_height + spacing * (len(lines) - 1)
    return (int(max_width), total_height)


def colorize(img, rgb_color):
    if rgb_color[0] != '#':
        rgb_color = '#' + rgb_color
    match = re.search(r'^#(?:[0-9a-fA-F]{3}){1,2}$', rgb_color)
    if not match:
        return False
    r, g, b = [int(rgb_color[i:i+2], 16)/255 for i in (1, 3, 5)]

    img = img.convert("RGBA")  # Ensure alpha channel
    alpha = img.getchannel("A")

    # Convert to grayscale for lightness
    gray = img.convert("L")

    # Prepare output image
    output = Image.new("RGBA", img.size)
    output_pixels = output.load()

    # Extract hue and saturation from the target RGB color
    hue, _, sat = colorsys.rgb_to_hls(r, g, b)

    for y in range(img.height):
        for x in range(img.width):
            l = gray.getpixel((x, y)) / 255.0
            r_c, g_c, b_c = colorsys.hls_to_rgb(hue, l, sat)
            a = alpha.getpixel((x, y))
            output_pixels[x, y] = (int(r_c * 255), int(g_c * 255), int(b_c * 255), a)

    return output
