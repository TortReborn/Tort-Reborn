import json
import time
import aiohttp
import discord
from discord.ext import commands
from discord.commands import slash_command
from io import BytesIO
from PIL import Image

from Helpers import local_ids
from Helpers.shell_exchange_generator import generate_images
from Helpers.database import (
    get_shell_exchange_config,
    save_shell_exchange_config,
    get_shell_exchange_ings,
    save_shell_exchange_ings,
    get_shell_exchange_mats,
    save_shell_exchange_mats,
)
from Helpers.component_media import IS_COMPONENTS_V2, rebind_attachment_urls
from Helpers.storage import delete_shell_exchange_icon
from Helpers.variables import (
    HOME_GUILD_IDS,
    LEGACY_MESSAGE_ID,
    LEGACY_WEBHOOK_URL,
    RATES_PING_ROLE_ID,
    RATES_THREAD_ID,
)

PANEL_FILENAMES = {
    "ingredients": "ingredient_shell_panel.png",
    "materials": "materials_shell_panel.png",
}


class ShellExchange(commands.Cog):
    # Create command groups
    shell_exchange_group = discord.SlashCommandGroup(
        "shell_exchange",
        "Shell exchange commands",
        guild_ids=HOME_GUILD_IDS,
        default_member_permissions=discord.Permissions(manage_roles=True),
    )
    edit_group = shell_exchange_group.create_subgroup("edit", "Edit shell exchange items")
    view_group = shell_exchange_group.create_subgroup("view", "View shell exchange items")

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
        lines = [f"<@&{local_ids.role(RATES_PING_ROLE_ID)}>"]
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

    def _panel_file(self, images, key):
        img = images.get(key)
        if img is None:
            return None
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return discord.File(buffer, PANEL_FILENAMES[key])

    async def _update_legacy_message(self, embed, files):
        async with aiohttp.ClientSession() as session:
            url = f"{LEGACY_WEBHOOK_URL}/messages/{LEGACY_MESSAGE_ID}"
            async with session.get(url) as resp:
                resp.raise_for_status()
                legacy_msg = await resp.json()

            components = legacy_msg.get("components", [])
            uses_components_v2 = bool(legacy_msg.get("flags", 0) & IS_COMPONENTS_V2)
            allow_content_embeds = not uses_components_v2

            if files:
                if uses_components_v2:
                    unreferenced = rebind_attachment_urls(components, [f.filename for f in files])
                    if unreferenced:
                        raise RuntimeError(
                            "The legacy message has no media slot for "
                            + ", ".join(unreferenced)
                            + ". Editing it would leave the panel invisible, so nothing was sent."
                        )

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

    @shell_exchange_group.command(name="config", description='Configure shell exchange settings')
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

    # ---- Bot panel hooks (Tasks/publish_bot_panels.py) ----
    # The panel text lives on the website now; publishing the shell-exchange
    # panel asks this cog for the rate images, then for the follow-ups the old
    # /shell_exchange generate did: the rates-thread diff and the legacy copy.

    def generate_panel_files(self) -> tuple[list[tuple[str, bytes]], list[str]]:
        """(filename, png bytes) per rate image, plus icons missing from storage.
        Blocking (PIL); run it in a thread."""
        config = self.load_config()
        images, skipped_icons = generate_images(
            config.get("output_mode", "both"), config,
            ings_data=self.load_ings_config(), mats_data=self.load_mats_config(),
        )
        files = []
        for key in ("ingredients", "materials"):
            panel = self._panel_file(images, key)
            if panel is not None:
                files.append((panel.filename, panel.fp.getvalue()))
        return files, skipped_icons

    async def after_panel_publish(self, embed: discord.Embed | None, files: list[tuple[str, bytes]]):
        """Announce rate changes; mirror to the legacy webhook message when on."""
        config = self.load_config()
        if config.get("legacy_mode", False) and embed is not None:
            await self._update_legacy_message(
                embed, files=[discord.File(BytesIO(data), filename=name) for name, data in files],
            )
        await self._post_rates_update(config)
        for key in ("channel_id", "panel_message_id", "text_message_id", "ings_message_id", "mats_message_id"):
            config.pop(key, None)  # the panel row owns placement now
        self.save_config(config)

    @shell_exchange_group.command(name="legacy", description="Toggle legacy webhook updates")
    async def shell_exchange_legacy(self, ctx: discord.ApplicationContext, enabled: discord.Option(bool, required=True)):
        config = self.load_config()
        config["legacy_mode"] = enabled
        self.save_config(config)
        await ctx.respond(f"Legacy mode set to {enabled}.", ephemeral=True)

    @edit_group.command(name="ingredient", description="Edit ingredient values")
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

    @edit_group.command(name="material", description="Edit material values")
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

    @edit_group.command(name="add_ingredient", description="Add a new ingredient entry")
    async def add_ingredient(self, ctx: discord.ApplicationContext,
                             name: discord.Option(str, required=True, description="Ingredient name (e.g. Ancient Heart)")):
        if not name or not name.strip():
            await ctx.respond("Name cannot be empty.", ephemeral=True)
            return
        ings_config = self.load_ings_config()
        key = name.strip().casefold()
        for k in ings_config:
            if k.casefold() == key:
                await ctx.respond(f"Ingredient '{name}' already exists.", ephemeral=True)
                return
        ings_config[key] = {"shells": 1, "per": 1, "highlight": False, "toggled": True}
        self.save_ings_config(ings_config)
        await ctx.respond(f"Added ingredient '{name.strip()}' with default values. Upload its 16x16 image via the website.", ephemeral=True)

    @edit_group.command(name="remove_ingredient", description="Remove an ingredient entry and its image")
    async def remove_ingredient(self, ctx: discord.ApplicationContext,
                                name: discord.Option(str, required=True, description="Ingredient name", autocomplete=autocomplete_ingredient_names)):
        ings_config = self.load_ings_config()
        key = None
        for k in ings_config:
            if k.casefold() == name.strip().casefold():
                key = k
                break
        if key is None:
            await ctx.respond(f"Ingredient '{name}' not found.", ephemeral=True)
            return
        del ings_config[key]
        self.save_ings_config(ings_config)
        delete_shell_exchange_icon("ings", key)
        await ctx.respond(f"Removed ingredient '{name}' and its image.", ephemeral=True)

    @edit_group.command(name="add_material", description="Add a new material entry")
    async def add_material(self, ctx: discord.ApplicationContext,
                           name: discord.Option(str, required=True, description="Material name (e.g. Dernic Gem)")):
        if not name or not name.strip():
            await ctx.respond("Name cannot be empty.", ephemeral=True)
            return
        mats_config = self.load_mats_config()
        key = name.strip().casefold()
        for k in mats_config:
            if k.casefold() == key:
                await ctx.respond(f"Material '{name}' already exists.", ephemeral=True)
                return
        mats_config[key] = {
            "t1": {"shells": 1, "per": 1, "highlight": False, "toggled": True},
            "t2": {"shells": 1, "per": 1, "highlight": False, "toggled": True},
            "t3": {"shells": 1, "per": 1, "highlight": False, "toggled": True},
        }
        self.save_mats_config(mats_config)
        await ctx.respond(f"Added material '{name.strip()}' with default tier values. Upload its 16x16 image via the website.", ephemeral=True)

    @edit_group.command(name="remove_material", description="Remove a material entry and its image")
    async def remove_material(self, ctx: discord.ApplicationContext,
                              name: discord.Option(str, required=True, description="Material name", autocomplete=autocomplete_material_names)):
        mats_config = self.load_mats_config()
        key = None
        for k in mats_config:
            if k.casefold() == name.strip().casefold():
                key = k
                break
        if key is None:
            await ctx.respond(f"Material '{name}' not found.", ephemeral=True)
            return
        del mats_config[key]
        self.save_mats_config(mats_config)
        delete_shell_exchange_icon("mats", key)
        await ctx.respond(f"Removed material '{name}' and its image.", ephemeral=True)

    @shell_exchange_group.command(name="list", description='List available items')
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

    @view_group.command(name="ingredient", description="View ingredient values")
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

    @view_group.command(name="material", description="View material values")
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
