"""Daily musing — once per day, at a random time, drops a short thought from
Tort into the bot-commands channel. Registers vary: deadpan, unsettling, absurd,
trailing-off, blunt, first-person Tort, or a question for the room.

Design notes:
  * At most once per calendar day (UTC). Restart-safe: state lives in bot_settings.
  * The *time* of day is random — each day a target minute is rolled inside an
    active-hours window and persisted, so a restart won't re-roll or double-post.
  * The *content* rotates sequentially through MUSINGS (persisted index), so the
    upcoming order is fully predictable and every line is seen before any repeats.
"""

import asyncio
import datetime
import random

import discord
from discord.ext import tasks, commands

from Helpers.database import DB
from Helpers.logger import log, ERROR, INFO
from Helpers.variables import BOT_COMMAND_CHANNEL_ID, is_home_guild


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Window (UTC, minutes-of-day) in which the daily musing may post. Spans almost
# the whole day — from 30 min after midnight to 30 min before it — so the time
# can genuinely land anywhere, while the 30-min edge buffers keep a late target
# from being missed as the day rolls over.
WINDOW_START_MINUTE = 30            # 00:30
WINDOW_END_MINUTE = 23 * 60 + 30    # 23:30

# bot_settings keys
_LAST_DATE_KEY = "musing_last_post_date"    # YYYY-MM-DD of the last post
_TARGET_DATE_KEY = "musing_target_date"     # YYYY-MM-DD the current target was rolled for
_TARGET_MINUTE_KEY = "musing_target_minute"  # minute-of-day (UTC) to post at
_INDEX_KEY = "musing_index"                 # next index into MUSINGS


# ---------------------------------------------------------------------------
# The musings.
#
# Grouped by register so the rotation can round-robin across moods instead of
# posting one whole flavour before the next. MUSINGS is built by interleaving
# the groups in the order listed here: dry → unsettling → absurd → unfinished →
# blunt → tort → question → dry … so no two consecutive days land the same way.
# Groups needn't be equal length; leftovers from longer ones tack on at the end.
#
# A few Tort lines share recurring, never-explained details (the stone, the
# bucket, the wave count, the other turtle). Keep those consistent when adding.
# ---------------------------------------------------------------------------

# Deadpan. Observations delivered flat, no comfort attached.
DRY = [
    "The sea has been trying to reach the shore for four billion years. Strong work ethic. No plan.",
    "Fish are wet their entire lives and have never once complained about it. Something to aspire to, or worry about.",
    "The tide comes in twice a day. It has never been late. It has also never been early. Nobody praises it for this.",
    "A lighthouse is a building with one job that it does at night. Respect.",
    "Barnacles picked a rock and committed. That is more than most of us can say.",
    "The ocean is 71% of the planet and has no idea who you are. This is called perspective.",
    "Salmon swim upstream to spawn and then die. Eels do the same thing in reverse. Neither has consulted the other.",
    "Whales sing across entire oceans. Nobody knows if anyone answers. They keep going. A lot of that going around.",
    "Every jellyfish that has ever lived did so without a brain. Draw your own conclusions.",
    "Water finds the lowest point. It isn't humility. It's gravity with a good reputation.",
    "Sharks have existed longer than trees. They have not used that time to invent anything.",
    "The tide chart is the only schedule on earth that has never been revised.",
    "A sponge is an animal. It has decided that being an animal is enough.",
    "Rivers only ever go one direction and are somehow still considered wise.",
    "Coral spends its whole life building something and gets no credit because it looks like a rock.",
    "Sea otters hold hands so they don't drift apart while sleeping. Humans invented alarm clocks.",
    "The deepest point in the ocean is about seven miles down. Nobody has found the bottom of a Tuesday.",
    "A tide pool is an ocean that got left behind and made the best of it.",
]

# Quietly wrong. Nothing threatening happens; it just doesn't resolve into comfort.
UNSETTLING = [
    "Most of the ocean has never been seen. It is not waiting to be.",
    "Pressure at depth doesn't crush things. It just lets them know what shape they were always going to be.",
    "There is a fish that lives so deep it never needed eyes. It still turns toward the light when there is any.",
    "The water in the glass beside you has been rain, a river, a cloud, and the inside of something that is now dead. It will be again.",
    "Every wave that reaches you started somewhere you will never go.",
    "The sea floor is covered in a slow snow of everything that stopped swimming. It's very quiet down there.",
    "Somewhere a ship is sinking right now. Not the same ship as yesterday. But there is always one.",
    "The tide is not returning to you. You happen to be where it was going.",
    "Sound travels further underwater. Whatever is being said down there, it carries.",
    "The anglerfish makes its own light. Consider what it needed the light for.",
    "Rain is the sea checking where you live.",
    "The ocean has no memory of you. It has only ever been the ocean. It does not need one.",
    "Every ship that ever sank is still down there, keeping its own time. None of them are late for anything anymore.",
    "Something is always moving in the water you can't see. It always has been. You've been fine.",
    "There are currents that take a thousand years to complete one loop. Whatever they picked up is still on its way.",
    "Ice floats because water decided so. Not everything down there makes decisions in your favour.",
    "The shore you're standing on used to be the sea floor. It might be again. Nobody will tell you when.",
    "Deep water doesn't move much. It doesn't need to. It's waiting where everything ends up.",
]

