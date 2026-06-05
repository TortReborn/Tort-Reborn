from datetime import datetime, timezone
import json
import requests
import os

from PIL import Image, ImageOps
from dateutil import parser
import discord
from discord.ui import InputText, Modal

from Helpers.database import (
    DB,
    get_current_guild_data_and_snapshot_count_with_db,
    get_player_activity_baselines_with_db,
)
from Helpers.functions import getPlayerUUID, getPlayerDatav3, getPlayerProfileDatav3, urlify, determine_starting_rank
from discord.ext.pages import Page as _Page

from Helpers.variables import wynn_ranks, WELCOME_CHANNEL_ID, discord_ranks

WELCOME_CHANNEL = WELCOME_CHANNEL_ID

class Guild:

    def __init__(self, guild, token=None):
        if len(guild) <= 4:
            url = f'https://api.wynncraft.com/v3/guild/prefix/{urlify(guild)}'
        else:
            url = f'https://api.wynncraft.com/v3/guild/{urlify(guild)}'

        resp = requests.get(url, timeout=10, headers={"Authorization": f"Bearer {os.getenv(token or 'WYNN_TOKEN')}"})
        resp.raise_for_status()
        guild_data = resp.json()

        self.data = guild_data
        self.name = guild_data['name']
        self.prefix = guild_data['prefix']
        self.level = guild_data['level']
        self.xpPercent = guild_data['xpPercent']
        self.territories = guild_data['territories']
        self.wars = guild_data['wars']
        self.created = guild_data['created']
        self.banner = guild_data.get('banner')

        self.members = guild_data['members']

        self.online = guild_data['online']

        self.all_members = self.get_all_members(self.members)

    def get_all_members(self, members):
        member_list = []
        for rank, group in members.items():
            if rank == 'total':
                continue
            for username, info in group.items():
                member_list.append({
                    'uuid':        info.get('uuid'),
                    'name':        username,
                    'rank':        rank,
                    'contributed': info.get('contributed'),
                    'joined':      info.get('joined'),
                    'online':      info.get('online'),
                    'server':      info.get('server')
                })
        return member_list

