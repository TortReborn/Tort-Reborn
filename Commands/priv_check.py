import asyncio
import os
from collections import Counter
from urllib.parse import quote

import discord
import requests
from discord.commands import Option, slash_command
from discord.ext import commands

from Helpers.functions import timed_get
from Helpers.rate_limiter import external_rate_limit


API_BASE = "https://api.wynncraft.com/v3"
DEFAULT_EMBED_COLOR = 0x2F3136

ACCESS_RULES = [
    ("mainAccess", "global data and featured stats"),
    ("characterListAccess", "character list"),
    ("characterDataAccess", "character stats"),
    ("characterBuildAccess", "skill points and ability trees"),
    ("huntedCharacterAccess", "characters using hunted mode"),
    ("onlineStatus", "online status, server, and last join"),
    ("guildHistoryAccess", "guild history"),
]

PROFILE_FIELDS = [
    ("username", None),
    ("online", "onlineStatus"),
    ("server", "onlineStatus"),
    ("activeCharacter", "characterListAccess"),
    ("nickname", None),
    ("uuid", None),
    ("rank", None),
    ("rankBadge", None),
    ("legacyRankColour.main", None),
    ("legacyRankColour.sub", None),
    ("shortenedRank", None),
    ("supportRank", None),
    ("veteran", None),
    ("lastJoin", "onlineStatus"),
    ("guild", None),
    ("ranking", "mainAccess"),
    ("previousRanking", "mainAccess"),
    ("firstJoin", "mainAccess"),
    ("playtime", "mainAccess"),
    ("globalData.contentCompletion", "mainAccess"),
    ("globalData.wars", "mainAccess"),
    ("globalData.totalLevel", "mainAccess"),
    ("globalData.mobsKilled", "mainAccess"),
    ("globalData.chestsFound", "mainAccess"),
    ("globalData.dungeons.total", "mainAccess"),
    ("globalData.dungeons.list", "mainAccess"),
    ("globalData.raids.total", "mainAccess"),
    ("globalData.raids.list", "mainAccess"),
    ("globalData.worldEvents", "mainAccess"),
    ("globalData.lootruns", "mainAccess"),
    ("globalData.caves", "mainAccess"),
    ("globalData.completedQuests", "mainAccess"),
    ("globalData.guildRaids", "mainAccess"),
    ("globalData.raidStats", "mainAccess"),
    ("globalData.pvp", "mainAccess"),
    ("featuredStats", "mainAccess"),
    ("wallpaper", None),
    ("avatar", None),
    ("restrictions", None),
    ("characters", "characterListAccess"),
]

CHARACTER_SUMMARY_FIELDS = [
    ("type", "characterListAccess"),
    ("reskin", "characterListAccess"),
    ("nickname", "characterListAccess"),
    ("level", "characterListAccess"),
    ("xp", "characterListAccess"),
    ("xpPercent", "characterListAccess"),
    ("totalLevel", "characterListAccess"),
    ("gamemode", "characterListAccess"),
    ("meta", "characterListAccess"),
]

CHARACTER_DETAIL_FIELDS = [
    ("type", "characterListAccess"),
    ("reskin", "characterListAccess"),
    ("nickname", "characterListAccess"),
    ("level", "characterListAccess"),
    ("xp", "characterListAccess"),
    ("xpPercent", "characterListAccess"),
    ("totalLevel", "characterListAccess"),
    ("preEconomy", "characterDataAccess"),
    ("gamemode", "characterListAccess"),
    ("contentCompletion", "characterDataAccess"),
    ("wars", "characterDataAccess"),
    ("playtime", "characterDataAccess"),
    ("mobsKilled", "characterDataAccess"),
    ("chestsFound", "characterDataAccess"),
    ("itemsIdentified", "characterDataAccess"),
    ("blocksWalked", "characterDataAccess"),
    ("logins", "characterDataAccess"),
    ("deaths", "characterDataAccess"),
    ("discoveries", "characterDataAccess"),
    ("pvp", "characterDataAccess"),
    ("skillPoints", "characterBuildAccess"),
    ("professions", "characterDataAccess"),
    ("dungeons", "characterDataAccess"),
    ("raids", "characterDataAccess"),
    ("worldEvents", "characterDataAccess"),
    ("lootruns", "characterDataAccess"),
    ("caves", "characterDataAccess"),
    ("quests", "characterDataAccess"),
    ("removedStat", None),
]


