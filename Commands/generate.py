import base64
import datetime
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from textwrap import dedent

import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup
from discord.ui import View, button
from discord import Permissions

from Helpers.database import DB
from Helpers.logger import log, ERROR
from Helpers.variables import (
    HOME_GUILD_IDS,
    TAQ_GUILD_ID,
    WEBSITE_URL,
    RAID_COLLECTING_CHANNEL_ID,
    WAR_INFO_CHANNEL_ID,
    SHELL_EXCHANGE_CHANNEL_ID,
    SHELL_EMOJI,
    ASPECT_EMOJI,
)

APPLICATION_TOKEN_SECRET = os.getenv("APPLICATION_TOKEN_SECRET", "")
GENERATE_EMBED_COLOR = 0x94C1FF
GUILD_INFO_ASSET_DIR = Path(__file__).parent.parent / "images" / "guild_info"
GUILD_RULES_BANNER = "guild_rules_banner.png"
GUILD_INFO_BANNER = "guild_info_banner.png"
RAID_COLLECTING_BANNER = "raidcollectingbanner.png"
TAQ_FAQ_BANNER = "taq_faq.png"
APPLICATIONS_BANNER = "applications.png"


def _custom_emoji(name: str, emoji_id: int) -> discord.PartialEmoji:
    return discord.PartialEmoji(name=name, id=emoji_id)


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
            "You can apply for Guild or Community Membership in <#1476866917854609408>! Once you are either a guild or community member, you gain access to <#1386413126697877626> where you can find more info about the guild and <#752917987853467669> where you can assign roles for the server.",
        ),
        (
            "𓆉  Permanent Discord Link",
            "https://discord.gg/fVzZ8qvEv9",
        ),
        (
            "𓆉  Ally Guild Raids",
            "If you want to ally guild raid and want to recruit in our raid channels, apply for community member, then you can get the raid specific roles in <#752917987853467669> to be pinged by our members too or recruit members by pinging the respective roles in <#1320140705602998282>.",
        ),
    )
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    return embed


class GuildInfoLinksView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Our website!",
            style=discord.ButtonStyle.link,
            url="https://www.the-aquarium.com",
            emoji=_custom_emoji("tort", 919659913054130196),
        ))
        self.add_item(discord.ui.Button(
            label="Forums Page",
            style=discord.ButtonStyle.link,
            url="https://forums.wynncraft.com/threads/join-the-aquarium-taq-first-guild-to-max-level-active-raiding-community-guild-lvl-100.322292/",
            emoji=_custom_emoji("TAq", 744256840254226553),
        ))
        self.add_item(discord.ui.Button(
            label="Wynncord Thread",
            style=discord.ButtonStyle.link,
            url="https://discord.com/channels/143852930036924417/1020187255480008724",
            emoji=_custom_emoji("Tome", 1482743136143806584),
        ))


# ---- Application button helpers ----