class PlayerStats:
    def __init__(self, name, days, load_timed_stats=True):
        db = DB()
        db.connect()
        try:
            pdata = self._load_player_payload(name)
            if self.error:
                return

            self.player_data = pdata
            self._load_player_fields(pdata)
            self._load_guild_fields(pdata)
            self._load_profile_db_state(db)
            if load_timed_stats:
                self._load_timed_stats(db, days)
            else:
                self._set_skipped_timed_defaults(days)
        finally:
            db.close()

    def _load_player_payload(self, name):
        pdata = getPlayerProfileDatav3(name)
        if pdata:
            self.error = False
            self.UUID = pdata['uuid']
            self.username = pdata['username']
            return pdata

        player_data = getPlayerUUID(name)
        if not player_data:
            self.error = True
            return None

        self.error = False
        self.UUID = player_data[1]
        self.username = player_data[0]
        pdata = getPlayerDatav3(self.UUID)
        if not pdata:
            self.error = True
            return None
        return pdata

    def _load_player_fields(self, pdata):
        test_last_joined = pdata.get('lastJoin')
        if test_last_joined:
            self.last_joined = parser.isoparse(test_last_joined)
            self.last_joined_is_private = False
        else:
            self.last_joined = parser.isoparse("2020-03-22T11:11:17.810000Z")
            self.last_joined_is_private = True

        self.characters = pdata.get('characters', {})
        self.online = pdata.get('online', False)
        self.server = pdata.get('server')

        try:
            raw_wars = pdata.get('globalData', {}).get('wars')
            self.wars = raw_wars if raw_wars is not None else 0
            self.wars_is_private = raw_wars is None
        except:
            self.wars = 0
            self.wars_is_private = True

        raw_playtime = pdata.get('playtime')
        self.playtime = raw_playtime if raw_playtime is not None else 0
        self.playtime_is_private = raw_playtime is None

        self.rank = pdata.get('rank', 'Player')
        try:
            raw_chests = pdata.get('globalData', {}).get('chestsFound')
            raw_quests = pdata.get('globalData', {}).get('completedQuests')
            self.chests = raw_chests if raw_chests is not None else 0
            self.quests = raw_quests if raw_quests is not None else 0
            self.chests_is_private = raw_chests is None
            self.quests_is_private = raw_quests is None
        except:
            self.chests = 0
            self.quests = 0
            self.chests_is_private = True
            self.quests_is_private = True

        self.background = 1
        self.backgrounds_owned = []
        self.gradient = ['#293786', '#1d275e']
        if self.rank == 'Player':
            support_rank = pdata.get('supportRank')
            self.tag = support_rank.upper() if support_rank is not None else 'Player'
        else:
            self.tag = self.rank
        self.tag_color = wynn_ranks[self.tag.lower()]['color'] if self.tag != 'Player' else '#66ccff'
        self.tag_display = wynn_ranks[self.tag.lower()]['display'] if self.tag != 'Player' else 'PLAYER'

        raw_total_level = pdata.get('globalData', {}).get('totalLevel')
        self.total_level = raw_total_level if raw_total_level is not None else 0
        self.total_level_is_private = raw_total_level is None

    def _load_guild_fields(self, pdata):
        taq_gdata = Guild('The Aquarium')
        taq_member_by_uuid = {member['uuid']: member for member in taq_gdata.all_members}
        guild_stats = taq_member_by_uuid.get(self.UUID)

        self.taq = guild_stats is not None
        self.guild = 'The Aquarium' if self.taq else pdata.get('guild')
        if self.guild is None:
            self.guild_data = None
            self.guild_rank = None
            self.guild_contributed = None
            self.guild_contributed_is_private = False
            self.guild_joined = None
            self.in_guild_for = None
            return

        self.guild = 'The Aquarium' if self.taq else pdata.get('guild', {}).get('name')
        gdata = taq_gdata if self.taq else Guild(self.guild)
        self.guild_data = gdata.data
        if guild_stats is None:
            for guildee in gdata.all_members:
                if guildee['uuid'] == self.UUID:
                    guild_stats = guildee
                    break
            if guild_stats is None:
                guild_stats = {}

        self.guild_rank = guild_stats.get('rank') if self.taq else pdata.get('guild', {}).get('rank')
        raw_contributed = guild_stats.get('contributed')
        self.guild_contributed = raw_contributed if raw_contributed is not None else 0
        self.guild_contributed_is_private = raw_contributed is None

        joined_at = guild_stats.get('joined')
        self.guild_joined = parser.isoparse(joined_at) if joined_at else self.last_joined
        self.in_guild_for = datetime.now(timezone.utc) - self.guild_joined

    def _load_profile_db_state(self, db):
        self.shells = 0
        self.balance = 0
        self.discord = None
        self.linked = False
        if self.taq:
            self.uncollected_raids = 0
            self.collected_raids = 0
            self.guild_raids = 0

        db.cursor.execute("""
            WITH target(uuid) AS (VALUES (%s::uuid))
            SELECT
                dl.discord_id,
                dl.rank,
                pc.background,
                pc.owned,
                pc.gradient,
                s.shells,
                s.balance,
                ur.uncollected_raids,
                ur.collected_raids
            FROM target t
            LEFT JOIN discord_links dl ON dl.uuid = t.uuid
            LEFT JOIN profile_customization pc ON pc."user" = dl.discord_id
            LEFT JOIN shells s ON s."user" = dl.discord_id
            LEFT JOIN uncollected_raids ur ON ur.uuid = t.uuid
        """, (self.UUID,))
        profile_row = db.cursor.fetchone()
        if not profile_row:
            return

        (
            discord_id,
            discord_rank,
            background,
            owned,
            gradient,
            shells,
            balance,
            uncollected_raids,
            collected_raids,
        ) = profile_row

        self.linked = discord_id is not None
        if self.linked:
            self.discord = discord_id
            self.rank = discord_rank
            if background is not None:
                self.background = background
                self.backgrounds_owned = owned
                self.gradient = gradient if gradient is not None else ['#293786', '#1d275e']
            self.shells = shells or 0
            self.balance = balance or 0

        if self.taq:
            self.uncollected_raids = uncollected_raids or 0
            self.collected_raids = collected_raids or 0
            self.guild_raids = self.uncollected_raids + self.collected_raids

    def _load_timed_stats(self, db, days):
        if not self.taq:
            self._set_non_taq_timed_defaults()
            return

        try:
            cur, num_snaps = get_current_guild_data_and_snapshot_count_with_db(db)
        except Exception:
            cur = {}
            num_snaps = 0
        cur_map = {m['uuid']: m for m in cur.get('members', [])}
        now_entry = cur_map.get(self.UUID)

        if now_entry:
            now_playtime_val = now_entry.get('playtime', self.playtime)
            now_wars_val = now_entry.get('wars', self.wars)
            now_contrib_val = now_entry.get('contributed', self.guild_contributed or 0)
            now_raids_val = now_entry.get('raids', self.guild_raids or 0)
        else:
            now_playtime_val = self.playtime
            now_wars_val = self.wars
            now_contrib_val = self.guild_contributed or 0
            now_raids_val = self.guild_raids or 0

        now_playtime_is_private = now_playtime_val is None
        now_wars_is_private = now_wars_val is None
        now_contrib_is_private = now_contrib_val is None

        now_playtime = int(now_playtime_val) if now_playtime_val is not None else 0
        now_wars = int(now_wars_val) if now_wars_val is not None else 0
        now_contrib = int(now_contrib_val) if now_contrib_val is not None else 0
        now_raids = int(now_raids_val) if now_raids_val is not None else 0

        if days > num_snaps:
            days = num_snaps
        if days > self.in_guild_for.days:
            days = self.in_guild_for.days
        if days < 1:
            days = 1
        self.stats_days = days

        if self.in_guild_for.days < 1:
            self._set_too_new_timed_defaults()
            return

        jd = self.guild_joined.date() if self.guild_joined else None
        baselines = get_player_activity_baselines_with_db(
            db,
            self.UUID,
            ['playtime', 'wars', 'contributed', 'raids'],
            days,
            joined_date=jd
        )
        base_pt, warn_pt = baselines.get('playtime', (0, True))
        base_wars, warn_wars = baselines.get('wars', (0, True))
        base_xp, warn_xp = baselines.get('contributed', (0, True))
        base_raids, warn_raids = baselines.get('raids', (0, True))
        warn_flag = warn_pt or warn_wars or warn_xp or warn_raids

        self.real_pt = max(int(now_playtime) - int(base_pt), 0)
        self.real_xp = max(int(now_contrib) - int(base_xp), 0)
        self.real_wars = max(int(now_wars) - int(base_wars), 0)
        self.real_raids = max(int(now_raids) - int(base_raids), 0)

        self.real_pt_is_private = now_playtime_is_private
        self.real_wars_is_private = now_wars_is_private
        self.real_xp_is_private = now_contrib_is_private
        self.real_raids_is_private = False
        self.stats_warn = bool(warn_flag or (now_entry is None))

    def _set_too_new_timed_defaults(self):
        self.real_pt = 'N/A'
        self.real_xp = 'N/A'
        self.real_wars = 'N/A'
        self.real_raids = 'N/A'
        self.real_pt_is_private = False
        self.real_wars_is_private = False
        self.real_xp_is_private = False
        self.real_raids_is_private = False
        self.stats_warn = False

    def _set_non_taq_timed_defaults(self):
        self.real_pt = 0
        self.real_xp = 0
        self.real_wars = 0
        self.real_raids = 0
        self.real_pt_is_private = False
        self.real_wars_is_private = False
        self.real_xp_is_private = False
        self.real_raids_is_private = False
        self.stats_warn = False

    def _set_skipped_timed_defaults(self, days):
        self.stats_days = days
        self._set_non_taq_timed_defaults()

    def isInTAq(self, uuid):
        guild_members = []
        for member in Guild('The Aquarium').all_members:
            guild_members.append(member['uuid'])
        return False if uuid not in guild_members else True

    def unlock_background(self, background):
        db = DB()
        db.connect()

        db.cursor.execute('SELECT * FROM profile_customization WHERE "user" = %s', (self.discord,))
        row = db.cursor.fetchone()

        db.cursor.execute('SELECT id FROM profile_backgrounds WHERE name = %s', (background,))
        bg_id = db.cursor.fetchone()[0]

        # Check if user owns any backgrounds, if not insert new entry to table
        if not row:
            db.cursor.execute(
                'INSERT INTO profile_customization ("user", background, owned) VALUES (%s, %s, %s)',
                (self.discord, bg_id, json.dumps([bg_id]))
            )
            db.connection.commit()
            db.close()
            return True

        bgs = row[2]
        # Check if user already owns selected background, if so return message
        if bg_id in bgs:
            db.close()
            return True

        bgs.append(bg_id)
        db.cursor.execute(
            'UPDATE profile_customization SET owned = %s WHERE "user" = %s',
            (json.dumps(bgs), self.discord)
        )
        db.connection.commit()
        db.close()
        return True