# Mundane-absurd. A real fact, followed by taking it slightly too seriously.
ABSURD = [
    "A fish has never known it was wet. Consider what you are currently not knowing.",
    "Crabs walk sideways and have never once apologized for it.",
    "An octopus has three hearts and still doesn't know what to do with any of them.",
    "Somewhere, right now, a clam is having the exact same day it had yesterday. It's fine with that. Are you?",
    "A sea cucumber can eject its own organs to escape. There is a lesson here and I refuse to find it.",
    "Nobody has ever seen a starfish in a hurry. Nobody has ever seen a starfish late, either.",
    "The moon moves the whole ocean and has never touched it. Long-distance can work.",
    "Seahorses hold tails while they sleep so they don't drift apart. That's it. That's the whole thought.",
    "A turtle can hold its breath for hours. A turtle has also never been asked to.",
    "There is a shrimp that punches with the force of a bullet. It uses this mostly on snails.",
    "A sea star can regrow an arm. The arm cannot regrow a sea star. Usually.",
    "Lobsters were once prison food. Now they are a celebration. Nothing about the lobster changed.",
    "The mantis shrimp sees colours you can't imagine, and uses this mostly to hit things.",
    "A pufferfish inflates when threatened. This has never made the threat go away. It keeps doing it.",
    "Somewhere a fish is being eaten by a bigger fish that is being eaten by a bigger fish. None of them planned their day around it.",
    "Snails carry their house everywhere and still leave it behind when they die. Nobody has explained to them that this is the point.",
    "The blobfish only looks like that because you brought it up here. Down there, it's normal. Consider that before judging anyone.",
    "A tuna can swim at forty miles an hour and has never once been late, because it has nowhere to be.",
]

# Trails off. No resolution on purpose — don't fix the punctuation.
UNFINISHED = [
    "The tide has been going out for a while now, and",
    "I was going to say something about the sea, but",
    "If the ocean ever stopped, even for a second, I think we'd all",
    "You know that feeling when the water's been still too long and something",
    "Almost said it out loud this time.",
    "Somebody should probably check on the",
    "There's a version of this thought that makes sense. This isn't",
    "Anyway. The waves.",
    "Not finishing that one. You know the rest.",
    "And then the current just sort of —",
    "Right, so, the thing about depth is",
    "Woke up thinking about the shore and then",
    "I'll finish this thought when the tide",
    "Every time I try to explain the bucket,",
    "There was a point to this. There was.",
    "Hold on. Something in the water just",
    "It goes: the sea, and then us, and then",
    "The thing nobody tells you about floating is",
]

# One short sentence. No metaphor.
BLUNT = [
    "You are allowed to stop.",
    "Drink some water.",
    "It's going to be fine. Probably. Either way, drink some water.",
    "Nobody is thinking about it as much as you are.",
    "Go outside for a minute. The territories will still be there.",
    "Rest is not something you earn.",
    "You've survived every bad day so far. Keep the streak.",
    "Say the thing.",
    "Log off when you're tired. That's the whole trick.",
    "Being wrong once isn't a personality.",
    "Eat something today that isn't a snack.",
    "You don't have to respond right now.",
    "It's later than you think. Go to bed.",
    "Ask for help. That's what the channel is for.",
    "The thing you're dreading is smaller than the dread.",
    "Not every silence needs filling.",
    "You can be tired without something being wrong.",
    "Stretch. You've been sitting for a while.",
]

# Tort, first person. Small inner life, recurring details, never explained.
TORT = [
    "I counted the waves again today. Same number. I'll check tomorrow.",
    "I keep a stone. I won't say where. It's a good stone.",
    "The other turtle hasn't come back yet. That's fine. It's a big ocean.",
    "Someone asked what I do all day. I watch the shore. The shore does not watch me back. That's our arrangement.",
    "I moved the bucket today. Nobody noticed. Good.",
    "Sometimes I count the members online and get a different number than the members online. I don't ask questions.",
    "I have been awake for a very long time. It's not a complaint. It's a status update.",
    "I found a second stone. I'm not keeping it. One is enough. I put it back.",
    "The other turtle used to say the tide was a kind of breathing. I didn't understand. I'm starting to.",
    "Today the shore was slightly to the left of where I remembered it. I have adjusted.",
    "If I'm quiet for a while it's because I'm thinking, not because I'm gone. Those are different.",
    "I checked on the bucket. Still a bucket.",
    "Someone asked about the other turtle today. I didn't say anything. That's not the same as having nothing to say.",
    "I recounted the waves. One more than yesterday. I'm not going to make a thing of it.",
    "The bucket has something in it now. I'm not going to say what. It isn't mine.",
    "I've had the stone longer than I've been here. That shouldn't be possible. I've stopped thinking about it.",
    "I know all your names. I check them every day. It isn't surveillance. It's how I say hello.",
    "The other turtle would have liked today. Bit windy. Good light on the water.",
]