def _generate_token(user: discord.User | discord.Member, app_type: str) -> str:
    """Generate a signed token carrying the user's Discord identity."""
    payload = json.dumps({
        "discord_id": str(user.id),
        "discord_username": user.name,
        "discord_avatar": user.avatar.key if user.avatar else "",
        "type": app_type,
        "exp": int(time.time()) + 1800,  # 30 minutes
    })
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(
        APPLICATION_TOKEN_SECRET.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{payload_b64}.{sig_b64}"


class ApplicationButtonView(discord.ui.View):
    """Persistent view with two application buttons."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Guild Member Application",
        style=discord.ButtonStyle.primary,
        custom_id="apply_guild",
        emoji="\U0001F420",
    )
    async def guild_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_button(interaction, "guild")

    @discord.ui.button(
        label="Community Member Application",
        style=discord.ButtonStyle.success,
        custom_id="apply_community",
        emoji="\U0001FABC",
    )
    async def community_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        await self._handle_button(interaction, "community")

    async def _handle_button(self, interaction: discord.Interaction, app_type: str):
        await interaction.response.defer(ephemeral=True)

        token = _generate_token(interaction.user, app_type)
        url = f"{WEBSITE_URL}/apply/{app_type}?token={token}"

        type_label = "Guild Member" if app_type == "guild" else "Community Member"

        embed = discord.Embed(
            title=f"{type_label} Application",
            description=(
                f"Click the link below to fill out your application on our website.\n\n"
                f"**[Open Application Form]({url})**\n\n"
                f"This link expires in **30 minutes** and is unique to your Discord account."
            ),
            color=GENERATE_EMBED_COLOR,
        )
        embed.set_footer(text="Do not share this link with anyone else.")

        await interaction.followup.send(embed=embed, ephemeral=True)


# ---- Raid collecting views ----

class ClaimView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="Claim rewards", style=discord.ButtonStyle.green, custom_id="raid_claim")
    async def claim(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT uuid FROM discord_links WHERE discord_id = %s",
            (interaction.user.id,)
        )
        row = db.cursor.fetchone()
        if not row:
            db.close()
            return await interaction.followup.send(
                "❌ You don't have a linked game account. Please use `/link` first.",
                ephemeral=True
            )

        uuid = row[0]
        db.cursor.execute(
            "SELECT uncollected_raids FROM uncollected_raids WHERE uuid = %s",
            (uuid,)
        )
        row   = db.cursor.fetchone()
        count = row[0] if row else 0
        db.close()

        if count <= 0:
            return await interaction.followup.send(
                "✅ You have no uncollected raids right now.",
                ephemeral=True
            )

        view = ConvertView(uuid, count)
        interaction.client.add_view(view)
        await interaction.followup.send(
            f"You have **{count}** uncollected raid(s).\nConvert them into:",
            view=view,
            ephemeral=True
        )


class ConvertView(View):
    def __init__(self, uuid: str, count: int):
        super().__init__(timeout=None)
        self.uuid  = uuid
        self.count = count

    @button(label="Shells", style=discord.ButtonStyle.primary, custom_id="convert_shells")
    async def shells(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = DB(); db.connect()

        db.cursor.execute("""
            UPDATE uncollected_raids
                SET uncollected_raids = uncollected_raids - %s,
                    collected_raids   = collected_raids   + %s
            WHERE uuid = %s AND uncollected_raids >= %s
        """, (self.count, self.count, self.uuid, self.count))
        db.connection.commit()

        if db.cursor.rowcount == 0:
            db.close()
            await interaction.followup.send("❌ You cannot claim raids twice.", ephemeral=True)
            return

        # Get IGN from discord_links
        db.cursor.execute(
            "SELECT ign FROM discord_links WHERE discord_id = %s",
            (interaction.user.id,)
        )
        ign_row = db.cursor.fetchone()
        ign = ign_row[0] if ign_row else "Unknown"

        db.cursor.execute("""
            INSERT INTO shells AS sh ("user", shells, balance, ign)
                VALUES (%s, %s, %s, %s)
            ON CONFLICT ("user") DO UPDATE SET
                shells  = sh.shells + EXCLUDED.shells,
                balance = sh.balance + EXCLUDED.balance,
                ign     = EXCLUDED.ign
        """, (str(interaction.user.id), self.count, self.count, ign))

        db.connection.commit()
        db.close()

        await interaction.edit_original_response(
            content=f"✅ Converted **{self.count}** raid(s) into shells!",
            view=None
        )

    @button(label="Aspects", style=discord.ButtonStyle.secondary, custom_id="convert_aspects")
    async def aspects(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = DB(); db.connect()

        db.cursor.execute("""
            SELECT uncollected_raids, uncollected_aspects
            FROM uncollected_raids
            WHERE uuid = %s
        """, (self.uuid,))
        row = db.cursor.fetchone()

        if not row:
            db.close()
            return await interaction.followup.send("❌ No raid data found.", ephemeral=True)

        total_raids = row[0]
        current_aspects = row[1]

        aspect_count = total_raids // 2
        if aspect_count == 0:
            db.close()
            return await interaction.followup.send(
                "❌ You need at least 2 uncollected raids to claim 1 Aspect.",
                ephemeral=True
            )

        remainder_raids = total_raids % 2
        raids_spent = aspect_count * 2

        db.cursor.execute("""
            UPDATE uncollected_raids
               SET uncollected_raids   = %s,
                   uncollected_aspects = uncollected_aspects + %s,
                   collected_raids     = collected_raids + %s
             WHERE uuid = %s AND uncollected_raids >= %s
        """, (remainder_raids, aspect_count, raids_spent, self.uuid, raids_spent))
        db.connection.commit()

        if db.cursor.rowcount == 0:
            db.close()
            await interaction.followup.send("❌ You cannot claim raids twice.", ephemeral=True)
            return

        db.close()

        await interaction.edit_original_response(
            content=(
                f"✅ Converted **{raids_spent}** uncollected raid(s) into "
                f"**{aspect_count}** new uncollected aspect(s)!\n"
                f"• You now have **{current_aspects + aspect_count}** total uncollected aspect(s).\n"
                f"• **{remainder_raids}** raid(s) remain uncollected."
            ),
            view=None
        )


# ---- Shell convert view ----

class ShellConvertView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="Shells -> Aspects", style=discord.ButtonStyle.green, custom_id="shells_to_aspects")
    async def shells_to_aspects(self, _: discord.ui.Button, interaction: discord.Interaction):
        # Linked account check
        db = DB(); db.connect()
        db.cursor.execute(
            "SELECT uuid FROM discord_links WHERE discord_id = %s",
            (interaction.user.id,)
        )
        link_row = db.cursor.fetchone()
        if not link_row:
            db.close()
            return await interaction.response.send_message(
                "You don't have a linked game account. Please use `/link` first.",
                ephemeral=True
            )
        uuid = str(link_row[0])

        # No pending uncollected aspects allowed
        db.cursor.execute(
            "SELECT uncollected_aspects FROM uncollected_raids WHERE uuid = %s",
            (uuid,)
        )
        raids_row = db.cursor.fetchone()
        if raids_row and raids_row[0] > 0:
            db.close()
            return await interaction.response.send_message(
                f"You already have **{raids_row[0]}** uncollected aspect(s) waiting. "
                "Collect them before converting more shells.",
                ephemeral=True
            )

        # Shell balance check (need at least 4 for one aspect)
        db.cursor.execute(
            'SELECT balance, last_aspect_convert_at FROM shells WHERE "user" = %s',
            (str(interaction.user.id),)
        )
        shells_row = db.cursor.fetchone()
        balance = shells_row[0] if shells_row else 0
        if balance < 6:
            db.close()
            return await interaction.response.send_message(
                f"You need at least **6** shells to convert (you have **{balance}**).",
                ephemeral=True
            )

        # Cooldown check (3 days between conversions)
        last_convert_at = shells_row[1] if shells_row else None
        if last_convert_at is not None:
            ready_at = last_convert_at + datetime.timedelta(days=3)
            if datetime.datetime.now(datetime.timezone.utc) < ready_at:
                db.close()
                ts = int(ready_at.timestamp())
                return await interaction.response.send_message(
                    f"You are on cooldown. You can convert again <t:{ts}:R>.",
                    ephemeral=True
                )
        db.close()

        # All guards passed -- open the amount modal (cap at 40 per conversion)
        max_aspects = min(balance // 6, 40)
        modal = ShellsToAspectsModal(
            discord_id=interaction.user.id,
            uuid=uuid,
            balance=balance,
            max_aspects=max_aspects,
        )
        await interaction.response.send_modal(modal)


# ---- Shells to aspects modal ----

class ShellsToAspectsModal(discord.ui.Modal):
    def __init__(self, discord_id: int, uuid: str, balance: int, max_aspects: int):
        super().__init__(title="Shells -> Aspects")
        self.discord_id  = discord_id
        self.uuid        = uuid
        self.balance     = balance
        self.max_aspects = max_aspects

        self.amount_input = discord.ui.InputText(
            label=f"How many aspects? (max {max_aspects}, 6 shells each)",
            placeholder=f"Enter a number from 1 to {max_aspects}",
            style=discord.InputTextStyle.short,
            max_length=4,
            required=True,
        )
        self.add_item(self.amount_input)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Parse and validate input
        raw = self.amount_input.value.strip()
        if not raw.isdigit() or int(raw) < 1:
            return await interaction.followup.send(
                "Enter a valid number of aspects (at least 1).",
                ephemeral=True
            )
        amount = int(raw)
        if amount > self.max_aspects:
            return await interaction.followup.send(
                f"You can convert at most **{self.max_aspects}** aspect(s) "
                f"with your current balance ({self.balance} shells).",
                ephemeral=True
            )

        cost = amount * 6
        remaining = self.balance - cost

        # Preview embed with conversion breakdown -- user must confirm
        embed = discord.Embed(title="Confirm Conversion", color=GENERATE_EMBED_COLOR)
        embed.add_field(name="Current Shells", value=f"{self.balance} {SHELL_EMOJI}", inline=False)
        embed.add_field(name="Cost", value=f"{cost} {SHELL_EMOJI} ({amount} {ASPECT_EMOJI})", inline=False)
        embed.add_field(name="Remaining Shells", value=f"{remaining} {SHELL_EMOJI}", inline=False)
        embed.set_footer(text="Click Confirm to complete the conversion.")

        view = ShellConfirmView(
            discord_id=self.discord_id,
            uuid=self.uuid,
            balance=self.balance,
            amount=amount,
            cost=cost,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ---- Shell confirm view ----

class ShellConfirmView(View):
    def __init__(self, discord_id: int, uuid: str, balance: int, amount: int, cost: int):
        super().__init__(timeout=180)
        self.discord_id = discord_id
        self.uuid       = uuid
        self.balance    = balance
        self.amount     = amount
        self.cost       = cost

    @button(label="Confirm", style=discord.ButtonStyle.green, custom_id="shell_confirm")
    async def confirm(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Single atomic transaction: deduct shells and credit aspects
        db = DB(); db.connect()
        try:
            db.cursor.execute("""
                UPDATE shells
                   SET balance                = balance - %s,
                       last_aspect_convert_at = NOW()
                 WHERE "user" = %s AND balance >= %s
            """, (self.cost, str(self.discord_id), self.cost))

            if db.cursor.rowcount == 0:
                db.connection.rollback()
                db.close()
                await interaction.edit_original_response(embed=None, view=None,
                    content="You no longer have enough shells for this conversion.")
                return

            # UPSERT handles both existing and new uncollected_raids rows
            db.cursor.execute("""
                INSERT INTO uncollected_raids (uuid, uncollected_aspects)
                     VALUES (%s, %s)
                ON CONFLICT (uuid) DO UPDATE
                        SET uncollected_aspects =
                              uncollected_raids.uncollected_aspects + EXCLUDED.uncollected_aspects
            """, (self.uuid, self.amount))

            # Audit log entry matching 
            db.cursor.execute(
                "INSERT INTO audit_log (log_type, actor_name, actor_id, action) VALUES (%s, %s, %s, %s)",
                ('shell', interaction.user.name, interaction.user.id,
                 f"converted {self.cost} shells into {self.amount} aspect(s) (self-service).")
            )

            db.connection.commit()
        except Exception as e:
            log(ERROR, f"shell convert transaction failed for {interaction.user} ({self.discord_id}): {e}", context="generate")
            db.connection.rollback()
            db.close()
            await interaction.edit_original_response(embed=None, view=None,
                content="An internal error occurred. No shells were deducted. Please try again.")
            return
        db.close()

        await interaction.edit_original_response(
            embed=None, view=None,
            content=(
                f"Converted **{self.cost}** {SHELL_EMOJI} into **{self.amount}** {ASPECT_EMOJI}!\n"
                f"Your aspects have been added to the collection queue."
            )
        )

    @button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="shell_confirm_cancel")
    async def cancel(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=None, view=None, content="Conversion cancelled.")

    async def on_timeout(self):
        # Disable all buttons when the 3-minute window expires
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self, content="This conversion request has expired.", embed=None)
        except Exception:
            pass  # message may already be gone


# ---- TAq FAQ embed builders ----

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
                "<#752917987853467669>\n"
                "<#1062062453137080462> \n"
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
                "full breakdown right here <#752917987853467669>!\n\n"
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


# ---- Cog ----

class Generate(commands.Cog):
    generate = SlashCommandGroup(
        "generate", "ADMIN: Generate persistent messages/panels",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=Permissions(administrator=True),
    )

    def __init__(self, client):
        self.client = client

    @generate.command(name="app_header", description="ADMIN: Post or update the application header with buttons")
    async def app_header(
        self,
        ctx: discord.ApplicationContext,
        level_requirement: discord.Option(int, "Minimum level requirement (e.g. 60, 80)", required=True),
        activity_requirement: discord.Option(str, "Weekly activity requirement (e.g. '4 hours a week')", required=True),
    ):
        await ctx.defer(ephemeral=True)

        banner_path = GUILD_INFO_ASSET_DIR / APPLICATIONS_BANNER
        if not banner_path.exists():
            return await ctx.followup.send(
                f"Missing application header banner asset: {banner_path.relative_to(Path(__file__).parent.parent)}",
                ephemeral=True,
            )

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

        view = ApplicationButtonView()

        existing_msg = None
        async for msg in ctx.channel.history(limit=50):
            if msg.author.id == self.client.user.id and msg.components:
                for row in msg.components:
                    for child in row.children:
                        if getattr(child, "custom_id", None) in ("apply_guild", "apply_community"):
                            existing_msg = msg
                            break
                    if existing_msg:
                        break
            if existing_msg:
                break

        if existing_msg:
            await existing_msg.edit(
                embed=embed,
                attachments=[],
                files=[discord.File(str(banner_path), filename=APPLICATIONS_BANNER)],
                view=view,
            )
            await ctx.followup.send("Application header updated!", ephemeral=True)
        else:
            await ctx.channel.send(
                embed=embed,
                file=discord.File(str(banner_path), filename=APPLICATIONS_BANNER),
                view=view,
            )
            await ctx.followup.send("Application header posted!", ephemeral=True)

    @generate.command(name="raid_collecting", description="ADMIN: Post the Raid Collecting panel")
    async def raid_collecting(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        channel = self.client.get_channel(RAID_COLLECTING_CHANNEL_ID)
        if not channel:
            return await ctx.followup.send(
                "❌ Could not find the raid-collecting channel.",
                ephemeral=True
            )

        banner_path = Path(__file__).parent.parent / "images" / "raids" / RAID_COLLECTING_BANNER
        if not banner_path.exists():
            return await ctx.followup.send(
                f"Missing raid collecting banner asset: {banner_path.relative_to(Path(__file__).parent.parent)}",
                ephemeral=True,
            )

        view = ClaimView()
        self.client.add_view(view)

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

        bot_messages = []
        async for msg in channel.history(limit=100):
            if msg.author.id != self.client.user.id:
                continue
            bot_messages.append(msg)

        banner_msg = None
        panel_msg = None
        for index, msg in enumerate(bot_messages):
            attachment_filenames = {attachment.filename for attachment in msg.attachments}
            if RAID_COLLECTING_BANNER in attachment_filenames:
                banner_msg = msg
                if not msg.embeds and index > 0:
                    panel_candidate = bot_messages[index - 1]
                    panel_description = panel_candidate.embeds[0].description if panel_candidate.embeds else ""
                    if "How to Claim Your Raid Rewards" in panel_description:
                        panel_msg = panel_candidate
                break

        if banner_msg:
            await banner_msg.edit(
                embed=embed,
                attachments=[],
                files=[discord.File(str(banner_path), filename=RAID_COLLECTING_BANNER)],
                view=view,
            )
            if panel_msg:
                await panel_msg.delete()
            return await ctx.followup.send("✅ Updated the raid-collecting message.", ephemeral=True)

        await channel.send(
            embed=embed,
            file=discord.File(str(banner_path), filename=RAID_COLLECTING_BANNER),
            view=view,
        )

        await ctx.followup.send("✅ Posted the raid-collecting message.", ephemeral=True)

    @generate.command(name="shell_convert", description="ADMIN: Post the Shells -> Aspects conversion panel")
    async def shell_convert(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        view = ShellConvertView()
        self.client.add_view(view)

        # Embed styled to match the raid collecting panel
        embed = discord.Embed(color=GENERATE_EMBED_COLOR)
        embed.description = dedent(f"""
            {SHELL_EMOJI} **Convert Shells into Aspects**

            Spend your shells to earn aspects directly. Shell conversion lets you use your shell balance to receive aspects without needing to complete additional raids.

            Conversion rate: **6 {SHELL_EMOJI} = 1 {ASPECT_EMOJI}**

            **Requirements**
            - No uncollected aspects already waiting
            - At least **6** shells in balance
            - At most **40** aspects per conversion
            - 3-day cooldown between conversions

            **How to Convert**
            1. Click the **Shells -> Aspects** button.
            2. Enter how many aspects you want to claim.
            3. Confirm your conversion in the preview.

            _Your aspects will be added to the collection queue automatically. Note: depending on current demand, it may take up to a week or two for your aspects to be delivered._
            """)

        await ctx.channel.send(embed=embed, view=view)
        await ctx.followup.send("Posted the shell conversion panel.", ephemeral=True)

    @generate.command(name="guild_info", description="ADMIN: Post the guild rules/info embeds with banners")
    async def guild_info(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        rules_banner_path = GUILD_INFO_ASSET_DIR / GUILD_RULES_BANNER
        info_banner_path = GUILD_INFO_ASSET_DIR / GUILD_INFO_BANNER
        missing_assets = [
            str(path.relative_to(Path(__file__).parent.parent))
            for path in (rules_banner_path, info_banner_path)
            if not path.exists()
        ]
        if missing_assets:
            return await ctx.followup.send(
                f"Missing guild info banner asset(s): {', '.join(missing_assets)}",
                ephemeral=True,
            )

        rules_msg = None
        info_msg = None
        async for msg in ctx.channel.history(limit=100):
            if msg.author.id != self.client.user.id:
                continue

            attachment_filenames = {attachment.filename for attachment in msg.attachments}
            if rules_msg is None and GUILD_RULES_BANNER in attachment_filenames:
                rules_msg = msg
            if info_msg is None and GUILD_INFO_BANNER in attachment_filenames:
                info_msg = msg
            if rules_msg and info_msg:
                break

        if rules_msg and info_msg:
            await rules_msg.edit(
                embed=_build_guild_rules_embed(),
                attachments=[],
                files=[discord.File(str(rules_banner_path), filename=GUILD_RULES_BANNER)],
                view=None,
            )
            await info_msg.edit(
                embed=_build_guild_info_embed(),
                attachments=[],
                files=[discord.File(str(info_banner_path), filename=GUILD_INFO_BANNER)],
                view=GuildInfoLinksView(),
            )
            return await ctx.followup.send("Updated the guild rules and info messages.", ephemeral=True)

        if rules_msg:
            await rules_msg.delete()
        if info_msg:
            await info_msg.delete()

        await ctx.channel.send(
            embed=_build_guild_rules_embed(),
            file=discord.File(str(rules_banner_path), filename=GUILD_RULES_BANNER),
        )
        await ctx.channel.send(
            embed=_build_guild_info_embed(),
            file=discord.File(str(info_banner_path), filename=GUILD_INFO_BANNER),
            view=GuildInfoLinksView(),
        )
        await ctx.followup.send("Posted the guild rules and info messages.", ephemeral=True)

    @generate.command(name="promotions", description="ADMIN: Post the promotions / rank-up info message")
    async def promotions(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        description = (
            "Promotions rely on your role in the guild and how you help it! We value all sorts of "
            "contributions, trying our best to give everyone a chance to rank up regardless of "
            "their interests and skills.\n"
            "\n"
            "🕗\u200e \u200e \u200e **Passive contributions**\n"
            "> • Being active in guild chat and/or on Discord\n"
            "> • Joining voice calls\n"
            "> • Playing with other guild members\n"
            "> • Helping out a fellow guild member\n"
            "> • Giving recommendations, advice and feedback.\n"
            "\n"
            "We are thankful for any positive effort that contributes to making TAq a friendly "
            "and welcoming community! ♡\n"
            "\n"
            f"🔧\u200e \u200e \u200e **Active contributions**\n"
            f"> • Joining the [war](https://discord.com/channels/{TAQ_GUILD_ID}/{WAR_INFO_CHANNEL_ID}) effort\n"
            f"> • Completing [Guild Raids](https://discord.com/channels/{TAQ_GUILD_ID}/{RAID_COLLECTING_CHANNEL_ID})\n"
            "> • Starting up giveaways (DM any chief)\n"
            "> • Recruiting new guild members\n"
            f"> • Donating [ingredients or materials](https://discord.com/channels/{TAQ_GUILD_ID}/{SHELL_EXCHANGE_CHANNEL_ID})\n"
            "\n"
            "The first rank-ups are easy to achieve. To get promoted to Manatee (recruiter), "
            "something as easy as regularly chatting with the guild is enough!\n"
            f"After reaching Angler, an [application]({WEBSITE_URL}/login?redirect=/apply/hammerhead) "
            "is required in order to rank up and become a part of our HR team."
        )

        warring_description = (
            "⚔️ **Ranking up through warring**\n"
            "Jumping into guild wars is like hitting the fast lane to rank up in no time! It's an "
            "absolute blast and a great way to dive into exciting end-game content. You get to "
            "team up with fellow guild members, form strategies, and kick some towers!\n"
            "\n"
            "> Being active in wars is super important for our guild because it keeps us strong "
            "and competitive. Plus, it's not just about the thrill – having war power means we "
            "get to hold territories and generate sweet emeralds, which we can then spend on "
            "guild events and community giveaways. So, if you're up for some action-packed fun "
            "and want to help our guild thrive, join the war efforts today!\n"
            "\n"
            "The amount of wars you participate in will always be taken into account for "
            "promotion waves. Bonus points if you help with starting rounds of FFA, teaching "
            "other members, pinging when we get attacked, etc!\n"
            "\n"
            "⏩ Rank up shortcuts\n"
            "```\n"
            "- Starfish/Manatee → Piranha = learn how to queue\n"
            "- Piranha → Angler = learning about defending our claim\n"
            "- Angler → Swordfish = learn how to eco\n"
            "```\n"
            "We are always looking for new warrers, so do not hesitate to ask for information!"
        )

        embed1 = discord.Embed(description=description, color=GENERATE_EMBED_COLOR)
        embed2 = discord.Embed(description=warring_description, color=GENERATE_EMBED_COLOR)
        await ctx.channel.send(embeds=[embed1, embed2])
        await ctx.followup.send("Posted the promotions info message.", ephemeral=True)

    @generate.command(name="taq-faq", description="ADMIN: Post or update the TAq FAQ messages")
    async def taq_faq(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)

        banner_path = GUILD_INFO_ASSET_DIR / TAQ_FAQ_BANNER
        if not banner_path.exists():
            return await ctx.followup.send(
                f"Missing TAq FAQ banner asset: {banner_path.relative_to(Path(__file__).parent.parent)}",
                ephemeral=True,
            )

        page1 = _build_taq_faq_embeds_page1()
        page2 = _build_taq_faq_embeds_page2()

        banner_msg = None
        page2_msg = None
        async for msg in ctx.channel.history(limit=100):
            if msg.author.id != self.client.user.id:
                continue
            filenames = {a.filename for a in msg.attachments}
            if banner_msg is None and TAQ_FAQ_BANNER in filenames:
                banner_msg = msg
            if page2_msg is None and msg.embeds and msg.embeds[0].title == "𓆉 Vanity Roles":
                page2_msg = msg
            if banner_msg and page2_msg:
                break

        if banner_msg:
            await banner_msg.edit(
                embeds=page1,
                attachments=[],
                files=[discord.File(str(banner_path), filename=TAQ_FAQ_BANNER)],
            )
            if page2_msg:
                await page2_msg.edit(embeds=page2)
            else:
                await ctx.channel.send(embeds=page2)
            return await ctx.followup.send("✅ Updated the TAq FAQ messages.", ephemeral=True)

        await ctx.channel.send(
            embeds=page1,
            file=discord.File(str(banner_path), filename=TAQ_FAQ_BANNER),
        )
        await ctx.channel.send(embeds=page2)
        await ctx.followup.send("✅ Posted the TAq FAQ messages.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(ApplicationButtonView())
        self.client.add_view(ClaimView())
        self.client.add_view(ShellConvertView())


def setup(client):
    client.add_cog(Generate(client))
