import json
import math
import time
import datetime
from io import BytesIO
from dateutil import parser

import discord
from discord.ext import commands, pages
from discord.commands import slash_command, Option
from PIL import Image, ImageFont, ImageDraw

from Helpers.classes import PlaceTemplate, Page, Guild
from Helpers.database import DB
from Helpers.functions import date_diff, isInCurrDay, expand_image, addLine, generate_rank_badge
from Helpers.variables import rank_map as RANK_STARS_MAP, discord_ranks, guilds


def _load_json(path: str, default):
    """
    Safely load JSON from the given file path.
    Returns default if file is missing or invalid.
    """
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return default


def _load_discord_ranks():
    """
    Query the database for uuid-to-rank mappings.
    """
    db = DB()
    db.connect()
    db.cursor.execute("SELECT uuid, rank FROM discord_links")
    mapping = {u: r for u, r in db.cursor.fetchall()}
    db.close()
    return mapping


def _text_width(text: str, font: ImageFont.FreeTypeFont) -> float:
    """
    Calculate pixel width of text for PIL, with fallback.
    """
    try:
        return font.getlength(text)
    except Exception:
        return len(text) * 9  # approximate fallback


def _clip_chars(text: str, max_chars: int) -> str:
    """
    Clip text to at most max_chars characters, adding '...' if truncated.
    """
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + '...'


