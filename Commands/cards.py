"""Card collection commands — the main guild.

The loop: /reel pulls cards and every pull pays pearls, duplicates included;
pearls buy star fusion and tank upgrades. Wishes bias which epic or legendary
you land. Nothing here touches shells; the two economies never meet.
"""

import asyncio

import discord
from discord.commands import SlashCommandGroup, slash_command
from discord.ext import commands, pages

from Helpers import cards as cardlib
from Helpers.card_render import card_file
from Helpers.logger import ERROR, SYSTEM, log
from Helpers.pagination import add_paginator_buttons
from Helpers.variables import TAQ_GUILD_IDS

CARDS_PER_PAGE = 20
POOL_PER_PAGE = 15

# /tank admin set-channel is deliberately exempt from the channel check. It is
# the command that fixes a wrong setting, so gating it behind the setting would
# lock the guild out of its own configuration.
CHANNEL_EXEMPT = {"set-channel"}
HISTORY_LINES = 12   # session pulls listed above the current card


class WrongCardChannel(discord.CheckFailure):
    """Raised when a card command is used outside the configured channel."""

    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        super().__init__(f"Card commands are limited to <#{channel_id}>.")


async def _channel_allowed(ctx: discord.ApplicationContext) -> bool:
    """Card commands run in one channel per guild, once one is configured."""
    if ctx.guild is None:
        return True
    if ctx.command is not None and ctx.command.name in CHANNEL_EXEMPT:
        return True
    wanted = await asyncio.to_thread(cardlib.db_get_card_channel, ctx.guild.id)
    if wanted is None or ctx.channel_id == wanted:
        return True
    raise WrongCardChannel(wanted)


async def _channel_error(ctx: discord.ApplicationContext, error: Exception):
    """Answer a wrong-channel attempt quietly instead of as a crash."""
    if not isinstance(error, WrongCardChannel):
        raise error
    msg = (f"Card commands only work in <#{error.channel_id}> "
           "— keeps the rest of the server clear.")
    try:
        if ctx.response.is_done():
            await ctx.followup.send(msg, ephemeral=True)
        else:
            await ctx.respond(msg, ephemeral=True)
    except discord.HTTPException:
        pass


def _tier_label(tier: str) -> str:
    return tier.capitalize()


def _stars(n: int) -> str:
    return "★" * n if n > 1 else ""


def _resolve(name: str) -> dict | None:
    """Find a card by display name across the set and the minted 1/1s."""
    wanted = name.strip().lower()
    for c in cardlib.load_card_set()["cards"]:
        if c["name"].lower() == wanted:
            return c
    for c in cardlib.db_get_member_cards().values():
        if c["name"].lower() == wanted:
            return c
    return None


def _level_label(card: dict, star: int) -> str:
    """How a level reads: blank when plain, stars below the top, MAX at it."""
    if star <= 0:
        return ""
    return "MAX" if star >= cardlib.tier_max_stars(card) else "★" * star


def _max_costs() -> str:
    """Unfused copies behind a MAX card, tier by tier.

    Grouped where tiers agree, so the line stays short as ceilings move: it
    reads off TIER_MAX_STARS rather than repeating what is in it.
    """
    groups = {}
    for tier in cardlib.CARD_TIERS:
        copies = cardlib.base_copies_for(cardlib.TIER_MAX_STARS[tier])
        groups.setdefault(copies, []).append(tier)

    parts = []
    for copies, tiers in groups.items():
        if len(tiers) > 1:
            names = f"{', '.join(tiers[:-1])} or {tiers[-1]}"
        else:
            names = tiers[0]
        parts.append(f"**{copies}** {names}")
    return " · ".join(parts)


def _star_name(card: dict, star: int) -> str:
    label = _level_label(card, star)
    return f"{card['name']} {label}".strip() if label else card["name"]


def _stack_label(card: dict, star: int, count: int) -> str:
    """How one stack reads in a picker: the card, its level, how many."""
    label = _level_label(card, star)
    return f"{card['name']}{' ' + label if label else ''} ×{count}"


def _parse_stack(value: str) -> tuple[str, int] | None:
    """Pull the slug and star back out of a picker value."""
    if ":" not in value:
        return None
    slug, _, star = value.rpartition(":")
    return (slug, int(star)) if star.isdigit() else None


async def _stack_choices(user_id: int, typed: str, resolve_member=True):
    """Every stack a user holds, one entry per star level."""
    owned = await asyncio.to_thread(cardlib.db_get_collection, user_id)
    if not owned:
        return []
    members = await asyncio.to_thread(
        cardlib.db_get_member_cards,
        [s for s in owned if cardlib.is_member_slug(s)]) if resolve_member else {}

    out = []
    for slug, entry in owned.items():
        card = cardlib.get_card(slug) or members.get(slug)
        if not card or typed not in card["name"].lower():
            continue
        for star, count in sorted(entry["levels"].items()):
            out.append(discord.OptionChoice(
                name=_stack_label(card, star, count), value=f"{slug}:{star}"))
    return sorted(out, key=lambda c: c.name)[:25]


async def _autocomplete_stacks(ctx: discord.AutocompleteContext):
    try:
        return await _stack_choices(ctx.interaction.user.id,
                                    (ctx.value or "").lower())
    except Exception:
        return []


async def _autocomplete_their_stacks(ctx: discord.AutocompleteContext):
    """The other side of a trade, once they have picked who."""
    try:
        member = (ctx.options or {}).get("member")
        if not member:
            return []
        uid = int(member["id"] if isinstance(member, dict) else member)
        return await _stack_choices(uid, (ctx.value or "").lower())
    except Exception:
        return []


async def _autocomplete_owned(ctx: discord.AutocompleteContext):
    try:
        owned = await asyncio.to_thread(cardlib.db_get_collection,
                                        ctx.interaction.user.id)
    except Exception:
        return []
    if not owned:
        return []
    members = await asyncio.to_thread(
        cardlib.db_get_member_cards,
        [s for s in owned if cardlib.is_member_slug(s)])

    typed = (ctx.value or "").lower()
    names = []
    for slug in owned:
        card = cardlib.get_card(slug) or members.get(slug)
        if card and typed in card["name"].lower():
            names.append(card["name"])
    return sorted(names)[:25]


