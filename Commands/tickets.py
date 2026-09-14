import asyncio
import re

import discord
from discord import ApplicationContext, SlashCommandGroup
from discord.ext import commands

from Helpers.logger import ERROR, log
from Helpers.tickets import (
    close_ticket,
    create_ticket_record,
    get_next_ticket_number,
    get_open_ticket_for_user,
    get_ticket_by_channel,
    get_ticket_counters,
    get_ticket_creator_ign,
    set_ticket_counter,
)
from Helpers.variables import (
    HOME_GUILD_IDS,
    SHELL_TICKET_CATEGORY_ID,
    WAR_TICKET_CATEGORY_ID,
    is_home_guild,
)


TICKET_EMBED_COLOR = 0x2FBE5F
OPEN_BUTTON_CUSTOM_IDS = {
    "war": "ticket_open:war",
    "shell": "ticket_open:shell",
}
CLOSE_BUTTON_CUSTOM_ID = "ticket_close"
MODERATOR_ROLE_ID = 741298208470466592
SR_MODERATOR_ROLE_ID = 754735279692054628
WAR_TRAINER_ROLE_ID = 1033420156690513970
SHELL_MANAGER_ROLE_ID = 1332814674869096498


def _ticket_label(ticket_type: str) -> str:
    return "War" if ticket_type == "war" else "Shell"


def _channel_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _ticket_channel_name(ticket_type: str, ticket_number: int, creator_name: str) -> str:
    base = f"{ticket_type}-ticket-{ticket_number:04d}"
    suffix = _channel_slug(creator_name)
    max_suffix_len = 100 - len(base) - 1
    return f"{base}-{suffix[:max_suffix_len]}"


def _closed_channel_name(ticket_type: str, ticket_number: int) -> str:
    return f"closed-{ticket_type}-{ticket_number:04d}"


def _category_id(ticket_type: str) -> int | None:
    return WAR_TICKET_CATEGORY_ID if ticket_type == "war" else SHELL_TICKET_CATEGORY_ID


def _staff_role_ids(ticket_type: str) -> tuple[int | None, ...]:
    if ticket_type == "war":
        return (SR_MODERATOR_ROLE_ID, WAR_TRAINER_ROLE_ID)
    return (SR_MODERATOR_ROLE_ID, MODERATOR_ROLE_ID, SHELL_MANAGER_ROLE_ID)


def _resolve_support_roles(guild: discord.Guild, ticket_type: str) -> list[discord.Role]:
    roles: list[discord.Role] = []
    seen: set[int] = set()
    for role_id in _staff_role_ids(ticket_type):
        if role_id is None:
            continue
        role = guild.get_role(role_id)
        if role and role.id not in seen:
            roles.append(role)
            seen.add(role.id)
    return roles


def _is_ticket_staff(member: discord.Member, ticket_type: str) -> bool:
    if member.guild_permissions.manage_channels:
        return True
    support_role_ids = {role.id for role in _resolve_support_roles(member.guild, ticket_type)}
    return any(role.id in support_role_ids for role in member.roles)


def _panel_embed(ticket_type: str) -> discord.Embed:
    if ticket_type == "war":
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

    return discord.Embed(
        title="Shell Collecting",
        description="To claim your shells create a ticket \U0001F4E9",
        color=TICKET_EMBED_COLOR,
    )


def _welcome_embed(ticket_type: str) -> discord.Embed:
    if ticket_type == "war":
        return discord.Embed(
            description=(
                "Thank you for reaching out!\n"
                "Let us know which aspect of warring you'd like to focus on"
            ),
            color=TICKET_EMBED_COLOR,
        )

    return discord.Embed(
        description=(
            "Thank you for donating!\n"
            "Please send a screenshot as evidence. A Narwhal will soon update your "
            "profile and close the ticket as soon as the transaction is completed."
        ),
        color=TICKET_EMBED_COLOR,
    )