def _has_path(payload, dotted_path):
    current = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
        if current is None:
            return True
    return True


def _removed_by_skeleton(dotted_path, removed_stats):
    parts = dotted_path.split(".")
    return dotted_path in removed_stats or parts[0] in removed_stats


def _collect_restrictions(*payloads):
    restrictions = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for rule, restricted in (payload.get("restrictions") or {}).items():
            restrictions[rule] = restrictions.get(rule, False) or bool(restricted)
    return restrictions


def _merge_restrictions(target, source):
    for rule, restricted in source.items():
        target[rule] = target.get(rule, False) or bool(restricted)


def _field_status(payload, dotted_path, rule, restrictions, removed_stats=None):
    removed_stats = set(removed_stats or [])
    if _has_path(payload, dotted_path):
        return "VISIBLE"
    if _removed_by_skeleton(dotted_path, removed_stats):
        return "HIDDEN_BY_SKELETON"
    if rule and restrictions.get(rule):
        return "PRIVATE"
    return "MISSING"


def _summarize_field_statuses(field_statuses):
    counts = Counter(status for _, status in field_statuses)
    visible = counts.get("VISIBLE", 0)
    total = len(field_statuses)
    if counts.get("PRIVATE"):
        return f"{visible}/{total} visible, {counts['PRIVATE']} private"
    if counts.get("HIDDEN_BY_SKELETON"):
        return f"{visible}/{total} visible, {counts['HIDDEN_BY_SKELETON']} skeleton-hidden"
    if counts.get("MISSING"):
        return f"{visible}/{total} visible, {counts['MISSING']} missing"
    return f"{visible}/{total} visible"


def _format_table(rows):
    if not rows:
        return "None"
    left_width = max(len(row[0]) for row in rows)
    return "\n".join(f"{left:<{left_width}}  {right}" for left, right in rows)


def _endpoint_status_label(status):
    if status == 200:
        return "Visible"
    if status == 403:
        return "Private"
    if status == 404:
        return "Not found"
    if status == 300:
        return "Ambiguous"
    if status == "ERROR":
        return "Error"
    return "Unexpected"


def _access_status_label(status):
    if status == "N/A":
        return "N/A"
    return str(status).replace("_", " ").title()