async def _autocomplete_pool(ctx: discord.AutocompleteContext):
    """Everything obtainable: the 1/1 members first, then the card set."""
    typed = (ctx.value or "").lower()
    try:
        members = [p["name"] for p in await asyncio.to_thread(cardlib.db_get_pool)
                   if typed in p["name"].lower()]
    except Exception:
        members = []
    cards = sorted(c["name"] for c in cardlib.load_card_set()["cards"]
                   if typed in c["name"].lower())
    return (sorted(members) + cards)[:25]


def _find_card(name: str) -> dict | None:
    """A card from the set by display name. Member 1/1s are looked up in DB."""
    wanted = name.strip().lower()
    for c in cardlib.load_card_set()["cards"]:
        if c["name"].lower() == wanted:
            return c
    return None


def _pool_entries(tier: str | None) -> list:
    """Everything that can drop, rarest first, optionally filtered by tier.

    Member 1/1s lead because they are the rarest thing in the game; the card
    set follows in tier order, most-spoken first inside each tier.
    """
    out = []
    if tier in (None, "member"):
        out += cardlib.db_get_pool()
    cards = cardlib.load_card_set()["cards"]
    if tier != "member":
        wanted = [c for c in cards if tier is None or c["tier"] == tier]
        order = {t: i for i, t in enumerate(cardlib.CARD_TIERS)}
        out += sorted(wanted, key=lambda c: (order[c["tier"]], -c["lines"]))
    return out


async def _autocomplete_wishable(ctx: discord.AutocompleteContext):
    """Any card in the set. Member 1/1s are never wishable."""
    typed = (ctx.value or "").lower()
    names = [c["name"] for c in cardlib.load_card_set()["cards"]
             if c["tier"] in cardlib.WISHABLE_TIERS and typed in c["name"].lower()]
    return sorted(names)[:25]


# Card art and dialogue come from the Wynncraft Wiki, credited by a link
# directly above the card. Member 1/1s are rendered from Minecraft skins
# instead, so they carry no link.


def _wiki_line(card: dict) -> str | None:
    """The source, as a link sitting immediately above the card image.

    A footer would put it below the art but Discord does not render links
    there, so the description is the closest spot that stays clickable.
    """
    url = card.get("wiki_url")
    if not url or card.get("member"):
        return None
    return f"[Wiki Page]({url})"


def _credit(card: dict, *bits) -> str:
    """Footer text — what the command wants to report, nothing more."""
    return " · ".join(b for b in bits if b)


def _card_color(card: dict) -> int:
    if card.get("member"):
        return cardlib.TIER_COLORS["member"]
    return cardlib.TIER_COLORS.get(card["tier"], 0x9CA3AF)


def _card_embed(card: dict, copies: int, remaining: int, filename: str,
                who: str, stars: int = 0, gained: int = 0) -> discord.Embed:
    # The card art already prints the name, the tier or rank, the star level
    # and the 1/1 badge, so the embed adds nothing but what the art cannot
    # show: who rolled it, and what it means for your collection.
    embed = discord.Embed(color=_card_color(card),
                          description=_wiki_line(card))
    embed.set_author(name=f"{who}'s reel")
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=_credit(
        card,
        "New to your tank" if copies == 1 else f"Copy #{copies}",
        f"+{gained:,} pearls" if gained else None,
        f"{remaining} reel{'' if remaining == 1 else 's'} left"))
    return embed


def _history_content(history: list) -> str:
    """The session's earlier pulls, one line each, above the current card."""
    lines = []
    shown = history[-HISTORY_LINES:]
    if len(history) > HISTORY_LINES:
        lines.append(f"-# ...and {len(history) - HISTORY_LINES} earlier")
    for c in shown:
        tier = "1/1" if c.get("member") else _tier_label(c["tier"])
        lines.append(f"-# {tier} · {c['name']}")
    refresh = cardlib.next_refresh_ts()
    lines.append(f"-# Next refresh <t:{refresh}:R>")
    return "\n".join(lines)


async def _do_reel(user_id: int, who: str):
    """Spend a reel and roll a card.

    Returns (embed, file, remaining, card) on success, or (None, None, reason,
    None) where reason is 'out' or 'render'. A failed render refunds the reel,
    so a broken image never costs anything.
    """
    spend = await asyncio.to_thread(cardlib.db_spend_reel, user_id)
    if spend is None:
        return None, None, "out", None
    remaining = spend["total"]

    wishes = set(await asyncio.to_thread(cardlib.db_get_wishes, user_id))
    card = cardlib.roll_card(wishes=wishes)

    if card.get("tier") == "member":
        minted = await asyncio.to_thread(cardlib.db_mint_member_card, user_id)
        # Every eligible member already holds a card — fall back to a normal
        # pull rather than silently eating the reel.
        card = minted or cardlib.roll_card(wishes=wishes)
        if card.get("tier") == "member":
            card = cardlib.roll_card()

    try:
        # Spelled out rather than left to defaults: a fresh pull is always
        # unfused, and that is worth saying at the call site.
        file = await asyncio.to_thread(card_file, card, None, 0,
                                       cardlib.tier_max_stars(card))
    except Exception as e:
        await asyncio.to_thread(cardlib.db_refund_reel, user_id,
                                spend["used_bait"])
        log(ERROR, f"Card render failed for {card.get('slug')}: {e}",
            context="cards")
        return None, None, "render", None

    pearls = cardlib.pull_value(card)
    result = await asyncio.to_thread(cardlib.db_add_card, user_id,
                                     card["slug"], pearls)
    embed = _card_embed(card, result["count"], remaining, file.filename, who,
                        gained=result["gained"])
    return embed, file, remaining, card


