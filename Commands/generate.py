import base64
import datetime
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

import discord
from discord.ext import commands
from discord.ui import View, button

from Helpers import local_ids
from Helpers.role_buttons import toggle_role
from Commands.tickets import TICKET_PANEL_ACTIONS
from Helpers.bot_panels import PanelAction
from Helpers.database import DB, apply_shell_delta, get_shell_conversion, record_shell_transaction
from Helpers.logger import log, ERROR
from Helpers.variables import (
    WEBSITE_URL,
    SHELL_EMOJI,
    ASPECT_EMOJI,
)

APPLICATION_TOKEN_SECRET = os.getenv("APPLICATION_TOKEN_SECRET", "")
GENERATE_EMBED_COLOR = 0x94C1FF
SHELL_CONVERT_BUTTON_ID = "shells_to_aspects"


def _custom_emoji(name: str, emoji_id: int) -> discord.PartialEmoji:
    return local_ids.partial_emoji(name, emoji_id)


@dataclass(frozen=True)
class RoleButtonConfig:
    key: str
    label: str
    role_id: int
    emoji: str | discord.PartialEmoji | None = None
    row: int | None = None
    gate_role_id: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "role_id", local_ids.role(self.role_id))
        object.__setattr__(self, "gate_role_id", local_ids.role(self.gate_role_id))


REACTION_ROLES = (
    RoleButtonConfig("reaction_class_builder", "Class Builder", 906269952938475602, "\U0001F3D7\ufe0f", row=0),
    RoleButtonConfig("reaction_crafter", "Crafter", 906265677667663902, "\U0001F477", row=0),
    RoleButtonConfig("reaction_ingredient_grinder", "Ingredient Grinder", 1050233131183112255, "\U0001F41A", row=0),
    RoleButtonConfig("reaction_merman", "Merman", 796817826987114557, "\U0001F468", row=1),
    RoleButtonConfig("reaction_mermaid", "Mermaid", 796817681276862485, "\U0001F469", row=1),
    RoleButtonConfig("reaction_ghost_fish", "Ghost Fish", 796817901188808796, "\U0001F47B", row=1),
    RoleButtonConfig("reaction_america", "America", 939940600248160316, "\U0001F30E", row=2),
    RoleButtonConfig("reaction_europe", "Europe", 939940342457856000, "\U0001F30D", row=2),
    RoleButtonConfig("reaction_asia_oceania", "Asia/Oceania", 939944900227657728, "\U0001F30F", row=2),
    RoleButtonConfig("reaction_events", "Events", 1209952006144401500, "\U0001F389", row=3),
    RoleButtonConfig("reaction_giveaways", "Giveaways", 919714824731127818, "\U0001F381", row=3),
    RoleButtonConfig("reaction_prelude_annihilation", "Prelude to Annihilation", 1275082923812458506, "\U0001F479", row=3),
)

PING_ROLES = (
    RoleButtonConfig("ping_notg", "NOTG", 1316539196378452039, _custom_emoji("notg", 1316539942524031017), row=0),
    RoleButtonConfig("ping_nol", "NOL", 1316539324006666300, _custom_emoji("nol", 1316539940418621530), row=0),
    RoleButtonConfig("ping_tcc", "TCC", 1316539449299173447, _custom_emoji("tcc", 1316539938917060658), row=0),
    RoleButtonConfig("ping_tna", "TNA", 1316539530655825930, _custom_emoji("tna", 1316539936438222850), row=0),
    RoleButtonConfig("ping_wtp", "WTP", 1485993226794962974, _custom_emoji("wtp", 1498097156282781847), row=0),
    RoleButtonConfig("ping_graid", "Graid", 1485993830778929182, _custom_emoji("graid", 1515117095250301018), row=1),
    RoleButtonConfig("ping_aurora_raids", "Aurora Raids", 1565835104708858096, row=1),
    RoleButtonConfig("ping_combat_xp_bomb", "Combat XP Bomb", 1423301085350858832, "\U0001F5E1\ufe0f", row=2),
    RoleButtonConfig("ping_loot_bomb", "Loot Bomb", 1423302359014047764, "\U0001F48E", row=2),
    RoleButtonConfig("ping_loot_chest_bomb", "Loot Chest Bomb", 1423302456091213978, "\U0001F4E6", row=2),
    RoleButtonConfig("ping_profession_xp_bomb", "Profession XP Bomb", 1423302526874292279, "\u26CF\ufe0f", row=3),
    RoleButtonConfig("ping_profession_speed_bomb", "Profession Speed Bomb", 1423302571640229961, "\U0001F6E0\ufe0f", row=3),
    RoleButtonConfig("ping_dungeon_bomb", "Dungeon Bomb", 1423302825651212349, "\U0001F3F0", row=3),
)


class SelfRoleButton(discord.ui.Button):
    def __init__(self, spec: RoleButtonConfig):
        super().__init__(
            label=spec.label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"role_info:{spec.key}",
            emoji=spec.emoji,
            row=spec.row,
        )
        self.spec = spec

    async def callback(self, interaction: discord.Interaction):
        await self.view.toggle_role(interaction, self.spec)


