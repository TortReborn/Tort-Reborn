import os
import time
import asyncio
from datetime import datetime
from io import BytesIO

import discord
from discord import SlashCommandGroup, ApplicationContext
from discord.ext import commands
from discord.ui import Modal, InputText
from PIL import Image, ImageDraw, ImageFont, ImageOps

from Helpers.classes import LinkAccount, PlayerStats, PlayerShells
from Helpers.database import DB, apply_shell_delta
from Helpers.links import LinkConflictError, link_change_refusal
from Helpers.member_roles import apply_rank_roles, rank_change_summary
from Helpers.registration import upsert_identity
from Helpers.functions import addLine, split_sentence, expand_image, getPlayerUUID, timed_get
from Helpers.logger import log, ERROR
from Helpers.variables import HOME_GUILD_IDS, discord_ranks


def _build_shell_modal_card(ign, operation, amount, reason, user_id, actor_name, actor_id):
    """Build the shell-adjustment card for the link-and-set modal path, and
    write the resulting link/balance rows. Blocking (HTTP + Pillow + DB) —
    always call via asyncio.to_thread so it never stalls the event loop."""
    player = PlayerStats(ign, 1, False)
    if player.error:
        raise ValueError(f"could not retrieve player data for '{ign}'")

    img = Image.new('RGBA', (375, 95), '#100010e2')
    draw = ImageDraw.Draw(img); draw.fontmode = '1'
    font = ImageFont.truetype('images/profile/game.ttf', 19)

    try:
        url = f"https://visage.surgeplay.com/bust/75/{player.UUID}"
        skin = Image.open(BytesIO(timed_get(url).content))
    except:
        skin = Image.open('images/profile/x-steve.webp')
    img.paste(skin, (10, 10), skin)

    diff = f'+{amount}' if operation == 'add' else f'-{amount}'

    # Connect only now that all external HTTP has completed. The modal path
    # exists for users with no link yet: establish the identity properly
    # (uuid required) rather than seeding a uuid-less row (TAQ-76).
    db = DB(); db.connect()
    try:
        upsert_identity(db.cursor, discord_id=user_id, ign=player.username, uuid=player.UUID, linked_by=actor_id)
        if operation == 'add':
            new_total, new_balance = apply_shell_delta(
                db, user_id, amount, amount, source='manage_shells', ign=ign,
                actor_id=actor_id, actor_name=actor_name, reason=reason or None,
            )
        else:
            new_total, new_balance = apply_shell_delta(
                db, user_id, -amount, 0, source='manage_shells', ign=ign,
                actor_id=actor_id, actor_name=actor_name, reason=reason or None,
            )
        db.cursor.execute(
            "INSERT INTO audit_log (log_type, actor_name, actor_id, action) VALUES (%s, %s, %s, %s)",
            ('shell', actor_name, actor_id,
             f'added {amount} to {player.username}.' if operation == 'add'
             else f'removed {amount} from {player.username}.')
        )
        db.connection.commit()
    finally:
        db.close()

    addLine(f'&f{player.username}', draw, font, 95, 15)
    addLine(f'&7Balance: &f{new_balance} &7({diff}&7)', draw, font, 95, 40)
    if operation == 'add':
        addLine(f'&7All-Time: &f{new_total} &7({diff}&7)', draw, font, 95, 61)
    else:
        addLine(f'&7All-Time: &f{new_total}', draw, font, 95, 61)

    if reason:
        for line in split_sentence(reason):
            img, draw = expand_image(img)
            addLine(f'&3{line}', draw, font, 10, img.height - 25)

    img = ImageOps.expand(img, border=(2, 2), fill='#100010e2')
    draw = ImageDraw.Draw(img)
    draw.rectangle((2, 2, img.width - 3, img.height - 3), outline='#240059', width=2)

    buf = BytesIO(); img.save(buf, 'PNG'); buf.seek(0)
    return discord.File(buf, filename=f"shell_{int(time.time())}.png")


