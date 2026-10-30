"""
One-off: seed the bot_panels tables from the content that used to live in the
/generate and /shell_exchange generate commands, so the website editor starts
from what is in Discord today instead of a blank page.

    ./venv/Scripts/python.exe scripts/seed_bot_panels.py --dry-run   # print the JSON
    ./venv/Scripts/python.exe scripts/seed_bot_panels.py             # dev DB + bucket
    prodctx python Tort-Reborn/scripts/seed_bot_panels.py            # prod, once

Channel and emoji ids come from the running profile (Helpers/variables.py),
so the same script seeds dev and prod correctly. Banners are uploaded to the
bucket as panel assets. Panels that already exist are left alone, so a second
run is a no-op.

Each panel's revision 1 is recorded as already published (nothing is posted).
The live messages are not linked yet: open the panel on the website, paste
the links of the messages it replaces under "Link existing messages", and
publish; the bot then edits those messages in place. The shell exchange panel
is linked automatically from its config.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from textwrap import dedent

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import discord  # noqa: E402

from Commands.tickets import TICKET_EMBED_COLOR  # noqa: E402
from Helpers import local_ids  # noqa: E402
from Helpers.bot_panels import ASSET_PREFIX, ASSET_SCHEME, DYNAMIC_SHELL_EXCHANGE  # noqa: E402
from Helpers.database import DB, get_shell_exchange_config  # noqa: E402
from Helpers.storage import storage  # noqa: E402
from Helpers.variables import (  # noqa: E402
    ASPECT_EMOJI,
    RAID_COLLECTING_CHANNEL_ID,
    SHELL_EMOJI,
    SHELL_EXCHANGE_CHANNEL_ID,
    TAQ_GUILD_ID,
    TAQ_ROLES_CHANNEL_ID,
)

ROOT = Path(__file__).parent.parent
GUILD_INFO_ASSET_DIR = ROOT / "images" / "guild_info"
ROLE_INFO_ASSET_DIR = GUILD_INFO_ASSET_DIR / "role_info"
ROLE_INFO_EMBED_DIR = ROOT / "data" / "embeds" / "guild_info" / "role_info"
GENERATE_EMBED_COLOR = 0x94C1FF
TAQ_ROLES_CHANNEL_MENTION = f"<#{TAQ_ROLES_CHANNEL_ID}>"


def _custom_emoji(name: str, emoji_id: int) -> str:
    return f"<:{name}:{emoji_id}>"


# ---- Content moved verbatim from Commands/generate.py ----

def _build_guild_rules_embed() -> discord.Embed:
    embed = discord.Embed(color=GENERATE_EMBED_COLOR)
    fields = (
        (
            "𓆉  1. Do not spam or flood chats",
            "This includes repeatedly sending text, media or links outside of media-and-memes or any bot command channel. Sending a really long message just for the sake of annoying other users is also not allowed.",
        ),
        (
            "𓆉  2. Be nice to other people",
            "Harassment, bullying, racism, sexism, prejudice behaviour or any other expressions of harm towards others WILL NOT be tolerated. Showing support or encouraging others to such actions is also considered as violation of this rule. Swearing is allowed as long as it isn't used targeting others.",
        ),
        (
            "𓆉  3. Avoid inappropriate topics",
            "This includes but is not limited to NSFW content of any form, politics or religion.\nKeep the server PG-13",
        ),
        (
            "𓆉  4. Do not advertise",
            "Advertising other Discord servers or guilds is not allowed. You are allowed to share your social media, YouTube, twitch etc, as long as you don't actively try to get people to follow/subscribe to you.",
        ),
        (
            "𓆉  5. Listen to Moderators",
            "If a staff member tells you to stop doing something, stop doing it regardless of how the situation looks, you might be right, you might be wrong, but its important, especially in conflict situations, to slow down and then solve the issue. It's hard to add every single aspect to a rule in the list, so finding loopholes is also not allowed.",
        ),
    )
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    return embed


def _build_guild_info_embed() -> discord.Embed:
    embed = discord.Embed(color=GENERATE_EMBED_COLOR)
    fields = (
        (
            "𓆉  Membership",
            f"You can apply for Guild or Community Membership in <#1476866917854609408>! Once you are either a guild or community member, you gain access to <#1386413126697877626> where you can find more info about the guild and {TAQ_ROLES_CHANNEL_MENTION} where you can assign roles for the server.",
        ),
        (
            "𓆉  Permanent Discord Link",
            "https://discord.gg/fVzZ8qvEv9",
        ),
        (
            "𓆉  Ally Guild Raids",
            f"If you want to ally guild raid and want to recruit in our raid channels, apply for community member, then you can get the raid specific roles in {TAQ_ROLES_CHANNEL_MENTION} to be pinged by our members too or recruit members by pinging the respective roles in <#1320140705602998282>.",
        ),
    )
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    return embed


def _build_taq_faq_embeds_page1() -> list[discord.Embed]:
    c = GENERATE_EMBED_COLOR
    return [
        discord.Embed(
            title="𓆉 What is TAq?",
            description=(
                "We are The Aquarium [TAq]! The awesome guild you just joined! \n"
                "First guild to ever reach the effective max level at 130 and one of the most active community, "
                "raiding and warring guilds out there with a heavy focus on being a cozy community for you to "
                "call your own and feel welcomed by in Wynncraft!\n\n"
                "Our Discord server offers lots of content to help you progress, whether you are a new player "
                "or an experienced Wynncraft warrior. Feel free to ask any questions, game-related or not!\n\n"
                "Some shortcuts for you:\n"
                f"{TAQ_ROLES_CHANNEL_MENTION}\n"
                "<#1152966582834827344> \n"
                "<#736920151081091122> \n"
                "<#1135510651981287424> \n"
                "<#1280196125340602478> \n"
                "<#846172145498980424>\n"
                "<#729163031321509938>"
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 What are the rules?",
            description=(
                "These are guild-specific rules. You can find more rules for how to interact with and between "
                "members of our community here: <#729162016505331765>\n"
                "> 1. Recruiters, Captains, and Strategists must always get approval from a Chief (or higher) "
                "before promoting, demoting, kicking, or inviting someone. No solo missions!\n"
                "> 2. If we're in an alliance, Captains and Strategists must not attack ally territories, only "
                "enemy or free-for-all (ffa) territories. Attacking allies on purpose is a big no-no.\n"
                "> 3. Keep all warring talk inside our military channels. Don't leak our battle plans!\n"
                "> 4. Consumables like food, scrolls, and potions in the guild bank are for wars only. Feel free "
                "to use other items though -  teleport scrolls, dungeon keys, ability shards, etc. - knock yourself out."
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 How do ranks work?",
            description=(
                "Wynn has 6 official ranks: Recruit, Recruiter, Captain, Strategist, Chief, and Owner. We've added "
                "our own custom ranks too — for better permissions and smoother promotions. Curious? You can read a "
                f"full breakdown right here {TAQ_ROLES_CHANNEL_MENTION}!\n\n"
                "**Promotions** are based on your role in the guild and how you help it! We value all sorts of "
                "contributions and try our best to give everyone a chance to rank up regardless of their interests and skills.\n\n"
                ":clock8: **Passive contributions**\n"
                "> • Being active in guild chat and/or on Discord\n"
                "> • Joining voice calls\n"
                "> • Playing with / helping other guild members\n"
                "> • Giving recommendations, advice and feedback.\n\n"
                ":wrench:‎ **Active contributions**\n"
                "> • Joining the war effort <#1152966582834827344>\n"
                "> • Completing guild raids <#1280196125340602478>\n"
                "> • Starting up giveaways (DM any chief)\n"
                "> • Recruiting new guild members\n"
                "> • Donating ingredients or materials at <#1135510651981287424>\n\n"
                "The first rank-ups are easy to achieve. To get promoted to Manatee (recruiter), even just regularly chatting is enough!\n"
                "After reaching Angler, you can submit an [application](https://www.the-aquarium.com/login?redirect=/apply/hammerhead) "
                "to become a part of our HR team."
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 How do I war?",
            description=(
                "Warring is one of the biggest parts of Guild life in Wynn, it's strategic, fun and engaging end-game content. \n"
                "Everyone's welcome to join in! If you're new to it, check ⁠<#1152966582834827344> we'll get you ready in no time.\n"
                "We also have war tickets you can open if you have *any* further questions here <#1287143628002558024>"
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 I wanna raid, how do I start?",
            description=(
                "Graids (Guild-Raids) are a great way to get good funds as well as the only way to get aspects "
                "and one of 3 ways to receive shells.\n\n"
                "Generally, we have roles for each raid that you can ping in <#1320140705602998282> "
                "(also the channel for raid talk) to get people's attention.\n"
                "Before you do that, try asking ingame if there's any party waiting for more people to join or "
                "start your own by writing graid 1/4 for example\n"
                "This tells all other people in chat what raid you are looking for and how many members you got ready. "
                "If people are ingame and willing, they'll respond. \n\n"
                "We are also allied with Nerfuria, so you can check out their discord linked [here](https://discord.com/channels/729147655875199017/1448040306057412769) to find more people to raid with!\n\n"
                "And of course, the **Guild Raiders** Discord is linked in that same channel, a massive discord to search for graid parties"
                "We also have our own set of custom, hand picked and kept up to date meta raid builds in "
                "<#1504148856936599644> that you can go get to be a more reliable team partner."
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 What are guild tomes, aspect and emerald rewards?",
            description=(
                "Owning territories and completing graids earns us emeralds, aspectsand tomes. Chiefs hand these aspects and tomes out to "
                "you based on your contribution and requests for them.\n\n"
                "Emeralds usually go to GordLonner (our saving alt) to fund events and rewards.\n"
                "Guild Tomes can't be traded but aren't soulbound either. They give bonus skill points or elemental boosts. "
                "You can claim them in ⁠<#846172145498980424> for shells or war participation."
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 Someone's breaking the rules — what now?",
            description=(
                "If someone's causing trouble, DM a Hammerhead or above. They'll handle it. "
                "Offenders might get a warning, permission or promotion freeze - repeat offenders risk getting kicked or banned."
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 Am I at risk of being kicked?",
            description=(
                "The guild is usually full and we tend to receive lots of recruits. \n"
                "If you are going to be inactive, please fill the format in ⁠⁠<#729163031321509938>. \n"
                "We are less likely to kick you if you have a valid reason to not play the game!\n"
                "Not contributing to the guild also makes you more likely to get kicked if we need to make space for new members."
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 Do we have a cape and/or uniform?",
            description=(
                "We do! [Here's the uniform](https://www.minecraftskins.com/skin/15930127/taq-uniform-recruit-captain-4px-arm/)\n"
                "And here's the cape: https://discord.com/channels/729147655875199017/729162124223447040/874339436555563008\n\n"
                "You'll find that a lot of our members are using our cosmetics. If you need help putting either of those on "
                "make sure to DM a Chief!\n"
            ),
            color=c,
        ),
    ]


def _build_taq_faq_embeds_page2() -> list[discord.Embed]:
    c = GENERATE_EMBED_COLOR
    return [
        discord.Embed(
            title="𓆉 Vanity Roles",
            description=(
                "TAq distributes Vanity Roles for your War and Raid acomplishments every 2 weeks\n"
                "These roles will be assigned each Sunday 00:00 UTC and are always based on your activity for the last 14 days.\n"
                "Here's how to get them:\n"
                "## War\n"
                "<@&1401236653472743668> -> 120 Wars in 2 weeks\n"
                "<@&1401236428368642243> -> 80 Wars in 2 weeks\n"
                "<@&1401226770069590089> -> 40 Wars in 2 weeks\n"
                "## Guild Raid\n"
                "<@&1401281458164990022> -> 80 Raids in 2 weeks\n"
                "<@&1401281504671305850> -> 50 Raids in 2 weeks\n"
                "<@&1401281543699431566> -> 30 raids in 2 weeks"
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 Raid Tracking with TAq's own mod- **Verge**!",
            description=(
                "This is TAq's mod to more accurately track your **guild raid completions/stats-**\n"
                "Just download it and put it into your mod folder- it's modrinth approved so no need to fear for a virus.\n"
                "To submit raids, you need to log in via discord, fear not as discord will tell you exactly what you allow the mod to do and what not before you confirm\n\n"
                "**Requirements**\n"
                "• 1.21.11 as Minecraft version\n"
                "• Fabric Loader 0.18.4 (minimum) *(should be default when you're playing modded 1.21.11)*\n\n"
                "**Depends on**\n"
                "• Wynntils\n"
                "• ModMenu *(optional, config can also be opened via command and hotkey)*\n\n"
                "https://modrinth.com/project/37mIxdyU\n\n"
                "*Also if you have anything you want added in here you can tell me!*"
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 Suggestions!",
            description=(
                "TAq always strives towards being a better guild, if you have any suggestions, you can share them in "
                "[this](https://docs.google.com/forms/d/e/1FAIpQLSd4gAK9dRK-zKebUVee8HxUSpLhWSpEEQAhq77kzqCxzoLYMA/viewform?usp=header) "
                "form with us **completely anonymous**. Just fill it out with your idea and press send!"
            ),
            color=c,
        ),
        discord.Embed(
            title="𓆉 I want to be a Hammerhead! Can I apply for that?",
            description=(
                "In order to apply for Hammerhead (active helper of the guild) you need to be at least Angler. \n"
                "If you have any questions feel free to ask a chief! Joke applications lower your chances of future promotions.\n"
                "Becoming a Hammerhead means actively helping shape the guild when it comes to management and important decisions, "
                "if that is something you want to be a part of, [apply here](https://the-aquarium.com/login?redirect=/apply/hammerhead)!"
            ),
            color=c,
        ),
    ]


# ---- Inline command text, moved verbatim ----
# Emojis are this environment's (the seed runs once per database). The
# shells -> aspects rate is a panel value (Helpers/panel_values.py), written as
# {{key}} so the panel follows the rate set on the website.

SHELLS_PER_ASPECT = "{{shells_per_aspect}}"
MAX_ASPECTS_PER_SHELL_CONVERSION = "{{max_aspects_per_conversion}}"


def _app_header_embed() -> discord.Embed:
    # Was a /generate app_header option each time; these are the values in the
    # live #applications post, so linking it changes nothing.
    level_requirement = "100"
    activity_requirement = "4 hours"
    embed = discord.Embed(
        title="The Aquarium \u2014 Applications",
        description=(
            "Welcome! Choose an application type below to get started.\n\n"
            "**Guild Member**\n"
            "Join The Aquarium as an in-game guild member.\n"
            f"- Level **{level_requirement}+**\n"
            f"- **{activity_requirement}** weekly activity\n\n"
            "**Community Member**\n"
            "Become part of our community without joining the in-game guild. "
            "Hang out, chat, and participate in events!"
        ),
        color=GENERATE_EMBED_COLOR,
    )
    embed.set_footer(text="Click a button below to begin your application.")
    return embed


def _raid_collecting_embed() -> discord.Embed:
    embed = discord.Embed(color=GENERATE_EMBED_COLOR)
    embed.description = dedent(f"""
        ❓ **How to Claim Your Raid Rewards**

        After completing raids, you become eligible for rewards. You can claim **Aspects** or **Shells** depending on how many raids you've completed.

        Make sure to check your eligibility by clicking on the **Claim rewards** button below. You'll see how many raids you've completed and which rewards are available.

        {ASPECT_EMOJI} **Claim Aspects**
        • 1 Aspect for every 2 Guild Raids you complete.

        {SHELL_EMOJI} **Claim Shells**
        • 1 Shell for every Guild Raid you complete.

        **How to Claim**
        1. Click the **Claim rewards** button.
        2. Choose either **Aspects** or **Shells**.
        3. Your claimed rewards will be updated automatically!

        _Complete more raids to earn more rewards!_
        """)
    return embed


def _shell_convert_embed() -> discord.Embed:
    embed = discord.Embed(color=GENERATE_EMBED_COLOR)
    embed.description = dedent(f"""
        {SHELL_EMOJI} **Convert Shells into Aspects**

        Spend your shells to earn aspects directly. Shell conversion lets you use your shell balance to receive aspects without needing to complete additional raids.

        Conversion rate: **{SHELLS_PER_ASPECT} {SHELL_EMOJI} = 1 {ASPECT_EMOJI}**

        **Requirements**
        - No uncollected aspects already waiting
        - At least **{SHELLS_PER_ASPECT}** shells in balance
        - At most **{MAX_ASPECTS_PER_SHELL_CONVERSION}** aspects per conversion
        - 3-day cooldown between conversions

        **How to Convert**
        1. Click the **Shells -> Aspects** button.
        2. Enter how many aspects you want to claim.
        3. Confirm your conversion in the preview.

        _Your aspects will be added to the collection queue automatically. Note: depending on current demand, it may take up to a week or two for your aspects to be delivered._
        """)
    return embed


def _shell_exchange_text() -> str:
    long_text = "𓆉  Ingredients are vital to the guild. We use them to craft XP gear, prof gear, war and to offer free war builds. We therefore rely on small donations to keep all guild activities running smoothly. To reward our contributors, we have created our exclusive currency: Shells. They can be traded for crafted gear, guild tomes, mythic items, and more. To find a more comprehensive list, check out the <#1462481145018060951>!\n\nThe list below shows our currently accepted ingredients and materials, which you can receive shells for donating. Other ingredients are welcome, but will not be eligible for shells. If you find an ingredient that isn't on the list but you think might still be useful, feel free to ask a Chief for confirmation. Specially outlined items are of higher need than usual right now, so while you will get exactly the shells displayed still, donating these specific ingredients and materials helps us a lot!\n\n⚠️ Shell rates and accepted ingredients are likely to change depending on their demand, how many we currently have in stock, and their price on the Trade Market. All modifications are announced in ⁠the \"Shell rate balances\" thread. If you'd like to be pinged for them, you can get the <@&1050233131183112255> role in <#1523984409538203789>. This role is also used when our supply of a specific ingredient gets low (pro tip: high demand ingredients will likely earn you more shells!).\n\n𓆉  In order to claim your shells: \nPut your ingredients in the guild bank and screenshot the log message and optionally the content, you can then **open a ticket** and **send a screenshot** as evidence. A Narwhal will soon update your profile and close the ticket as soon as the transaction is completed.\n\n⚙️ There are two useful commands to check your balance:\n`/profile [user]`\n`/leaderboard (total/timespan)`"
    return long_text


# ---- Panels ----

def _war_ticket_embed() -> discord.Embed:
    # Moved verbatim from Commands/tickets.py (/tickets post war).
    embed = discord.Embed(title="War Ticket", color=TICKET_EMBED_COLOR)
    embed.add_field(
        name="War Training",
        value=(
            "Are you interested in enhancing your skills in warring or economy strategies?\n"
            "We're excited to offer lessons tailored to help you excel! If you'd like to "
            "participate and learn more, please let us know by opening a ticket."
        ),
        inline=False,
    )
    embed.add_field(
        name="Role Request",
        value=(
            "Do you need to update your war roles? Want to see war channels? "
            "Open a ticket to request DPS/Tank/Healer and Military roles."
        ),
        inline=False,
    )
    embed.add_field(
        name="War Build Request",
        value=(
            "Do you need help with funding your war build?\n"
            "Req. Active participation in wars and Trust."
        ),
        inline=False,
    )
    return embed


def _embed_json(embed: discord.Embed) -> dict:
    d = embed.to_dict()
    out = {"color": d.get("color", GENERATE_EMBED_COLOR)}
    for key in ("title", "url", "description"):
        if d.get(key):
            out[key] = d[key]
    if d.get("fields"):
        out["fields"] = [
            {"name": f["name"], "value": f["value"], "inline": bool(f.get("inline"))}
            for f in d["fields"]
        ]
    if d.get("footer", {}).get("text"):
        out["footer"] = {"text": d["footer"]["text"]}
    return out


def _discohook_embeds(json_filename: str) -> list[dict]:
    with open(ROLE_INFO_EMBED_DIR / json_filename, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return [
        _embed_json(discord.Embed.from_dict({**e, "color": GENERATE_EMBED_COLOR}))
        for e in payload.get("embeds", [])
    ]


def _military_roles_embeds() -> list[dict]:
    # From the webhook post in #military-roles ("The Aquarium", 2023); only the
    # footer changed, from reactions (Carl-bot) to Tort's buttons.
    color = 10721252
    return [
        {"title": '𓆉  __Class roles__', "color": color,
         "description": 'Class roles are used to form precise teams. Anybody can select the two solo/vlow-med roles, but the sniping roles can only be assigned by HR.\n\n🍃 - <@&1311125265153265795>: People who want to be pinged after a successful snipe to eat up the rest of the claim\n🪄 -  <@&1154999995741769728>: Voluntary roles for people with a Soloer build. Usually can take territories with high defense\n🤺 - <@&906265863735369728>: Players with an verified DPS build. \n❤️\u200d🩹 - <@&906265943045439529>: Players with an HQ viable Healer build. \n🛡️ - <@&906266064818700338>: Players with an HQ viable Tank build. \n** **'},
        {"title": '𓆉  __Military roles__', "color": color,
         "description": 'These roles are assigned by HR, and are usually given to our most experienced warrers.\n\n<@&1033420156690513970>: High rank staff who handle politics, alliances and other important war matters. \n<@&923202820679794779>: People who are able to eco.\n<@&1405514713554485309>: Members that have at least 1 HQ war ready build.\n** **'},
        {"color": color, "footer": {"text": '\u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e  \u200e \u200e \u200e Use the buttons below to get the roles from this list! \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e  \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e \u200e  \u200e \u200e \u200e \u200e \u200e'}},
    ]

def _role_rows(specs) -> list[list[dict]]:
    rows: dict[int, list[dict]] = {}
    for spec in specs:
        emoji = spec.emoji
        if isinstance(emoji, discord.PartialEmoji):
            emoji = str(emoji)
        rows.setdefault(spec.row or 0, []).append(
            {"kind": "role", "role_id": str(spec.role_id), "label": spec.label, "emoji": emoji,
             **({"requires_role_id": str(spec.gate_role_id)} if spec.gate_role_id else {})}
        )
    return [rows[r] for r in sorted(rows)]


class AssetUploader:
    def __init__(self, dry_run: bool):
        self.dry_run = dry_run

    def __call__(self, path: Path) -> str:
        data = path.read_bytes()
        ext = path.suffix.lower().lstrip(".")
        name = f"{hashlib.sha256(data).hexdigest()[:16]}.{ext}"
        if not self.dry_run:
            content_type = "image/jpeg" if ext == "jpg" else f"image/{ext}"
            storage.put_bytes(ASSET_PREFIX + name, data, content_type=content_type)
        return ASSET_SCHEME + name


def build_panels(upload) -> list[dict]:
    from Commands.generate import PING_ROLES, REACTION_ROLES

    guild_info_links = [[
        {"kind": "link", "label": "Our website!", "url": "https://www.the-aquarium.com",
         "emoji": _custom_emoji("tort", 919659913054130196)},
        {"kind": "link", "label": "Forums Page",
         "url": "https://forums.wynncraft.com/threads/join-the-aquarium-taq-first-guild-to-max-level-active-raiding-community-guild-lvl-100.322292/",
         "emoji": _custom_emoji("TAq", 744256840254226553)},
        {"kind": "link", "label": "Wynncord Thread",
         "url": "https://discord.com/channels/143852930036924417/1020187255480008724",
         "emoji": _custom_emoji("Tome", 1482743136143806584)},
    ]]

    role_info = [
        ("guild_ranks.json", "guild_ranks.PNG", None),
        ("discord_roles.json", "discord_roles.PNG", None),
        ("reaction_roles.json", "reaction_roles.PNG", REACTION_ROLES),
        ("ping_roles.json", "ping_roles.PNG", PING_ROLES),
    ]

    shell_config = get_shell_exchange_config()
    shell_channel = shell_config.get("channel_id") or SHELL_EXCHANGE_CHANNEL_ID
    shell_message = shell_config.get("panel_message_id")

    panels = [
        {
            "slug": "guild-info", "name": "Guild Rules & Info", "channel_id": None, "message_ids": [],
            "content": {"messages": [
                {"images": [upload(GUILD_INFO_ASSET_DIR / "guild_rules_banner.png")],
                 "embeds": [_embed_json(_build_guild_rules_embed())]},
                {"images": [upload(GUILD_INFO_ASSET_DIR / "guild_info_banner.png")],
                 "embeds": [_embed_json(_build_guild_info_embed())],
                 "rows": guild_info_links},
            ]},
        },
        {
            "slug": "role-info", "name": "Role Info", "channel_id": TAQ_ROLES_CHANNEL_ID, "message_ids": [],
            "content": {"messages": [
                {"images": [upload(ROLE_INFO_ASSET_DIR / banner)],
                 "embeds": _discohook_embeds(json_name),
                 **({"rows": _role_rows(specs)} if specs else {})}
                for json_name, banner, specs in role_info
            ]},
        },
        {
            "slug": "taq-faq", "name": "TAq FAQ", "channel_id": None, "message_ids": [],
            "content": {"messages": [
                {"images": [upload(GUILD_INFO_ASSET_DIR / "taq_faq.png")],
                 "embeds": [_embed_json(e) for e in _build_taq_faq_embeds_page1()]},
                {"embeds": [_embed_json(e) for e in _build_taq_faq_embeds_page2()]},
            ]},
        },
        {
            "slug": "app-header", "name": "Application Header", "channel_id": None, "message_ids": [],
            "content": {"messages": [{
                "images": [upload(GUILD_INFO_ASSET_DIR / "applications.png")],
                "embeds": [_embed_json(_app_header_embed())],
                "rows": [[{"kind": "action", "action": "apply_guild"},
                          {"kind": "action", "action": "apply_community"}]],
            }]},
        },
        {
            "slug": "raid-collecting", "name": "Raid Collecting", "channel_id": RAID_COLLECTING_CHANNEL_ID,
            "message_ids": [],
            "content": {"messages": [{
                "images": [upload(ROOT / "images" / "raids" / "raidcollectingbanner.png")],
                "embeds": [_embed_json(_raid_collecting_embed())],
                "rows": [[{"kind": "action", "action": "raid_claim"}]],
            }]},
        },
        {
            "slug": "shell-convert", "name": "Shells to Aspects", "channel_id": None, "message_ids": [],
            "content": {"messages": [{
                "embeds": [_embed_json(_shell_convert_embed())],
                "rows": [[{"kind": "action", "action": "shells_to_aspects"}]],
            }]},
        },
        {
            "slug": "shell-exchange", "name": "Shell Exchange", "channel_id": shell_channel,
            "message_ids": [shell_message] if shell_message else [],
            # One Components V2 box, as /shell_exchange generate posted it, so
            # linking the live message edits it in place.
            "content": {"messages": [{
                "layout": "box",
                "content": _shell_exchange_text(),
                "color": GENERATE_EMBED_COLOR,
                "dynamic": DYNAMIC_SHELL_EXCHANGE,
                # The shell ticket button lives here now; it used to be its own
                # "Shell Collecting" post under the box.
                "rows": [[{"kind": "action", "action": "ticket_open:shell"}]],
            }]},
        },
        {
            "slug": "military-roles", "name": "Military Roles", "channel_id": None, "message_ids": [],
            "content": {"messages": [{
                "embeds": _military_roles_embeds(),
                "rows": [[
                    {"kind": "role", "role_id": "1311125265153265795", "label": "Territory Munching", "emoji": "🍃"},
                    {"kind": "role", "role_id": "1154999995741769728", "label": "Soloer", "emoji": "🪄"},
                ]],
            }]},
        },
        {
            "slug": "war-ticket", "name": "War Ticket", "channel_id": None, "message_ids": [],
            "content": {"messages": [{
                "embeds": [_embed_json(_war_ticket_embed())],
                "rows": [[{"kind": "action", "action": "ticket_open:war"}]],
            }]},
        },
    ]
    # Maincord ids in the content -> the test servers' under the dev bot (no-op in prod).
    return [{**p, "content": local_ids.data(p["content"])} for p in panels]


def seed(panels: list[dict]) -> None:
    db = DB()
    db.connect()
    try:
        for panel in panels:
            db.cursor.execute(
                """
                INSERT INTO bot_panels (slug, name, channel_id, message_ids, published_rev)
                VALUES (%s, %s, %s, %s::bigint[], 1)
                ON CONFLICT (slug) DO NOTHING
                RETURNING id
                """,
                (panel["slug"], panel["name"], panel["channel_id"], panel["message_ids"]),
            )
            row = db.cursor.fetchone()
            if row is None:
                print(f"  {panel['slug']}: exists, left alone")
                continue
            db.cursor.execute(
                """
                INSERT INTO bot_panel_revisions
                    (panel_id, rev_number, content, channel_id, note, author_name, status, published_at)
                VALUES (%s, 1, %s, %s, 'Imported from the /generate commands', 'seed', 'published', NOW())
                """,
                (row[0], json.dumps(panel["content"]), panel["channel_id"]),
            )
            print(f"  {panel['slug']}: seeded ({len(panel['content']['messages'])} message(s))")
        db.connection.commit()
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Seed the bot_panels tables from the old /generate content.")
    parser.add_argument("--dry-run", action="store_true", help="print the panels; write nothing")
    args = parser.parse_args()

    panels = build_panels(AssetUploader(args.dry_run))
    if args.dry_run:
        print(json.dumps(panels, indent=2, ensure_ascii=False))
        return
    print(f"Seeding bot panels for guild {TAQ_GUILD_ID}:")
    seed(panels)


if __name__ == "__main__":
    main()
