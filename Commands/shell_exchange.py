import json
import time
import aiohttp
import discord
from discord.ext import commands
from discord.commands import slash_command
from io import BytesIO
from PIL import Image

from Helpers.shell_exchange_generator import generate_images
from Helpers.database import (
    get_shell_exchange_config,
    save_shell_exchange_config,
    get_shell_exchange_ings,
    save_shell_exchange_ings,
    get_shell_exchange_mats,
    save_shell_exchange_mats,
)
from Helpers.variables import (
    ALL_GUILD_IDS,
    LEGACY_MESSAGE_ID,
    LEGACY_WEBHOOK_URL,
    RATES_PING_ROLE_ID,
    RATES_THREAD_ID,
    IS_TEST_MODE,
)

class ShellExchange(commands.Cog):
    # Create command groups
    shell_exchange_group = discord.SlashCommandGroup(
        "shell_exchange",
        "ADMIN: Shell exchange commands",
        guild_ids=ALL_GUILD_IDS,
        default_member_permissions=discord.Permissions(administrator=True),
    )
    edit_group = shell_exchange_group.create_subgroup("edit", "ADMIN: Edit shell exchange items")
    view_group = shell_exchange_group.create_subgroup("view", "ADMIN: View shell exchange items")

    def __init__(self, client):
        self.client = client

    def load_config(self):
        return get_shell_exchange_config()

    def save_config(self, config):
        save_shell_exchange_config(config)

    def load_ings_config(self):
        return get_shell_exchange_ings()

    def save_ings_config(self, config):
        save_shell_exchange_ings(config)

    def load_mats_config(self):
        return get_shell_exchange_mats()

    def save_mats_config(self, config):
        save_shell_exchange_mats(config)

    def _format_rate_name(self, name):
        cleaned = name.replace("_", " ").strip()
        return " ".join(part.capitalize() for part in cleaned.split())

    def _priority_label(self, highlight):
        return "high" if highlight else "low"

    def _collect_current_rates(self):
        rates = {}
        ings_config = self.load_ings_config()
        for name, data in ings_config.items():
            if not isinstance(data, dict) or not data.get("toggled", True):
                continue
            key = f"ing:{name.casefold()}"
            rates[key] = {
                "name": self._format_rate_name(name),
                "shells": int(data.get("shells", 1)),
                "per": int(data.get("per", 1)),
                "priority": self._priority_label(data.get("highlight", False)),
            }

        mats_config = self.load_mats_config()
        for name, data in mats_config.items():
            if not isinstance(data, dict) or not data.get("toggled", True):
                continue
            for tier in (1, 2, 3):
                tier_key = f"t{tier}"
                td = data.get(tier_key)
                if not isinstance(td, dict) or not td.get("toggled", True):
                    continue
                key = f"mat:{name.casefold()}:{tier}"
                rates[key] = {
                    "name": f"{self._format_rate_name(name)} (Tier {tier})",
                    "shells": int(td.get("shells", 1)),
                    "per": int(td.get("per", 1)),
                    "priority": self._priority_label(td.get("highlight", False)),
                }

        return rates

    def _build_rates_message(self, added, removed, modified):
        lines = [f"<@&{RATES_PING_ROLE_ID}>"]
        ts = int(time.time())
        lines.append(f"## <t:{ts}:d> Shell Exchange changes")

        def format_entry(entry):
            return f"**{entry['name']}** (Shell rates: **{entry['shells']}/{entry['per']}**, Priority: **{entry['priority']}**)"

        def format_modified(old_entry, new_entry):
            changes = []
            if old_entry["shells"] != new_entry["shells"] or old_entry["per"] != new_entry["per"]:
                changes.append(
                    f"Shell rates: **{old_entry['shells']}/{old_entry['per']}** \u2794 **{new_entry['shells']}/{new_entry['per']}**"
                )
            if old_entry["priority"] != new_entry["priority"]:
                changes.append(f"Priority: {old_entry['priority']} \u2794 **{new_entry['priority']}**")
            change_text = ", ".join(changes) if changes else "No changes"
            return f"**{new_entry['name']}** ({change_text})"

        if added:
            lines.append("### \U0001F195 Added")
            lines.extend(format_entry(entry) for entry in added)
        if removed:
            lines.append("### \U0001F5D1\ufe0f Removed")
            lines.extend(format_entry(entry) for entry in removed)
        if modified:
            lines.append("### \u270f\ufe0f Modified")
            lines.extend(format_modified(old_entry, new_entry) for old_entry, new_entry in modified)

        return "\n".join(lines)

    async def _update_legacy_message(self, embed, files):
        async with aiohttp.ClientSession() as session:
            url = f"{LEGACY_WEBHOOK_URL}/messages/{LEGACY_MESSAGE_ID}"
            async with session.get(url) as resp:
                resp.raise_for_status()
                legacy_msg = await resp.json()

            components = legacy_msg.get("components", [])
            allow_content_embeds = True
            if legacy_msg.get("flags") and components:
                allow_content_embeds = False

            if files:
                form = aiohttp.FormData()
                attachments = [{"id": i, "filename": f.filename} for i, f in enumerate(files)]
                payload = {
                    "components": components,
                    "attachments": attachments,
                }
                if allow_content_embeds:
                    payload["embeds"] = [embed.to_dict()]
                form.add_field("payload_json", json.dumps(payload), content_type="application/json")
                for i, f in enumerate(files):
                    f.fp.seek(0)
                    form.add_field(
                        f"files[{i}]",
                        f.fp,
                        filename=f.filename,
                        content_type="application/octet-stream",
                    )
                async with session.patch(url, data=form) as resp:
                    if resp.status >= 400:
                        detail = await resp.text()
                        raise RuntimeError(f"Legacy webhook update failed ({resp.status}): {detail}")
            else:
                payload = {
                    "components": components,
                }
                if allow_content_embeds:
                    payload["embeds"] = [embed.to_dict()]
                async with session.patch(url, json=payload) as resp:
                    if resp.status >= 400:
                        detail = await resp.text()
                        raise RuntimeError(f"Legacy webhook update failed ({resp.status}): {detail}")

    async def _post_rates_update(self, config):
        old_rates = config.get("rates_snapshot")
        new_rates = self._collect_current_rates()

        if old_rates is None:
            config["rates_snapshot"] = new_rates
            return False

        added = []
        removed = []
        modified = []

        for key, new_entry in new_rates.items():
            old_entry = old_rates.get(key)
            if not old_entry:
                added.append(new_entry)
                continue
            if (
                old_entry.get("shells") != new_entry.get("shells")
                or old_entry.get("per") != new_entry.get("per")
                or old_entry.get("priority") != new_entry.get("priority")
            ):
                modified.append((old_entry, new_entry))

        for key, old_entry in old_rates.items():
            if key not in new_rates:
                removed.append(old_entry)

        if added:
            added.sort(key=lambda entry: entry["name"].casefold())
        if removed:
            removed.sort(key=lambda entry: entry["name"].casefold())
        if modified:
            modified.sort(key=lambda entry: entry[1]["name"].casefold())

        if not (added or removed or modified):
            return False

        thread = self.client.get_channel(RATES_THREAD_ID)
        if thread is None:
            try:
                thread = await self.client.fetch_channel(RATES_THREAD_ID)
            except Exception:
                return False

        message = self._build_rates_message(added, removed, modified)
        await thread.send(message)
        config["rates_snapshot"] = new_rates
        return True

    
    async def autocomplete_ingredient_names(self, ctx: discord.AutocompleteContext):
        ings_config = self.load_ings_config()
        names = list(ings_config.keys())
        value = (ctx.value or "").lower()
        
        seen = set()
        unique_names = []
        for name in names:
            folded = name.casefold()
            if folded not in seen:
                seen.add(folded)
                unique_names.append(name)
        return [name for name in unique_names if value in name.lower()][:25]  # Discord limit

    async def autocomplete_material_names(self, ctx: discord.AutocompleteContext):
        mats_config = self.load_mats_config()
        names = list(mats_config.keys())
        value = (ctx.value or "").lower()
        
        seen = set()
        unique_names = []
        for name in names:
            folded = name.casefold()
            if folded not in seen:
                seen.add(folded)
                unique_names.append(name)
        return [name for name in unique_names if value in name.lower()][:25]  # Discord limit

    @shell_exchange_group.command(name="config", description='ADMIN: Configure shell exchange settings')
    async def shell_exchange_config(self, ctx: discord.ApplicationContext, 
                                    setting: discord.Option(str, choices=[
                                        "output_mode", "highlight_mode", "cols_ings", "cols_mats"
                                    ], required=True),
                                    value: discord.Option(str, required=True, description="Value for the setting")):
        config = self.load_config()
        if setting == "output_mode":
            if value not in ["ingredients", "materials", "both"]:
                await ctx.respond("Invalid output mode. Use: ingredients, materials, or both", ephemeral=True)
                return
            config["output_mode"] = value
            self.save_config(config)
            await ctx.respond(f"Output mode set to {value}", ephemeral=True)
        elif setting == "highlight_mode":
            if value not in ["none", "font", "outline", "both"]:
                await ctx.respond("Invalid highlight mode. Use: none, font, outline, or both", ephemeral=True)
                return
            config["highlight_mode"] = value
            self.save_config(config)
            await ctx.respond(f"Highlight mode set to {value}", ephemeral=True)
        elif setting == "cols_ings":
            try:
                cols = int(value)
                if not 1 <= cols <= 8:
                    raise ValueError
            except:
                await ctx.respond("Invalid column count. Use a number between 1-8", ephemeral=True)
                return
            config["cols_ings"] = cols
            self.save_config(config)
            await ctx.respond(f"Ingredient columns set to {cols}", ephemeral=True)
        elif setting == "cols_mats":
            try:
                cols = int(value)
                if not 1 <= cols <= 8:
                    raise ValueError
            except:
                await ctx.respond("Invalid column count. Use a number between 1-8", ephemeral=True)
                return
            config["cols_mats"] = cols
            self.save_config(config)
            await ctx.respond(f"Material columns set to {cols}", ephemeral=True)

    @shell_exchange_group.command(name="generate", description='ADMIN: Generate and post/update shell exchange')
    async def shell_exchange_generate(self, ctx: discord.ApplicationContext):
        await ctx.defer(ephemeral=True)
        config = self.load_config()
        legacy_mode = config.get("legacy_mode", False)
        channel_id = config.get("channel_id")
        if not channel_id and not legacy_mode:
            await ctx.followup.send("Channel not set. Set channel first", ephemeral=True)
            return

        channel = self.client.get_channel(channel_id) if channel_id else None
        if not channel and not legacy_mode:
            await ctx.followup.send("Invalid channel.", ephemeral=True)
            return

        output_mode = config.get("output_mode", "both")
        ings_data = self.load_ings_config()
        mats_data = self.load_mats_config()
        images = generate_images(output_mode, config, ings_data=ings_data, mats_data=mats_data)

        if not images:
            await ctx.followup.send("No images generated.", ephemeral=True)
            return

        # Embed creation (if not legacy message update)
        long_text = "𓆉  Ingredients are vital to the guild. We use them to craft XP gear, prof gear, war and to offer free war builds. We therefore rely on small donations to keep all guild activities running smoothly. To reward our contributors, we have created our exclusive currency: Shells. They can be traded for crafted gear, guild tomes, mythic items, and more. To find a more comprehensive list, check out the <#1251838254098153492>!\n\nThe list below shows our currently accepted ingredients and materials, which you can receive shells for donating. Other ingredients are welcome, but will not be eligible for shells. If you find an ingredient that isn't on the list but you think might still be useful, feel free to ask a Chief for confirmation. Specially outlined items are of higher need than usual right now, so while you will get exactly the shells displayed still, donating these specific ingredients and materials helps us a lot!\n\n⚠️ Shell rates and accepted ingredients are likely to change depending on their demand, how many we currently have in stock, and their price on the Trade Market. All modifications are announced in ⁠the \"Shell rate balances\" thread. If you'd like to be pinged for them, you can get the <@&1050233131183112255> role in <#752917987853467669>. This role is also used when our supply of a specific ingredient gets low (pro tip: high demand ingredients will likely earn you more shells!).\n\n𓆉  In order to claim your shells: \nPut your ingredients in the guild bank and screenshot the log message and optionally the content, you can then **open a ticket** and **send a screenshot** as evidence. A Narwhal will soon update your profile and close the ticket as soon as the transaction is completed.\n\n⚙️ There are two useful commands to check your balance:\n`/profile [user]`\n`/leaderboard (total/timespan)`"

        embed = discord.Embed(description=long_text)

        ings_file = None
        mats_file = None

        ings_img = images.get("ingredients")
        if ings_img:
            buffer = BytesIO()
            ings_img.save(buffer, format="PNG")
            buffer.seek(0)
            ings_file = discord.File(buffer, "ingredient_shell_panel.png")

        mats_img = images.get("materials")
        if mats_img:
            buffer = BytesIO()
            mats_img.save(buffer, format="PNG")
            buffer.seek(0)
            mats_file = discord.File(buffer, "materials_shell_panel.png")

        # Send or update text message
        if legacy_mode:
            files = [f for f in (ings_file, mats_file) if f is not None]
            try:
                await self._update_legacy_message(embed, files=files)
            except Exception as e:
                await ctx.followup.send(f"Legacy message update failed: {type(e).__name__}: {e}", ephemeral=True)
                return
        else:
            text_msg_id = config.get("text_message_id")
            if text_msg_id:
                try:
                    text_msg = await channel.fetch_message(text_msg_id)
                    await text_msg.edit(embed=embed)
                    updated = True
                except Exception as e:
                    text_msg = await channel.send(embed=embed)
                    config["text_message_id"] = text_msg.id
                    updated = False
            else:
                text_msg = await channel.send(embed=embed)
                config["text_message_id"] = text_msg.id
                updated = False

            # Send or update ingredients panel
            if ings_file is not None:
                ings_msg_id = config.get("ings_message_id")
                if ings_msg_id:
                    try:
                        ings_msg = await channel.fetch_message(ings_msg_id)
                        await ings_msg.edit(attachments=[], files=[ings_file])
                    except Exception as e:
                        ings_msg = await channel.send(file=ings_file)
                        config["ings_message_id"] = ings_msg.id
                else:
                    ings_msg = await channel.send(file=ings_file)
                    config["ings_message_id"] = ings_msg.id

            # Send or update materials panel
            if mats_file is not None:
                mats_msg_id = config.get("mats_message_id")
                if mats_msg_id:
                    try:
                        mats_msg = await channel.fetch_message(mats_msg_id)
                        await mats_msg.edit(attachments=[], files=[mats_file])
                    except Exception as e:
                        mats_msg = await channel.send(file=mats_file)
                        config["mats_message_id"] = mats_msg.id
                else:
                    mats_msg = await channel.send(file=mats_file)
                    config["mats_message_id"] = mats_msg.id

        await self._post_rates_update(config)
        self.save_config(config)
        await ctx.followup.send("Posted shell exchange", ephemeral=True)

    @shell_exchange_group.command(name="legacy", description="ADMIN: Toggle legacy webhook updates")
    async def shell_exchange_legacy(self, ctx: discord.ApplicationContext, enabled: discord.Option(bool, required=True)):
        config = self.load_config()
        config["legacy_mode"] = enabled
        self.save_config(config)
        await ctx.respond(f"Legacy mode set to {enabled}.", ephemeral=True)

    @shell_exchange_group.command(name="set_channel", description='ADMIN: Set the channel for shell exchange posts')
    async def shell_exchange_set_channel(self, ctx: discord.ApplicationContext, channel: discord.Option(discord.TextChannel, required=True)):
        config = self.load_config()
        config["channel_id"] = channel.id
        self.save_config(config)
        await ctx.respond(f"Shell exchange channel set to {channel.mention}", ephemeral=True)

    @edit_group.command(name="ingredient", description="ADMIN: Edit ingredient values")
    async def edit_ingredient(self, ctx: discord.ApplicationContext, 
                              name: discord.Option(str, required=True, description="Ingredient name", autocomplete=autocomplete_ingredient_names),
                              shells: discord.Option(int, min_value=0, required=True),
                              per: discord.Option(int, min_value=1, required=True),
                              highlight: discord.Option(bool, required=True),
                              toggled: discord.Option(bool, required=True)):
        ings_config = self.load_ings_config()
        name_stripped = name.strip()
        key = None
        for k in ings_config:
            if k.casefold() == name_stripped.casefold():
                key = k
                break
        if key is None:
            await ctx.respond(f"Ingredient '{name}' not found.", ephemeral=True)
            return
        ings_config[key]["shells"] = shells
        ings_config[key]["per"] = per
        ings_config[key]["highlight"] = highlight
        ings_config[key]["toggled"] = toggled
        self.save_ings_config(ings_config)
        await ctx.respond(f"Updated ingredient '{name}': shells={shells}, per={per}, highlight={highlight}, toggled={toggled}", ephemeral=True)

    @edit_group.command(name="material", description="ADMIN: Edit material values")
    async def edit_material(self, ctx: discord.ApplicationContext, 
                            name: discord.Option(str, required=True, description="Material name", autocomplete=autocomplete_material_names),
                            tier: discord.Option(int, choices=[1,2,3], required=True),
                            shells: discord.Option(int, min_value=0, required=True),
                            per: discord.Option(int, min_value=1, required=True),
                            highlight: discord.Option(bool, required=True),
                            toggled: discord.Option(bool, required=True)):
        mats_config = self.load_mats_config()
        name_stripped = name.strip()
        key = None
        for k in mats_config:
            if k.casefold() == name_stripped.casefold():
                key = k
                break
        if key is None:
            await ctx.respond(f"Material '{name}' not found.", ephemeral=True)
            return
        t_key = f"t{tier}"
        if t_key not in mats_config[key]:
            mats_config[key][t_key] = {}
        mats_config[key][t_key]["shells"] = shells
        mats_config[key][t_key]["per"] = per
        mats_config[key][t_key]["highlight"] = highlight
        mats_config[key][t_key]["toggled"] = toggled
        self.save_mats_config(mats_config)
        await ctx.respond(f"Updated material '{name}' tier {tier}: shells={shells}, per={per}, highlight={highlight}, toggled={toggled}", ephemeral=True)

    @shell_exchange_group.command(name="list", description='ADMIN: List available items')
    async def shell_exchange_list(self, ctx: discord.ApplicationContext, 
                                  type: discord.Option(str, choices=["ingredients", "materials"], required=True)):
        if type == "ingredients":
            ings_config = self.load_ings_config()
            names = list(ings_config.keys())
            title = "Ingredients"
        else:
            mats_config = self.load_mats_config()
            names = list(mats_config.keys())
            title = "Materials"
        
        if not names:
            await ctx.respond(f"No {type} found.", ephemeral=True)
            return
        embed = discord.Embed(title=title, description="\n".join(f"• {name}" for name in sorted(names)))
        await ctx.respond(embed=embed, ephemeral=True)

    @view_group.command(name="ingredient", description="ADMIN: View ingredient values")
    async def view_ingredient(self, ctx: discord.ApplicationContext, name: discord.Option(str, required=True, autocomplete=autocomplete_ingredient_names)):
        ings_config = self.load_ings_config()
        name_stripped = name.strip()
        key = None
        for k in ings_config:
            if k.casefold() == name_stripped.casefold():
                key = k
                break
        if key is None:
            await ctx.respond(f"Ingredient '{name}' not found.", ephemeral=True)
            return
        data = ings_config[key]
        embed = discord.Embed(title=f"Ingredient: {name}")
        embed.add_field(name="Shells", value=data.get("shells", 1), inline=True)
        embed.add_field(name="Per", value=data.get("per", 1), inline=True)
        embed.add_field(name="Highlight", value=data.get("highlight", False), inline=True)
        embed.add_field(name="Toggled", value=data.get("toggled", True), inline=True)
        await ctx.respond(embed=embed, ephemeral=True)

    @view_group.command(name="material", description="ADMIN: View material values")
    async def view_material(self, ctx: discord.ApplicationContext, name: discord.Option(str, required=True, autocomplete=autocomplete_material_names), tier: discord.Option(int, choices=[1,2,3], required=True)):
        mats_config = self.load_mats_config()
        name_stripped = name.strip()
        key = None
        for k in mats_config:
            if k.casefold() == name_stripped.casefold():
                key = k
                break
        if key is None:
            await ctx.respond(f"Material '{name}' not found.", ephemeral=True)
            return
        t_key = f"t{tier}"
        if t_key not in mats_config[key]:
            await ctx.respond(f"Tier {tier} not found for '{name}'.", ephemeral=True)
            return
        data = mats_config[key][t_key]
        embed = discord.Embed(title=f"Material: {name} (Tier {tier})")
        embed.add_field(name="Shells", value=data.get("shells", 1), inline=True)
        embed.add_field(name="Per", value=data.get("per", 1), inline=True)
        embed.add_field(name="Highlight", value=data.get("highlight", False), inline=True)
        embed.add_field(name="Toggled", value=data.get("toggled", True), inline=True)
        await ctx.respond(embed=embed, ephemeral=True)
def setup(client):
    client.add_cog(ShellExchange(client))