def _rank_lookup(invoker_id, target_id):
    """Fetch the invoker's rank, the target's rank, and the target's ign rows
    in one short checkout. Blocking DB read — call via asyncio.to_thread."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            "SELECT rank FROM discord_links WHERE discord_id = %s", (invoker_id,)
        )
        inv = db.cursor.fetchone()
        db.cursor.execute(
            "SELECT rank FROM discord_links WHERE discord_id = %s", (target_id,)
        )
        tgt = db.cursor.fetchone()
        db.cursor.execute(
            "SELECT ign FROM discord_links WHERE discord_id = %s", (target_id,)
        )
        rows = db.cursor.fetchall()
        return inv, tgt, rows
    finally:
        db.close()


def _rank_update(target_id, rank):
    """Persist a rank change. Blocking DB write — call via asyncio.to_thread."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            "UPDATE discord_links SET rank = %s WHERE discord_id = %s",
            (rank, target_id)
        )
        db.connection.commit()
    finally:
        db.close()


def _link_user(user_id, ign, actor_id):
    """Resolve the ign's UUID (HTTP first — no checkout held across it), then
    upsert the identity row. Blocking — call via asyncio.to_thread.

    Returns 'updated' or 'linked', or None when the ign cannot be resolved.
    The rank is not touched: linking says who the account is, /manage rank
    says what standing they have (TAQ-76)."""
    player_data = getPlayerUUID(ign)
    if not player_data:
        return None
    canonical_ign, uuid = player_data

    db = DB(); db.connect()
    try:
        db.cursor.execute(
            "SELECT 1 FROM discord_links WHERE discord_id = %s", (user_id,)
        )
        existed = db.cursor.fetchone() is not None
        upsert_identity(db.cursor, discord_id=user_id, ign=canonical_ign, uuid=uuid, linked_by=actor_id)
        db.cursor.execute(
            "INSERT INTO shells (\"user\") VALUES (%s) ON CONFLICT DO NOTHING",
            (str(user_id),)
        )
        db.connection.commit()
        return 'updated' if existed else 'linked'
    finally:
        db.close()


