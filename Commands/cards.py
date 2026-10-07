"""Card collection commands for the main guild.

The loop: /reel pulls cards and every pull pays pearls, duplicates included;
pearls buy star fusion and tank upgrades. Wishes bias which legendary or
fabled you land. Nothing here touches shells; the two economies never meet.
"""

import asyncio

import discord
from discord.commands import SlashCommandGroup, slash_command
from discord.ext import commands, pages

from Helpers import cards as cardlib
from Helpers import card_copy as ctext
from Helpers.card_render import card_file, spread_file
from Helpers.logger import ERROR, SYSTEM, log
from Helpers.pagination import add_paginator_buttons
from Helpers.variables import CARD_PING_ROLE_ID, TAQ_GUILD_IDS

CARDS_PER_PAGE = 20
POOL_PER_PAGE = 20

# /tank admin set-channel is deliberately exempt from the channel check. It is
# the command that fixes a wrong setting, so gating it behind the setting would
# lock the guild out of its own configuration.
CHANNEL_EXEMPT = {"set-channel"}
HISTORY_LINES = 12   # session pulls listed above the current card


class WrongCardChannel(discord.CheckFailure):
    """Raised when a card command is used outside the configured channel."""

    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        super().__init__(f"Use <#{channel_id}>")


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
    embed = _notice(f"Use <#{error.channel_id}>")
    try:
        if ctx.response.is_done():
            await ctx.followup.send(embed=embed, ephemeral=True)
        else:
            await ctx.respond(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


def _tier_label(tier: str) -> str:
    return ctext.tier_label(tier)


def _tier_of(card: dict | None) -> str:
    """The tier a card sorts and filters under; member cards are their own."""
    if not card:
        return ""
    return "member" if card.get("member") else card["tier"]


# One filter list for every command that narrows by tier, member cards first.
TIER_CHOICES = [discord.OptionChoice(_tier_label(t), t)
                for t in cardlib.TIER_ORDER]
LEADERBOARD_SIZE = 15
OWNERS_SHOWN = 20      # /card owners lists this many holders before "+N more"


def _notice(text: str) -> discord.Embed:
    """A short service reply — errors, empties, confirmations — as an embed,
    so every answer the bot gives shares one look."""
    return discord.Embed(description=text, color=ctext.ACCENT)


def _stars(n: int) -> str:
    return "★" * n if n > 1 else ""


def _resolve(name: str) -> dict | None:
    """Find a card by display name across the set and the minted member cards."""
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
    reads off FUSION_COPIES rather than repeating what is in it.
    """
    groups = {}
    for tier in cardlib.CARD_TIERS:
        copies = cardlib.copies_for(tier, cardlib.MAX_STARS)
        groups.setdefault(copies, []).append(tier)

    parts = []
    for copies, tiers in groups.items():
        labels = [_tier_label(t) for t in tiers]
        if len(labels) > 1:
            names = f"{', '.join(labels[:-1])} or {labels[-1]}"
        else:
            names = labels[0]
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


async def _autocomplete_discardable(ctx: discord.AutocompleteContext):
    """Plain stacks of anything that has a tier below it."""
    try:
        choices = await _stack_choices(ctx.interaction.user.id,
                                       (ctx.value or "").lower(),
                                       resolve_member=False)
    except Exception:
        return []
    out = []
    for ch in choices:
        picked = _parse_stack(ch.value)
        if picked and picked[1] == 0 and cardlib.discard_yield(
                cardlib.get_card(picked[0])):
            out.append(ch)
    return out


async def _autocomplete_fusable(ctx: discord.AutocompleteContext):
    try:
        choices = await _stack_choices(ctx.interaction.user.id,
                                       (ctx.value or "").lower(),
                                       resolve_member=False)
    except Exception:
        return []
    out = []
    for ch in choices:
        picked = _parse_stack(ch.value)
        if picked and not cardlib.is_member_slug(picked[0]) \
                and picked[1] < cardlib.MAX_STARS:
            out.append(ch)
    return out


async def _autocomplete_mergeable(ctx: discord.AutocompleteContext):
    try:
        choices = await _stack_choices(ctx.interaction.user.id,
                                       (ctx.value or "").lower())
        first = _parse_stack((ctx.options or {}).get("first") or "")
        wanted = None
        if first:
            card = cardlib.get_card(first[0]) or await asyncio.to_thread(
                cardlib.db_get_member_card, first[0])
            wanted = _tier_of(card)
    except Exception:
        return []
    out = []
    for ch in choices:
        slug, star = _parse_stack(ch.value)
        if star != 0:
            continue
        if wanted and _tier_of(cardlib.get_card(slug) or
                               {"member": True}) != wanted:
            continue
        out.append(ch)
    return out


async def _autocomplete_pool(ctx: discord.AutocompleteContext):
    """Everything obtainable: member cards first, then the card set."""
    typed = (ctx.value or "").lower()
    try:
        members = [p["name"] for p in await asyncio.to_thread(cardlib.db_get_pool)
                   if typed in p["name"].lower()]
    except Exception as e:
        # The list must still answer, but a member silently missing from it
        # reads as "I'm not in the pool", so say what actually happened.
        log(ERROR, f"Pool autocomplete lost the members: {e!r}", context="cards")
        members = []
    cards = sorted(c["name"] for c in cardlib.load_card_set()["cards"]
                   if typed in c["name"].lower())
    return (sorted(members) + cards)[:25]


def _find_card(name: str) -> dict | None:
    """A card from the set by display name. Member cards live in the DB."""
    wanted = name.strip().lower()
    for c in cardlib.load_card_set()["cards"]:
        if c["name"].lower() == wanted:
            return c
    return None


def _pool_entries(tier: str | None, set_id: str | None = None) -> list:
    """Everything that can drop, rarest first, narrowed by tier and set.

    Member cards lead because they are the rarest thing in the game; the card
    set follows in tier order, alphabetical inside each tier. A set never
    holds a member card, so naming one leaves them out.
    """
    out = []
    if tier in (None, "member") and set_id is None:
        out += cardlib.db_get_pool()
    cards = cardlib.load_card_set()["cards"]
    if set_id is not None:
        wanted = next((s["slugs"] for s in cardlib.load_card_set()["sets"]
                       if s["id"] == set_id), [])
        cards = [c for c in cards if c["slug"] in wanted]
    if tier != "member":
        wanted = [c for c in cards if tier is None or c["tier"] == tier]
        order = {t: i for i, t in enumerate(cardlib.CARD_TIERS)}
        out += sorted(wanted, key=lambda c: (order[c["tier"]], c["name"]))
    return out


def _set_choices() -> list:
    return [discord.OptionChoice(s["name"], s["id"])
            for s in cardlib.load_card_set()["sets"]]


async def _autocomplete_wishable(ctx: discord.AutocompleteContext):
    """Any card in the set. Member cards are never wishable."""
    typed = (ctx.value or "").lower()
    names = [c["name"] for c in cardlib.load_card_set()["cards"]
             if c["tier"] in cardlib.WISHABLE_TIERS and typed in c["name"].lower()]
    return sorted(names)[:25]


# Card art and dialogue come from the Wynncraft Wiki, credited by a link
# directly above the card. Member cards are rendered from Minecraft skins
# instead, so they carry no link.


def _wiki_line(card: dict) -> str | None:
    """The source, as a link sitting immediately above the card image.

    A footer would put it below the art but Discord does not render links
    there, so the description is the closest spot that stays clickable.
    """
    url = card.get("wiki_url")
    if not url or card.get("member"):
        return None
    return ctext.wiki(card)


def _card_description(card: dict) -> str | None:
    """Wiki link plus the sets the card counts toward, if any."""
    bits = [_wiki_line(card)]
    if not card.get("member"):
        names = [s["name"] for s in cardlib.sets_of(card["slug"])]
        if names:
            bits.append(f"-# {ctext.plural(len(names), 'Set')}: {', '.join(names)}")
    return "\n".join(b for b in bits if b) or None


def _set_bar(owned: int, total: int, width: int = 10) -> str:
    filled = round(width * owned / total) if total else 0
    return "█" * filled + "░" * (width - filled)


def _set_line(p: dict) -> str:
    mark = "✓" if p["complete"] else " "
    return (f"`{mark} {_set_bar(p['owned'], p['total'])} {p['owned']:>2}/{p['total']:<2}` "
            f"**{p['set']['name']}**")


LANDING = "__sets__"   # the select value that returns to the overview


class SetPickView(discord.ui.View):
    """Pick a set to see its cards; the viewer's missing ones are dimmed.

    Every pick edits the one message rather than posting another, so browsing
    six sets leaves one message in the channel, not seven. The first option
    brings the overview back.
    """

    def __init__(self, owner_id: int, target: discord.abc.User, progress: list,
                 owned: set, landing: discord.Embed):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.target = target
        self.progress = {p["set"]["id"]: p for p in progress}
        self.owned = owned
        self.landing = landing
        self.message = None
        self.pick.options = [discord.SelectOption(
            label="Sets", value=LANDING, description="Back to every set",
            emoji="\N{OPEN FILE FOLDER}")]
        self.pick.options += [
            discord.SelectOption(
                label=p["set"]["name"], value=p["set"]["id"],
                description=f"{p['owned']}/{p['total']}"
                            + (" Complete" if p["complete"] else ""),
                emoji="✅" if p["complete"] else None)
            for p in progress]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=_notice("Use `/card sets`"), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.select(placeholder="Open set", min_values=1, max_values=1)
    async def pick(self, select: discord.ui.Select,
                   interaction: discord.Interaction):
        if select.values[0] == LANDING:
            # attachments=[] drops the spread image the set page attached.
            return await interaction.response.edit_message(
                embed=self.landing, attachments=[], view=self)
        await interaction.response.defer()
        p = self.progress[select.values[0]]
        s = p["set"]
        cards = [cardlib.get_card(x) for x in s["slugs"]]
        file = await asyncio.to_thread(spread_file, cards, self.owned)
        embed = discord.Embed(
            title=s["name"],
            description=s["description"],
            color=_card_color(cards[0]))
        embed.set_image(url=f"attachment://{file.filename}")
        who = "You" if self.target.id == self.owner_id else self.target.display_name
        if p["complete"]:
            state = f"{who}: complete"
        else:
            names = ", ".join(cardlib.get_card(x)["name"] for x in p["missing"])
            state = f"{who}: missing {names}"
        embed.add_field(name=f"{p['owned']}/{p['total']}", value=state, inline=False)
        embed.set_footer(text=f"+{s['pearls']:,} Pearls")
        await interaction.edit_original_response(
            embed=embed, file=file, attachments=[], view=self)


def _credit(card: dict, *bits) -> str:
    """Footer text: what the command wants to report, nothing more."""
    return ctext.credit(*bits)


def _card_color(card: dict) -> int:
    if card.get("member"):
        return cardlib.TIER_COLORS["member"]
    return cardlib.TIER_COLORS.get(card["tier"], 0xFFFFFF)


def _card_embed(card: dict, copies: int, remaining: int, filename: str,
                who: str, stars: int = 0, gained: int = 0) -> discord.Embed:
    # The card art already prints the name, the tier or rank, the star level
    # and the member badge, so the embed adds nothing but what the art cannot
    # show: who rolled it, and what it means for your collection.
    embed = discord.Embed(color=_card_color(card),
                          description=_wiki_line(card))
    embed.set_author(name=f"{who}'s reel")
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=_credit(
        card,
        ctext.copy_label(copies),
        f"+{gained:,} Pearls" if gained else None,
        f"{ctext.count(remaining, 'Reel')} left"))
    return embed


def _history_content(history: list) -> str:
    """The session's earlier pulls, one line each, above the current card."""
    lines = []
    shown = history[-HISTORY_LINES:]
    if len(history) > HISTORY_LINES:
        lines.append(f"-# +{len(history) - HISTORY_LINES} earlier")
    for c in shown:
        tier = _tier_label(_tier_of(c))
        lines.append(f"-# {tier}: {c['name']}")
    refresh = cardlib.next_refresh_ts()
    lines.append(f"-# {ctext.next_line('Reels refresh', refresh)}")
    return "\n".join(lines)


async def _do_reel(user_id: int, who: str):
    """Spend a reel and roll a card.

    Returns (embed, file, remaining, card) on success, or (None, None, reason,
    None) where reason is 'out', 'render' or 'failed'. Anything that goes
    wrong before the card is banked refunds the reel and releases a member
    card minted on the way, so a failed pull never costs anything. Once the
    card is banked nothing is refunded: the pull happened.
    """
    spend = await asyncio.to_thread(cardlib.db_spend_reel, user_id)
    if spend is None:
        return None, None, "out", None
    remaining = spend["total"]

    card, minted, stage = None, None, "roll"
    try:
        wishes = set(await asyncio.to_thread(cardlib.db_get_wishes, user_id))
        card = cardlib.roll_card(wishes=wishes)

        if card.get("tier") == "member":
            minted = await asyncio.to_thread(cardlib.db_mint_member_card,
                                             user_id)
            # Every eligible member already holds a card: fall back to a normal
            # pull rather than silently eating the reel.
            card = minted or cardlib.roll_card(wishes=wishes)
            if card.get("tier") == "member":
                card = cardlib.roll_card()

        stage = "render"
        # Spelled out rather than left to defaults: a fresh pull is always
        # unfused, and that is worth saying at the call site.
        file = await asyncio.to_thread(card_file, card, None, 0,
                                       cardlib.tier_max_stars(card))

        stage = "grant"
        pearls = cardlib.pull_value(card)
        result = await asyncio.to_thread(cardlib.db_add_card, user_id,
                                         card["slug"], pearls)
    except Exception as e:
        slug = card.get("slug") if card else None
        log(ERROR, f"Reel failed at {stage} for {user_id} ({slug}): {e}",
            context="cards")
        await _undo_pull(user_id, spend["used_bait"], minted)
        return None, None, ("render" if stage == "render" else "failed"), None

    embed = _card_embed(card, result["count"], remaining, file.filename, who,
                        gained=result["gained"])
    return embed, file, remaining, card


async def _undo_pull(user_id: int, bait: bool, minted: dict | None):
    """Give back what a failed pull took.

    A refund that itself fails is logged with what the player is owed, so they
    can be made whole by hand.
    """
    try:
        await asyncio.to_thread(cardlib.db_refund_reel, user_id, bait)
    except Exception as e:
        log(ERROR, f"Reel refund failed for {user_id}; owed one "
            f"{'bait ' if bait else ''}reel: {e}", context="cards")
    if minted:
        try:
            await asyncio.to_thread(cardlib.db_release_member_card,
                                    minted["slug"], user_id)
        except Exception as e:
            log(ERROR, f"Member card {minted['slug']} not released after a "
                f"failed pull by {user_id}: {e}", context="cards")


def _failure_text(reason: str) -> str:
    return ctext.render_failed() if reason == "render" else ctext.reel_failed()


def _milestone_embed(user, kind: str, name, pearls: int) -> discord.Embed:
    """One payout, in a form that cannot be scrolled past by accident.

    A set completion is worth up to a month of pulls, so it gets the same
    weight as the card that finished it.
    """
    if kind == "set":
        title, color = f"Set complete · {name}", ctext.ACCENT
    elif kind == "tier":
        title = f"Every {_tier_label(name)} card"
        color = cardlib.TIER_COLORS.get(name, ctext.ACCENT)
    else:
        title, color = f"{name} unique cards", ctext.ACCENT
    return discord.Embed(
        title=title,
        description=f"{user.mention} · **+{pearls:,}** Pearls",
        color=color)


async def _announce_and_reward(channel, user, card):
    """Shout about a member card and hand out any milestones the pull just
    completed."""
    try:
        if card and card.get("member"):
            await channel.send(embed=discord.Embed(
                title="Limited card found",
                description=f"{user.mention} pulled **{card['name']}** ({card['rank']})",
                color=cardlib.TIER_COLORS["member"]))

        collection = await asyncio.to_thread(cardlib.db_get_collection, user.id)
        earned = await asyncio.to_thread(cardlib.check_milestones, user.id,
                                         collection)
        for award in earned:
            await channel.send(embed=_milestone_embed(user, *award))
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
        self.reel_again.label = "No Reels" if exhausted else "Reel again"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=_notice("Run your own `/reel`"),
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
        # Acknowledge before any work. A pull renders an image and makes
        # several database round trips, which at a reel refresh can outlast
        # Discord's three-second window; a reply that fails after the card is
        # banked makes a paid reel look eaten.
        await interaction.response.defer()
        embed, file, info, card = await _do_reel(self.owner_id, self.owner_name)

        if embed is None:
            if info == "out":
                self.set_exhausted(True)
                refresh = cardlib.next_refresh_ts()
                await interaction.edit_original_response(view=self)
                return await interaction.followup.send(
                    embed=_notice(ctext.no_reels(refresh)),
                    ephemeral=True)
            return await interaction.followup.send(
                embed=_notice(_failure_text(info)), ephemeral=True)

        # The card being replaced becomes part of the session log above it.
        if self.current is not None:
            self.history.append(self.current)
        self.current = card

        self.set_exhausted(info == 0)
        try:
            # attachments=[] drops the previous card image; the new file
            # replaces it.
            await interaction.edit_original_response(
                content=_history_content(self.history),
                embed=embed, file=file, attachments=[], view=self)
        except discord.HTTPException as e:
            log(ERROR, f"Reel reply failed for {self.owner_id}; "
                f"{card['slug']} is banked: {e}", context="cards")
            try:
                await interaction.followup.send(
                    embed=_notice(ctext.pull_unshown(card["name"])),
                    ephemeral=True)
            except discord.HTTPException:
                pass
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
                embed=_notice("Not your trade"), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content="-# Trade expired", view=self)
        except discord.HTTPException:
            pass

    async def _close(self, interaction, text):
        self.done = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=None, embed=_notice(text), view=self)

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
                "Trade changed")
        await self._close(
            interaction,
            f"Trade done\n"
            f"{self.proposer.mention}: **{_star_name(self.want, self.want_star)}**\n"
            f"{self.target.mention}: **{_star_name(self.give, self.give_star)}**")
        # Trading for the last missing card is how most sets will get finished.
        for user in (self.proposer, self.target):
            await _announce_and_reward(interaction.channel, user, None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, button: discord.ui.Button,
                      interaction: discord.Interaction):
        await self._close(interaction, "Trade declined")