async def _announce_and_reward(channel, user, card):
    """Shout about a 1/1 and hand out any milestones the pull just completed."""
    try:
        if card and card.get("member"):
            await channel.send(embed=discord.Embed(
                title="A 1/1 has been minted",
                description=(f"{user.mention} pulled the one and only "
                             f"**{card['name']}** ({card['rank']}). "
                             "No second copy will ever exist."),
                color=cardlib.TIER_COLORS["member"]))

        collection = await asyncio.to_thread(cardlib.db_get_collection, user.id)
        earned = await asyncio.to_thread(cardlib.check_milestones, user.id,
                                         collection)
        for label, pearls in earned:
            await channel.send(
                f"-# {user.mention} completed **{label}** — +{pearls:,} pearls")
    except Exception as e:
        log(ERROR, f"Post-reel announce failed: {e}", context="cards")


class ReelView(discord.ui.View):
    """Reel again without retyping the command.

    Keyed to whoever ran /reel: anyone else pressing it is told to run their
    own, so a card in a busy channel can't be rerolled out from under someone.
    """

    def __init__(self, owner_id: int, owner_name: str, card: dict,
                 exhausted: bool = False):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.owner_name = owner_name
        self.message = None
        self.history = []         # everything rerolled past this session
        self.current = card       # shown on the card image right now
        self.set_exhausted(exhausted)

    def set_exhausted(self, exhausted: bool):
        self.reel_again.disabled = exhausted
        self.reel_again.label = "No reels left" if exhausted else "Reel again"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "That's someone else's reel — run `/reel` for your own.",
                ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Reel again", style=discord.ButtonStyle.primary,
                       emoji="\N{FISHING POLE AND FISH}")
    async def reel_again(self, button: discord.ui.Button,
                         interaction: discord.Interaction):
        embed, file, info, card = await _do_reel(self.owner_id, self.owner_name)

        if embed is None:
            if info == "out":
                self.set_exhausted(True)
                refresh = cardlib.next_refresh_ts()
                await interaction.response.edit_message(view=self)
                return await interaction.followup.send(
                    f"You're out of reels. More arrive <t:{refresh}:R>.",
                    ephemeral=True)
            return await interaction.response.send_message(
                "That card wouldn't render, so your reel has been refunded. "
                "Try again.", ephemeral=True)

        # The card being replaced becomes part of the session log above it.
        if self.current is not None:
            self.history.append(self.current)
        self.current = card

        self.set_exhausted(info == 0)
        # attachments=[] drops the previous card image; the new file replaces it.
        await interaction.response.edit_message(
            content=_history_content(self.history),
            embed=embed, file=file, attachments=[], view=self)
        await _announce_and_reward(interaction.channel, interaction.user, card)


class TradeView(discord.ui.View):
    """Two-sided confirm. Only the recipient can accept or decline."""

    def __init__(self, proposer: discord.User, target: discord.User,
                 give: dict, give_star: int, want: dict, want_star: int):
        super().__init__(timeout=300)
        self.proposer = proposer
        self.target = target
        self.give = give
        self.give_star = give_star
        self.want = want
        self.want_star = want_star
        self.message = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message(
                "This trade isn't addressed to you.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content="-# Trade expired.", view=self)
        except discord.HTTPException:
            pass

    async def _close(self, interaction, text):
        self.done = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=text, view=self)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, button: discord.ui.Button,
                     interaction: discord.Interaction):
        ok = await asyncio.to_thread(
            cardlib.db_trade, self.proposer.id, self.target.id,
            self.give["slug"], self.give_star,
            self.want["slug"], self.want_star)
        if not ok:
            return await self._close(
                interaction,
                "Trade failed — one side no longer holds that copy.")
        await self._close(
            interaction,
            f"Trade complete: {self.proposer.mention} gave "
            f"**{_star_name(self.give, self.give_star)}** and received "
            f"**{_star_name(self.want, self.want_star)}** "
            f"from {self.target.mention}.")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, button: discord.ui.Button,
                      interaction: discord.Interaction):
        await self._close(interaction, "Trade declined.")