def _unlink_user(user_id, actor_id, actor_name):
    """Delete the identity row (rank and all) and leave an audit line.
    Returns the (ign, uuid) that was removed, or None. Blocking."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            "DELETE FROM discord_links WHERE discord_id = %s RETURNING ign, uuid::text", (user_id,)
        )
        row = db.cursor.fetchone()
        if row:
            db.cursor.execute(
                "INSERT INTO audit_log (log_type, actor_name, actor_id, action) VALUES (%s, %s, %s, %s)",
                ('link', actor_name, actor_id, f'unlinked {row[0]} ({row[1]}) from discord {user_id}.')
            )
        db.connection.commit()
        return row
    finally:
        db.close()


def _shells_lookup(user_id):
    """Return the discord_links rows for a user (used to decide render vs modal).
    Blocking DB read — call via asyncio.to_thread."""
    db = DB(); db.connect()
    try:
        db.cursor.execute(
            "SELECT ign FROM discord_links WHERE discord_id = %s", (user_id,)
        )
        return db.cursor.fetchall()
    finally:
        db.close()


def _build_shells_card(user_id, ign, operation, amount, actor_name, actor_id):
    """Build the shell-adjustment card for the /manage shells render path, and
    apply the balance + audit writes. Blocking (HTTP + Pillow + DB) — always
    call via asyncio.to_thread so it never stalls the event loop."""
    player = PlayerShells(user_id)

    img = Image.new('RGBA', (375, 95), '#100010e2')
    draw = ImageDraw.Draw(img); draw.fontmode = '1'
    font = ImageFont.truetype('images/profile/game.ttf', 19)

    try:
        headers = {'User-Agent': os.getenv("visage_UA")}
        url = f"https://visage.surgeplay.com/bust/75/{player.UUID}"
        skin = Image.open(BytesIO(timed_get(url, headers=headers).content))
    except:
        skin = Image.open('images/profile/X-Steve.webp')
    img.paste(skin, (10, 10), skin)

    # Connect only now that all external HTTP has completed.
    db = DB(); db.connect()
    try:
        diff = f'+{amount}' if operation == 'add' else f'-{amount}'
        if operation == 'add':
            new_total, new_balance = apply_shell_delta(
                db, user_id, amount, amount, source='manage_shells', ign=ign,
                actor_id=actor_id, actor_name=actor_name,
            )
        else:
            new_total, new_balance = apply_shell_delta(
                db, user_id, -amount, 0, source='manage_shells', ign=ign,
                actor_id=actor_id, actor_name=actor_name,
            )

        addLine(f'&f{player.username}', draw, font, 95, 15)
        addLine(f'&7Balance: &f{new_balance} &7({diff}&7)', draw, font, 95, 40)
        if operation == 'add':
            addLine(f'&7All-Time: &f{new_total} &7({diff}&7)', draw, font, 95, 61)
        else:
            addLine(f'&7All-Time: &f{new_total}', draw, font, 95, 61)

        db.connection.commit()

        try:
            db.cursor.execute(
                "INSERT INTO audit_log (log_type, actor_name, actor_id, action) VALUES (%s, %s, %s, %s)",
                ('shell', actor_name, actor_id,
                 f'added {amount} to {player.username}.' if operation == 'add'
                 else f'removed {amount} from {player.username}.')
            )
            db.connection.commit()
        except Exception as e:
            log(ERROR, f"audit_log write failed: {e}", context="manage")
    finally:
        db.close()

    img = ImageOps.expand(img, border=(2, 2), fill='#100010e2')
    draw = ImageDraw.Draw(img)
    draw.rectangle((2, 2, img.width - 3, img.height - 3), outline='#240059', width=2)
    buf = BytesIO(); img.save(buf, 'PNG'); buf.seek(0)
    return discord.File(buf, filename=f"shells_{int(time.time())}.png")


class ShellModalName(Modal):
    def __init__(self, user: discord.Member, operation: str, amount: int, reason: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.operation = operation
        self.amount = amount
        self.reason = reason
        self.add_item(
            InputText(
                label="Player's Name", 
                placeholder="Player's In-Game Name without rank"
            )
        )

    async def callback(self, interaction: discord.Interaction):
        # Modal errors route to Modal.on_error (stderr), not the command error
        # handler — an unhandled raise here would strand the deferred
        # interaction at "thinking…" forever. Report failures ourselves.
        await interaction.response.defer()
        try:
            file = await asyncio.to_thread(
                _build_shell_modal_card,
                self.children[0].value,
                self.operation,
                self.amount,
                self.reason,
                self.user.id,
                interaction.user.name,
                interaction.user.id,
            )
        except LinkConflictError as e:
            await interaction.followup.send(e.user_message(), ephemeral=True)
            return
        except Exception as e:
            log(ERROR, f"Shell modal card failed for '{self.children[0].value}': {e}", context="manage")
            await interaction.followup.send(
                f"⚠️ Could not process shells for `{self.children[0].value}`. "
                "Check the name and try again.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(file=file)


class Manage(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client

    manage_group = SlashCommandGroup(
        'manage', 'HR: Guild management commands',
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True)
    )


    @manage_group.command(name='rank', description='HR: Assign or update a user\'s guild rank')
    async def rank(
        self,
        ctx: ApplicationContext,
        user: discord.Member,
        rank: discord.Option(str, choices=list(discord_ranks.keys()))
    ):
        # One short checkout for all three reads; permission math is pure and
        # runs here. No pool slot is held across the role-edit awaits below.
        inv, tgt, rows = await asyncio.to_thread(_rank_lookup, ctx.user.id, user.id)

        if not inv or inv[0] not in discord_ranks:
            await ctx.respond(':no_entry: You must link your account before assigning ranks.', ephemeral=True)
            return
        initiator_rank = inv[0]
        initiator_index = list(discord_ranks).index(initiator_rank)

        # Prevent self-assignment
        if user.id == ctx.user.id:
            await ctx.respond(':no_entry: You cannot change your own rank.', ephemeral=True)
            return

        if tgt and tgt[0] in discord_ranks:
            current_rank = tgt[0]
            target_index = list(discord_ranks).index(current_rank)
            if target_index >= initiator_index:
                await ctx.respond(':no_entry: You can only change ranks for members below your own.', ephemeral=True)
                return

        # Validate that the chosen new rank is below the initiator's rank
        new_rank_index = list(discord_ranks).index(rank)
        if new_rank_index >= initiator_index:
            await ctx.respond(':no_entry: You cannot assign a rank equal to or above your own.', ephemeral=True)
            return

        if rows:
            await ctx.defer(ephemeral=True)
            # Same role planner as the modal path below (TAQ-86)
            added, removed = await apply_rank_roles(
                user, ctx.guild.roles, rank, reason=f'/manage rank (ran by {ctx.user.name})'
            )
            # Persist the rank change (its own brief checkout)
            await asyncio.to_thread(_rank_update, user.id, rank)
            # Update nickname
            try:
                current = user.nick or user.name
                parts = current.split(' ', 1)
                base = parts[1] if len(parts) > 1 else parts[0]
                await user.edit(nick=f"{rank} {base}")
            except:
                pass
            await ctx.followup.send(rank_change_summary(added, removed), ephemeral=True)
        else:
            # No identity row yet: the modal links them, then applies the rank
            # (roles + nickname + DB) exactly like the branch above.
            modal = LinkAccount(title="Link User to Minecraft IGN", user=user, rank=rank)
            await ctx.interaction.response.send_modal(modal)

    @manage_group.command(name='shells', description='HR: Add or remove shells from a user')
    async def shells(
        self,
        ctx: ApplicationContext,
        operation: discord.Option(str, choices=['add','remove']),
        user: discord.Member,
        amount: int,
        # reason: discord.Option(str, required=False, default='')
    ):
        rows = await asyncio.to_thread(_shells_lookup, user.id)

        if rows:
            await ctx.defer()
            ign = rows[0][0]
            file = await asyncio.to_thread(
                _build_shells_card, user.id, ign, operation, amount, ctx.user.name, ctx.user.id
            )
            await ctx.followup.send(file=file)
        else:
            modal = ShellModalName(
                title="Link User to Minecraft IGN",
                user=user,
                operation=operation,
                amount=amount,
                # set to empty for now
                reason=''
            )
            await ctx.interaction.response.send_modal(modal)

    @manage_group.command(name='link', description='HR: Link a user to an IGN')
    async def link(
        self,
        ctx: ApplicationContext,
        user: discord.Member,
        ign: str
    ):
        await ctx.defer(ephemeral=True)
        # Same gate as rank/unlink. Linking says who an account *is*, and the
        # website derives exec access from discord_links, so re-pointing a
        # member's identity is at least as sensitive as changing their rank.
        inv, tgt, _ = await asyncio.to_thread(_rank_lookup, ctx.user.id, user.id)
        refusal = link_change_refusal(inv[0] if inv else None, tgt[0] if tgt else None)
        if refusal:
            await ctx.followup.send(refusal, ephemeral=True)
            return
        try:
            result = await asyncio.to_thread(_link_user, user.id, ign, ctx.user.id)
        except LinkConflictError as e:
            await ctx.followup.send(e.user_message(), ephemeral=True)
            return

        if result is None:
            # Previously getPlayerUUID(ign)[1] crashed with TypeError on a
            # failed lookup; fail with a clear message instead.
            await ctx.followup.send(
                f':no_entry: Could not resolve **{discord.utils.escape_markdown(ign)}** — check the spelling and try again.',
                ephemeral=True
            )
        elif result == 'updated':
            try:
                base = user.nick.split(' ')[0]
                await user.edit(nick=f"{base} {ign}")
            except:
                pass
            await ctx.followup.send(
                f'Updated link for **{discord.utils.escape_markdown(user.name)}** to **{discord.utils.escape_markdown(ign)}**',
                ephemeral=True
            )
        else:
            await ctx.followup.send(
                f'Linked **{discord.utils.escape_markdown(user.name)}** to **{discord.utils.escape_markdown(ign)}**',
                ephemeral=True
            )

    @manage_group.command(name='unlink', description='HR: Remove a user\'s Minecraft link entirely')
    async def unlink(self, ctx: ApplicationContext, user: discord.Member):
        await ctx.defer(ephemeral=True)
        inv, tgt, _ = await asyncio.to_thread(_rank_lookup, ctx.user.id, user.id)
        refusal = link_change_refusal(inv[0] if inv else None, tgt[0] if tgt else None)
        if refusal:
            await ctx.followup.send(refusal, ephemeral=True)
            return
        row = await asyncio.to_thread(_unlink_user, user.id, ctx.user.id, ctx.user.name)
        if not row:
            await ctx.followup.send(f'**{discord.utils.escape_markdown(user.name)}** has no link.', ephemeral=True)
            return
        await ctx.followup.send(
            f'Unlinked **{discord.utils.escape_markdown(user.name)}** from **{discord.utils.escape_markdown(row[0])}**. '
            f'Their Discord roles were not changed — use `/reset_roles` if they should lose them.',
            ephemeral=True
        )

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(Manage(client))
