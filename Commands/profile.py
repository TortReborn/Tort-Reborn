import os
import time
from time import perf_counter
from io import BytesIO
import asyncio
import json
import hashlib
from pathlib import Path
from datetime import datetime, timedelta

import discord
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from discord.ext import commands
from discord.commands import slash_command

from Helpers.classes import PlayerStats
from Helpers.functions import (
    pretty_date, generate_rank_badge, generate_banner, getData, format_number,
    addLine, vertical_gradient, round_corners, generate_badge
)
from Helpers.variables import discord_ranks, minecraft_colors, minecraft_banner_colors


# ──────────────────────────────────────────────────────────────────────────────
# Caching infrastructure (disk + small in-process helpers)
# ──────────────────────────────────────────────────────────────────────────────

ROOT = Path(".").resolve()
PROFILE_CACHE = ROOT / "profile_cache"
BANNER_CACHE = PROFILE_CACHE / "banner_cache"
AVATAR_DIR = PROFILE_CACHE / "avatars"
GRADIENT_DIR = PROFILE_CACHE / "gradients"
MASK_DIR = PROFILE_CACHE / "masks"
BACKGROUND_DIR = PROFILE_CACHE / "backgrounds"
BADGE_DIR = PROFILE_CACHE / "badges"
RANK_BADGE_DIR = PROFILE_CACHE / "rank_badges"
GUILD_DATA_DIR = PROFILE_CACHE / "guild_data"