def _yield_line() -> str:
    """The discard table in one line, read off DISCARD_YIELD."""
    return " · ".join(f"{_tier_label(t)} → {n} {_tier_label(below)}"
                      for t, (below, n) in cardlib.DISCARD_YIELD.items())


async def _do_discard(user_id: int, card: dict, count: int):
    """Roll the outputs, make the swap, and build the reveal.

    Returns (embed, file, outputs) or (None, None, None) when the copies were
    gone by the time the write ran.
    """
    below, per = cardlib.discard_yield(card)
    outputs = [cardlib.roll_in_tier(below) for _ in range(count * per)]

    result = await asyncio.to_thread(cardlib.db_discard, user_id, card["slug"],
                                     count, [c["slug"] for c in outputs])
    if result is None:
        return None, None, None

    file = await asyncio.to_thread(spread_file, outputs)
    tally = {}
    for c in outputs:
        tally[c["slug"]] = tally.get(c["slug"], 0) + 1
    by_slug = {c["slug"]: c for c in outputs}
    lines = []
    for slug, n in sorted(tally.items(), key=lambda kv: by_slug[kv[0]]["name"]):
        mark = " New" if slug in result["new"] else ""
        lines.append(f"{n}× **{by_slug[slug]['name']}**{mark}")

    embed = discord.Embed(
        title=f"Discarded {count}× {card['name']}",
        description=(f"**{len(outputs)}** {_tier_label(below)}"
                     f"{'' if len(outputs) == 1 else 's'}\n"
                     + "\n".join(lines)),
        color=cardlib.TIER_COLORS[below])
    embed.set_image(url=f"attachment://{file.filename}")
    embed.set_footer(text=_credit(
        card,
        f"{result['left']} Plain left",
        f"{len(result['new'])} New" if result["new"] else None))
    return embed, file, outputs