class Activity(commands.Cog):
    """
    Cog for generating and sending an activity leaderboard as paginated images.
    """

    def __init__(self, client: commands.Bot):
        self.client = client

    def _make_activity_pages(self, playerdata: list, order_by: str, days: int) -> pages.Paginator:
        """
        Create image pages for the leaderboard.
        """
        # Load background templates
        bg_templates = {
            'first': PlaceTemplate('images/profile/first.png'),
            'second': PlaceTemplate('images/profile/second.png'),
            'third': PlaceTemplate('images/profile/third.png'),
            'warn': PlaceTemplate('images/profile/warning.png'),
            'other': PlaceTemplate('images/profile/other.png'),
        }

        # Load fonts and static images
        rank_star = Image.open('images/profile/rank_star.png')
        game_font = ImageFont.truetype('images/profile/game.ttf', 19)
        legend_font = ImageFont.truetype('images/profile/5x5.ttf', 20)
        bg_layout = Image.open('images/profile/leaderboard_bg.png')

        # Icon and header maps
        icon_map = {
            'Playtime': Image.open('images/profile/playtime.png'),
            'Inactivity': Image.open('images/profile/inactive.png'),
            'Kick Suitability': Image.open('images/profile/event_team.png'),
        }
        title_map = {
            'Playtime': 'images/profile/playtime_title.png',
            'Inactivity': 'images/profile/inactivity_title.png',
            'Kick Suitability': 'images/profile/kick_title.png',
        }
        # Thumbnail icons and load header
        for icon in icon_map.values():
            icon.thumbnail((16, 16))
        header_img = Image.open(title_map[order_by])

        # Pagination setup
        pages_list = []
        items_per_page = 15
        total_items = len(playerdata)
        total_pages = max(1, math.ceil(total_items / items_per_page))

        # Pre-calculate widths
        star_slot = 12
        max_stars = 5
        star_block_width = star_slot * max_stars
        RANK_MAX_CHARS = len('Hammerhead')
        rank_block_width = int(_text_width('Hammerhead', game_font))
        NAME_MAX_CHARS = 16
        name_block_width = int(_text_width('W' * NAME_MAX_CHARS, game_font))
        # Stats offsets
        PLAY_OFFSET = 10
           # clamp playtime to 7 chars
        INACT_OFFSET = 130   # shifted right to avoid overlap of playtime and inactivity   # shifted right to avoid overlap
        MEMBER_OFFSET = 160  # kept from prior adjustment  # moved right to avoid overlap

        # Generate each page
        for page_idx in range(total_pages):
            # Start with an empty transparent image, we'll expand it as we draw rows
            canvas = Image.new('RGBA', (980, 0), (0, 0, 0, 0))
            draw = ImageDraw.Draw(canvas)
            draw.fontmode = '1'

            start = page_idx * items_per_page
            end = start + items_per_page
            entries = playerdata[start:end]

            for row_idx, player in enumerate(entries, start=1):
                canvas, draw = expand_image(canvas, border=(0, 0, 0, 36), fill=(0, 0, 0, 0))
                # Choose background by rank or warning
                if order_by == 'Kick Suitability':
                    tmpl = bg_templates['warn'] if player['score'] >= -1 else bg_templates['other']
                else:
                    rank_idx = row_idx if row_idx <= 3 else None
                    tmpl = bg_templates['first' if rank_idx == 1 else 'second' if rank_idx == 2 else 'third' if rank_idx == 3 else 'other']
                tmpl.add(canvas, 930, (0, row_idx * 36 - 33))

                base_y = row_idx * 36 - 33
                text_y = row_idx * 36 - 27

                # 1) Position number
                addLine(f'&f{start + row_idx}.', draw, game_font, 10, text_y)
                canvas.paste(tmpl.divider, (55, base_y), tmpl.divider)

                # 2) Stars (fixed block)
                stars_raw = RANK_STARS_MAP.get((player.get('game_rank') or '').lower(), '')
                star_count = stars_raw if isinstance(stars_raw, int) else (stars_raw.count('*') if isinstance(stars_raw, str) else 0)
                star_count = max(0, min(max_stars, star_count))
                for i in range(star_count):
                    canvas.paste(rank_star, (65 + i * star_slot, base_y + 11), rank_star)
                after_stars = 65 + star_block_width + 5
                canvas.paste(tmpl.divider, (after_stars, base_y), tmpl.divider)

                # 3) Discord rank (fixed width)
                dr = _clip_chars(player.get('discord_rank') or '', RANK_MAX_CHARS)
                dr_x = after_stars + 8
                addLine(f'&f{dr}', draw, game_font, dr_x, text_y)
                after_dr = dr_x + rank_block_width + 8
                canvas.paste(tmpl.divider, (after_dr, base_y), tmpl.divider)

                # 4) Player name (fixed width)
                pname = player['name'][:NAME_MAX_CHARS]
                name_x = after_dr + 10
                addLine(f'&f{pname}', draw, game_font, name_x, text_y)
                name_div = name_x + name_block_width + 8

                # 5) Playtime
                play_x = name_div + 10
                canvas.paste(icon_map['Playtime'], (play_x + PLAY_OFFSET, base_y + 11), icon_map['Playtime'])
                hrs = int(player.get('playtime', 0))
                
                play_text = f"{hrs} hr{'s' if hrs != 1 else ''}"
                
                addLine(f"&f{play_text}", draw, game_font, play_x + 36, text_y)
                canvas.paste(tmpl.divider, (play_x, base_y), tmpl.divider)

                # 6) Inactivity
                inact_x = play_x + INACT_OFFSET
                canvas.paste(icon_map['Inactivity'], (inact_x + PLAY_OFFSET, base_y + 11), icon_map['Inactivity'])
                # clamp and clip to max 9 chars
                days_inactive = max(0, player.get('last_join', 0))
                days_text = str(days_inactive) + ' day' + ('s' if days_inactive != 1 else '')
                days_text = days_text[:9]
                addLine(f'&f{days_text}', draw, game_font, inact_x + 36, text_y)
                canvas.paste(tmpl.divider, (inact_x, base_y), tmpl.divider)

                # 7) Member for
                mem_x = inact_x + MEMBER_OFFSET
                canvas.paste(icon_map['Kick Suitability'], (mem_x + PLAY_OFFSET, base_y + 11), icon_map['Kick Suitability'])
                days_mem = player.get('member_for', 0)
                addLine(f"&f{days_mem} day{'s' if days_mem != 1 else ''}", draw, game_font, mem_x + 36, text_y)
                canvas.paste(tmpl.divider, (mem_x, base_y), tmpl.divider)

            # Add header and legend
            canvas, draw = expand_image(canvas, border=(0, 120, 0, 20), fill=(0, 0, 0, 0))
            canvas.paste(header_img, ((canvas.width - header_img.width) // 2, 10), header_img)
            badge = generate_rank_badge(f"{days} days", "#0477c9", scale=1)
            canvas.paste(badge, ((canvas.width - badge.width) // 2, 98), badge)

            # Legend icons
            canvas.paste(icon_map['Playtime'], (10, canvas.height - 18), icon_map['Playtime'])
            draw.text((36, canvas.height - 23), "Playtime", font=legend_font)
            canvas.paste(icon_map['Inactivity'], (160, canvas.height - 18), icon_map['Inactivity'])
            draw.text((186, canvas.height - 23), "Inactivity", font=legend_font)
            canvas.paste(icon_map['Kick Suitability'], (330, canvas.height - 18), icon_map['Kick Suitability'])
            draw.text((356, canvas.height - 23), "Member for", font=legend_font)

            # Composite background + content
            final_img = Image.new('RGBA', (canvas.width, canvas.height), (0, 0, 0, 0))
            final_img.paste(bg_layout, ((canvas.width - bg_layout.width) // 2, (canvas.height - bg_layout.height) // 2))
            final_img.paste(canvas, (0, 0), canvas)

            # Save to buffer
            buffer = BytesIO()
            final_img.save(buffer, format='PNG')
            buffer.seek(0)
            file = discord.File(buffer, filename=f"activity_{int(time.time())}_{page_idx}.png")
            pages_list.append(Page(content='', files=[file]))

        # Paginator setup
        paginator = pages.Paginator(pages=pages_list)
        paginator.add_button(pages.PaginatorButton("first", emoji="<:first_arrows:1198703152204103760>", style=discord.ButtonStyle.blurple))
        paginator.add_button(pages.PaginatorButton("prev", emoji="<:left_arrow:1198703157501509682>", style=discord.ButtonStyle.red))
        paginator.add_button(pages.PaginatorButton("next", emoji="<:right_arrow:1198703156088021112>", style=discord.ButtonStyle.green))
        paginator.add_button(pages.PaginatorButton("last", emoji="<:last_arrows:1198703153726627880>", style=discord.ButtonStyle.blurple))
        return paginator

    @slash_command(
        description='Displays activity of members',
        guild_ids=guilds
    )
    async def activity(
        self,
        ctx: discord.ApplicationContext,
        order_by: Option(str, "Which metric to sort by", choices=['Playtime', 'Inactivity', 'Kick Suitability']),
        days: Option(int, "How many days to look back", min_value=1, max_value=30, default=7)
    ):  
        """
        Slash command entrypoint. Loads data, sorts, and invokes paginator.
        """
        await ctx.interaction.response.defer()

        # Load rank overrides and activity snapshots
        uuid_to_rank = _load_discord_ranks()
        history = _load_json('player_activity.json', [])
        current = _load_json('current_activity.json', {})
        current_members = current.get('members', []) if isinstance(current, dict) else []

        # Fetch guild join data
        taq_members = Guild('The Aquarium').all_members

        # Helper to get history snapshot
        def _get_snapshot(day_index):
            idx = max(0, min(len(history) - 1, day_index - 1))
            return history[idx].get('members', []) if history else []

        playerdata = []
        now_dt = datetime.datetime.utcnow()

        # Build player records
        for member in current_members:
            if not isinstance(member, dict):
                continue

            uuid = member.get('uuid')
            last_join_iso = member['lastJoin']
            try:
                days_since = date_diff(parser.isoparse(last_join_iso))
            except Exception:
                days_since = 9999
            days_since = max(0, days_since)

            playtime = member.get('playtime', 0) or 0
            look_back = days
            if history:
                while look_back > 1 and not isInCurrDay(_get_snapshot(look_back), uuid):
                    look_back -= 1
            prev_hist = _get_snapshot(look_back)
            prev_entry = next((p for p in prev_hist if p.get('uuid') == uuid), {})
            real_pt = max(0, playtime - prev_entry.get('playtime', 0))

            # Calculate guild membership duration
            joined = next((p for p in taq_members if p.get('uuid') == uuid), {})
            try:
                joined_dt = parser.isoparse(joined.get('joined'))
                if joined_dt.tzinfo:
                    joined_dt = joined_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                member_for = max(0, (now_dt - joined_dt).days)
            except Exception:
                member_for = 0

            # Determine ranks and score
            discord_rank = uuid_to_rank.get(uuid, member.get('rank', 'unknown'))
            raw_stars = RANK_STARS_MAP.get((discord_rank or '').lower(), '')
            star_count = raw_stars if isinstance(raw_stars, int) else (raw_stars.count('*') if isinstance(raw_stars, str) else 0)
            score = (days_since * 1.4) - (look_back * real_pt) * 1.3 - (member_for / 20) - (star_count * 1.2)

            if order_by != 'Kick Suitability' or member_for >= 7:
                playerdata.append({
                    'uuid': uuid,
                    'name': member.get('name', 'Unknown'),
                    'playtime': real_pt,
                    'last_join': days_since,
                    'member_for': member_for,
                    'score': score,
                    'game_rank': joined.get('rank', member.get('rank')),
                    'discord_rank': discord_rank,
                })

        # Sort and paginate
        sort_keys = {'Playtime': 'playtime', 'Inactivity': 'last_join', 'Kick Suitability': 'score'}
        playerdata.sort(key=lambda x: x[sort_keys[order_by]], reverse=True)
        paginator = self._make_activity_pages(playerdata, order_by, days)
        await paginator.respond(ctx.interaction, ephemeral=False)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client: commands.Bot):
    client.add_cog(Activity(client))