class PrivCheck(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(
        name="priv-check",
        description="Check a player's Wynncraft API privacy",
        integration_types={discord.IntegrationType.guild_install, discord.IntegrationType.user_install},
        contexts={
            discord.InteractionContextType.guild,
            discord.InteractionContextType.bot_dm,
            discord.InteractionContextType.private_channel,
        },
    )
    @external_rate_limit()
    async def priv_check(
        self,
        ctx: discord.ApplicationContext,
        player: Option(str, "Username or UUID", required=True),
    ):
        await ctx.defer()

        report = await asyncio.to_thread(self._build_report, player)
        if report["error"]:
            embed = discord.Embed(
                title="Couldn't check privacy",
                description=report["error"],
                color=0xE33232,
            )
            await ctx.followup.send(embed=embed, ephemeral=True)
            return

        embed = self._build_embed(report)
        await ctx.followup.send(embed=embed)

    def _headers(self):
        token = os.getenv("WYNN_TOKEN")
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _get_json(self, path, timeout=12):
        url = f"{API_BASE}{path}"
        response = timed_get(url, timeout=timeout, headers=self._headers())
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return {
            "url": url,
            "status": response.status_code,
            "payload": payload,
        }

    def _build_report(self, player):
        safe_player = quote(str(player).strip())
        if not safe_player:
            return {"error": "Enter a username or UUID."}

        try:
            profile_result = self._get_json(f"/player/{safe_player}?fullResult", timeout=20)
        except requests.RequestException as exc:
            return {"error": f"Wynncraft did not respond: `{exc}`"}

        profile = profile_result["payload"]
        status = profile_result["status"]

        if status == 300:
            return {"error": self._format_multi_selector(profile)}
        if status == 404:
            return {"error": f"`{player}` was not found."}
        if status != 200 or not isinstance(profile, dict) or not profile.get("uuid"):
            detail = profile.get("detail") if isinstance(profile, dict) else None
            suffix = f"\n`{detail}`" if detail else ""
            return {"error": f"Wynncraft returned `{status}`.{suffix}"}

        uuid = profile["uuid"]
        username = profile.get("username") or str(player)

        char_list_result = None
        ability_result = None
        sample_character = None
        try:
            char_list_result = self._get_json(f"/player/{quote(uuid)}/characters", timeout=12)
        except requests.RequestException as exc:
            char_list_result = {"url": "", "status": "ERROR", "payload": {"detail": str(exc)}}

        characters = profile.get("characters") if isinstance(profile.get("characters"), dict) else {}
        character_list = (
            char_list_result["payload"]
            if isinstance(char_list_result.get("payload"), dict) and char_list_result.get("status") == 200
            else {}
        )
        sample_character_id = self._select_character_id(profile, character_list or characters)

        if sample_character_id:
            sample_character = characters.get(sample_character_id)
            if not isinstance(sample_character, dict) or "removedStat" not in sample_character:
                try:
                    detail_result = self._get_json(
                        f"/player/{quote(uuid)}/characters/{quote(sample_character_id)}",
                        timeout=12,
                    )
                    if detail_result["status"] == 200 and isinstance(detail_result["payload"], dict):
                        sample_character = detail_result["payload"]
                except requests.RequestException:
                    pass
            try:
                ability_result = self._get_json(
                    f"/player/{quote(uuid)}/characters/{quote(sample_character_id)}/abilities",
                    timeout=12,
                )
            except requests.RequestException as exc:
                ability_result = {"url": "", "status": "ERROR", "payload": {"detail": str(exc)}}

        restrictions = _collect_restrictions(profile)
        for char_payload in characters.values():
            _merge_restrictions(restrictions, _collect_restrictions(char_payload))
        _merge_restrictions(
            restrictions,
            _collect_restrictions(sample_character, ability_result["payload"] if ability_result else None),
        )

        profile_field_statuses = [
            (field, _field_status(profile, field, rule, restrictions))
            for field, rule in PROFILE_FIELDS
            if field != "characters" or "characters" in profile
        ]

        summary_field_statuses = []
        if character_list:
            first_summary = next(iter(character_list.values()), {})
            summary_field_statuses = [
                (field, _field_status(first_summary, field, rule, restrictions))
                for field, rule in CHARACTER_SUMMARY_FIELDS
            ]

        detail_field_statuses = []
        removed_stats_by_character = {}
        for char_id, char_payload in characters.items():
            if not isinstance(char_payload, dict):
                continue
            removed = set(char_payload.get("removedStat") or [])
            if removed:
                removed_stats_by_character[char_id] = sorted(removed)
            for field, rule in CHARACTER_DETAIL_FIELDS:
                detail_field_statuses.append((
                    f"{char_id}.{field}",
                    _field_status(char_payload, field, rule, restrictions, removed),
                ))

        if not detail_field_statuses and isinstance(sample_character, dict):
            removed = set(sample_character.get("removedStat") or [])
            if removed and sample_character_id:
                removed_stats_by_character[sample_character_id] = sorted(removed)
            detail_field_statuses = [
                (field, _field_status(sample_character, field, rule, restrictions, removed))
                for field, rule in CHARACTER_DETAIL_FIELDS
            ]

        rule_rows = self._build_rule_rows(
            restrictions,
            profile,
            char_list_result,
            characters,
            ability_result,
        )
        endpoint_rows = [
            ("Profile", _endpoint_status_label(profile_result["status"])),
            ("Characters", _endpoint_status_label(char_list_result["status"])),
        ]
        if ability_result:
            endpoint_rows.append(("Ability tree", _endpoint_status_label(ability_result["status"])))

        hidden_stats = Counter()
        for removed in removed_stats_by_character.values():
            hidden_stats.update(removed)

        return {
            "error": None,
            "username": username,
            "rule_rows": rule_rows,
            "endpoint_rows": endpoint_rows,
            "profile_summary": _summarize_field_statuses(profile_field_statuses),
            "character_summary": _summarize_field_statuses(detail_field_statuses) if detail_field_statuses else "no visible characters",
            "hidden_stats": hidden_stats,
            "character_count": len(character_list or characters),
        }

    @staticmethod
    def _format_multi_selector(payload):
        objects = payload.get("objects") if isinstance(payload, dict) else {}
        if not isinstance(objects, dict) or not objects:
            return "That name matches multiple players. Try the UUID."
        lines = ["That name matches multiple players. Try one of these UUIDs:"]
        for uuid, data in list(objects.items())[:8]:
            username = data.get("username", "Unknown") if isinstance(data, dict) else "Unknown"
            lines.append(f"`{username}` - `{uuid}`")
        return "\n".join(lines)

    @staticmethod
    def _select_character_id(profile, characters):
        active = profile.get("activeCharacter")
        if active and active in characters:
            return active
        if not characters:
            return None

        def sort_key(item):
            _, payload = item
            if not isinstance(payload, dict):
                return 0
            return payload.get("totalLevel") or payload.get("level") or 0

        return max(characters.items(), key=sort_key)[0]

    @staticmethod
    def _build_rule_rows(restrictions, profile, char_list_result, characters, ability_result):
        rows = []
        has_hunted = any(
            isinstance(char, dict)
            and any(str(mode).lower() == "hunted" for mode in (char.get("gamemode") or []))
            for char in characters.values()
        )

        for rule, description in ACCESS_RULES:
            if restrictions.get(rule) is True:
                status = "PRIVATE"
            elif rule in restrictions and restrictions[rule] is False:
                status = "VISIBLE"
            elif rule == "mainAccess":
                status = "VISIBLE" if profile.get("globalData") is not None and "featuredStats" in profile else "UNKNOWN"
            elif rule == "characterListAccess":
                if char_list_result.get("status") == 200:
                    status = "VISIBLE"
                elif char_list_result.get("status") == 403:
                    status = "PRIVATE"
                else:
                    status = "UNKNOWN"
            elif rule == "characterDataAccess":
                status = "VISIBLE" if any("removedStat" in c for c in characters.values() if isinstance(c, dict)) else "UNKNOWN"
            elif rule == "characterBuildAccess":
                if ability_result and ability_result.get("status") == 200:
                    status = "VISIBLE"
                elif ability_result and ability_result.get("status") == 403:
                    status = "PRIVATE"
                else:
                    status = "UNKNOWN"
            elif rule == "huntedCharacterAccess":
                status = "VISIBLE" if has_hunted else "N/A"
            elif rule == "onlineStatus":
                status = "VISIBLE" if all(key in profile for key in ("online", "server", "lastJoin")) else "UNKNOWN"
            else:
                status = "UNKNOWN"
            rows.append((rule, status, description))
        return rows

    @staticmethod
    def _build_embed(report):
        rule_lines = [
            f"{rule:<22} {_access_status_label(status)}"
            for rule, status, _ in report["rule_rows"]
        ]
        endpoints = _format_table(report["endpoint_rows"])

        hidden_stats = report["hidden_stats"]
        if hidden_stats:
            hidden_summary = ", ".join(
                stat for stat, _ in hidden_stats.most_common(12)
            )
        else:
            hidden_summary = "None"

        embed = discord.Embed(
            title=f"{discord.utils.escape_markdown(report['username'])} privacy",
            color=DEFAULT_EMBED_COLOR,
        )
        embed.add_field(name="Access", value=f"```text\n{chr(10).join(rule_lines)[:1000]}\n```", inline=False)
        embed.add_field(name="Checks", value=f"```text\n{endpoints[:1000]}\n```", inline=False)
        embed.add_field(
            name="Hidden Stats",
            value=f"`{hidden_summary[:350]}`",
            inline=False,
        )
        return embed

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(PrivCheck(client))