async def _do_fuse(user_id: int, card: dict, from_star: int, steps: int,
                   expect: dict | None = None):
    feed, per_pearls = cardlib.fusion_cost(card["tier"])
    result = await asyncio.to_thread(cardlib.db_fuse, user_id, card["slug"],
                                     from_star, steps, feed, per_pearls, expect)
    if result is None:
        return None, None

    ceiling = cardlib.tier_max_stars(card)
    to_star = result["stars"]
    file = await asyncio.to_thread(card_file, card, None, to_star, ceiling)
    spent = "\n".join(
        f"-{n} {ctext.level_name(_level_label(card, st))}"
        for st, n in sorted(result["spent"].items()))
    summary = (f"{spent}\n-{per_pearls * steps:,} Pearls\n"
               f"{result['pearls']:,} Pearls left")
    if to_star >= ceiling:
        summary = f"**MAX**\n{summary}"
    line = _wiki_line(card)
    embed = discord.Embed(
        title=f"{card['name']} {_level_label(card, to_star)}".strip(),
        description=f"{summary}\n{line}" if line else summary,
        color=cardlib.TIER_COLORS[card["tier"]])
    embed.set_image(url=f"attachment://{file.filename}")
    embed.set_footer(text=_credit(
        card, f"{cardlib.copies_for(card['tier'], to_star)} copies behind"))
    return embed, file


class FuseView(discord.ui.View):
    def __init__(self, owner_id: int, card: dict, from_star: int, steps: int,
                 spend: dict):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.card = card
        self.from_star = from_star
        self.steps = steps
        self.spend = spend
        self.message = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=_notice("Not your fusion"), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content="-# Fusion expired", view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Fuse", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button,
                      interaction: discord.Interaction):
        self.done = True
        await interaction.response.defer()
        embed, file = await _do_fuse(self.owner_id, self.card, self.from_star,
                                     self.steps, self.spend)
        if embed is None:
            return await interaction.edit_original_response(
                content=None, embed=_notice("Copies changed\nTry again"),
                view=None)
        await interaction.edit_original_response(
            content=None, embed=embed, file=file, attachments=[], view=None)

    @discord.ui.button(label="Keep", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button,
                     interaction: discord.Interaction):
        self.done = True
        await interaction.response.edit_message(
            content=None, embed=_notice(f"Kept **{self.card['name']}**"),
            view=None)


async def _do_merge(user_id: int, cards: list):
    tier = cards[0]["tier"]
    wishes = set(await asyncio.to_thread(cardlib.db_get_wishes, user_id))
    out = cardlib.roll_in_tier(tier, wishes)
    result = await asyncio.to_thread(cardlib.db_merge, user_id,
                                     [c["slug"] for c in cards], out["slug"])
    if result is None:
        return None, None

    file = await asyncio.to_thread(card_file, out, None, 0,
                                   cardlib.tier_max_stars(out))
    embed = discord.Embed(
        title=out["name"],
        description=(f"-{cards[0]['name']}\n-{cards[1]['name']}"
                     + ("\nNew" if result["new"] else "")),
        color=cardlib.TIER_COLORS[tier])
    embed.set_image(url=f"attachment://{file.filename}")
    embed.set_footer(text=_credit(out, f"{_tier_label(tier)} merge"))
    return embed, file


async def _do_merge_limited(user_id: int, cards: list):
    result = await asyncio.to_thread(cardlib.db_merge_limited, user_id,
                                     [c["slug"] for c in cards])
    if "reason" in result:
        text = ("No unminted Limited cards left" if result["reason"] == "empty"
                else "Cards changed\nTry again")
        return _notice(text), None, None

    card = result["card"]
    file = await asyncio.to_thread(card_file, card, None, 0, 0)
    if result["kept"]:
        lost = next(c["name"] for c in cards if c["slug"] in result["lost"])
        title, body = f"{card['name']} came back", f"Lost **{lost}**"
    else:
        title = card["name"]
        body = f"Lost **{cards[0]['name']}** and **{cards[1]['name']}**"
    embed = discord.Embed(title=title, description=body,
                          color=cardlib.TIER_COLORS["member"])
    embed.set_image(url=f"attachment://{file.filename}")
    embed.set_footer(text=_credit(card, f"{result['pool']} unminted in the draw"))
    return embed, file, None if result["kept"] else card


class LimitedMergeView(discord.ui.View):
    def __init__(self, owner_id: int, cards: list):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.cards = cards
        self.message = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=_notice("Not your merge"), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content="-# Merge expired", view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Merge", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button,
                      interaction: discord.Interaction):
        self.done = True
        await interaction.response.defer()
        embed, file, announce = await _do_merge_limited(self.owner_id, self.cards)
        await interaction.edit_original_response(
            content=None, embed=embed, file=file, attachments=[], view=None)
        await _announce_and_reward(interaction.channel, interaction.user, announce)

    @discord.ui.button(label="Keep", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button,
                     interaction: discord.Interaction):
        self.done = True
        await interaction.response.edit_message(
            content=None, embed=_notice("Kept both Limited cards"), view=None)