class Cards(commands.Cog):
    # Two homes: /tank is yours, /pool is the world. /reel and /bait stay at
    # the top level because they are run constantly and burying the everyday
    # actions behind a group would cost more than the tidiness is worth.
    tank = SlashCommandGroup(name="tank", description="Your card collection",
                             guild_ids=TAQ_GUILD_IDS)
    wish = tank.create_subgroup(name="wishlist",
                                description="Aim your luck at a card")
    admin = tank.create_subgroup(name="admin",
                                 description="Card system settings")
    pool = SlashCommandGroup(name="pool",
                             description="Every card that can drop",
                             guild_ids=TAQ_GUILD_IDS)

    def __init__(self, client):
        self.client = client

    async def cog_check(self, ctx: discord.ApplicationContext) -> bool:
        return await _channel_allowed(ctx)

    async def cog_command_error(self, ctx: discord.ApplicationContext,
                                error: Exception):
        await _channel_error(ctx, error)

    @commands.Cog.listener()
    async def on_ready(self):
        await asyncio.to_thread(cardlib.db_ensure_tables)
        try:
            cs = cardlib.load_card_set()
            log(SYSTEM, f"Card set loaded: {len(cs['cards'])} cards",
                context="cards")
        except Exception as e:
            log(ERROR, f"Card set failed to load: {e}", context="cards")

    # ── /reel ────────────────────────────────────────────────────────────────

    @slash_command(name="reel",
                   description="Reel in a card. Refreshes every 6 hours.",
                   guild_ids=TAQ_GUILD_IDS)
    async def reel(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        who = ctx.author.display_name
        embed, file, info, card = await _do_reel(ctx.author.id, who)

        if embed is None:
            if info == "out":
                refresh = cardlib.next_refresh_ts()
                return await ctx.followup.send(
                    f"You're out of reels. More arrive <t:{refresh}:R>.",
                    ephemeral=True)
            return await ctx.followup.send(
                "That card wouldn't render, so your reel has been refunded. "
                "Try again.", ephemeral=True)

        view = ReelView(ctx.author.id, who, card, exhausted=(info == 0))
        # wait=True so the view can disable its own button when it times out.
        view.message = await ctx.followup.send(
            content=_history_content([]),
            embed=embed, file=file, view=view, wait=True)
        await _announce_and_reward(ctx.channel, ctx.author, card)

    # ── /bait ────────────────────────────────────────────────────────────────

    @slash_command(name="bait",
                   description="Claim your daily pearls and reels",
                   guild_ids=TAQ_GUILD_IDS)
    async def bait(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        result = await asyncio.to_thread(cardlib.db_claim_daily, ctx.author.id)

        if not result["ok"]:
            if result["reason"] == "unspent":
                held = result["bait_reels"]
                return await ctx.followup.send(
                    f"You still have **{held}** bait reel"
                    f"{'' if held == 1 else 's'} in hand. Spend them with "
                    f"`/reel` and you can bait again.", ephemeral=True)
            reset = await asyncio.to_thread(cardlib.db_next_daily_reset)
            return await ctx.followup.send(
                f"You've already baited today. The next one lands "
                f"<t:{reset}:R>, at <t:{reset}:t>.", ephemeral=True)

        embed = discord.Embed(
            title="Bait cast",
            description=(f"**+{result['gained']:,}** pearls and "
                         f"**+{result['bait_reels']}** bait reels"),
            color=0x38C9BD)
        embed.add_field(name="Streak", value=f"{result['streak']} day"
                        f"{'' if result['streak'] == 1 else 's'}")
        embed.add_field(name="Pearls", value=f"{result['pearls']:,}")
        embed.add_field(
            name="Reels",
            value=f"{result['total_reels']} "
                  f"({result['bait_reels']} from bait)")

        # Name the next rung rather than the whole ladder: one line, and it
        # is the only part of the ladder that is worth acting on.
        nxt = cardlib.next_daily_tier(result["streak"])
        embed.set_footer(
            text=(f"Day {nxt['from_day']}: {nxt['reels']} reels and "
                  f"{nxt['pearls']} pearls a day" if nxt else
                  "Top streak — keep it up."))
        reset = await asyncio.to_thread(cardlib.db_next_daily_reset)
        await ctx.followup.send(
            content=f"-# Bait reels are spent first, and the next bait waits "
                    f"until they are gone. Next bait <t:{reset}:R>",
            embed=embed)

    @tank.command(name="help", description="How the card system works")
    async def tank_help(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        cs = cardlib.load_card_set()
        cap = cardlib.bank_cap(wallet["tank_tier"])
        pct = int(cardlib.WISH_REDIRECT_CHANCE * 100)

        embed = discord.Embed(
            title="How the Tank works",
            description=(
                f"Reel in cards of Wynncraft characters — **{len(cs['cards'])}** "
                "of them in total. "
                "You keep everything you pull, duplicates included, and every "
                "card pays **pearls** you spend on upgrades."),
            color=0x38C9BD)

        embed.add_field(
            name="Start here",
            value=(f"`/reel` — pull a card. You get **{cardlib.REELS_PER_WINDOW}** "
                   f"more every 6 hours, and can hold **{cap}** at a time.\n"
                   "`/bait` — your daily pearls and bait reels. Both climb "
                   f"with the streak, up to **{cardlib.MAX_BAIT_REELS}** "
                   f"reels and **{cardlib.DAILY_TIERS[-1]['pearls']}** "
                   "pearls a day."),
            inline=False)
        embed.add_field(
            name="Your collection",
            value=("`/tank list` — everything you or another player owns.\n"
                   "`/tank view` — look at one of your cards up close.\n"
                   "`/tank profile` — pearls, streak, tank tier and totals."),
            inline=False)
        embed.add_field(
            name="Spending pearls",
            value=(f"`/tank fuse` — merge "
                   f"**{cardlib.FUSION_COPIES_PER_STEP}** copies of a level "
                   "into one of the next, or several at once with "
                   "`count`. To MAX: "
                   f"{_max_costs()}.\n"
                   "`/tank upgrade` — a bigger tank lets you bank more reels, "
                   "gives you passive pearls, and more wishlist slots."),
            inline=False)
        embed.add_field(
            name="Aiming your luck",
            value=(f"`/tank wishlist add` — wish for any card and **{pct}%** of "
                   "that tier's pulls become it."),
            inline=False)
        embed.add_field(
            name="The whole set",
            value=("`/pool list` — everything that can be reeled in.\n"
                   "`/pool view` — any card, owned or not.\n"
                   "`/pool rates` — the drop rates for each tier."),
            inline=False)
        embed.add_field(
            name="With other people",
            value=("`/tank trade` — swap any card you hold.\n"
                   "`/tank leaderboard` — the best collections in the guild."),
            inline=False)

        refresh = cardlib.next_refresh_ts()
        embed.set_footer(
            text=f"You have {wallet['total_reels']} reel"
                 f"{'' if wallet['total_reels'] == 1 else 's'} and "
                 f"{wallet['pearls']:,} pearls right now")
        await ctx.followup.send(
            content=f"-# Next refresh <t:{refresh}:R>", embed=embed)

    # ── /tank view ───────────────────────────────────────────────────────────

    @tank.command(name="view", description="Show a card you own")
    async def tank_view(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="Card name",
                             autocomplete=_autocomplete_owned),
    ):
        await ctx.defer()
        match = await asyncio.to_thread(_resolve, card)
        if match is None:
            return await ctx.followup.send(
                f"No card called **{card}** exists.", ephemeral=True)

        entry = await asyncio.to_thread(cardlib.db_get_entry, ctx.author.id,
                                        match["slug"])
        if not entry:
            return await ctx.followup.send(
                f"**{match['name']}** isn't in your tank yet.", ephemeral=True)

        stars = entry["best"]
        file = await asyncio.to_thread(card_file, match, None, stars,
                                       cardlib.tier_max_stars(match))
        embed = discord.Embed(color=_card_color(match),
                              description=_wiki_line(match))
        embed.set_image(url=f"attachment://{file.filename}")
        held = "" if match.get("member") else " · ".join(
            f"{c}× {_level_label(match, st) or 'plain'}"
            for st, c in sorted(entry["levels"].items()))
        embed.set_footer(text=_credit(match, held))
        await ctx.followup.send(embed=embed, file=file)

    # ── /tank list ───────────────────────────────────────────────────────────

    @tank.command(name="list", description="List every card in a tank")
    async def tank_list(
        self, ctx: discord.ApplicationContext,
        member: discord.Option(
            discord.Member,
            description="Whose tank to look through (defaults to you)",
            required=False, default=None),
    ):
        await ctx.defer()
        target = member or ctx.author
        mine = target.id == ctx.author.id

        owned = await asyncio.to_thread(cardlib.db_get_collection, target.id)
        cs = cardlib.load_card_set()
        # Only read a wallet for the viewer. db_get_wallet creates the row it
        # reads, so asking for someone else's would open a tank they may never
        # have played, and their pearls are not the viewer's business anyway.
        wallet = (await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
                  if mine else None)

        if not owned:
            if not mine:
                return await ctx.followup.send(
                    f"{target.display_name} hasn't reeled anything yet.",
                    ephemeral=True)
            return await ctx.followup.send(
                f"Your tank is empty. You have {wallet['total_reels']} reel"
                f"{'' if wallet['total_reels'] == 1 else 's'} — try `/reel`.",
                ephemeral=True)

        members = await asyncio.to_thread(
            cardlib.db_get_member_cards,
            [s for s in owned if cardlib.is_member_slug(s)])

        rows = []
        for slug, entry in owned.items():
            c = cardlib.get_card(slug) or members.get(slug)
            if c:
                rows.append((c, entry))

        def sort_key(r):
            tier = "member" if r[0].get("member") else r[0]["tier"]
            idx = (cardlib.TIER_ORDER.index(tier)
                   if tier in cardlib.TIER_ORDER else 9)
            return (idx, r[0]["name"])
        rows.sort(key=sort_key)

        total_copies = sum(e["total"] for _, e in rows)
        header = f"**{len(rows)}** of {len(cs['cards'])} cards · {total_copies} total"
        if mine:
            header += (f" · {wallet['pearls']:,} pearls · "
                       f"{wallet['total_reels']} reel"
                       f"{'' if wallet['total_reels'] == 1 else 's'}")
        else:
            spares = sum(e["total"] - 1 for _, e in rows if e["total"] > 1)
            header += f" · {spares} spare{'' if spares == 1 else 's'} to trade"

        page_list = []
        for i in range(0, len(rows), CARDS_PER_PAGE):
            chunk = rows[i:i + CARDS_PER_PAGE]
            lines = []
            for c, e in chunk:
                member = c.get("member")
                tier = "1/1" if member else _tier_label(c["tier"])
                # A 1/1 has no count worth printing: there is one, there was
                # only ever going to be one, and it cannot be fused.
                bits = []
                if not member:
                    for st, n in sorted(e["levels"].items()):
                        label = _level_label(c, st)
                        bits.append(f"{label}×{n}" if label else f"×{n}")
                line = f"`{tier:9}` {c['name']}"
                lines.append(f"{line} {' '.join(bits)}" if bits else line)
            embed = discord.Embed(
                title=f"{target.display_name}'s Tank",
                description=header + "\n\n" + "\n".join(lines),
                color=_card_color(chunk[0][0]))
            embed.set_footer(text=f"Page {i // CARDS_PER_PAGE + 1} of "
                                  f"{(len(rows) - 1) // CARDS_PER_PAGE + 1}")
            page_list.append(pages.Page(embeds=[embed]))

        if len(page_list) == 1:
            return await ctx.followup.send(embed=page_list[0].embeds[0])
        paginator = pages.Paginator(pages=page_list)
        add_paginator_buttons(paginator)
        await paginator.respond(ctx.interaction)

    # ── /tank profile ────────────────────────────────────────────────────────

    @tank.command(name="profile",
                  description="Your pearls, streak and tank tier")
    async def tank_profile(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        owned = await asyncio.to_thread(cardlib.db_get_collection, ctx.author.id)
        wishes = await asyncio.to_thread(cardlib.db_get_wishes, ctx.author.id)
        cs = cardlib.load_card_set()

        tier = wallet["tank_tier"]
        spec = cardlib.TANK_TIERS[tier]
        copies = sum(e["total"] for e in owned.values()) if owned else 0

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Tank",
                              color=0x38C9BD)
        embed.add_field(name="Tank", value=f"{spec['name']} (tier {tier})")
        embed.add_field(name="Pearls", value=f"{wallet['pearls']:,}")
        reels_value = f"{wallet['reels']}/{spec['bank']}"
        if wallet["bait_reels"]:
            reels_value += f" + {wallet['bait_reels']} bait"
        embed.add_field(name="Reels", value=reels_value)
        embed.add_field(
            name="Collection",
            value=f"{len(owned)}/{len(cs['cards'])} unique · {copies} copies")
        embed.add_field(name="Daily streak", value=f"{wallet['streak']} day"
                        f"{'' if wallet['streak'] == 1 else 's'}")
        embed.add_field(name="Reeled",
                        value=f"{wallet['total_reeled']:,} all time")
        if spec["trickle"]:
            embed.add_field(
                name="Trickle",
                value=f"{spec['trickle']} pearls/hour "
                      f"(caps at {cardlib.TRICKLE_CAP_HOURS}h offline)")
        embed.add_field(
            name=f"Wishes ({len(wishes)}/{spec['wishes']})",
            value=", ".join((cardlib.get_card(w) or {"name": w})["name"]
                            for w in wishes) or "none set",
            inline=False)
        if tier < cardlib.MAX_TANK:
            nxt = cardlib.TANK_TIERS[tier + 1]
            embed.set_footer(
                text=f"Next: {nxt['name']} for {nxt['cost']:,} pearls "
                     f"— /tank upgrade")
        await ctx.followup.send(embed=embed)

    # ── /tank upgrade ────────────────────────────────────────────────────────

    @tank.command(name="upgrade",
                  description="Spend pearls on the next tank tier")
    async def tank_upgrade(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        tier = wallet["tank_tier"]
        if tier >= cardlib.MAX_TANK:
            return await ctx.followup.send(
                "Your tank is already an Abyss — there's nothing deeper.",
                ephemeral=True)

        nxt = cardlib.TANK_TIERS[tier + 1]
        if wallet["pearls"] < nxt["cost"]:
            short = nxt["cost"] - wallet["pearls"]
            return await ctx.followup.send(
                f"**{nxt['name']}** costs {nxt['cost']:,} pearls — "
                f"you're {short:,} short.", ephemeral=True)

        ok = await asyncio.to_thread(cardlib.db_upgrade_tank, ctx.author.id,
                                     tier + 1)
        if not ok:
            return await ctx.followup.send(
                "Upgrade failed — your pearls or tank tier changed. Try again.",
                ephemeral=True)

        await ctx.followup.send(embed=discord.Embed(
            title=f"Tank upgraded to {nxt['name']}",
            description=(f"Reel bank **{nxt['bank']}** · "
                         f"trickle **{nxt['trickle']}/hour** · "
                         f"wish slots **{nxt['wishes']}**"),
            color=0x38C9BD))

    # ── /tank leaderboard ────────────────────────────────────────────────────

    @tank.command(name="leaderboard",
                  description="Biggest collections in the guild")
    async def tank_leaderboard(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        rows = await asyncio.to_thread(cardlib.db_leaderboard, 15)
        if not rows:
            return await ctx.followup.send("Nobody has reeled anything yet.",
                                           ephemeral=True)
        cs = cardlib.load_card_set()
        lines = []
        for i, r in enumerate(rows, 1):
            member = ctx.guild.get_member(r["user"]) if ctx.guild else None
            name = member.display_name if member else f"User {r['user']}"
            lines.append(f"`{i:2}` **{name}** — {r['uniques']}/{len(cs['cards'])} "
                         f"unique · {r['copies']} copies · {r['pearls']:,} pearls")
        await ctx.followup.send(embed=discord.Embed(
            title="Deepest Tanks", description="\n".join(lines), color=0x38C9BD))

    # ── /tank fuse ───────────────────────────────────────────────────────────

    @tank.command(
        name="fuse",
        description="Merge three copies of a level into one of the next, "
                    "or several at once")
    async def tank_fuse(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="The stack to merge",
                             autocomplete=_autocomplete_stacks),
        count: discord.Option(
            int, description="How many of the next level to make (default 1)",
            required=False, default=1, min_value=1),
    ):
        await ctx.defer()
        picked = _parse_stack(card)
        if picked is None:
            return await ctx.followup.send(
                "Pick a card from the list so I know which level to merge.",
                ephemeral=True)
        slug, from_star = picked

        match = cardlib.get_card(slug) or await asyncio.to_thread(
            cardlib.db_get_member_card, slug)
        if match is None:
            return await ctx.followup.send("That card doesn't exist.",
                                           ephemeral=True)
        if match.get("member"):
            return await ctx.followup.send(
                "1/1 cards can't be fused — there is only ever one.",
                ephemeral=True)
        ceiling = cardlib.tier_max_stars(match)
        if from_star >= ceiling:
            return await ctx.followup.send(
                f"**{match['name']}** is {_tier_label(match['tier']).lower()}, "
                f"so {ceiling}★ is its ceiling — that is as far as it goes.",
                ephemeral=True)

        to_star = from_star + 1
        per_merge, per_pearls = cardlib.fusion_cost(to_star, match["tier"])
        entry = await asyncio.to_thread(cardlib.db_get_entry, ctx.author.id, slug)
        have = (entry or {}).get("levels", {}).get(from_star, 0)
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)

        level = _level_label(match, from_star) or "plain"
        possible = have // per_merge
        if possible < 1:
            return await ctx.followup.send(
                f"Merging into {to_star}★ takes **{per_merge}** {level} copies "
                f"of **{match['name']}**. You have {have}.", ephemeral=True)
        if count > possible:
            return await ctx.followup.send(
                f"**{count}** would take {per_merge * count} {level} copies of "
                f"**{match['name']}** and you have {have} — enough for "
                f"**{possible}**.", ephemeral=True)

        need, pearls = per_merge * count, per_pearls * count
        if wallet["pearls"] < pearls:
            return await ctx.followup.send(
                f"Merging {count} costs **{pearls:,}** pearls — you have "
                f"{wallet['pearls']:,}.", ephemeral=True)

        result = await asyncio.to_thread(cardlib.db_fuse, ctx.author.id, slug,
                                         to_star, need, pearls, count)
        if result is None:
            return await ctx.followup.send(
                "Merge failed — your copies or pearls changed. Try again.",
                ephemeral=True)

        file = await asyncio.to_thread(card_file, match, None, to_star,
                                       ceiling)
        into = _level_label(match, to_star)
        summary = (f"Merged {need} {level} copies into {count}× {into} · "
                   f"{pearls:,} pearls · {result['pearls']:,} left")
        if to_star >= ceiling:
            summary = f"**Maxed.** {summary}"
        line = _wiki_line(match)
        embed = discord.Embed(
            title=f"{match['name']} {_level_label(match, to_star)}".strip(),
            description=f"{summary}\n{line}" if line else summary,
            color=cardlib.TIER_COLORS[match["tier"]])
        embed.set_image(url=f"attachment://{file.filename}")
        embed.set_footer(text=_credit(
            match,
            f"{result['now']}× {_level_label(match, to_star)} · "
            f"{result['left']} {level} left",
            f"{cardlib.base_copies_for(to_star)} copies behind it"))
        await ctx.followup.send(embed=embed, file=file)

    # ── /tank trade ──────────────────────────────────────────────────────────

    @tank.command(name="trade", description="Swap a card with someone")
    async def tank_trade(
        self, ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Who to trade with"),
        give: discord.Option(str, description="The copy you give",
                             autocomplete=_autocomplete_stacks),
        want: discord.Option(str, description="The copy you want",
                             autocomplete=_autocomplete_their_stacks),
    ):
        await ctx.defer()
        if member.id == ctx.author.id:
            return await ctx.followup.send("You can't trade with yourself.",
                                           ephemeral=True)
        if member.bot:
            return await ctx.followup.send("Bots don't collect cards.",
                                           ephemeral=True)

        mine_pick, theirs_pick = _parse_stack(give), _parse_stack(want)
        if mine_pick is None or theirs_pick is None:
            return await ctx.followup.send(
                "Pick both cards from the lists so I know which copies you "
                "mean — a 1★ and a 2★ are different things.", ephemeral=True)
        give_slug, give_star = mine_pick
        want_slug, want_star = theirs_pick

        give_card = cardlib.get_card(give_slug) or await asyncio.to_thread(
            cardlib.db_get_member_card, give_slug)
        want_card = cardlib.get_card(want_slug) or await asyncio.to_thread(
            cardlib.db_get_member_card, want_slug)
        if give_card is None or want_card is None:
            return await ctx.followup.send("One of those cards doesn't exist.",
                                           ephemeral=True)

        mine = await asyncio.to_thread(cardlib.db_get_entry, ctx.author.id,
                                       give_slug)
        theirs = await asyncio.to_thread(cardlib.db_get_entry, member.id,
                                         want_slug)
        if not mine or not mine["levels"].get(give_star):
            return await ctx.followup.send(
                f"You don't have a {give_star}★ **{give_card['name']}**.",
                ephemeral=True)
        if not theirs or not theirs["levels"].get(want_star):
            return await ctx.followup.send(
                f"{member.display_name} doesn't have a {want_star}★ "
                f"**{want_card['name']}**.", ephemeral=True)

        embed = discord.Embed(
            title="Trade offer",
            description=(
                f"{ctx.author.mention} gives "
                f"**{_star_name(give_card, give_star)}** "
                f"({_tier_label(give_card['tier'])})\n"
                f"{member.mention} gives "
                f"**{_star_name(want_card, want_star)}** "
                f"({_tier_label(want_card['tier'])})"),
            color=0x38C9BD)
        embed.set_footer(
            text="Only the recipient can accept. Expires in 5 minutes.")

        view = TradeView(ctx.author, member, give_card, give_star,
                         want_card, want_star)
        view.message = await ctx.followup.send(
            content=member.mention, embed=embed, view=view, wait=True)

    # ── /tank admin ──────────────────────────────────────────────────────────

    @admin.command(name="set-channel",
                   description="Confine card commands to one channel")
    @commands.has_permissions(administrator=True)
    async def tank_set_channel(
        self, ctx: discord.ApplicationContext,
        channel: discord.Option(
            discord.TextChannel,
            description="Leave empty to allow every channel again",
            required=False, default=None),
    ):
        await ctx.defer(ephemeral=True)
        await asyncio.to_thread(cardlib.db_set_card_channel, ctx.guild.id,
                                channel.id if channel else None)
        if channel is None:
            return await ctx.followup.send(
                "Card commands are no longer restricted — they work in every "
                "channel again.")
        await ctx.followup.send(
            f"Card commands are now limited to {channel.mention}. "
            "`/tank admin set-channel` itself still works anywhere, so you "
            "can always move or clear it.")

    # ── /pool ────────────────────────────────────────────────────────────────

    @pool.command(name="view",
                  description="Preview any card, a character or a member 1/1")
    async def pool_view(
        self, ctx: discord.ApplicationContext,
        name: discord.Option(str, description="Character or guild member",
                             autocomplete=_autocomplete_pool),
    ):
        await ctx.defer()
        wanted = name.strip().lower()
        entries = await asyncio.to_thread(cardlib.db_get_pool)
        member = next((p for p in entries if p["name"].lower() == wanted), None)
        card = member or _find_card(name)
        if card is None:
            return await ctx.followup.send(
                f"Nothing called **{name}** can drop. That covers every "
                "character in the set and every member eligible for a 1/1.",
                ephemeral=True)

        entry = await asyncio.to_thread(cardlib.db_get_entry, ctx.author.id,
                                        card["slug"])
        stars = entry["best"] if entry else 0
        file = await asyncio.to_thread(card_file, card, None, stars,
                                       cardlib.tier_max_stars(card))

        embed = discord.Embed(color=_card_color(card),
                              description=_wiki_line(card))
        embed.set_image(url=f"attachment://{file.filename}")

        if member:
            if card["retired"]:
                state = ("Retired — the holder has left, and no copy will be "
                         "minted again")
            elif card["minted"]:
                state = f"Already minted — held by <@{card['owner']}>"
            else:
                state = "Not minted yet — still out there to be reeled"
            embed.add_field(name="Status", value=state, inline=False)

        embed.set_footer(text=_credit(
            card,
            f"You own {entry['total']} cop"
            f"{'y' if entry['total'] == 1 else 'ies'}" if entry
            else "Not in your tank yet"))
        await ctx.followup.send(embed=embed, file=file)

    @pool.command(name="list", description="Everything that can drop")
    async def pool_list(
        self, ctx: discord.ApplicationContext,
        tier: discord.Option(
            str, description="Narrow it down (default: everything)",
            required=False, default=None,
            choices=["member 1/1", "legendary", "epic", "rare", "uncommon",
                     "common"]),
    ):
        await ctx.defer()
        want = "member" if tier == "member 1/1" else tier
        entries = await asyncio.to_thread(_pool_entries, want)
        if not entries:
            return await ctx.followup.send("Nothing to show there yet.",
                                           ephemeral=True)

        owned = await asyncio.to_thread(cardlib.db_get_collection, ctx.author.id)
        members = [e for e in entries if e.get("member")]
        cards = len(entries) - len(members)
        header = (f"**{len(entries)}** obtainable"
                  + (f" · {len(members)} one-of-ones" if members else "")
                  + (f" · {cards} characters" if cards else "")
                  + " · rarest first")

        page_list = []
        for i in range(0, len(entries), POOL_PER_PAGE):
            lines = []
            for e in entries[i:i + POOL_PER_PAGE]:
                if e.get("member"):
                    label = "1/1"
                    if e["retired"]:
                        tail = "retired"
                    elif e["minted"]:
                        tail = f"held by <@{e['owner']}>"
                    else:
                        tail = "unminted"
                else:
                    label = _tier_label(e["tier"])
                    tail = f"{e['lines']:,} lines"
                mark = " ✓" if e["slug"] in owned else ""
                lines.append(f"`{label:9}` **{e['name']}**{mark} — {tail}")
            embed = discord.Embed(
                title="Everything That Can Drop",
                description=header + "\n\n" + "\n".join(lines),
                color=_card_color(entries[i]))
            embed.set_footer(
                text=f"Page {i // POOL_PER_PAGE + 1} of "
                     f"{(len(entries) - 1) // POOL_PER_PAGE + 1} · "
                     f"✓ marks what you own · /pool view for a card")
            page_list.append(pages.Page(embeds=[embed]))

        if len(page_list) == 1:
            return await ctx.followup.send(embed=page_list[0].embeds[0])
        paginator = pages.Paginator(pages=page_list)
        add_paginator_buttons(paginator)
        await paginator.respond(ctx.interaction)

    @pool.command(name="rates", description="Drop chances for every tier")
    async def pool_rates(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        counts = cardlib.tier_counts()
        entries = await asyncio.to_thread(cardlib.db_get_pool)
        unminted = sum(1 for p in entries if not p["minted"])

        rows = []
        for tier in cardlib.TIER_ORDER:
            weight = cardlib.TIER_WEIGHTS[tier]
            if tier == "member":
                label, pool = "Member 1/1", unminted
            else:
                label, pool = _tier_label(tier), counts.get(tier, 0)
            # Chance of a *named* card: the tier has to land, then that one
            # card has to be the pick inside it.
            named = f"1 in {round(pool / (weight / 100)):,}" if pool else "\u2014"
            rows.append(f"{label:<11}{weight:>7.2f}%{pool:>7}{named:>15}")

        table = ("`" + f"{'Tier':<11}{'Chance':>8}{'Cards':>7}{'One specific':>15}"
                 + "`\n```\n" + "\n".join(rows) + "\n```")

        per_day = cardlib.REELS_PER_WINDOW * (24 * 3600 // cardlib.WINDOW_SECONDS)
        embed = discord.Embed(
            title="Drop rates",
            description=table,
            color=0x38C9BD)
        embed.add_field(
            name="Reading it",
            value=("**Chance** is per reel. **One specific** is the odds of a named "
                   "card — the tier has to land, then that card has to be "
                   "the one drawn from it."),
            inline=False)
        pct = int(cardlib.WISH_REDIRECT_CHANCE * 100)
        embed.add_field(
            name="Wishlist",
            value=(f"A wished card takes **{pct}%** of that tier's pulls. It "
                   "never changes how often a tier lands, so it cannot change "
                   "what you earn — only which card you get. Member 1/1s "
                   "can't be wished for."),
            inline=False)
        embed.add_field(
            name="At {} reels a day".format(per_day),
            value=(f"An epic about weekly, a legendary about monthly, and a "
                   f"1-in-4 shot at a member 1/1 over a month."),
            inline=False)
        embed.set_footer(
            text=f"{unminted} of {len(entries)} member 1/1s are still unminted, "
                 "so those odds shift as they are claimed")
        await ctx.followup.send(embed=embed)

    @pool.command(name="reset-reels",
                  description="[Admin] Refill everyone's reels for testing")
    @commands.has_permissions(administrator=True)
    async def pool_reset_reels(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        n = await asyncio.to_thread(cardlib.db_reset_all_reels)
        refresh = cardlib.next_refresh_ts()
        await ctx.followup.send(
            f"Refilled reels for **{n}** wallet{'' if n == 1 else 's'} to a "
            f"full bank. The next natural refresh is still <t:{refresh}:R>.")

    # ── /tank wishlist ───────────────────────────────────────────────────────

    @wish.command(
        name="add",
        description="Wish for a card — 25% of that tier's pulls go to it")
    async def wish_add(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="Any card except a member 1/1",
                             autocomplete=_autocomplete_wishable),
    ):
        await ctx.defer(ephemeral=True)
        match = await asyncio.to_thread(_resolve, card)
        if match is None:
            return await ctx.followup.send(f"No card called **{card}** exists.")
        if not cardlib.is_wishable(match):
            return await ctx.followup.send(
                "Member 1/1s can't be wished for. There is only one of each, "
                "so nobody gets to aim at a particular person's card.")

        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        limit = cardlib.wish_slots(wallet["tank_tier"])
        outcome = await asyncio.to_thread(cardlib.db_add_wish, ctx.author.id,
                                          match["slug"], limit)
        if outcome == "duplicate":
            return await ctx.followup.send(
                f"**{match['name']}** is already on your wishlist.")
        if outcome == "full":
            return await ctx.followup.send(
                f"You've used all {limit} wish slot"
                f"{'' if limit == 1 else 's'}. Remove one, or upgrade your tank.")
        pct = int(cardlib.WISH_REDIRECT_CHANCE * 100)
        await ctx.followup.send(
            f"Wishing for **{match['name']}**. When a "
            f"{_tier_label(match['tier']).lower()} lands, there's a {pct}% "
            "chance it's this one.")

    @wish.command(name="remove", description="Stop wishing for a card")
    async def wish_remove(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="Card to drop",
                             autocomplete=_autocomplete_wishable),
    ):
        await ctx.defer(ephemeral=True)
        match = await asyncio.to_thread(_resolve, card)
        if match is None:
            return await ctx.followup.send(f"No card called **{card}** exists.")
        ok = await asyncio.to_thread(cardlib.db_remove_wish, ctx.author.id,
                                     match["slug"])
        await ctx.followup.send(
            f"Removed **{match['name']}** from your wishlist." if ok
            else f"**{match['name']}** wasn't on your wishlist.")

    @wish.command(name="list", description="Show your wishlist")
    async def wish_list(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        wishes = await asyncio.to_thread(cardlib.db_get_wishes, ctx.author.id)
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        limit = cardlib.wish_slots(wallet["tank_tier"])
        if not wishes:
            return await ctx.followup.send(
                f"No wishes set. You have {limit} slot"
                f"{'' if limit == 1 else 's'} — try `/tank wishlist add`.")
        lines = []
        for slug in wishes:
            c = cardlib.get_card(slug)
            if c:
                lines.append(f"`{_tier_label(c['tier']):9}` {c['name']}")
        pct = int(cardlib.WISH_REDIRECT_CHANCE * 100)
        await ctx.followup.send(
            f"**Wishlist ({len(wishes)}/{limit})**\n" + "\n".join(lines)
            + f"\n-# When one of these tiers lands, there's a {pct}% chance "
              "the card is one you wished for. Tier odds are untouched.")


def setup(client):
    client.add_cog(Cards(client))
