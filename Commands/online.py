# online.py

import time
import os
from io import BytesIO

import discord
from PIL import Image, ImageDraw, ImageFont, ImageColor
from discord.ext import commands
from discord.commands import slash_command

from Helpers.classes import Guild
from Helpers.functions import (
    addLine, generate_banner, expand_image, get_guild_color,
    create_progress_bar, vertical_gradient, get_rank_stars,
    generate_badge
)
from Helpers.variables import ALL_GUILD_IDS


class Online(commands.Cog):
    def __init__(self, client):
        self.client = client

        # Cache directory for banners
        self.cache_dir = os.path.join(os.getcwd(), 'cache', 'banners')
        os.makedirs(self.cache_dir, exist_ok=True)

        # Fonts (2x sizes)
        self.font_game_18 = ImageFont.truetype('images/profile/game.ttf', 36)
        self.font_game_24 = ImageFont.truetype('images/profile/game.ttf', 48)
        self.font_game_36 = ImageFont.truetype('images/profile/game.ttf', 72)
        self.font_5x5_16 = ImageFont.truetype('images/profile/5x5.ttf', 32)
        self.font_5x5_20 = ImageFont.truetype('images/profile/5x5.ttf', 40)
        self.font_5x5_24 = ImageFont.truetype('images/profile/5x5.ttf', 48)

        # Icons (load and 2x)
        def _load_rgba(path: str):
            return Image.open(path).convert("RGBA")

        def _2x(img: Image.Image) -> Image.Image:
            return img.resize((img.width * 2, img.height * 2), Image.Resampling.NEAREST)

        self.player_icon_online = _2x(_load_rgba('images/profile/player_icon_online.png'))
        self.player_icon_offline = _2x(_load_rgba('images/profile/player_icon_offline.png'))
        self.crown_icon = _2x(_load_rgba('images/profile/crown.png'))
        self.flag_icon = _2x(_load_rgba('images/profile/flag.png'))
        self.shield_icon = _2x(_load_rgba('images/profile/shield.png'))
        self.wars_icon = _2x(_load_rgba('images/profile/wars_new.png'))
        self.rank_star = _2x(_load_rgba('images/profile/rank_star.png'))

    # --------- drawing helpers ----------
    @staticmethod
    def _pill(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, r=20, fill=(26, 26, 29, 200)):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=r, fill=fill)

    @staticmethod
    def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont):
        bbox = draw.textbbox((0, 0), text, font=font)
        if not bbox:
            return 0, 0
        return max(0, bbox[2] - bbox[0]), max(0, bbox[3] - bbox[1])
    # ------------------------------------

    @slash_command(description='Sends a list of online guild members', guild_ids=ALL_GUILD_IDS)
    async def online(self, ctx: discord.ApplicationContext, guild: discord.Option(str, required=True)):
        await ctx.defer()

        try:
            guild_data = Guild(guild)
        except Exception:
            embed = discord.Embed(
                title=':no_entry: Something went wrong',
                description=f'Wasn\'t able to retrieve data for {guild}.',
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        # ---------- API data only ----------
        members_raw = guild_data.all_members

        owner_member = None
        online_members = []
        for m in members_raw:
            rank = (m.get('rank') or '').lower()
            is_online = bool(m.get('online'))
            if rank == 'owner':
                owner_member = {
                    'name': m.get('name') or 'Unknown',
                    'display_name': m.get('name') or 'Unknown',
                    'rank': 'owner',
                    'online': is_online,
                    'server': m.get('server')
                }
            if is_online:
                online_members.append({
                    'name': m.get('name') or 'Unknown',
                    'display_name': m.get('name') or 'Unknown',
                    'rank': rank,
                    'online': True,
                    'server': m.get('server')
                })

        ranks_order = ['chief', 'strategist', 'captain', 'recruiter', 'recruit']
        player_data = {r: [] for r in ranks_order}
        for m in online_members:
            if m['rank'] in player_data:
                player_data[m['rank']].append(m)

        guild_color = get_guild_color({'banner': guild_data.banner})

        # Base canvas (2x)
        img_width, img_height = 1220, 360
        img = Image.new('RGBA', (img_width, img_height), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        try:
            d.fontmode = '1'
        except Exception:
            pass

        # ---------- HEADER ----------
        safe_name = ''.join(c for c in guild_data.name if c.isalnum() or c in (' ', '_')).rstrip()
        cache_path = os.path.join(self.cache_dir, f"{safe_name}.png")
        if os.path.exists(cache_path):
            banner = Image.open(cache_path).convert('RGBA')
        else:
            banner = generate_banner(guild_data.name, 4, style='2')
            if banner.mode != 'RGBA':
                banner = banner.convert('RGBA')
            banner.save(cache_path)

        # banner at (20,20), size (160x312)
        banner = banner.resize((160, 312), Image.Resampling.NEAREST)
        img.paste(banner, (20, 20))

        # Guild title (top aligned with banner top)
        addLine(f'&f{guild_data.name}', d, self.font_game_36, 200, 20, 9, 9)

        # Guild badge under banner (use scale=2 for higher-res asset)
        guild_badge = generate_badge(text=f"{guild_data.prefix}", base_color=guild_color, scale=2)
        badge_w, badge_h = guild_badge.size
        img.paste(guild_badge, (100 - badge_w // 2, 320), guild_badge)

        # Progress pill (covers shield + bar)
        pill_left, pill_right = 200, 1192
        self._pill(d, pill_left, 100, pill_right, 212, r=24)

        # Shield & level (on top of pill)
        img.paste(self.shield_icon, (208, 114), self.shield_icon)
        addLine(f'&f{guild_data.level}', d, self.font_5x5_24, 224, 120)

        # Progress bar (2x width and scale)
        bar_x, bar_y, bar_w = 300, 140, 440
        progress_bar = create_progress_bar(bar_w, guild_data.xpPercent, color='#1c54b4', scale=2)
        img.paste(progress_bar, (bar_x, bar_y), progress_bar)

        # XP % aligned with the end of the actual (scaled) fill
        actual_bar_w = progress_bar.size[0]            # accounts for scale=2
        actual_bar_h = progress_bar.size[1]

        xp_pct = max(0, min(100, int(guild_data.xpPercent)))
        xp_text = f"{xp_pct}%"
        tw, th = self._text_size(d, xp_text, self.font_5x5_24)

        # end-of-fill position across the drawn bar
        target_center_x = bar_x + (actual_bar_w * xp_pct) // 100

        # keep label fully inside bar bounds
        min_center = bar_x + tw // 2
        max_center = bar_x + actual_bar_w - tw // 2
        center_x = max(min_center, min(target_center_x, max_center))

        # draw a bit below the bar
        xp_y = bar_y + actual_bar_h - 4
        addLine(f'&f{xp_text}', d, self.font_5x5_24, center_x - tw // 2, xp_y)

        # ---------- TOP STATS PILLS (full width with small gaps) ----------
        stats_top, stats_bottom = 232, 284
        gap = 12
        left = pill_left
        right = pill_right
        total_span = right - left           # 992
        inner = total_span - 2 * gap        # 968
        w1 = inner // 3                     # 322
        w2 = inner // 3                     # 322
        # w3 = inner - w1 - w2 = 324 (implied by x2)

        # bounds
        o_x1, o_x2 = left, left + w1
        w_x1, w_x2 = o_x2 + gap, o_x2 + gap + w2
        t_x1, t_x2 = w_x2 + gap, right

        # Online players pill
        self._pill(d, o_x1, stats_top, o_x2, stats_bottom)
        online_count = len(online_members) + (1 if owner_member and owner_member['online'] else 0)
        online_text = f"{online_count}/{guild_data.members['total']}"
        _, text_h = self._text_size(d, online_text, self.font_game_18)
        icon_h = self.player_icon_online.height
        icon_y = stats_top + (stats_bottom - stats_top - icon_h) // 2
        text_y = stats_top + (stats_bottom - stats_top - text_h) // 2
        img.paste(self.player_icon_online, (o_x1 + 12, icon_y), self.player_icon_online)
        addLine(f'&f{online_text}', d, self.font_game_18, o_x1 + 44, text_y - 4)

        # Wars pill
        self._pill(d, w_x1, stats_top, w_x2, stats_bottom)
        wars_text = f"{guild_data.wars}"
        _, wars_h = self._text_size(d, wars_text, self.font_game_18)
        icon_h2 = self.wars_icon.height
        icon_y2 = stats_top + (stats_bottom - stats_top - icon_h2) // 2
        text_y2 = stats_top + (stats_bottom - stats_top - wars_h) // 2
        img.paste(self.wars_icon, (w_x1 + 12, icon_y2), self.wars_icon)
        addLine(f'&f{wars_text}', d, self.font_game_18, w_x1 + 60, text_y2 - 4)

        # Territories pill
        self._pill(d, t_x1, stats_top, t_x2, stats_bottom)
        terr_text = f"{guild_data.territories}"
        _, terr_h = self._text_size(d, terr_text, self.font_game_18)
        icon_h3 = self.flag_icon.height
        icon_y3 = stats_top + (stats_bottom - stats_top - icon_h3) // 2
        text_y3 = stats_top + (stats_bottom - stats_top - terr_h) // 2
        img.paste(self.flag_icon, (t_x1 + 12, icon_y3), self.flag_icon)
        addLine(f'&f{terr_text}', d, self.font_game_18, t_x1 + 60, text_y3 - 4)

        # Owner pill aligned with progress bar left
        self._pill(d, pill_left, 300, pill_left + 660, 352)
        img.paste(self.crown_icon, (pill_left + 12, 310), self.crown_icon)
        if owner_member:
            addLine(f'&f{owner_member["display_name"]}', d, self.font_game_18, pill_left + 64, 308)
            status_icon = self.player_icon_online if owner_member['online'] else self.player_icon_offline
            img.paste(status_icon, (pill_left + 600, 310), status_icon)
        else:
            addLine('&fUnknown', d, self.font_game_18, pill_left + 64, 308)

        # ---------- MEMBERS ----------
        row_h = 60
        row_gap = 16

        for rank in ranks_order:
            if not player_data[rank]:
                continue

            img, d = expand_image(img, border=(0, 0, 0, 60), fill='#00000000')
            try:
                d.fontmode = '1'
            except Exception:
                pass
            header_top = img.height - 48

            star_count = get_rank_stars(rank)
            rank_label = rank.upper() + 'S'
            _, title_h = self._text_size(d, rank_label, self.font_5x5_20)
            star_h = self.rank_star.height
            star_y = header_top + (title_h - star_h) // 2

            for i in range(star_count):
                img.paste(self.rank_star, (40 + i * 30, star_y + 16), self.rank_star)

            # rank titles in yellow (&6)
            addLine(f'&6{rank_label}', d, self.font_5x5_20, 40 + (star_count * 30) + 10, header_top)

            for i, player in enumerate(player_data[rank]):
                if i % 2 == 0:
                    img, d = expand_image(img, border=(0, 0, 0, row_h + row_gap), fill='#00000000')
                    try:
                        d.fontmode = '1'
                    except Exception:
                        pass

                col = i % 2
                row_bottom = img.height - row_gap
                row_top = row_bottom - row_h

                x1, x2 = (32, 600) if col == 0 else (632, 1200)
                self._pill(d, x1, row_top, x2, row_bottom, r=20)

                icon = self.player_icon_online
                icon_h = icon.height
                name = player["display_name"]
                server = player.get("server")
                _, name_h = self._text_size(d, name, self.font_game_18)
                server_text = server if server else None
                server_h = 0 if not server_text else self._text_size(d, server_text, self.font_game_18)[1]

                center_y = (row_top + row_bottom) // 2
                icon_y = center_y - icon_h // 2
                text_y = center_y - name_h // 2 - 1
                server_y = center_y - server_h // 2 - 1

                icon_x = 438 if col == 0 else 1038
                img.paste(icon, (icon_x, icon_y), icon)

                player_x = 42 if col == 0 else 642
                addLine(f'&f{name}', d, self.font_game_18, player_x + 5, text_y, drop_y=1)

                if server_text:
                    server_x = 480 if col == 0 else 1080
                    addLine(f'&f{server_text}', d, self.font_game_18, server_x, server_y, drop_y=1)

        img, d = expand_image(img, border=(0, 0, 0, 40), fill='#00000000')

        # ---------- BACKGROUND (top gradient, bottom solid gray) ----------
        top_h = int(img.height / 2.5)
        gray_hex = '#2C2C30'

        top_grad = vertical_gradient(img.width, top_h, guild_color, gray_hex)
        gray_rgb = ImageColor.getrgb(gray_hex)
        bottom = Image.new('RGBA', (img.width, img.height - top_h), (*gray_rgb, 255))

        final_background = Image.new('RGBA', (img.width, img.height), (0, 0, 0, 0))
        final_background.paste(top_grad, (0, 0))
        final_background.paste(bottom, (0, top_h))

        final_img = Image.new('RGBA', (img.width, img.height), (0, 0, 0, 0))
        final_img.paste(final_background, (0, 0))
        final_img.paste(img, (0, 0), img)

        with BytesIO() as buffer:
            final_img.save(buffer, format="PNG")
            buffer.seek(0)
            await ctx.followup.send(file=discord.File(buffer, f'online_{int(time.time())}.png'))

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Online(client))