for d in [PROFILE_CACHE, BANNER_CACHE, AVATAR_DIR, GRADIENT_DIR, MASK_DIR, BACKGROUND_DIR, BADGE_DIR, RANK_BADGE_DIR, GUILD_DATA_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# TTLs (tune as desired)
AVATAR_TTL = timedelta(hours=24)
GUILD_DATA_TTL = timedelta(minutes=5)

def _log_step(step_no: int, name: str, t0: float, t_start: float):
    dt = (perf_counter() - t_start) * 1000.0
    total = (perf_counter() - t0) * 1000.0
    print(f"[profile] {step_no:02d} {name} took {dt:.1f} ms (total {total:.1f} ms)")

def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def _open_png(path: Path, mode="RGBA") -> Image.Image:
    # Ensure we don't leak file handles; copy() detaches from file descriptor.
    with Image.open(path) as im:
        return im.convert(mode).copy()

def _save_png(img: Image.Image, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")

def _cache_hit(name: str, path: Path):
    print(f"[cache] HIT {name}: {path}")

def _cache_miss(name: str, path: Path):
    print(f"[cache] MISS {name}: will create {path}")

def _is_fresh(path: Path, ttl: timedelta) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return (datetime.now() - mtime) <= ttl
    except Exception:
        return False


# ── Cached assets builders ────────────────────────────────────────────────────

def get_card_base_cached(tag_color: str) -> Image.Image:
    key = f"cardbase|tag={tag_color}"
    path = GRADIENT_DIR / f"{_sha1(key)}.png"
    if path.exists():
        _cache_hit("card_base", path)
        return _open_png(path)
    _cache_miss("card_base", path)
    # Build then cache
    base = vertical_gradient(main_color=tag_color)
    base = round_corners(base)
    _save_png(base, path)
    return _open_png(path)

def get_card_color_gradient_cached(bg_primary: str, bg_secondary: str) -> Image.Image:
    key = f"cardgrad|w=850|h=1130|p={bg_primary}|s={bg_secondary}"
    path = GRADIENT_DIR / f"{_sha1(key)}.png"
    if path.exists():
        _cache_hit("card_color_gradient", path)
        return _open_png(path)
    _cache_miss("card_color_gradient", path)
    grad = vertical_gradient(width=850, height=1130, main_color=bg_primary, secondary_color=bg_secondary)
    _save_png(grad, path)
    return _open_png(path)

def get_bg_outline_cached(tag_color: str) -> Image.Image:
    key = f"bgoutline|w=818|h=545|tag={tag_color}|rev=1"
    path = GRADIENT_DIR / f"{_sha1(key)}.png"
    if path.exists():
        _cache_hit("bg_outline", path)
        return _open_png(path)
    _cache_miss("bg_outline", path)
    grad = vertical_gradient(width=818, height=545, main_color=tag_color, reverse=True)
    rounded = round_corners(grad)
    _save_png(rounded, path)
    return _open_png(path)

def get_rounded_bg_image_cached(background_id: int) -> Image.Image:
    # Source image lives at images/profile_backgrounds/{id}.png
    src = Path(f"images/profile_backgrounds/{background_id}.png")
    key = f"rounded_bg|src={src}|r=20"
    path = BACKGROUND_DIR / f"{_sha1(key)}.png"
    if path.exists():
        _cache_hit("rounded_background", path)
        return _open_png(path)
    _cache_miss("rounded_background", path)
    bg = _open_png(src)
    rounded = round_corners(bg, radius=20)
    _save_png(rounded, path)
    return _open_png(path)

def get_badge_cached(text: str, base_color: str, scale: int) -> Image.Image:
    key = f"badge|t={text}|c={base_color}|s={scale}"
    path = BADGE_DIR / f"{_sha1(key)}.png"
    if path.exists():
        _cache_hit("badge", path)
        return _open_png(path)
    _cache_miss("badge", path)
    img = generate_badge(text=text, base_color=base_color, scale=scale)
    # Ensure tight bbox like original
    img.crop(img.getbbox())
    _save_png(img, path)
    return _open_png(path)

def get_rank_badge_cached(tag_display: str, tag_color: str) -> Image.Image:
    key = f"rankbadge|t={tag_display}|c={tag_color}"
    path = RANK_BADGE_DIR / f"{_sha1(key)}.png"
    if path.exists():
        _cache_hit("rank_badge", path)
        return _open_png(path)
    _cache_miss("rank_badge", path)
    img = generate_rank_badge(tag_display, tag_color)
    _save_png(img, path)
    return _open_png(path)

def get_banner_cached(guild_name: str) -> Image.Image:
    # Per requirement: banner_cache/<Guild Name>.png
    safe_name = guild_name.replace("/", "／")
    path = BANNER_CACHE / f"{safe_name}.png"
    if path.exists():
        _cache_hit("banner", path)
        return _open_png(path)
    _cache_miss("banner", path)
    img = generate_banner(guild_name, 15, "2")
    _save_png(img, path)
    return _open_png(path)

def get_avatar_cached(uuid: str) -> Image.Image:
    # Cache file
    path = AVATAR_DIR / f"{uuid}.png"
    if path.exists() and _is_fresh(path, AVATAR_TTL):
        _cache_hit("avatar", path)
        im = _open_png(path)
        im.thumbnail((480, 480))
        return im

    _cache_miss("avatar", path)
    try:
        headers = {'User-Agent': os.getenv("visage_UA")}
        url = f"https://visage.surgeplay.com/bust/500/{uuid}"
        response = requests.get(url, headers=headers, timeout=(4, 6))
        response.raise_for_status()
        img = Image.open(BytesIO(response.content)).convert("RGBA")
    except Exception as e:
        print(f"[profile] WARN: avatar fetch failed: {e}")
        img = _open_png(Path('images/profile/x-steve500.png'))
    img.thumbnail((480, 480))
    _save_png(img, path)
    return _open_png(path)

def get_guild_data_cached(guild_name: str) -> dict:
    # Cache JSON payload from getData(); TTL modest (changes relatively slowly).
    safe_name = guild_name.replace("/", "／")
    path = GUILD_DATA_DIR / f"{safe_name}.json"

    if path.exists() and _is_fresh(path, GUILD_DATA_TTL):
        _cache_hit("guild_data", path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass  # fall through to refetch

    _cache_miss("guild_data", path)
    data = getData(guild_name)
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        print(f"[cache] WARN: failed to write guild_data cache: {e}")
    return data


# ──────────────────────────────────────────────────────────────────────────────

class Profile(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(description='Displays a guild profile of guild member')
    async def profile(self, ctx: discord.ApplicationContext, name: discord.Option(str, required=True), days: discord.Option(int, min=1, max=30, default=7)):
        t0 = perf_counter()
        print(f"[profile] ---- START for '{name}' ({days}d) ----")

        # 01 Defer + PlayerStats
        t_start = perf_counter()
        await ctx.defer()
        player = await asyncio.to_thread(PlayerStats, name, days)
        _log_step(1, "Defer + PlayerStats load", t0, t_start)

        if player.error:
            embed = discord.Embed(
                title=':no_entry: Oops! Something did not go as intended.',
                description=f'Could not retrieve information of `{name}`.\nPlease check your spelling or try again later.',
                color=0xe33232
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            print("[profile] ABORT: PlayerStats error")
            return

        # Kick off network-touching work in background (they also benefit from cache hits)
        avatar_task = asyncio.to_thread(get_avatar_cached, player.UUID) if player.UUID else None
        guild_data_task = asyncio.to_thread(get_guild_data_cached, player.guild) if player.guild else None

        # 02 Card base (from cache)
        t_start = perf_counter()
        card = get_card_base_cached(player.tag_color).copy()  # copy so we can draw on it
        draw = ImageDraw.Draw(card)
        _log_step(2, "Card base (edge gradient + round) [cache-backed]", t0, t_start)

        # 03 Card color gradient (from cache)
        t_start = perf_counter()
        if player.background == 2 and player.gradient == ['#293786', '#1d275e']:
            card_color = get_card_color_gradient_cached('#4585db', '#2f2b73')
        else:
            card_color = get_card_color_gradient_cached(player.gradient[0], player.gradient[1])
        _log_step(3, "Card color gradient build [cache-backed]", t0, t_start)

        # 04 Paste card color
        t_start = perf_counter()
        card.paste(card_color, (25, 25), card_color)
        _log_step(4, "Paste card color", t0, t_start)

        # 05 Background outline gradient (from cache) + paste
        t_start = perf_counter()
        bg_outline = get_bg_outline_cached(player.tag_color)
        card.paste(bg_outline, (41, 100), bg_outline)
        _log_step(5, "Background outline gradient + paste [cache-backed]", t0, t_start)

        # 06 Background load (rounded, from cache) + paste
        t_start = perf_counter()
        background = get_rounded_bg_image_cached(player.background)
        card.paste(background, (50, 110), background)
        _log_step(6, "Background load + paste [cache-backed]", t0, t_start)

        # 07 Player name
        t_start = perf_counter()
        name_font = ImageFont.truetype('images/profile/game.ttf', 50)
        addLine(text=player.username, draw=draw, font=name_font, x=50, y=40, drop_x=7, drop_y=7)
        _log_step(7, "Player name font load + draw", t0, t_start)

        # 08 Avatar (await cached/loaded image and paste)
        t_start = perf_counter()
        if avatar_task is not None:
            skin = await avatar_task
        else:
            skin = _open_png(Path('images/profile/x-steve500.png'))
            skin.thumbnail((480, 480))
        card.paste(skin, (200, 156), skin)
        _log_step(8, "Avatar awaited + paste [cache-backed]", t0, t_start)

        # 09 Rank badge (cache)
        t_start = perf_counter()
        rank = get_rank_badge_cached(player.tag_display, player.tag_color)
        rank_w, rank_h = rank.size
        card.paste(rank, (450 - int(rank_w / 2), 96), rank)
        _log_step(9, "Rank badge generate + paste [cache-backed]", t0, t_start)

        # 10 Guild section (await cached getData; cache badges + banner)
        if player.guild:
            t_start = perf_counter()

            # 10a: getData (cache-backed)
            gb = None
            try:
                guild_data = await guild_data_task if guild_data_task else get_guild_data_cached(player.guild)
                gb = guild_data.get('banner') if guild_data else None
            except Exception as e:
                print(f"[profile] WARN: guild getData failed: {e}")
                gb = None

            # derive color
            try:
                if gb:
                    if gb.get('base') in ['BLACK', 'GRAY', 'BROWN']:
                        guild_colour = "WHITE"
                        for layer in gb.get('layers', []):
                            if layer.get('colour') not in ['BLACK', 'GRAY', 'BROWN']:
                                guild_colour = layer.get('colour')
                                break
                    else:
                        guild_colour = gb.get('base', "WHITE")
                else:
                    guild_colour = "WHITE"
            except Exception as e:
                print(f"[profile] WARN: guild color parse failed: {e}")
                guild_colour = "WHITE"

            # 10b compose (cache-backed)
            t10b = perf_counter()

            guild_badge = get_badge_cached(
                text=player.guild,
                base_color='#{:02x}{:02x}{:02x}'.format(*minecraft_banner_colors[guild_colour]),
                scale=3
            )
            card.paste(guild_badge, (108, 615), guild_badge)

            if player.taq and player.linked:
                try:
                    grb = get_badge_cached(text=player.rank.upper(), base_color=discord_ranks[player.rank]['color'], scale=3)
                except Exception:
                    grb = get_badge_cached(text=player.guild_rank.upper(), base_color='#a0aeb0', scale=3)
            else:
                grb = get_badge_cached(text=player.guild_rank.upper(), base_color='#a0aeb0', scale=3)

            mfb = get_badge_cached(text=f'{player.in_guild_for.days} D', base_color='#363636', scale=3)

            grb_w = grb.width
            card.paste(mfb, (90 + grb_w, 667), mfb)
            card.paste(grb, (108, 667), grb)

            banner = get_banner_cached(player.guild)
            banner.thumbnail((157, 157))
            card.paste(banner, (41, 562))
            t10b_ms = (perf_counter() - t10b) * 1000.0
            print(f"[profile] 10b compose badges+banner [cache-backed] took {t10b_ms:.1f} ms")

            _log_step(10, "Guild getData + badges + banner [cache-backed]", t0, t_start)

        # 11 Build entries
        t_start = perf_counter()
        card_entries = {}
        try:
            if player.online:
                card_entries['World'] = player.server
            else:
                card_entries['Last Seen'] = pretty_date(player.last_joined)
            card_entries['Total Level'] = f'{player.total_level}'
            card_entries['Playtime'] = f'{int(player.playtime)} hrs'
            if player.taq and player.in_guild_for.days >= 1:
                card_entries[f'Playtime / {player.stats_days} D'] = f'{int(player.real_pt)} hrs'
            card_entries['Wars'] = str(player.wars)
            if player.taq and player.in_guild_for.days >= 1:
                card_entries[f'Wars / {player.stats_days} D'] = str(player.real_wars)
            if player.guild:
                card_entries['Guild XP'] = format_number(player.guild_contributed)
            if player.taq and player.in_guild_for.days >= 1:
                card_entries[f'Guild XP / {player.stats_days} D'] = format_number(player.real_xp)
            if player.taq:
                card_entries['Guild Raids'] = str(player.guild_raids)
                if player.in_guild_for.days >= 1:
                    card_entries[f'Guild Raids / {player.stats_days} D'] = str(player.real_raids)
            if len(card_entries) < 10:
                card_entries['Chests Looted'] = str(player.chests)
            if len(card_entries) < 10:
                card_entries['Quests'] = str(player.quests)
        except Exception as e:
            print(f"[profile] WARN: entry build failed: {e}")
        entry_keys = list(card_entries.keys())
        _log_step(11, "Entries assembled", t0, t_start)

        # 12 Fonts + box
        t_start = perf_counter()
        title_font = ImageFont.truetype('images/profile/5x5.ttf', 40)
        data_font = ImageFont.truetype('images/profile/game.ttf', 35)
        box = Image.new('RGBA', (390, 75), (0, 0, 0, 0))
        box_draw = ImageDraw.Draw(box)
        box_draw.rounded_rectangle(((0, 0), (390, 75)), fill=(0, 0, 0, 30), radius=10)
        _log_step(12, "Entry fonts load + box prep", t0, t_start)

        # 13 Draw entries
        t_start = perf_counter()
        for entry in range(len(card_entries)):
            card.paste(box, (50 + ((entry % 2) * 410), 730 + (int(entry / 2) * 85)), box)
            draw.text((60 + ((entry % 2) * 410), 720 + (int(entry / 2) * 85)), text=entry_keys[entry], font=title_font, fill='#fad51e')
            draw.text((430 + ((entry % 2) * 410), 765 + (int(entry / 2) * 85)), text=card_entries[entry_keys[entry]], font=data_font, anchor="ra")
        _log_step(13, "Entry boxes rendered", t0, t_start)

        # 14 Shells icon + text
        if player.guild and player.taq and player.in_guild_for.days >= 1:
            t_start = perf_counter()
            data_font2 = ImageFont.truetype('images/profile/game.ttf', 50)
            shells_img = _open_png(Path('images/profile/shells.png'))
            shells_img.thumbnail((50, 50))
            addLine(text=str(player.balance), draw=draw, font=data_font2, x=781, y=46, drop_x=7, drop_y=7, anchor="rt")
            card.paste(shells_img, (800, 40), shells_img)
            _log_step(14, "Shells icon + text", t0, t_start)

        # 15 Unlock checks
        t_start = perf_counter()
        if player.linked:
            try:
                if str(ctx.author.id) == str(player.discord) and player.in_guild_for.days >= 365 and 3 not in player.backgrounds_owned:
                    player.unlock_background('1 Year Anniversary')
                if str(ctx.author.id) == str(player.discord) and player.rank.upper() in ['NARWHAL', 'HYDRA'] and 2 not in player.backgrounds_owned:
                    player.unlock_background('TAq Sea Turtle')
            except Exception as e:
                print(f"[profile] WARN: unlocks failed: {e}")
        _log_step(15, "Unlock checks", t0, t_start)

        # 16 Encode + send (keep the split logs for visibility)
        t16 = perf_counter()
        with BytesIO() as file:
            t16a = perf_counter()
            card.save(file, format="PNG")
            encode_ms = (perf_counter() - t16a) * 1000.0
            print(f"[profile] 16a PNG encode took {encode_ms:.1f} ms")

            file.seek(0)
            tstamp = int(time.time())
            profile_card = discord.File(file, filename=f"profile{tstamp}.png")

            t16b = perf_counter()
            await ctx.followup.send(file=profile_card)
            send_ms = (perf_counter() - t16b) * 1000.0
            print(f"[profile] 16b Discord send took {send_ms:.1f} ms")

        _log_step(16, "PNG encode + send (see 16a/16b)", t0, t16)

        total_ms = (perf_counter() - t0) * 1000.0
        print(f"[profile] ---- DONE for '{name}' in {total_ms:.1f} ms ----")

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Profile(client))