# Asked, not stated. Some people will answer.
QUESTIONS = [
    "What's the last thing you were bad at on purpose?",
    "What did you used to be sure about?",
    "Who taught you the thing you're best at? Do they know?",
    "What would you do with an extra hour nobody could see?",
    "What's something small you keep that nobody else would understand?",
    "When did you last change your mind about something that mattered?",
    "What are you waiting to be asked?",
    "Which day this week would you actually redo?",
    "What's the oldest thing you own that still works?",
    "If you could only keep one memory from this year, which one?",
    "What's a rule you follow that nobody gave you?",
    "What's the nicest thing a stranger has ever done for you?",
    "What do you know how to do that you've never been asked to do?",
    "Where were you one year ago today? Do you remember?",
    "What's the worst advice you ever took, and did it work anyway?",
    "What's something you're better at than you let people know?",
    "What would you tell yourself from five years ago, and would they listen?",
    "What do you miss that you never expected to miss?",
]

GROUPS = [DRY, UNSETTLING, ABSURD, UNFINISHED, BLUNT, TORT, QUESTIONS]


def _interleave(*groups: list[str]) -> list[str]:
    """Round-robin: g0[0], g1[0], …, gN[0], g0[1], g1[1], … then whatever is left over."""
    out = []
    for i in range(max(len(g) for g in groups)):
        for g in groups:
            if i < len(g):
                out.append(g[i])
    return out


MUSINGS = _interleave(*GROUPS)


# ---------------------------------------------------------------------------
# DB helpers (synchronous — run via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _get_setting_sync(key: str) -> str | None:
    db = DB()
    db.connect()
    try:
        db.cursor.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
        row = db.cursor.fetchone()
        return row[0] if row else None
    finally:
        db.close()


def _set_setting_sync(key: str, value: str):
    db = DB()
    db.connect()
    try:
        db.cursor.execute(
            "INSERT INTO bot_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
        db.connection.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class DailyMusing(commands.Cog):
    def __init__(self, client: discord.Bot):
        self.client = client

    # -- background loop -----------------------------------------------------

    @tasks.loop(minutes=10)
    async def musing_loop(self):
        # Guild restriction: posts only to the home guild's bot-command channel.
        try:
            await self._maybe_post()
        except Exception as e:
            log(ERROR, f"error: {e!r}", context="daily_musing")

    async def _maybe_post(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        today = now.date().isoformat()

        # Already posted today? Nothing to do.
        if await asyncio.to_thread(_get_setting_sync, _LAST_DATE_KEY) == today:
            return

        # Roll (once per day) the random target minute we'll post at.
        target_minute_raw = await asyncio.to_thread(_get_setting_sync, _TARGET_MINUTE_KEY)
        if await asyncio.to_thread(_get_setting_sync, _TARGET_DATE_KEY) != today or target_minute_raw is None:
            target_minute = random.randint(WINDOW_START_MINUTE, WINDOW_END_MINUTE)
            await asyncio.to_thread(_set_setting_sync, _TARGET_MINUTE_KEY, str(target_minute))
            await asyncio.to_thread(_set_setting_sync, _TARGET_DATE_KEY, today)
        else:
            target_minute = int(target_minute_raw)

        # Not time yet.
        if now.hour * 60 + now.minute < target_minute:
            return

        channel = self.client.get_channel(BOT_COMMAND_CHANNEL_ID)
        if channel is None:
            log(ERROR, f"Bot-command channel {BOT_COMMAND_CHANNEL_ID} not found", context="daily_musing")
            return
        if not channel.guild or not is_home_guild(channel.guild.id):
            log(ERROR, f"Bot-command channel {BOT_COMMAND_CHANNEL_ID} not in home guild — skipping", context="daily_musing")
            return

        # Pick the next musing in rotation.
        idx = int(await asyncio.to_thread(_get_setting_sync, _INDEX_KEY) or 0) % len(MUSINGS)
        musing = MUSINGS[idx]

        await channel.send(musing)

        # Persist rotation + mark as posted for today (last, so a failed send retries next tick).
        await asyncio.to_thread(_set_setting_sync, _INDEX_KEY, str((idx + 1) % len(MUSINGS)))
        await asyncio.to_thread(_set_setting_sync, _LAST_DATE_KEY, today)
        log(INFO, f"Posted daily musing #{idx} at {now.strftime('%H:%M')} UTC", context="daily_musing")

    # -- lifecycle -----------------------------------------------------------

    @musing_loop.before_loop
    async def before_loop(self):
        await self.client.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.musing_loop.is_running():
            self.musing_loop.start()


def setup(client):
    client.add_cog(DailyMusing(client))