class SelfRoleView(View):
    def __init__(self, specs: tuple[RoleButtonConfig, ...]):
        super().__init__(timeout=None)
        for spec in specs:
            self.add_item(SelfRoleButton(spec))

    async def toggle_role(self, interaction: discord.Interaction, spec: RoleButtonConfig):
        # Legacy role_info:<key> buttons (Role Info before its role buttons were
        # republished as panel role buttons); same toggle and safety rule.
        await toggle_role(interaction, spec.role_id, spec.gate_role_id)


def _reaction_role_view() -> SelfRoleView:
    return SelfRoleView(REACTION_ROLES)


def _ping_role_view() -> SelfRoleView:
    return SelfRoleView(PING_ROLES)


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

        apply_shell_delta(
            db, interaction.user.id, self.count, self.count,
            source='raid_conversion', ign=ign,
            reason=f"Converted {self.count} raid(s)",
        )

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

    @button(label="Shells -> Aspects", style=discord.ButtonStyle.green, custom_id=SHELL_CONVERT_BUTTON_ID)
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

        # Shell balance check
        db.cursor.execute(
            'SELECT balance, last_aspect_convert_at FROM shells WHERE "user" = %s',
            (str(interaction.user.id),)
        )
        shells_row = db.cursor.fetchone()
        balance = shells_row[0] if shells_row else 0
        # The rate is edited on the website, so read it per click.
        conversion = get_shell_conversion()
        per_aspect = conversion["shells_per_aspect"]
        if balance < per_aspect:
            db.close()
            return await interaction.response.send_message(
                f"You need at least **{per_aspect}** shells to convert "
                f"(you have **{balance}**).",
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

        # All guards passed -- open the amount modal
        max_aspects = min(
            balance // per_aspect,
            conversion["max_aspects_per_conversion"],
        )
        modal = ShellsToAspectsModal(
            discord_id=interaction.user.id,
            uuid=uuid,
            balance=balance,
            max_aspects=max_aspects,
            per_aspect=per_aspect,
        )
        await interaction.response.send_modal(modal)


# ---- Shells to aspects modal ----

class ShellsToAspectsModal(discord.ui.Modal):
    def __init__(self, discord_id: int, uuid: str, balance: int, max_aspects: int, per_aspect: int):
        super().__init__(title="Shells -> Aspects")
        self.discord_id  = discord_id
        self.uuid        = uuid
        self.balance     = balance
        self.max_aspects = max_aspects
        self.per_aspect  = per_aspect

        self.amount_input = discord.ui.InputText(
            # Discord caps labels at 45 characters; this fits up to 5-digit numbers.
            label=f"Aspects (max {max_aspects}, {per_aspect} shells each)",
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

        cost = amount * self.per_aspect
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
             RETURNING shells, balance
            """, (self.cost, str(self.discord_id), self.cost))
            row = db.cursor.fetchone()

            if row is None:
                db.connection.rollback()
                db.close()
                await interaction.edit_original_response(embed=None, view=None,
                    content="You no longer have enough shells for this conversion.")
                return

            shells_after, balance_after = row
            record_shell_transaction(
                db, self.discord_id, -self.cost, 0, balance_after, shells_after,
                source='aspect_conversion', actor_id=self.discord_id,
                reason=f"Converted {self.cost} shells into {self.amount} aspect(s)",
            )

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


# ---- Bot panels ----
# The messages these views sit on are edited on the website (/exec/bot-panels)
# and posted by Tasks/publish_bot_panels.py. A panel can place any of these
# buttons; the custom_id is what the persistent views below answer to.

PANEL_ACTIONS: dict[str, PanelAction] = {
    "apply_guild": PanelAction("apply_guild", "Guild Member Application", "Applications", "🐠", "primary"),
    "apply_community": PanelAction("apply_community", "Community Member Application", "Applications", "🪼", "success"),
    "raid_claim": PanelAction("raid_claim", "Claim rewards", "Rewards", None, "success"),
    SHELL_CONVERT_BUTTON_ID: PanelAction(SHELL_CONVERT_BUTTON_ID, "Shells -> Aspects", "Rewards", None, "success"),
    **TICKET_PANEL_ACTIONS,
    **{
        f"role:{spec.key}": PanelAction(
            f"role_info:{spec.key}", spec.label, group,
            str(spec.emoji) if spec.emoji else None, "secondary",
        )
        for group, specs in (("Reaction roles", REACTION_ROLES), ("Ping roles", PING_ROLES))
        for spec in specs
    },
}


class Generate(commands.Cog):
    """Registers the persistent views behind bot panel buttons."""

    def __init__(self, client):
        self.client = client

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(ApplicationButtonView())
        self.client.add_view(ClaimView())
        self.client.add_view(ShellConvertView())
        self.client.add_view(_reaction_role_view())
        self.client.add_view(_ping_role_view())


def setup(client):
    client.add_cog(Generate(client))