class DiscardView(discord.ui.View):
    """Confirm before a fabled or mythic goes. Owner only, one shot."""

    def __init__(self, owner_id: int, card: dict, count: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.card = card
        self.count = count
        self.message = None
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=_notice("Not your discard"), ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(content="-# Discard expired", view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.danger)
    async def confirm(self, button: discord.ui.Button,
                      interaction: discord.Interaction):
        self.done = True
        await interaction.response.defer()
        embed, file, _ = await _do_discard(self.owner_id, self.card, self.count)
        if embed is None:
            return await interaction.edit_original_response(
                content=None,
                embed=_notice("Copies changed"), view=None)
        await interaction.edit_original_response(
            content=None, embed=embed, file=file, attachments=[], view=None)
        await _announce_and_reward(interaction.channel, interaction.user, None)

    @discord.ui.button(label="Keep", style=discord.ButtonStyle.secondary)
    async def cancel(self, button: discord.ui.Button,
                     interaction: discord.Interaction):
        self.done = True
        await interaction.response.edit_message(
            content=None, embed=_notice(f"Kept **{self.card['name']}**"),
            view=None)


class Cards(commands.Cog):
    # /reel and /bait stay at the top level because they are run constantly.
    tank = SlashCommandGroup(name="tank", description=ctext.TANK,
                             guild_ids=TAQ_GUILD_IDS)
    admin = tank.create_subgroup(name="admin",
                                 description=ctext.ADMIN)
    card = SlashCommandGroup(name="card", description=ctext.CARD,
                             guild_ids=TAQ_GUILD_IDS)
    wish = card.create_subgroup(name="wishlist",
                                description=ctext.WISH)

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
                   description=ctext.REEL,
                   guild_ids=TAQ_GUILD_IDS)
    async def reel(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        who = ctx.author.display_name
        embed, file, info, card = await _do_reel(ctx.author.id, who)

        if embed is None:
            if info == "out":
                refresh = cardlib.next_refresh_ts()
                return await ctx.followup.send(
                    embed=_notice(ctext.no_reels(refresh)),
                    ephemeral=True)
            return await ctx.followup.send(
                embed=_notice(_failure_text(info)), ephemeral=True)

        view = ReelView(ctx.author.id, who, card, exhausted=(info == 0))
        # wait=True so the view can disable its own button when it times out.
        view.message = await ctx.followup.send(
            content=_history_content([]),
            embed=embed, file=file, view=view, wait=True)
        await _announce_and_reward(ctx.channel, ctx.author, card)

    # ── /bait ────────────────────────────────────────────────────────────────

    @slash_command(name="bait",
                   description=ctext.BAIT,
                   guild_ids=TAQ_GUILD_IDS)
    async def bait(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        result = await asyncio.to_thread(cardlib.db_claim_daily, ctx.author.id)

        if not result["ok"]:
            if result["reason"] == "unspent":
                held = result["bait_reels"]
                return await ctx.followup.send(
                    embed=_notice(f"Spend your **{held}** bait reel"
                                  f"{'' if held == 1 else 's'} first"),
                    ephemeral=True)
            reset = await asyncio.to_thread(cardlib.db_next_daily_reset)
            return await ctx.followup.send(
                embed=_notice(f"Bait already used, more <t:{reset}:R>"),
                ephemeral=True)

        # Two numbers and the streak. What bait reels are and where passive
        # pearls come from belong in /tank help, not in every claim.
        streak = result["streak"]
        embed = discord.Embed(
            title="Bait",
            description=(f"**+{result['bait_reels']}** Reels · "
                         f"**+{result['gained']:,}** Pearls"),
            color=ctext.ACCENT)
        if cardlib.next_daily_tier(streak):
            embed.set_footer(text=f"{streak} Day Streak")
        else:
            embed.set_footer(text="Max Streak")
        reset = await asyncio.to_thread(cardlib.db_next_daily_reset)
        await ctx.followup.send(
            content=f"-# {ctext.next_line('Bait refresh', reset)}", embed=embed)

    @tank.command(name="help", description=ctext.HELP)
    async def tank_help(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        cs = cardlib.load_card_set()
        cap = cardlib.bank_cap(wallet["tank_tier"])
        pct = int(cardlib.WISH_REDIRECT_CHANCE * 100)

        embed = discord.Embed(
            title="Tank help",
            description=(
                f"**{len(cs['cards'])}** Wynncraft cards\n"
                "Reeling cards gives you Pearls"),
            color=ctext.ACCENT)

        embed.add_field(
            name="Basics",
            value=(f"`/reel` Pull a card\n"
                   f"**{cardlib.REELS_PER_WINDOW}** Reels every 6h\n"
                   f"Bank: **{cap}** (Reels you can store before a refresh "
                   "goes to waste)\n\n"
                   "`/bait` Claim daily Reels and Pearls (stronger with streak)\n"
                   f"Caps at **{cardlib.MAX_BAIT_REELS}** Reels and "
                   f"**{cardlib.DAILY_TIERS[-1]['pearls']}** Pearls\n\n"
                   "`/tank ping` Toggle refresh pings"),
            inline=False)
        embed.add_field(
            name="Your tank",
            value=("`/tank profile` View balance and stats\n"
                   "`/tank list` View tank and its contents\n"
                   "`/tank view` View a specific card in your tank\n"
                   "`/tank upgrade` Upgrade for a bigger bank, Passive Pearls "
                   "and more wishes\n"
                   "`/tank leaderboard` View top collectors by Rating"),
            inline=False)
        embed.add_field(
            name="Card actions",
            value=("`/card fuse` Feed a card its own copies for a star\n"
                   f"MAX costs: {_max_costs()}\n"
                   "`/card merge` Merge two plain cards of a rarity into a "
                   "new roll (wishlist applies)\n"
                   "`/card discard` Discard plain dupes into lower-tier rolls "
                   "(wishlist does not apply)\n"
                   f"{_yield_line()}\n"
                   "`/card trade` Trade cards with other collectors\n"
                   "`/card sets` View progress in card sets"),
            inline=False)
        embed.add_field(
            name="Card catalogue",
            value=("`/card list` View all cards\n"
                   "`/card view` Preview a card\n"
                   "`/card owners` View who holds a card\n"
                   "`/card rates` View the odds"),
            inline=False)
        embed.add_field(
            name="Wishes",
            value=(f"`/card wishlist add` Target a card\n"
                   f"**{pct}%** of that tier can become it "
                   "(does not apply to Limited)"),
            inline=False)

        refresh = cardlib.next_refresh_ts()
        embed.set_footer(
            text=f"{wallet['total_reels']} Reel"
                 f"{'' if wallet['total_reels'] == 1 else 's'} · "
                 f"{wallet['pearls']:,} Pearls")
        await ctx.followup.send(
            content=f"-# {ctext.next_line('Next', refresh)}", embed=embed)

    # ── /tank view ───────────────────────────────────────────────────────────

    @tank.command(name="view", description="View a specific card in your tank")
    async def tank_view(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="Owned card",
                             autocomplete=_autocomplete_owned),
    ):
        await ctx.defer()
        match = await asyncio.to_thread(_resolve, card)
        entry = match and await asyncio.to_thread(
            cardlib.db_get_entry, ctx.author.id, match["slug"])
        # Whether the name is a card they have not pulled, a member whose card
        # is unminted, or a typo, the answer is the same: it is not in here.
        if not entry:
            return await ctx.followup.send(
                embed=_notice(f"**{card.strip()}** isn't in your tank"),
                ephemeral=True)

        stars = entry["best"]
        file = await asyncio.to_thread(card_file, match, None, stars,
                                       cardlib.tier_max_stars(match))
        embed = discord.Embed(color=_card_color(match),
                              description=_card_description(match))
        embed.set_image(url=f"attachment://{file.filename}")
        held = "" if match.get("member") else " · ".join(
            f"{c}× {ctext.level_name(_level_label(match, st))}"
            for st, c in sorted(entry["levels"].items()))
        embed.set_footer(text=_credit(match, held))
        await ctx.followup.send(embed=embed, file=file)

    # ── /tank list ───────────────────────────────────────────────────────────

    @tank.command(name="list", description="View tank and its contents")
    async def tank_list(
        self, ctx: discord.ApplicationContext,
        member: discord.Option(
            discord.Member,
            description="Player",
            required=False, default=None),
        tier: discord.Option(
            str, description="Tier",
            required=False, default=None, choices=TIER_CHOICES),
        sort: discord.Option(
            str, description="Order",
            required=False, default="tier",
            choices=[discord.OptionChoice("Tier", "tier"),
                     discord.OptionChoice("Copies", "copies"),
                     discord.OptionChoice("Name", "name")]),
    ):
        await ctx.defer()
        target = member or ctx.author
        mine = target.id == ctx.author.id

        owned = await asyncio.to_thread(cardlib.db_get_collection, target.id)
        rating = cardlib.collection_rating(owned)
        if tier:
            members = await asyncio.to_thread(
                cardlib.db_get_member_cards,
                [s for s in owned if cardlib.is_member_slug(s)])
            owned = {slug: e for slug, e in owned.items()
                     if _tier_of(cardlib.get_card(slug) or members.get(slug)) == tier}
        cs = cardlib.load_card_set()
        # Only read a wallet for the viewer. db_get_wallet creates the row it
        # reads, so asking for someone else's would open a tank they may never
        # have played, and their pearls are not the viewer's business anyway.
        wallet = (await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
                  if mine else None)

        if not owned:
            if tier:
                whose = "your" if mine else f"{target.display_name}'s"
                return await ctx.followup.send(
                    embed=_notice(f"No {_tier_label(tier)} cards "
                                  f"in {whose} tank"),
                    ephemeral=True)
            if not mine:
                return await ctx.followup.send(
                    embed=_notice(f"{target.display_name} has no cards"),
                    ephemeral=True)
            return await ctx.followup.send(
                embed=_notice(f"Empty tank\n{wallet['total_reels']} Reel"
                              f"{'' if wallet['total_reels'] == 1 else 's'} ready"),
                ephemeral=True)

        members = await asyncio.to_thread(
            cardlib.db_get_member_cards,
            [s for s in owned if cardlib.is_member_slug(s)])

        rows = []
        for slug, entry in owned.items():
            c = cardlib.get_card(slug) or members.get(slug)
            if c:
                rows.append((c, entry))

        def tier_idx(card):
            t = _tier_of(card)
            return cardlib.TIER_ORDER.index(t) if t in cardlib.TIER_ORDER else 9

        if sort == "copies":
            # Most copies first, so the stacks worth fusing or trading are on
            # page one; rarity breaks ties, then the name.
            rows.sort(key=lambda r: (-r[1]["total"], tier_idx(r[0]), r[0]["name"]))
        elif sort == "name":
            rows.sort(key=lambda r: r[0]["name"].lower())
        else:
            rows.sort(key=lambda r: (tier_idx(r[0]), r[0]["name"]))

        total_copies = sum(e["total"] for _, e in rows)
        if tier == "member":
            of = len(await asyncio.to_thread(cardlib.db_get_pool))
        elif tier:
            of = cardlib.tier_counts().get(tier, 0)
        else:
            of = len(cs["cards"])
        what = f"{_tier_label(tier)} Cards" if tier else "Cards"
        header = (f"**{len(rows)}/{of}** {what}\n{total_copies} copies"
                  f"\n{rating:,} Rating")
        if mine:
            header += (f"\n{wallet['pearls']:,} Pearls\n"
                       f"{wallet['total_reels']} Reel"
                       f"{'' if wallet['total_reels'] == 1 else 's'}")
        else:
            spares = sum(e["total"] - 1 for _, e in rows if e["total"] > 1)
            header += f"\n{spares} spare{'' if spares == 1 else 's'}"

        page_list = []
        for i in range(0, len(rows), CARDS_PER_PAGE):
            chunk = rows[i:i + CARDS_PER_PAGE]
            lines = []
            for c, e in chunk:
                member = c.get("member")
                label_col = _tier_label(_tier_of(c))
                # A member card has no count worth printing: there is one,
                # there was only ever going to be one, and it cannot be fused.
                bits = []
                if not member:
                    for st, n in sorted(e["levels"].items()):
                        label = _level_label(c, st)
                        bits.append(f"{label}×{n}" if label else f"×{n}")
                    # Sorting by copies orders on the total, which a split
                    # stack does not show; a single level already reads as one.
                    if sort == "copies" and len(e["levels"]) > 1:
                        bits.append(f"({e['total']} total)")
                line = f"`{label_col:9}` {c['name']}"
                lines.append(f"{line} {' '.join(bits)}" if bits else line)
            embed = discord.Embed(
                title=f"{target.display_name}'s tank",
                description=header + "\n\n" + "\n".join(lines),
                color=_card_color(chunk[0][0]))
            embed.set_footer(text=f"{i // CARDS_PER_PAGE + 1}/"
                                  f"{(len(rows) - 1) // CARDS_PER_PAGE + 1}")
            page_list.append(pages.Page(embeds=[embed]))

        if len(page_list) == 1:
            return await ctx.followup.send(embed=page_list[0].embeds[0])
        paginator = pages.Paginator(pages=page_list)
        add_paginator_buttons(paginator)
        await paginator.respond(ctx.interaction)

    @card.command(name="sets", description="View progress in card sets")
    async def card_sets(
        self, ctx: discord.ApplicationContext,
        member: discord.Option(
            discord.Member,
            description="Player",
            required=False, default=None),
    ):
        await ctx.defer()
        target = member or ctx.author
        mine = target.id == ctx.author.id
        owned = await asyncio.to_thread(cardlib.db_get_collection, target.id)
        progress = cardlib.set_progress(owned)
        if not progress:
            return await ctx.followup.send(embed=_notice("No sets yet"),
                                           ephemeral=True)
        progress.sort(key=lambda p: (not p["complete"],
                                     -p["owned"] / p["total"], p["set"]["name"]))
        done = sum(p["complete"] for p in progress)
        embed = discord.Embed(
            title=f"{'Your' if mine else target.display_name + chr(39) + 's'} sets",
            description=f"**{done}/{len(progress)}** complete\n\n"
                        + "\n".join(_set_line(p) for p in progress),
            color=ctext.ACCENT)
        embed.set_footer(text="Pick a set")
        view = SetPickView(ctx.author.id, target, progress, set(owned), embed)
        view.message = await ctx.followup.send(embed=embed, view=view)

    # ── /tank profile ────────────────────────────────────────────────────────

    @tank.command(name="profile",
                  description="View balance and stats")
    async def tank_profile(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        owned = await asyncio.to_thread(cardlib.db_get_collection, ctx.author.id)
        wishes = await asyncio.to_thread(cardlib.db_get_wishes, ctx.author.id)
        cs = cardlib.load_card_set()

        tier = wallet["tank_tier"]
        spec = cardlib.TANK_TIERS[tier]
        copies = sum(e["total"] for e in owned.values()) if owned else 0

        embed = discord.Embed(title=f"{ctx.author.display_name}'s tank",
                              color=ctext.ACCENT)
        embed.add_field(name="Tank", value=f"{spec['name']} (Tier {tier})")
        embed.add_field(name="Pearls", value=f"{wallet['pearls']:,}")
        reels_value = f"{wallet['reels']}/{spec['bank']}"
        if wallet["bait_reels"]:
            reels_value += f" + {wallet['bait_reels']} Bait"
        embed.add_field(name="Reels", value=reels_value)
        embed.add_field(
            name="Cards",
            value=f"{len(owned)}/{len(cs['cards'])} unique · {copies} copies")
        embed.add_field(name="Rating",
                        value=f"{cardlib.collection_rating(owned):,}")
        embed.add_field(name="Streak", value=f"{wallet['streak']} day"
                        f"{'' if wallet['streak'] == 1 else 's'}")
        embed.add_field(name="Pulled",
                        value=f"{wallet['total_reeled']:,} all time")
        progress = cardlib.set_progress(owned)
        if progress:
            embed.add_field(
                name="Sets",
                value=f"{sum(p['complete'] for p in progress)}/{len(progress)}")
        embed.add_field(
            name="Passive Pearls",
            value=ctext.passive_value(spec["trickle"]))
        embed.add_field(
            name=f"Wishes ({len(wishes)}/{spec['wishes']})",
            value=", ".join((cardlib.get_card(w) or {"name": w})["name"]
                            for w in wishes) or "None",
            inline=False)
        if tier < cardlib.MAX_TANK:
            nxt = cardlib.TANK_TIERS[tier + 1]
            embed.set_footer(
                text=f"Next: {nxt['name']} · {nxt['cost']:,} Pearls")
        await ctx.followup.send(embed=embed)

    # ── /tank ping ───────────────────────────────────────────────────────────

    @tank.command(name="ping",
                  description="Toggle refresh pings")
    async def tank_ping(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        role = ctx.guild.get_role(CARD_PING_ROLE_ID) if CARD_PING_ROLE_ID else None
        if role is None:
            return await ctx.followup.send(
                embed=_notice("No reel ping role set"))
        me = ctx.guild.me
        if not me or not me.guild_permissions.manage_roles or role >= me.top_role:
            return await ctx.followup.send(
                embed=_notice(f"Move {role.mention} below my top role"))

        reason = f"/tank ping by {ctx.author} ({ctx.author.id})"
        try:
            if role in ctx.author.roles:
                await ctx.author.remove_roles(role, reason=reason)
                return await ctx.followup.send(
                    embed=_notice("Reel pings off"))
            await ctx.author.add_roles(role, reason=reason)
        except discord.Forbidden:
            return await ctx.followup.send(
                embed=_notice(f"I can't hand out {role.mention}"))
        refresh = cardlib.next_refresh_ts()
        await ctx.followup.send(
            embed=_notice("Reel pings on\n"
                          + ctext.next_line("Next", refresh)))

    # ── /tank upgrade ────────────────────────────────────────────────────────

    @tank.command(name="upgrade",
                  description="Upgrade for a bigger bank, Passive Pearls and more wishes")
    async def tank_upgrade(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        tier = wallet["tank_tier"]
        if tier >= cardlib.MAX_TANK:
            return await ctx.followup.send(
                embed=_notice("Tank already maxed"),
                ephemeral=True)

        nxt = cardlib.TANK_TIERS[tier + 1]
        if wallet["pearls"] < nxt["cost"]:
            return await ctx.followup.send(
                embed=_notice(
                    f"{nxt['name']} costs {nxt['cost']:,} Pearls — you have "
                    f"{wallet['pearls']:,}"), ephemeral=True)

        ok = await asyncio.to_thread(cardlib.db_upgrade_tank, ctx.author.id,
                                     tier + 1)
        if not ok:
            return await ctx.followup.send(
                embed=_notice("Upgrade changed\nTry again"),
                ephemeral=True)

        embed = discord.Embed(
            title=f"Tank: {nxt['name']}",
            description=(f"Bank: **{nxt['bank']}**\n"
                         f"Passive Pearls: **{ctext.per_hour(nxt['trickle'])}**\n"
                         f"Wishes: **{nxt['wishes']}**"),
            color=ctext.ACCENT)
        if tier + 1 < cardlib.MAX_TANK:
            after = cardlib.TANK_TIERS[tier + 2]
            embed.set_footer(
                text=f"Next: {after['name']} · {after['cost']:,} Pearls")
        await ctx.followup.send(embed=embed)

    # ── /tank leaderboard ────────────────────────────────────────────────────

    @tank.command(name="leaderboard",
                  description="View top collectors by Rating")
    async def tank_leaderboard(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        standings = await asyncio.to_thread(cardlib.db_standings)
        rows = standings[:LEADERBOARD_SIZE]
        if not rows:
            return await ctx.followup.send(embed=_notice("No tanks yet"),
                                           ephemeral=True)
        cs = cardlib.load_card_set()
        lines = []
        for r in rows:
            member = ctx.guild.get_member(r["user"]) if ctx.guild else None
            name = member.display_name if member else f"User {r['user']}"
            lines.append(f"`{r['rank']:2}` **{name}**: {r['rating']:,} Rating, "
                         f"{r['uniques']}/{len(cs['cards'])}, "
                         f"{r['copies']} copies")
        embed = discord.Embed(title="Top tanks", description="\n".join(lines),
                              color=ctext.ACCENT)
        mine = next((r for r in standings if r["user"] == ctx.author.id), None)
        if mine and mine["rank"] > LEADERBOARD_SIZE:
            embed.set_footer(text=f"You: #{mine['rank']} · "
                                  f"{mine['rating']:,} Rating")
        await ctx.followup.send(embed=embed)

    @card.command(
        name="fuse",
        description="Feed a card its own copies for a star")
    async def card_fuse(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="Card to raise",
                             autocomplete=_autocomplete_fusable),
        stars: discord.Option(
            int, description="Stars to add",
            required=False, default=1, min_value=1,
            max_value=cardlib.MAX_STARS),
    ):
        await ctx.defer()
        picked = _parse_stack(card)
        if picked is None:
            return await ctx.followup.send(
                embed=_notice("Pick a stack"),
                ephemeral=True)
        slug, from_star = picked

        match = cardlib.get_card(slug) or await asyncio.to_thread(
            cardlib.db_get_member_card, slug)
        if match is None:
            return await ctx.followup.send(embed=_notice("Card missing"),
                                           ephemeral=True)
        if match.get("member"):
            return await ctx.followup.send(
                embed=_notice("Member cards cannot fuse"),
                ephemeral=True)
        ceiling = cardlib.tier_max_stars(match)
        if from_star >= ceiling:
            return await ctx.followup.send(
                embed=_notice(f"**{match['name']}** is maxed"),
                ephemeral=True)
        room = ceiling - from_star
        if stars > room:
            return await ctx.followup.send(
                embed=_notice(f"Only **{room}** star"
                              f"{'' if room == 1 else 's'} left"),
                ephemeral=True)

        per_stars, per_pearls = cardlib.fusion_cost(match["tier"])
        entry = await asyncio.to_thread(cardlib.db_get_entry, ctx.author.id, slug)
        levels = (entry or {}).get("levels", {})
        if not levels.get(from_star):
            return await ctx.followup.send(
                embed=_notice(f"You lack **{_star_name(match, from_star)}**"),
                ephemeral=True)

        played = cardlib.simulate_fusion(levels, from_star, stars, per_stars)
        if played is None:
            spare = sum(levels.values()) - 1
            return await ctx.followup.send(
                embed=_notice(f"Need **{per_stars * stars}** other copies\n"
                              f"You have {spare}"),
                ephemeral=True)

        pearls = per_pearls * stars
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        if wallet["pearls"] < pearls:
            return await ctx.followup.send(
                embed=_notice(f"Need **{pearls:,}** Pearls\n"
                              f"You have {wallet['pearls']:,}"),
                ephemeral=True)

        starred = {st: n for st, n in played["spent"].items() if st > 0}
        if starred:
            lost = ", ".join(f"{n}× {_star_name(match, st)}"
                             for st, n in sorted(starred.items()))
            view = FuseView(ctx.author.id, match, from_star, stars,
                            played["spent"])
            view.message = await ctx.followup.send(
                embed=_notice(f"Fusing **{match['name']}** spends {lost}\n"
                              "Their stars are lost"),
                view=view, wait=True)
            return

        embed, file = await _do_fuse(ctx.author.id, match, from_star, stars,
                                     played["spent"])
        if embed is None:
            return await ctx.followup.send(
                embed=_notice("Copies changed\nTry again"),
                ephemeral=True)
        await ctx.followup.send(embed=embed, file=file)

    @card.command(
        name="discard",
        description="Discard plain dupes into lower-tier rolls")
    async def card_discard(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="Plain copies",
                             autocomplete=_autocomplete_discardable),
        count: discord.Option(
            int, description="Amount",
            required=False, default=1, min_value=1),
    ):
        await ctx.defer()
        picked = _parse_stack(card)
        if picked is None:
            return await ctx.followup.send(
                embed=_notice("Pick from the list"),
                ephemeral=True)
        slug, star = picked
        if cardlib.is_member_slug(slug):
            return await ctx.followup.send(
                embed=_notice("Member cards cannot discard"),
                ephemeral=True)
        match = cardlib.get_card(slug)
        if match is None:
            return await ctx.followup.send(embed=_notice("Card missing"),
                                           ephemeral=True)
        if star != 0:
            return await ctx.followup.send(
                embed=_notice(
                    f"Only Plain copies\n{_level_label(match, star)} is fused"),
                ephemeral=True)
        yld = cardlib.discard_yield(match)
        if yld is None:
            return await ctx.followup.send(
                embed=_notice("Commons cannot discard"), ephemeral=True)
        below, per = yld

        entry = await asyncio.to_thread(cardlib.db_get_entry, ctx.author.id, slug)
        have = (entry or {}).get("levels", {}).get(0, 0)
        if have < 1:
            return await ctx.followup.send(
                embed=_notice(f"No plain **{match['name']}**"),
                ephemeral=True)
        if count > have:
            return await ctx.followup.send(
                embed=_notice(f"Only **{have}** Plain "
                              f"cop{'y' if have == 1 else 'ies'}"),
                ephemeral=True)

        total = count * per
        if match["tier"] in cardlib.DISCARD_CONFIRM_TIERS:
            view = DiscardView(ctx.author.id, match, count)
            view.message = await ctx.followup.send(
                embed=_notice(
                    f"Discard **{count}× {match['name']}**\n"
                    f"Get **{total}** {_tier_label(below)}"
                    f"{'' if total == 1 else 's'}"),
                view=view, wait=True)
            return

        embed, file, _ = await _do_discard(ctx.author.id, match, count)
        if embed is None:
            return await ctx.followup.send(
                embed=_notice("Copies changed"),
                ephemeral=True)
        await ctx.followup.send(embed=embed, file=file)
        await _announce_and_reward(ctx.channel, ctx.author, None)

    @card.command(
        name="merge",
        description="Merge two plain cards of a rarity into a new roll")
    async def card_merge(
        self, ctx: discord.ApplicationContext,
        first: discord.Option(str, description="First card",
                              autocomplete=_autocomplete_mergeable),
        second: discord.Option(str, description="Second card",
                               autocomplete=_autocomplete_mergeable),
    ):
        await ctx.defer()
        picks = [_parse_stack(first), _parse_stack(second)]
        if None in picks:
            return await ctx.followup.send(embed=_notice("Pick both cards"),
                                           ephemeral=True)
        if any(star != 0 for _, star in picks):
            return await ctx.followup.send(embed=_notice("Only Plain copies"),
                                           ephemeral=True)
        slugs = [slug for slug, _ in picks]

        cards = []
        for slug in slugs:
            found = cardlib.get_card(slug) or await asyncio.to_thread(
                cardlib.db_get_member_card, slug)
            if found is None:
                return await ctx.followup.send(embed=_notice("Card missing"),
                                               ephemeral=True)
            cards.append(found)
        if _tier_of(cards[0]) != _tier_of(cards[1]):
            return await ctx.followup.send(
                embed=_notice("Pick two cards of one rarity"), ephemeral=True)

        owned = await asyncio.to_thread(cardlib.db_get_collection, ctx.author.id)
        for card in cards:
            have = owned.get(card["slug"], {}).get("levels", {}).get(0, 0)
            if have < slugs.count(card["slug"]):
                return await ctx.followup.send(
                    embed=_notice(f"Not enough Plain **{card['name']}**"),
                    ephemeral=True)

        if cards[0].get("member"):
            if cards[0]["slug"] == cards[1]["slug"]:
                return await ctx.followup.send(
                    embed=_notice("Pick two different Limited cards"),
                    ephemeral=True)
            pool = await asyncio.to_thread(cardlib.db_count_unminted)
            if pool == 0:
                return await ctx.followup.send(
                    embed=_notice("No unminted Limited cards left"),
                    ephemeral=True)
            chance = pool / (pool + cardlib.MERGE_INPUTS)
            view = LimitedMergeView(ctx.author.id, cards)
            view.message = await ctx.followup.send(
                embed=_notice(
                    f"Merge **{cards[0]['name']}** and **{cards[1]['name']}**\n"
                    f"**{chance:.0%}** chance of a new Limited card\n"
                    "Otherwise one of them comes back and the other is lost"),
                view=view, wait=True)
            return

        embed, file = await _do_merge(ctx.author.id, cards)
        if embed is None:
            return await ctx.followup.send(
                embed=_notice("Merge changed\nTry again"), ephemeral=True)
        await ctx.followup.send(embed=embed, file=file)
        await _announce_and_reward(ctx.channel, ctx.author, None)

    @card.command(name="trade", description="Trade cards with other collectors")
    async def card_trade(
        self, ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Player"),
        give: discord.Option(str, description="You give",
                             autocomplete=_autocomplete_stacks),
        want: discord.Option(str, description="You get",
                             autocomplete=_autocomplete_their_stacks),
    ):
        await ctx.defer()
        if member.id == ctx.author.id:
            return await ctx.followup.send(embed=_notice("Pick someone else"),
                                           ephemeral=True)
        if member.bot:
            return await ctx.followup.send(embed=_notice("Bots have no tank"),
                                           ephemeral=True)

        mine_pick, theirs_pick = _parse_stack(give), _parse_stack(want)
        if mine_pick is None or theirs_pick is None:
            return await ctx.followup.send(
                embed=_notice("Pick both stacks"),
                ephemeral=True)
        give_slug, give_star = mine_pick
        want_slug, want_star = theirs_pick

        give_card = cardlib.get_card(give_slug) or await asyncio.to_thread(
            cardlib.db_get_member_card, give_slug)
        want_card = cardlib.get_card(want_slug) or await asyncio.to_thread(
            cardlib.db_get_member_card, want_slug)
        if give_card is None or want_card is None:
            return await ctx.followup.send(embed=_notice("Card missing"),
                                           ephemeral=True)

        mine = await asyncio.to_thread(cardlib.db_get_entry, ctx.author.id,
                                       give_slug)
        theirs = await asyncio.to_thread(cardlib.db_get_entry, member.id,
                                         want_slug)
        if not mine or not mine["levels"].get(give_star):
            return await ctx.followup.send(
                embed=_notice(f"You lack **{_star_name(give_card, give_star)}**"),
                ephemeral=True)
        if not theirs or not theirs["levels"].get(want_star):
            return await ctx.followup.send(
                embed=_notice(f"{member.display_name} lacks "
                              f"**{_star_name(want_card, want_star)}**"),
                ephemeral=True)

        embed = discord.Embed(
            title="Trade",
            description=(
                f"{ctx.author.mention}: **{_star_name(give_card, give_star)}**\n"
                f"{member.mention}: **{_star_name(want_card, want_star)}**"),
            color=ctext.ACCENT)
        embed.set_footer(
            text="Recipient only · 5 min")

        view = TradeView(ctx.author, member, give_card, give_star,
                         want_card, want_star)
        view.message = await ctx.followup.send(
            content=member.mention, embed=embed, view=view, wait=True)

    # ── /tank admin ──────────────────────────────────────────────────────────

    @admin.command(name="set-channel",
                   description="Set card channel")
    @commands.has_permissions(administrator=True)
    async def tank_set_channel(
        self, ctx: discord.ApplicationContext,
        channel: discord.Option(
            discord.TextChannel,
            description="Channel",
            required=False, default=None),
    ):
        await ctx.defer(ephemeral=True)
        await asyncio.to_thread(cardlib.db_set_card_channel, ctx.guild.id,
                                channel.id if channel else None)
        if channel is None:
            return await ctx.followup.send(
                embed=_notice("Card channel cleared"))
        await ctx.followup.send(
            embed=_notice(f"Card channel: {channel.mention}"))

    @card.command(name="view",
                  description="Preview a card")
    async def card_view(
        self, ctx: discord.ApplicationContext,
        name: discord.Option(str, description="Card",
                             autocomplete=_autocomplete_pool),
    ):
        await ctx.defer()
        wanted = name.strip().lower()
        entries = await asyncio.to_thread(cardlib.db_get_pool)
        member = next((p for p in entries if p["name"].lower() == wanted), None)
        card = member or _find_card(name)
        if card is None:
            return await ctx.followup.send(
                embed=_notice(f"No drop named **{name.strip()}**"),
                ephemeral=True)

        entry = await asyncio.to_thread(cardlib.db_get_entry, ctx.author.id,
                                        card["slug"])
        stars = entry["best"] if entry else 0
        file = await asyncio.to_thread(card_file, card, None, stars,
                                       cardlib.tier_max_stars(card))

        embed = discord.Embed(color=_card_color(card),
                              description=_card_description(card))
        embed.set_image(url=f"attachment://{file.filename}")

        if member:
            if card["retired"]:
                state = "Retired"
            elif card["minted"]:
                state = f"Held by <@{card['owner']}>"
            else:
                state = "Unminted"
            embed.add_field(name="Status", value=state, inline=False)

        owners = await asyncio.to_thread(cardlib.db_get_owners, card["slug"])
        embed.set_footer(text=_credit(
            card,
            f"You own {entry['total']}" if entry else "Not owned",
            ctext.count(len(owners), "owner")))
        await ctx.followup.send(embed=embed, file=file)

    @card.command(name="owners", description="View who holds a card")
    async def card_owners(
        self, ctx: discord.ApplicationContext,
        name: discord.Option(str, description="Card",
                             autocomplete=_autocomplete_pool),
    ):
        await ctx.defer()
        # Same lookup as /card view, so an unminted member answers "nobody"
        # rather than "no such card".
        wanted = name.strip().lower()
        entries = await asyncio.to_thread(cardlib.db_get_pool)
        card = (next((p for p in entries if p["name"].lower() == wanted), None)
                or _find_card(name))
        if card is None:
            return await ctx.followup.send(
                embed=_notice(f"No drop named **{name.strip()}**"),
                ephemeral=True)

        owners = await asyncio.to_thread(cardlib.db_get_owners, card["slug"])
        if not owners:
            return await ctx.followup.send(
                embed=_notice(f"Nobody holds **{card['name']}** yet"),
                ephemeral=True)

        lines = []
        for i, o in enumerate(owners[:OWNERS_SHOWN], 1):
            member = ctx.guild.get_member(o["user"]) if ctx.guild else None
            who = member.display_name if member else f"<@{o['user']}>"
            best = _level_label(card, o["best"])
            copies = ctext.count(o["copies"], "copy", "copies")
            lines.append(f"`{i:2}` **{who}**: {copies}"
                         + (f" · Best {best}" if best else ""))
        if len(owners) > OWNERS_SHOWN:
            lines.append(f"-# +{len(owners) - OWNERS_SHOWN} more")

        total = sum(o["copies"] for o in owners)
        embed = discord.Embed(
            title=f"{card['name']} · {ctext.count(len(owners), 'owner')}",
            description="\n".join(lines),
            color=_card_color(card))
        embed.set_footer(text=_credit(
            card, _tier_label(_tier_of(card)),
            f"{total} in circulation"))
        await ctx.followup.send(embed=embed)

    @card.command(name="list", description="View all cards")
    async def card_list(
        self, ctx: discord.ApplicationContext,
        tier: discord.Option(
            str, description="Tier",
            required=False, default=None, choices=TIER_CHOICES),
        card_set: discord.Option(
            str, name="set", description="Set",
            required=False, default=None, choices=_set_choices()),
        show: discord.Option(
            str, description="Which cards",
            required=False, default="all",
            choices=[discord.OptionChoice("All", "all"),
                     discord.OptionChoice("Missing", "missing"),
                     discord.OptionChoice("Owned", "owned")]),
    ):
        await ctx.defer()
        entries = await asyncio.to_thread(_pool_entries, tier, card_set)
        owned = await asyncio.to_thread(cardlib.db_get_collection, ctx.author.id)
        if show == "missing":
            entries = [e for e in entries if e["slug"] not in owned]
        elif show == "owned":
            entries = [e for e in entries if e["slug"] in owned]
        if not entries:
            return await ctx.followup.send(embed=_notice("Nothing here"),
                                           ephemeral=True)

        have = sum(1 for e in entries if e["slug"] in owned)
        members = sum(1 for e in entries if e.get("member"))
        header = (f"**{len(entries)}** Cards · You own {have}"
                  + (f"\n{ctext.count(members, ctext.LIMITED + ' card')}"
                     if members else "")
                  + "\nRarest first")

        page_list = []
        for i in range(0, len(entries), POOL_PER_PAGE):
            lines = []
            for e in entries[i:i + POOL_PER_PAGE]:
                mark = " ✓" if e["slug"] in owned else ""
                if e.get("member"):
                    if e["retired"]:
                        tail = "Retired"
                    elif e["minted"]:
                        tail = f"Held by <@{e['owner']}>"
                    else:
                        tail = "Unminted"
                    lines.append(f"`{ctext.LIMITED:9}` **{e['name']}**{mark} · {tail}")
                else:
                    lines.append(f"`{_tier_label(e['tier']):9}` "
                                 f"**{e['name']}**{mark}")
            embed = discord.Embed(
                title="Pool",
                description=header + "\n\n" + "\n".join(lines),
                color=_card_color(entries[i]))
            page_list.append(pages.Page(embeds=[embed]))

        if len(page_list) == 1:
            return await ctx.followup.send(embed=page_list[0].embeds[0])
        paginator = pages.Paginator(pages=page_list)
        add_paginator_buttons(paginator)
        await paginator.respond(ctx.interaction)

    @card.command(name="rates", description="View the odds")
    async def card_rates(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        counts = cardlib.tier_counts()
        entries = await asyncio.to_thread(cardlib.db_get_pool)
        unminted = sum(1 for p in entries if not p["minted"])

        rows = []
        for tier in cardlib.TIER_ORDER:
            if tier == "member":
                weight, pool = cardlib.MEMBER_CHANCE, unminted
            else:
                weight, pool = cardlib.TIER_WEIGHTS[tier], counts.get(tier, 0)
            # Odds of one specific card: the tier has to land, then that card
            # has to be the pick inside it.
            one = f"1 in {round(pool / (weight / 100)):,}" if pool else "-"
            rows.append(f"{_tier_label(tier):<11}{weight:>8.2f}%{pool:>7}{one:>15}")

        table = ("```\n"
                 + f"{'Tier':<11}{'Rate':>9}{'Cards':>7}{'Specific Card':>15}\n"
                 + "\n".join(rows) + "\n```")

        per_day = cardlib.REELS_PER_WINDOW * (24 * 3600 // cardlib.WINDOW_SECONDS)
        pct = int(cardlib.WISH_REDIRECT_CHANCE * 100)
        embed = discord.Embed(
            title="Rates",
            description=table,
            color=ctext.ACCENT)
        embed.add_field(
            name=f"Expected rates with {per_day} Reels a day:",
            value=("Legendary weekly\n"
                   "Fabled monthly\n"
                   "Mythic bi-monthly\n"
                   f"{ctext.LIMITED} every 4 months"),
            inline=False)
        embed.add_field(
            name="Wishlist",
            value=(f"{pct}% chance to get a wishlisted card in a given tier\n"
                   f"{ctext.LIMITED} cards cannot be wished for"),
            inline=False)
        embed.set_footer(
            text=f"{unminted}/{len(entries)} {ctext.LIMITED} cards available")
        await ctx.followup.send(embed=embed)

    @wish.command(
        name="add",
        description="Target a card")
    async def wish_add(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="Card",
                             autocomplete=_autocomplete_wishable),
    ):
        await ctx.defer(ephemeral=True)
        match = await asyncio.to_thread(_resolve, card)
        if match is None:
            return await ctx.followup.send(
                embed=_notice(f"No card named **{card}**"))
        if not cardlib.is_wishable(match):
            return await ctx.followup.send(
                embed=_notice("Member cards cannot be wished"))

        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        limit = cardlib.wish_slots(wallet["tank_tier"])
        outcome = await asyncio.to_thread(cardlib.db_add_wish, ctx.author.id,
                                          match["slug"], limit)
        if outcome == "duplicate":
            return await ctx.followup.send(embed=_notice(
                f"**{match['name']}** is already on your wishlist"))
        if outcome == "full":
            return await ctx.followup.send(embed=_notice(
                "Wish slot already used" if limit == 1
                else f"All {limit} wish slots already used"))
        pct = int(cardlib.WISH_REDIRECT_CHANCE * 100)
        used = len(await asyncio.to_thread(cardlib.db_get_wishes, ctx.author.id))
        await ctx.followup.send(embed=_notice(
            f"Wishing for **{match['name']}**. When you obtain a "
            f"{_tier_label(match['tier'])} there is a {pct}% chance "
            f"it will pull from your wishlist directly. {used}/{limit} "
            f"{ctext.plural(limit, 'slot')} used"))

    @wish.command(name="remove", description="Stop targeting a card")
    async def wish_remove(
        self, ctx: discord.ApplicationContext,
        card: discord.Option(str, description="Card",
                             autocomplete=_autocomplete_wishable),
    ):
        await ctx.defer(ephemeral=True)
        match = await asyncio.to_thread(_resolve, card)
        if match is None:
            return await ctx.followup.send(
                embed=_notice(f"No card named **{card}**"))
        ok = await asyncio.to_thread(cardlib.db_remove_wish, ctx.author.id,
                                     match["slug"])
        await ctx.followup.send(embed=_notice(
            f"Removed **{match['name']}**" if ok
            else f"**{match['name']}** not wished"))

    @wish.command(name="list", description="View your wishlist")
    async def wish_list(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        wishes = await asyncio.to_thread(cardlib.db_get_wishes, ctx.author.id)
        wallet = await asyncio.to_thread(cardlib.db_get_wallet, ctx.author.id)
        limit = cardlib.wish_slots(wallet["tank_tier"])
        if not wishes:
            return await ctx.followup.send(embed=_notice(
                f"No wishes. {ctext.count(limit, 'slot')} open"))
        lines = []
        for slug in wishes:
            c = cardlib.get_card(slug)
            if c:
                lines.append(f"`{_tier_label(c['tier']):9}` {c['name']}")
        pct = int(cardlib.WISH_REDIRECT_CHANCE * 100)
        embed = discord.Embed(
            title=f"Wishes {len(wishes)}/{limit}",
            description="\n".join(lines),
            color=ctext.ACCENT)
        embed.set_footer(
            text=f"{pct}% chance to get a wishlisted card in a given tier")
        await ctx.followup.send(embed=embed)


def setup(client):
    client.add_cog(Cards(client))
