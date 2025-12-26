"""
/top_wars command - Reward top warrers with shells

Logic:
1. Look at NON-Hydra/Narwhal/Dolphin members first
2. Filter those who meet min_wars threshold
3. Fill top 5 slots with qualifying non-leadership members
4. If slots remain, fill with Hydra/Narwhal/Dolphin members (no min requirement)
5. Display results and confirm before awarding shells
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any

import discord
from discord.ext import commands
from discord.commands import slash_command

from Helpers.database import DB, get_current_guild_data

# Leadership ranks that are deprioritized
LEADERSHIP_RANKS = {'Hydra', 'Narwhal', 'Dolphin'}

# Shells awarded to each top warrer
SHELLS_REWARD = 15

# Number of top warrers to reward
TOP_N = 5


def get_wars_in_range(start_date: datetime.date, end_date: datetime.date) -> Dict[str, Dict[str, Any]]:
    """
    Get war counts for all players between start_date and end_date.

    Returns dict mapping uuid -> {name, discord_rank, wars_delta, discord_id}
    """
    db = DB()
    db.connect()

    try:
        # Get current guild data for names
        current_data = get_current_guild_data()
        if not current_data or not current_data.get('members'):
            return {}

        current_members = current_data.get('members', [])
        uuid_to_name = {m['uuid']: m.get('name') or m.get('username') or 'Unknown' for m in current_members}
        current_uuids = set(uuid_to_name.keys())

        # Get discord ranks and discord_ids for all members
        db.cursor.execute("SELECT uuid, rank, discord_id FROM discord_links")
        discord_data = {str(row[0]): {'rank': row[1], 'discord_id': row[2]} for row in db.cursor.fetchall()}

        # Get wars at start of period (or closest date after if not available)
        db.cursor.execute("""
            SELECT DISTINCT snapshot_date FROM player_activity
            WHERE snapshot_date <= %s
            ORDER BY snapshot_date DESC
            LIMIT 1
        """, (start_date,))
        start_row = db.cursor.fetchone()

        if not start_row:
            # No data before start_date, try to find earliest available
            db.cursor.execute("""
                SELECT DISTINCT snapshot_date FROM player_activity
                ORDER BY snapshot_date ASC
                LIMIT 1
            """)
            start_row = db.cursor.fetchone()

        if not start_row:
            return {}

        actual_start = start_row[0]

        # Get wars at end of period (or closest date before if not available)
        db.cursor.execute("""
            SELECT DISTINCT snapshot_date FROM player_activity
            WHERE snapshot_date <= %s
            ORDER BY snapshot_date DESC
            LIMIT 1
        """, (end_date,))
        end_row = db.cursor.fetchone()

        if not end_row:
            return {}

        actual_end = end_row[0]

        # Get wars at start date for all players
        db.cursor.execute("""
            SELECT uuid, wars FROM player_activity
            WHERE snapshot_date = %s
        """, (actual_start,))
        start_wars = {str(row[0]): row[1] or 0 for row in db.cursor.fetchall()}

        # Get wars at end date for all players
        db.cursor.execute("""
            SELECT uuid, wars FROM player_activity
            WHERE snapshot_date = %s
        """, (actual_end,))
        end_wars = {str(row[0]): row[1] or 0 for row in db.cursor.fetchall()}

        # Calculate deltas for current guild members only
        result = {}
        for uuid in current_uuids:
            start_val = start_wars.get(uuid, 0)
            end_val = end_wars.get(uuid, 0)

            # If player doesn't have end data, skip them
            if uuid not in end_wars:
                continue

            delta = end_val - start_val
            if delta < 0:
                delta = 0  # Handle data resets

            discord_info = discord_data.get(uuid, {})

            result[uuid] = {
                'name': uuid_to_name.get(uuid, 'Unknown'),
                'discord_rank': discord_info.get('rank', 'Unknown'),
                'discord_id': discord_info.get('discord_id'),
                'wars_delta': delta,
                'uuid': uuid
            }

        return result

    finally:
        db.close()


def select_top_warrers(war_data: Dict[str, Dict[str, Any]], min_wars: int) -> List[Dict]:
    """
    Select top 5 warrers following the priority rules.

    Returns:
        List of up to 5 top warrers
    """
    # Separate into leadership and non-leadership
    non_leadership = []
    leadership = []

    for uuid, data in war_data.items():
        if data['discord_rank'] in LEADERSHIP_RANKS:
            leadership.append(data)
        else:
            non_leadership.append(data)

    # Sort both groups by wars descending
    non_leadership.sort(key=lambda x: x['wars_delta'], reverse=True)
    leadership.sort(key=lambda x: x['wars_delta'], reverse=True)

    # Select qualifying non-leadership (must meet min_wars)
    qualifying_non_leadership = [p for p in non_leadership if p['wars_delta'] >= min_wars]

    # Fill top 5: first with qualifying non-leadership
    selected = []
    for player in qualifying_non_leadership[:TOP_N]:
        player['from_leadership'] = False
        selected.append(player)

    # If we have remaining slots, fill with leadership (no min requirement)
    remaining_slots = TOP_N - len(selected)
    if remaining_slots > 0:
        for player in leadership[:remaining_slots]:
            player['from_leadership'] = True
            selected.append(player)

    return selected


class ConfirmView(discord.ui.View):
    """View with Confirm and Cancel buttons for shell awards."""

    def __init__(self, winners: List[Dict], invoker_id: int, start_date, end_date):
        super().__init__(timeout=300)  # 5 minute timeout
        self.winners = winners
        self.invoker_id = invoker_id
        self.start_date = start_date
        self.end_date = end_date
        self.confirmed = None

    @discord.ui.button(label="Confirm & Award Shells", style=discord.ButtonStyle.green, emoji="\U0001F41A")
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the command invoker can confirm.", ephemeral=True)
            return

        self.confirmed = True
        self.stop()

        # Award shells to winners
        db = DB()
        db.connect()

        try:
            awarded = []
            failed = []

            for winner in self.winners:
                discord_id = winner.get('discord_id')
                if not discord_id:
                    failed.append(f"{winner['name']} (no Discord link)")
                    continue

                # Check if user exists in shells table
                db.cursor.execute('SELECT balance FROM shells WHERE "user" = %s', (discord_id,))
                row = db.cursor.fetchone()

                if row:
                    # Update existing balance
                    db.cursor.execute(
                        'UPDATE shells SET balance = balance + %s WHERE "user" = %s',
                        (SHELLS_REWARD, discord_id)
                    )
                else:
                    # Insert new record
                    db.cursor.execute(
                        'INSERT INTO shells ("user", shells, balance, ign) VALUES (%s, 0, %s, %s)',
                        (discord_id, SHELLS_REWARD, winner['name'])
                    )

                awarded.append({'discord_id': discord_id, 'name': winner['name']})

            db.connection.commit()

            # Disable all buttons and update ephemeral message
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(content="Shells awarded!", embed=None, view=None)

            # Build pretty public embed
            embed = discord.Embed(
                title="Shell Payouts",
                description=f"**Top Warrers: {self.start_date} to {self.end_date}**\nThe top warrers of the past week receive their payout of **{SHELLS_REWARD} shells** each!",
                color=discord.Color.gold()
            )
            embed.set_footer(text="Reminder that the top 5 warrers of each week receive shells and if you meet thresholds you can receive cool name colors!")

            # Build winner list with separator
            winner_text = ""
            leadership_started = False
            for i, winner in enumerate(self.winners, 1):
                if winner.get('from_leadership') and not leadership_started:
                    if winner_text:
                        winner_text += "--\n"
                    leadership_started = True

                discord_id = winner.get('discord_id')
                mention = f"<@{discord_id}>" if discord_id else winner['discord_rank']
                winner_text += f"**{i}.** {mention} `{winner['name']}` - **{winner['wars_delta']}** wars\n"

            embed.add_field(name="Winners", value=winner_text, inline=False)

            # Add failed notice if any
            if failed:
                failed_text = "\n".join(f"- {name}" for name in failed)
                embed.add_field(name="Failed to award (no Discord link)", value=failed_text, inline=False)

            # Send public message
            await interaction.channel.send(embed=embed)

        except Exception as e:
            db.connection.rollback()
            await interaction.response.send_message(f"Error awarding shells: {e}", ephemeral=True)
        finally:
            db.close()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the command invoker can cancel.", ephemeral=True)
            return

        self.confirmed = False
        self.stop()

        # Disable all buttons
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(content="**Cancelled.** No shells were awarded.", embed=None, view=None)


class TopWars(commands.Cog):
    def __init__(self, client):
        self.client = client

    @slash_command(
        description="Display and reward top warrers for the week",
        default_member_permissions=discord.Permissions(administrator=True)
    )
    async def top_wars(
        self,
        ctx: discord.ApplicationContext,
        min_wars: discord.Option(
            int,
            description="Minimum wars required for non-leadership members to qualify",
            required=True,
            min_value=1
        ),
        start_date: discord.Option(
            str,
            description="Start date (YYYY-MM-DD). If empty, uses 7 days ago.",
            required=False,
            default=None
        )
    ):
        await ctx.defer()

        # Parse date range
        try:
            if start_date:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
            else:
                start = (datetime.now() - timedelta(days=7)).date()

            end = start + timedelta(days=7)
        except ValueError:
            await ctx.followup.send(
                "Invalid date format. Please use YYYY-MM-DD (e.g., 2025-12-12)",
                ephemeral=True
            )
            return

        # Get war data
        war_data = get_wars_in_range(start, end)

        if not war_data:
            await ctx.followup.send(
                f"No war data found for the period {start} to {end}.",
                ephemeral=True
            )
            return

        # Select top warrers
        winners = select_top_warrers(war_data, min_wars)

        if not winners:
            await ctx.followup.send(
                f"No qualifying warrers found for the period {start} to {end} with min_wars={min_wars}.",
                ephemeral=True
            )
            return

        # Build the display message
        embed = discord.Embed(
            title=f"Top Warrers: {start} to {end}",
            description=f"**Reward:** {SHELLS_REWARD} shells each",
            color=discord.Color.gold()
        )

        # Show selected winners
        winner_text = ""
        leadership_started = False

        for i, winner in enumerate(winners, 1):
            # Add separator when transitioning to leadership
            if winner.get('from_leadership') and not leadership_started:
                if winner_text:
                    winner_text += "--\n"
                leadership_started = True

            discord_id = winner.get('discord_id')
            mention = f"<@{discord_id}>" if discord_id else winner['discord_rank']
            winner_text += f"**{i}.** {mention} `{winner['name']}` - **{winner['wars_delta']}** wars\n"

        embed.add_field(name="Winners", value=winner_text, inline=False)

        # Create confirmation view
        view = ConfirmView(winners, ctx.author.id, start, end)

        await ctx.followup.send(embed=embed, view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        pass


def setup(client):
    client.add_cog(TopWars(client))
