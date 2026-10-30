"""
Self-assign role buttons (TAQ-114): the safety rule and the toggle.

Any panel can carry a role button; the editor offers, the publisher accepts
and the toggle hands out only roles that pass `unassignable_reason`, so a
button can never give a role that grants permissions, belongs to a bot or
integration, is @everyone, or sits at or above Tort's own highest role.
"""

import discord


def unassignable_reason(role: discord.Role, bot_top: discord.Role | None) -> str | None:
    """Why `role` can't be a self-assign button, or None when it can."""
    if role.is_default():
        return "it's @everyone"
    if role.managed:
        return "a bot or integration manages it"
    if role.permissions.value:
        return "it grants permissions"
    if bot_top is None or role >= bot_top:
        return "it's at or above Tort's highest role"
    return None


async def toggle_role(interaction: discord.Interaction, role_id: int, requires_role_id: int | None = None):
    """Give or take `role_id` from whoever clicked, replying privately."""
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if not guild:
        return await interaction.followup.send("Server only.", ephemeral=True)

    member = interaction.user
    if not isinstance(member, discord.Member):
        member = await guild.fetch_member(interaction.user.id)

    if requires_role_id:
        gate = guild.get_role(requires_role_id)
        if gate is None:
            return await interaction.followup.send("Gate role not found.", ephemeral=True)
        if gate not in member.roles:
            return await interaction.followup.send(f"You need the {gate.name} role for this.", ephemeral=True)

    role = guild.get_role(role_id)
    if role is None:
        return await interaction.followup.send("Role not found.", ephemeral=True)

    bot_member = guild.me
    if not bot_member or not bot_member.guild_permissions.manage_roles:
        return await interaction.followup.send("I need Manage Roles.", ephemeral=True)
    if unassignable_reason(role, bot_member.top_role):
        return await interaction.followup.send("I can't hand out that role.", ephemeral=True)

    reason = f"Role button used by {member} ({member.id})"
    try:
        if role in member.roles:
            await member.remove_roles(role, reason=reason)
            return await interaction.followup.send(f"Removed {role.name}.", ephemeral=True)
        await member.add_roles(role, reason=reason)
        await interaction.followup.send(f"Added {role.name}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("I can't manage that role.", ephemeral=True)
    except discord.HTTPException:
        await interaction.followup.send("Role update failed.", ephemeral=True)
