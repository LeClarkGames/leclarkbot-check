import discord
from discord import app_commands
from discord.ext import commands
from typing import List

import database
import config
from utils import is_bot_admin

# --- Reusable Dropdown Components ---

class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, setting_name: str, placeholder: str, parent_view: discord.ui.View, channel_types: List[discord.ChannelType]):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, channel_types=channel_types)
        self.setting_name = setting_name
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await database.update_setting(interaction.guild.id, self.setting_name, channel.id)
        await interaction.response.send_message(f"✅ {self.placeholder.replace('Select', 'Set')} to {channel.mention}", ephemeral=True)

class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, setting_name: str, placeholder: str, parent_view: discord.ui.View):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.setting_name = setting_name
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await database.update_setting(interaction.guild.id, self.setting_name, role.id)
        await interaction.response.send_message(f"✅ {self.placeholder.replace('Select', 'Set')} to {role.mention}", ephemeral=True)

class VerificationModeSelect(discord.ui.Select):
    def __init__(self, parent_view: discord.ui.View):
        options = [
            discord.SelectOption(label="Captcha Verification", value="captcha", emoji="⌨️"),
            discord.SelectOption(label="Twitch Verification", value="twitch"),
            discord.SelectOption(label="YouTube Verification", value="youtube"),
            discord.SelectOption(label="Gmail Verification", value="gmail", emoji="✉️"),
        ]
        super().__init__(placeholder="Select a verification method...", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        selected_mode = self.values[0]
        await database.update_setting(interaction.guild.id, "verification_mode", selected_mode)
        await interaction.response.send_message(f"✅ Verification mode set to **{selected_mode.capitalize()}**.", ephemeral=True)

class RoleManagementSelect(discord.ui.RoleSelect):
    def __init__(self, action: str, role_type: str, parent_view: discord.ui.View):
        super().__init__(placeholder=f"Select a role to {action} as a Bot {role_type.capitalize()}")
        self.action = action
        self.role_type = role_type
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        setting_name = f"{self.role_type}_role_ids"
        roles_str = await database.get_setting(interaction.guild.id, setting_name) or ""
        role_ids = [r for r in roles_str.split(',') if r]

        if self.action == "add":
            if str(role.id) not in role_ids:
                role_ids.append(str(role.id))
                await interaction.response.send_message(f"✅ {role.mention} added as a bot {self.role_type}.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {role.mention} is already a bot {self.role_type}.", ephemeral=True)
        
        elif self.action == "remove":
            if str(role.id) in role_ids:
                role_ids.remove(str(role.id))
                await interaction.response.send_message(f"✅ {role.mention} removed as a bot {self.role_type}.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {role.mention} is not a bot {self.role_type}.", ephemeral=True)
        
        await database.update_setting(interaction.guild.id, setting_name, ",".join(role_ids))
        # After updating, we need to refresh the main embed, but the original interaction is already responded to.
        # This requires editing the original message from the sub-view's parent.
        await self.parent_view.refresh_and_show(interaction, edit_original=True)

# --- Base View & Back Button ---
class BaseSettingsView(discord.ui.View):
    def __init__(self, bot: commands.Bot, parent_view: discord.ui.View = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.parent_view = parent_view
        if parent_view:
            self.add_item(self.BackButton())

    async def refresh_and_show(self, interaction: discord.Interaction, edit_original: bool = False):
        target_view = self.parent_view if edit_original else self
        embed = await target_view.get_settings_embed(interaction.guild)
        
        # Check if the interaction is from a button/select within this view or a sub-view
        if interaction.response.is_done():
            # This is likely from a sub-sub-view like RoleManagementSelect, edit the original message
            await interaction.edit_original_response(embed=embed, view=target_view)
        else:
            # This is from a button on this view, edit the current message
            await interaction.response.edit_message(embed=embed, view=target_view)

    class BackButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Back", style=discord.ButtonStyle.grey, emoji="⬅️", row=4)
        
        async def callback(self, interaction: discord.Interaction):
            embed = await self.view.parent_view.get_settings_embed(interaction.guild)
            await interaction.response.edit_message(content=None, embed=embed, view=self.view.parent_view)

# --- Sub-Menu Views ---
class ChannelSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: discord.ui.View):
        super().__init__(bot, parent_view)
        self.add_item(ChannelSelect("log_channel_id", "Set Log Channel", self, [discord.ChannelType.text]))
        self.add_item(ChannelSelect("report_channel_id", "Set Report Button Channel", self, [discord.ChannelType.text]))
        self.add_item(ChannelSelect("mod_chat_channel_id", "Set Mod Chat Channel", self, [discord.ChannelType.text]))
        self.add_item(ChannelSelect("announcement_channel_id", "Set Announcement Channel", self, [discord.ChannelType.text]))

class RoleManagementView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: discord.ui.View):
        super().__init__(bot, parent_view)

    @discord.ui.button(label="Add Admin Role", style=discord.ButtonStyle.success)
    async def add_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleManagementSelect("add", "admin", self.parent_view))
        await interaction.response.send_message("Select a role to add:", view=view, ephemeral=True)

    @discord.ui.button(label="Remove Admin Role", style=discord.ButtonStyle.danger)
    async def remove_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleManagementSelect("remove", "admin", self.parent_view))
        await interaction.response.send_message("Select a role to remove:", view=view, ephemeral=True)
    
    @discord.ui.button(label="Add Mod Role", style=discord.ButtonStyle.success, row=1)
    async def add_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleManagementSelect("add", "mod", self.parent_view))
        await interaction.response.send_message("Select a role to add:", view=view, ephemeral=True)

    @discord.ui.button(label="Remove Mod Role", style=discord.ButtonStyle.danger, row=1)
    async def remove_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleManagementSelect("remove", "mod", self.parent_view))
        await interaction.response.send_message("Select a role to remove:", view=view, ephemeral=True)

class VerificationSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: discord.ui.View):
        super().__init__(bot, parent_view)
        self.add_item(VerificationModeSelect(parent_view))
        self.add_item(ChannelSelect("verification_channel_id", "Set Verification Channel", self, [discord.ChannelType.text]))
        self.add_item(RoleSelect("unverified_role_id", "Set Unverified Role", parent_view))
        self.add_item(RoleSelect("member_role_id", "Set Member Role", parent_view))

class TempVCSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: discord.ui.View):
        super().__init__(bot, parent_view)
        self.add_item(ChannelSelect("temp_vc_hub_id", "Set 'Join to Create' Hub Channel", self, [discord.ChannelType.voice]))
        self.add_item(ChannelSelect("temp_vc_category_id", "Set Category for New VCs", self, [discord.ChannelType.category]))

class SubmissionsSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: discord.ui.View):
        super().__init__(bot, parent_view)
        self.add_item(ChannelSelect("submission_channel_id", "Set Regular Submission Channel", self, [discord.ChannelType.text]))
        self.add_item(ChannelSelect("review_channel_id", "Set Review Channel", self, [discord.ChannelType.text]))
        self.add_item(ChannelSelect("koth_submission_channel_id", "Set KOTH Submission Channel", self, [discord.ChannelType.text]))
        self.add_item(RoleSelect("koth_winner_role_id", "Set KOTH Winner Role", parent_view))

# --- Main Navigation View ---
class SettingsMainView(BaseSettingsView):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

    async def get_settings_embed(self, guild: discord.Guild):
        settings_data = await database.get_all_settings(guild.id)
        embed = discord.Embed(title=f"Settings for {guild.name}", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        def f_ch(k): return f"<#{settings_data.get(k)}>" if settings_data.get(k) else "Not Set"
        def f_rl(k): return f"<@&{settings_data.get(k)}>" if settings_data.get(k) else "Not Set"
        def f_rls(k): return ", ".join([f"<@&{r}>" for r in (settings_data.get(k) or "").split(',') if r]) or "Not Set"

        embed.add_field(name="General Channels", value=f"**Log:** {f_ch('log_channel_id')}\n**Report:** {f_ch('report_channel_id')}\n**Announce:** {f_ch('announcement_channel_id')}", inline=False)
        embed.add_field(name="Role Permissions", value=f"**Admins:** {f_rls('admin_role_ids')}\n**Mods:** {f_rls('mod_role_ids')}", inline=False)
        embed.add_field(name="Verification", value=f"**Mode:** `{settings_data.get('verification_mode', 'captcha').capitalize()}`\n**Channel:** {f_ch('verification_channel_id')}\n**Roles:** {f_rl('unverified_role_id')} -> {f_rl('member_role_id')}", inline=False)
        embed.add_field(name="Temporary VCs", value=f"**Hub:** {f_ch('temp_vc_hub_id')}\n**Category:** {f_ch('temp_vc_category_id')}", inline=False)
        embed.add_field(name="Submissions", value=f"**Regular:** {f_ch('submission_channel_id')} -> {f_ch('review_channel_id')}\n**KOTH:** {f_ch('koth_submission_channel_id')} -> {f_rl('koth_winner_role_id')}", inline=False)
        return embed

    @discord.ui.button(label="Channels", style=discord.ButtonStyle.secondary, emoji="📺")
    async def channel_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Configure general bot channels.", view=ChannelSettingsView(self.bot, self))

    @discord.ui.button(label="Roles", style=discord.ButtonStyle.secondary, emoji="🛡️")
    async def role_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Manage Bot Admin/Moderator roles.", view=RoleManagementView(self.bot, self))

    @discord.ui.button(label="Verification", style=discord.ButtonStyle.secondary, emoji="✅", row=1)
    async def verification_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Configure the member verification system.", view=VerificationSettingsView(self.bot, self))

    @discord.ui.button(label="Temp VCs", style=discord.ButtonStyle.secondary, emoji="🔊", row=1)
    async def temp_vc_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Configure the temporary voice channel system.", view=TempVCSettingsView(self.bot, self))
    
    @discord.ui.button(label="Submissions", style=discord.ButtonStyle.secondary, emoji="🎵", row=1)
    async def submissions_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Configure the music submission systems.", view=SubmissionsSettingsView(self.bot, self))

# --- Main Cog ---
class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="settings", description="Opens the interactive settings panel for the bot.")
    @is_bot_admin()
    async def settings(self, interaction: discord.Interaction):
        view = SettingsMainView(self.bot)
        embed = await view.get_settings_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingsCog(bot))