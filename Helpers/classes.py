from datetime import datetime, timezone, timedelta
import json
import requests
import os

from PIL import Image, ImageOps
from dateutil import parser
import discord
from discord.ui import InputText, Modal

from Helpers.database import DB, get_current_guild_data, get_player_activity_baseline
from Helpers.functions import getPlayerUUID, getPlayerDatav3, urlify
from discord.ext.pages import Page as _Page

from Helpers.variables import wynn_ranks, welcome_channel

WELCOME_CHANNEL = welcome_channel

class Guild:

    def __init__(self, guild):
        if len(guild) <= 4:
            url = f'https://api.wynncraft.com/v3/guild/prefix/{urlify(guild)}'
        else:
            url = f'https://api.wynncraft.com/v3/guild/{urlify(guild)}'

        resp = requests.get(url, timeout=10, headers={"Authorization": f"Bearer {os.getenv('WYNN_TOKEN')}"})
        resp.raise_for_status()
        guild_data = resp.json()

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
    def __init__(self, name, days):
        db = DB()
        db.connect()
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

        # Track which fields are private/null
        test_last_joined = pdata.get('lastJoin')
        if test_last_joined:
            self.last_joined = parser.isoparse(test_last_joined)
            self.last_joined_is_private = False
        else:
            # TAq Creation Date (default)
            self.last_joined = parser.isoparse("2020-03-22T11:11:17.810000Z")
            self.last_joined_is_private = True

        self.characters = pdata.get('characters', {})
        self.online = pdata.get('online', False)
        self.server = pdata.get('server')

        # Wars - track if null or missing
        try:
            raw_wars = pdata.get('globalData', {}).get('wars')
            self.wars = raw_wars if raw_wars is not None else 0
            self.wars_is_private = raw_wars is None
        except:
            self.wars = 0
            self.wars_is_private = True

        # Playtime - track if null or missing
        raw_playtime = pdata.get('playtime')
        self.playtime = raw_playtime if raw_playtime is not None else 0
        self.playtime_is_private = raw_playtime is None

        self.rank = pdata.get('rank', 'Player')
        # self.mobs = pdata.get('globalData', {}).get('killedMobs')

        # Chests and quests - track if null or missing
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

        # Total level - track if null or missing
        raw_total_level = pdata.get('globalData', {}).get('totalLevel')
        self.total_level = raw_total_level if raw_total_level is not None else 0
        self.total_level_is_private = raw_total_level is None

        # guild data
        self.taq = self.isInTAq(self.UUID)
        self.guild = 'The Aquarium' if self.taq else pdata.get('guild')
        if self.guild is not None:
            self.guild = 'The Aquarium' if self.taq else pdata.get('guild', {}).get('name')
            gdata = Guild(self.guild)
            for guildee in gdata.all_members:
                if guildee['uuid'] == self.UUID:
                    guild_stats = guildee
                    break
                else:
                    pass
            self.guild_rank = guild_stats.get('rank') if self.taq else pdata.get('guild', {}).get('rank')

            # Guild contributed - track if null or missing
            raw_contributed = guild_stats.get('contributed')
            self.guild_contributed = raw_contributed if raw_contributed is not None else 0
            self.guild_contributed_is_private = raw_contributed is None

            self.guild_joined = parser.isoparse(guild_stats.get('joined'))
            now_utc   = datetime.now(timezone.utc)
            delta     = now_utc - self.guild_joined
            self.in_guild_for = delta
        else:
            self.guild = None
            self.guild_rank = None
            self.guild_contributed = None
            self.guild_contributed_is_private = False
            self.guild_joined = None
            self.in_guild_for = None

        # linked
        db.cursor.execute('SELECT * FROM discord_links WHERE uuid = %s', (self.UUID,))
        rows = db.cursor.fetchall()
        self.linked = True if len(rows) != 0 else False
        if self.linked:
            self.rank = rows[0][4]
            self.discord = rows[0][0]

            # profile_customization
            db.cursor.execute('SELECT "user", background, owned, gradient FROM profile_customization WHERE "user" = %s', (self.discord,))
            row = db.cursor.fetchone()
            if row:
                self.background = row[1]
                self.backgrounds_owned = row[2]
                self.gradient = row[3] if row[3] is not None else ['#293786', '#1d275e']
        else:
            self.discord = None
            
        # shells
        self.shells = 0
        self.balance = 0
        if self.linked:
            db.cursor.execute('SELECT * FROM shells WHERE "user" = %s', (self.discord,))
            rows = db.cursor.fetchall()
            self.shells = 0 if len(rows) == 0 else rows[0][1]
            self.balance = 0 if len(rows) == 0 else rows[0][2]

        # raids
        if self.taq:
            db.cursor.execute('SELECT * from uncollected_raids WHERE uuid = %s', (self.UUID,))
            rows = db.cursor.fetchall()
            self.uncollected_raids = 0 if len(rows) == 0 else rows[0][1]
            self.collected_raids = 0 if len(rows) == 0 else rows[0][2]
            self.guild_raids = self.uncollected_raids + self.collected_raids

        # timed stats (using database for both current and baseline data)
        if self.taq:
            # 1) Load NOW from database cache so profiles match the leaderboard exactly
            cur = get_current_guild_data()
            cur_map = {m['uuid']: m for m in cur.get('members', [])}
            now_entry = cur_map.get(self.UUID)

            # Fallback to previously-fetched live values if not present in cache
            # Also track if any of the base values are private
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

            # Check if any are None (private)
            now_playtime_is_private = now_playtime_val is None
            now_wars_is_private = now_wars_val is None
            now_contrib_is_private = now_contrib_val is None

            now_playtime = int(now_playtime_val) if now_playtime_val is not None else 0
            now_wars = int(now_wars_val) if now_wars_val is not None else 0
            now_contrib = int(now_contrib_val) if now_contrib_val is not None else 0
            now_raids = int(now_raids_val) if now_raids_val is not None else 0

            # 2) Bound the requested days for display (keep your UX constraints)
            # Get number of available snapshots from database
            db_temp = DB()
            db_temp.connect()
            try:
                db_temp.cursor.execute("SELECT COUNT(DISTINCT snapshot_date) FROM player_activity")
                num_snaps = db_temp.cursor.fetchone()[0] or 0
            except Exception:
                num_snaps = 0
            finally:
                db_temp.close()

            # Keep your original limits
            if days > num_snaps:
                days = num_snaps
            if days > self.in_guild_for.days:
                days = self.in_guild_for.days
            if days < 1:
                days = 1
            self.stats_days = days

            if self.in_guild_for.days >= 1:
                # Get baseline values from database
                base_pt, warn_pt = get_player_activity_baseline(self.UUID, 'playtime', days)
                base_wars, warn_wars = get_player_activity_baseline(self.UUID, 'wars', days)
                base_xp, warn_xp = get_player_activity_baseline(self.UUID, 'contributed', days)
                base_raids, warn_raids = get_player_activity_baseline(self.UUID, 'raids', days)
                warn_flag = warn_pt or warn_wars or warn_xp or warn_raids

                # 3) Compute inclusive deltas (clamped >= 0)
                self.real_pt    = max(int(now_playtime) - int(base_pt),   0)
                self.real_xp    = max(int(now_contrib)  - int(base_xp),   0)
                self.real_wars  = max(int(now_wars)     - int(base_wars), 0)
                self.real_raids = max(int(now_raids)    - int(base_raids),0)

                # Mark timed stats as private if the current value was private
                self.real_pt_is_private = now_playtime_is_private
                self.real_wars_is_private = now_wars_is_private
                self.real_xp_is_private = now_contrib_is_private
                self.real_raids_is_private = False  # raids is not directly from API

                # Optional: stash a flag if you ever want to render a warning icon on the profile
                self.stats_warn = bool(warn_flag or (now_entry is None))
            else:
                # Too new in guild to show windowed stats
                self.real_pt = 'N/A'
                self.real_xp = 'N/A'
                self.real_wars = 'N/A'
                self.real_raids = 'N/A'
                self.real_pt_is_private = False
                self.real_wars_is_private = False
                self.real_xp_is_private = False
                self.real_raids_is_private = False
                self.stats_warn = False
        else:
            # Non-TAq profiles keep zeros (as before)
            self.real_pt = 0
            self.real_xp = 0
            self.real_wars = 0
            self.real_raids = 0
            self.real_pt_is_private = False
            self.real_wars_is_private = False
            self.real_xp_is_private = False
            self.real_raids_is_private = False
            self.stats_warn = False


            db.close()

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
        self.to_remove = ['Land Crab', 'Honored Fish', 'Ex-Member']
        self.to_add = ['Member', 'The Aquarium [TAq]', '☆Reef', 'Starfish', '🥇 RANKS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
                       '🛠️ PROFESSIONS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '✨ COSMETIC ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', 
                       'CONTRIBUTION ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
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

        to_remove = ['Land Crab', 'Honored Fish', 'Ex-Member']
        to_add = ['Member', 'The Aquarium [TAq]', '☆Reef', 'Starfish', '🥇 RANKS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀',
                  '🛠️ PROFESSIONS⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀', '✨ COSMETIC ROLES⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀']
        roles_to_add = []
        roles_to_remove = []
        all_roles = interaction.guild.roles
        for add_role in to_add:
            role = discord.utils.find(lambda r: r.name == add_role, all_roles)
            roles_to_add.append(role)

        await self.user.add_roles(*roles_to_add, reason=f"New member registration (ran by {interaction.user.name})",
                                  atomic=True)

        for remove_role in to_remove:
            role = discord.utils.find(lambda r: r.name == remove_role, all_roles)
            roles_to_remove.append(role)

        await self.user.remove_roles(*roles_to_remove,
                                     reason=f"New member registration (ran by {interaction.user.name})",
                                     atomic=True)

        if len(rows) != 0:
            db.cursor.execute(
                'UPDATE discord_links SET rank = %s, ign = %s, wars_on_join = %s, uuid = %s WHERE discord_id = %s',
                ('Starfish', self.children[0].value, pdata.wars, pdata.UUID, self.user.id)
            )
            db.connection.commit()
        else:
            db.cursor.execute(
                'INSERT INTO discord_links (discord_id, ign, uuid, linked, rank, wars_on_join) VALUES (%s, %s, %s, %s, %s, %s)',
                (self.user.id, pdata.username, pdata.UUID, False, 'Starfish', pdata.wars)
            )
            db.connection.commit()
        db.close()
        await self.user.edit(nick="Starfish " + self.children[0].value)
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