class TicketOpenView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create ticket",
        style=discord.ButtonStyle.secondary,
        custom_id=OPEN_BUTTON_CUSTOM_IDS["war"],
        emoji="\U0001F4E9",
    )
    async def war_ticket(self, _: discord.ui.Button, interaction: discord.Interaction):
        await self._open_ticket(interaction, "war")

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.primary,
        custom_id=OPEN_BUTTON_CUSTOM_IDS["shell"],
        emoji="\U0001F4E9",
    )
    async def shell_ticket(self, _: discord.ui.Button, interaction: discord.Interaction):
        await self._open_ticket(interaction, "shell")

    async def _open_ticket(self, interaction: discord.Interaction, ticket_type: str):
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None or not is_home_guild(guild.id):
            await interaction.followup.send("Tickets can only be opened in the TAq server.", ephemeral=True)
            return

        category_id = _category_id(ticket_type)
        if category_id is None:
            await interaction.followup.send(
                f"{_ticket_label(ticket_type)} tickets are not configured in this environment.",
                ephemeral=True,
            )
            return

        category = guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send(
                f"Could not find the {_ticket_label(ticket_type).lower()} ticket category.",
                ephemeral=True,
            )
            return

        existing = await asyncio.to_thread(get_open_ticket_for_user, ticket_type, interaction.user.id)
        if existing:
            existing_channel = guild.get_channel(existing.channel_id)
            if existing_channel is None:
                try:
                    existing_channel = await guild.fetch_channel(existing.channel_id)
                except discord.NotFound:
                    await asyncio.to_thread(
                        close_ticket,
                        existing.channel_id,
                        interaction.client.user.id if interaction.client.user else 0,
                        "Channel missing during ticket open",
                    )
                    existing_channel = None
                except discord.Forbidden:
                    await interaction.followup.send(
                        f"You already have an open {_ticket_label(ticket_type).lower()} ticket, "
                        "but I cannot access its channel.",
                        ephemeral=True,
                    )
                    return
            if existing_channel is not None:
                await interaction.followup.send(
                    f"You already have an open {_ticket_label(ticket_type).lower()} ticket: "
                    f"{existing_channel.mention}",
                    ephemeral=True,
                )
                return

        member = interaction.user
        if not isinstance(member, discord.Member):
            try:
                member = await guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                member = None

        ticket_number = await asyncio.to_thread(get_next_ticket_number, ticket_type)
        creator_ign = await asyncio.to_thread(get_ticket_creator_ign, interaction.user.id)
        creator_name = creator_ign or interaction.user.display_name or interaction.user.name
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            ),
        }
        if member is not None:
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
        for role in _resolve_support_roles(guild, ticket_type):
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )

        try:
            channel = await guild.create_text_channel(
                name=_ticket_channel_name(ticket_type, ticket_number, creator_name),
                category=category,
                overwrites=overwrites,
                topic=(
                    f"{_ticket_label(ticket_type)} ticket {ticket_number:04d} "
                    f"opened by {interaction.user} ({interaction.user.id})"
                ),
                reason=f"{_ticket_label(ticket_type)} ticket opened by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send("I do not have permission to create that ticket.", ephemeral=True)
            return
        except discord.HTTPException as exc:
            log(ERROR, f"Failed to create {ticket_type} ticket: {exc!r}", context="tickets")
            await interaction.followup.send("Could not create the ticket. Please try again.", ephemeral=True)
            return

        try:
            await asyncio.to_thread(
                create_ticket_record,
                ticket_type,
                ticket_number,
                channel.id,
                interaction.user.id,
                creator_name,
            )
        except Exception as exc:
            log(ERROR, f"Failed to record ticket {channel.id}: {exc!r}", context="tickets")

        await channel.send(
            content=f"{interaction.user.mention} Welcome",
            embed=_welcome_embed(ticket_type),
            view=TicketCloseView(),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        await interaction.followup.send(f"Created {channel.mention}.", ephemeral=True)


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.secondary,
        custom_id=CLOSE_BUTTON_CUSTOM_ID,
        emoji="\U0001F512",
    )
    async def close(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("This button only works in ticket channels.", ephemeral=True)
            return

        record = await asyncio.to_thread(get_ticket_by_channel, channel.id)
        if record is None:
            await interaction.followup.send("This is not a Tort ticket channel.", ephemeral=True)
            return
        if record.status == "closed":
            await interaction.followup.send("This ticket is already closed.", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.followup.send("Could not verify your server roles.", ephemeral=True)
            return
        if member.id != record.opener_discord_id and not _is_ticket_staff(member, record.ticket_type):
            await interaction.followup.send("Only the ticket owner or staff can close this ticket.", ephemeral=True)
            return

        changed = await asyncio.to_thread(close_ticket, channel.id, member.id, "Closed from Discord button")
        if not changed:
            await interaction.followup.send("This ticket is already closed.", ephemeral=True)
            return

        opener = channel.guild.get_member(record.opener_discord_id)
        if opener is None:
            try:
                opener = await channel.guild.fetch_member(record.opener_discord_id)
            except discord.HTTPException:
                opener = None
        if opener is not None:
            try:
                await channel.set_permissions(opener, overwrite=None)
            except discord.Forbidden:
                pass

        try:
            await channel.edit(
                name=_closed_channel_name(record.ticket_type, record.ticket_number),
                reason=f"Ticket closed by {member}",
            )
        except discord.HTTPException:
            pass

        try:
            await interaction.message.edit(view=TicketClosedView())
        except discord.HTTPException:
            pass

        await channel.send(f"Ticket closed by {member.mention}.")
        await interaction.followup.send("Ticket closed.", ephemeral=True)


class TicketClosedView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Closed",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_closed",
        emoji="\U0001F512",
        disabled=True,
    )
    async def close(self, _: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.send_message("This ticket is already closed.", ephemeral=True)


class Tickets(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    tickets = SlashCommandGroup(
        "tickets",
        "Manage Tort ticket panels",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_channels=True),
    )

    @tickets.command(name="post", description="Post a war or shell ticket panel")
    async def post(
        self,
        ctx: ApplicationContext,
        ticket_type: discord.Option(str, "Ticket panel type", choices=["war", "shell"]),
    ):
        await ctx.defer(ephemeral=True)
        if ctx.guild is None or not is_home_guild(ctx.guild.id):
            await ctx.followup.send("Ticket panels can only be posted in a TAq server.", ephemeral=True)
            return

        view = TicketOpenView()
        for item in list(view.children):
            if getattr(item, "custom_id", None) != OPEN_BUTTON_CUSTOM_IDS[ticket_type]:
                view.remove_item(item)

        await ctx.channel.send(embed=_panel_embed(ticket_type), view=view)
        await ctx.followup.send(f"Posted the {_ticket_label(ticket_type).lower()} ticket panel.", ephemeral=True)

    @tickets.command(name="counters", description="Show stored ticket counters")
    async def counters(self, ctx: ApplicationContext):
        await ctx.defer(ephemeral=True)
        counters = await asyncio.to_thread(get_ticket_counters)
        await ctx.followup.send(
            f"War tickets: `{counters['war']:04d}`\n"
            f"Shell tickets: `{counters['shell']:04d}`",
            ephemeral=True,
        )

    @tickets.command(name="set-counter", description="Set the current ticket counter")
    async def set_counter(
        self,
        ctx: ApplicationContext,
        ticket_type: discord.Option(str, "Ticket counter type", choices=["war", "shell"]),
        current_number: discord.Option(int, "Current last-used ticket number", min_value=0),
    ):
        await ctx.defer(ephemeral=True)
        await asyncio.to_thread(set_ticket_counter, ticket_type, current_number)
        await ctx.followup.send(
            f"{_ticket_label(ticket_type)} ticket counter set to `{current_number:04d}`. "
            f"The next ticket will be `{current_number + 1:04d}`.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_ready(self):
        self.client.add_view(TicketOpenView())
        self.client.add_view(TicketCloseView())


def setup(client: commands.Bot):
    client.add_cog(Tickets(client))