class BasicPlayerStats:
    def __init__(self, name):
        player_data = getPlayerUUID(name)
        if not player_data:
            self.error = True
            return
        else:
            self.error = False
        self.UUID = player_data[1]
        self.username = player_data[0]

        # player data
        pdata = getPlayerDatav3(self.UUID)
        self.rank = pdata.get('rank', 'Player')
        if self.rank == 'Player':
            support_rank = pdata.get('supportRank')
            self.tag = support_rank.upper() if support_rank is not None else 'Player'
        else:
            self.tag = self.rank
        self.tag_color = wynn_ranks[self.tag.lower()]['color'] if self.tag != 'Player' else '#66ccff'
        self.wars = pdata.get('globalData', {}).get('wars', 0)
        self.total_level = pdata.get('globalData', {}).get('totalLevel', 0)
        self.completed_quests = pdata.get('globalData', {}).get('completedQuests', 0)
        self.playtime = pdata.get('playtime', 0)
        self.rank = pdata.get('rank', 'Player')


class PlayerShells:
    def __init__(self, discord_id):
        db = DB()
        db.connect()

        db.cursor.execute(
            "SELECT ign, uuid FROM discord_links WHERE discord_id = %s",
            (discord_id,)
        )
        link = db.cursor.fetchone()
        if link:
            self.username, self.UUID = link[0], link[1]
            self.error = False
        else:
            self.username, self.UUID = None, None
            self.error = True

        self.shells = 0
        self.balance = 0

        if not self.error:
            db.cursor.execute(
                "SELECT shells, balance FROM shells WHERE \"user\" = %s",
                (str(discord_id),)
            )
            row = db.cursor.fetchone()
            if row:
                self.shells, self.balance = row

        db.close()


class LinkAccount(Modal):
    def __init__(self, user, added, removed, rank, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        self.added = added
        self.removed = removed
        self.rank = rank
        self.add_item(InputText(label="Player's Name", placeholder="Player's In-Game Name without rank"))

    async def callback(self, interaction: discord.Interaction):
        db = DB()
        db.connect()
        db.cursor.execute(
            'INSERT INTO discord_links (discord_id, ign, linked, rank) VALUES (%s, %s, %s, %s)',
            (self.user.id, self.children[0].value, False, self.rank)
        )
        db.connection.commit()
        await self.user.edit(nick=f"{self.rank} {self.children[0].value}")
        await interaction.response.send_message(f'{self.added}\n\n{self.removed}', ephemeral=True)
        db.close()


class NewMember(Modal):
    def __init__(self, user, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        self.to_remove = ['Land Crab', 'Honored Fish', 'Retired Chief', 'Ex-Member']
        self.roles_to_add = []
        self.roles_to_remove = []
        self.add_item(InputText(label="Player's Name", placeholder="Player's In-Game Name without rank"))

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message('Working on it', ephemeral=True)
        msg = await interaction.original_response()

        db = DB()
        db.connect()
        db.cursor.execute('SELECT * FROM discord_links WHERE discord_id = %s', (self.user.id,))
        rows = db.cursor.fetchall()
        pdata = BasicPlayerStats(self.children[0].value)
        if pdata.error:
            embed = discord.Embed(title=':no_entry: Oops! Something did not go as intended.',
                                  description=f'Could not retrieve information of `{self.children[0].value}`.\nPlease check your spelling or try again later.',
                                  color=0xe33232)
            await msg.edit(embed=embed)
            return

        starting_rank = determine_starting_rank(self.user)
        rank_roles = discord_ranks[starting_rank]['roles']

        to_remove = ['Land Crab', 'Honored Fish', 'Retired Chief', 'Ex-Member']
        to_add = ['Member', 'The Aquarium [TAq]', *rank_roles, '🥇 RANKS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
                  '🛠️ PROFESSIONS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '✨ COSMETIC ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
        roles_to_add = []
        roles_to_remove = []
        all_roles = interaction.guild.roles
        for add_role in to_add:
            role = discord.utils.find(lambda r: r.name == add_role, all_roles)
            if role is not None:
                roles_to_add.append(role)

        if roles_to_add:
            await self.user.add_roles(*roles_to_add, reason=f"New member registration (ran by {interaction.user.name})",
                                      atomic=True)

        for remove_role in to_remove:
            role = discord.utils.find(lambda r: r.name == remove_role, all_roles)
            if role is not None:
                roles_to_remove.append(role)

        if roles_to_remove:
            await self.user.remove_roles(*roles_to_remove,
                                         reason=f"New member registration (ran by {interaction.user.name})",
                                         atomic=True)

        if len(rows) != 0:
            db.cursor.execute(
                'UPDATE discord_links SET rank = %s, ign = %s, wars_on_join = %s, uuid = %s, linked = TRUE WHERE discord_id = %s',
                (starting_rank, self.children[0].value, pdata.wars, pdata.UUID, self.user.id)
            )
            db.connection.commit()
        else:
            db.cursor.execute(
                'INSERT INTO discord_links (discord_id, ign, uuid, linked, rank, wars_on_join) VALUES (%s, %s, %s, %s, %s, %s)',
                (self.user.id, pdata.username, pdata.UUID, True, starting_rank, pdata.wars)
            )
            db.connection.commit()
        db.close()
        await self.user.edit(nick=f"{starting_rank} {self.children[0].value}")
        embed = discord.Embed(title=':white_check_mark: New member registered',
                              description=f'<@{self.user.id}> was linked to `{pdata.username}`', color=0x3ed63e)
        await msg.edit('', embed=embed)

        # ─── WELCOME EMBED ────────────────────────────────────────────────────────
        welcome_location = interaction.guild.get_channel(WELCOME_CHANNEL)
        if welcome_location:
            welcome_embed = discord.Embed(
                description=f":ocean: Dive right in, {self.user.mention}! The water's fine.",
                color=discord.Color.blue()
            )
            welcome_embed.set_author(name="Welcome Aboard!", icon_url=self.user.display_avatar.url)
            await welcome_location.send(embed=welcome_embed)


class PlaceTemplate:
    def __init__(self, image):
        self.loaded_image = Image.open(image)
        self.divider = self.loaded_image.crop((0, 0, 2, 32))
        self.filling = self.loaded_image.crop((2, 0, 3, 32))
        self.ending = self.loaded_image.crop((3, 0, 8, 32))

    def add(self, img, width, pos, start=False):
        x, y = pos
        end = 0
        if not start:
            for i in range(width - 5):
                img.paste(self.filling, (x + i, y), self.filling)
                end = i
            img.paste(self.ending, (x + end + 1, y), self.ending)
        else:
            for i in range(width - 10):
                img.paste(self.filling, (x + 5 + i, y), self.filling)
                end = i
            img.paste(ImageOps.mirror(self.ending), (x, y), ImageOps.mirror(self.ending))
            img.paste(self.ending, (x + 5 + end + 1, y), self.ending)


class Page(_Page):
    def update_files(self):
        for file in self._files:
            if file.fp.closed and (fn := getattr(file.fp, "name", None)):
                file.fp = open(fn, "rb")
            file.reset()
            file.fp.close = lambda: None
        return self._files
