import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
import string
import logging
import secrets
from urllib.parse import urlencode
import os
import aiosmtplib

import database
import config
import utils

log = logging.getLogger(__name__)

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")

# --- Helper function for sending emails ---
async def send_verification_email(recipient_email: str, code: str):
    sender = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        log.error("Gmail credentials are not set in .env file.")
        return False
    
    message = f"""Subject: Your Discord Verification Code

    Hello,

    Your verification code is: {code}

    This code will expire in 10 minutes. Please enter it in the modal on Discord to complete your verification.
    """
    try:
        await aiosmtplib.send(
            message,
            sender=sender,
            recipients=[recipient_email],
            hostname="smtp.gmail.com",
            port=465,
            use_tls=True,
            username=sender,
            password=password,
        )
        return True
    except Exception as e:
        log.error(f"Failed to send verification email: {e}")
        return False

# --- Modals for different verification flows ---
class EmailInputModal(discord.ui.Modal, title="Gmail Verification"):
    email = discord.ui.TextInput(label="Please enter your Gmail address", style=discord.TextStyle.short, required=True, placeholder="example@gmail.com")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code = str(random.randint(100000, 999999))
        
        await database.store_gmail_code(interaction.guild.id, interaction.user.id, code)
        
        success = await send_verification_email(self.email.value, code)
        if success:
            await interaction.followup.send(
                "✅ An email with your verification code has been sent. Please check your inbox (and spam folder), then **send the 6-digit code to me in a direct message (DM)** to complete verification.",
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ Failed to send verification email. Please contact an admin.", ephemeral=True)

class CaptchaModal(discord.ui.Modal, title="Server Verification"):
    def __init__(self, captcha_text: str):
        super().__init__()
        self.captcha_text = captcha_text
        self.add_item(discord.ui.TextInput(label=f"Please type the following text:", placeholder=self.captcha_text, style=discord.TextStyle.short, required=True, max_length=len(captcha_text)))
        
    async def on_submit(self, interaction: discord.Interaction):
        member_role_id = await database.get_setting(interaction.guild.id, 'member_role_id')
        unverified_role_id = await database.get_setting(interaction.guild.id, 'unverified_role_id')
        member_role = interaction.guild.get_role(member_role_id)
        unverified_role = interaction.guild.get_role(unverified_role_id)
        if not member_role or not unverified_role:
            return await interaction.response.send_message("❌ Verification roles not configured correctly.", ephemeral=True)

        if self.children[0].value.lower() == self.captcha_text.lower():
            await interaction.user.add_roles(member_role, reason="Captcha success.")
            await interaction.user.remove_roles(unverified_role, reason="Captcha success.")
            await interaction.response.send_message("✅ Verification successful!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Incorrect captcha. Please try again.", ephemeral=True)

class VerificationButton(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
    
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="persistent_verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member_role_id = await database.get_setting(interaction.guild.id, 'member_role_id')
        if member_role_id and member_role_id in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("You are already verified.", ephemeral=True)

        mode = await database.get_setting(interaction.guild.id, 'verification_mode') or 'captcha'

        if mode == 'captcha':
            captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            await interaction.response.send_modal(CaptchaModal(captcha_text))
        
        elif mode == 'twitch' or mode == 'youtube':
            state = secrets.token_urlsafe(16)
            
            await database.create_verification_link(
                state,
                interaction.guild.id,
                interaction.user.id,
                interaction.guild.name,
                self.bot.user.display_avatar.url
            )

            # --- IMPROVED: Load URL from .env file ---
            base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")

            if mode == 'twitch':
                client_id = os.getenv("TWITCH_CLIENT_ID")
                # --- FIXED: Use dynamic redirect URI ---
                redirect_uri = f"{base_url}/callback/twitch"
                params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "user:read:email", "state": state}
                auth_url = f"https://id.twitch.tv/oauth2/authorize?{urlencode(params)}"
                button_label = "Verify with Twitch"
            else: # mode == 'youtube'
                client_id = os.getenv("YOUTUBE_CLIENT_ID")
                # --- FIXED: Use dynamic redirect URI ---
                redirect_uri = f"{base_url}/callback/youtube"
                params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "https://www.googleapis.com/auth/userinfo.profile", "state": state}
                auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
                button_label = "Verify with YouTube/Google"

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label=button_label, url=auth_url))
            await interaction.response.send_message(f"Please click the button below to verify with your {mode.capitalize()} account.", view=view, ephemeral=True)

        elif mode == 'gmail':
            await interaction.response.send_modal(EmailInputModal())
            
        else:
            await interaction.response.send_message("❌ Unknown verification mode.", ephemeral=True)

# --- The Main Cog for Verification ---
class VerificationCog(commands.Cog, name="Verification"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_verifications.start()

    def cog_unload(self):
        self.check_verifications.cancel()

    @tasks.loop(seconds=15)
    async def check_verifications(self):
        completed_users = await database.get_completed_verifications()
        for state, guild_id, user_id in completed_users:
            guild = self.bot.get_guild(guild_id)
            if not guild: continue
            
            member = guild.get_member(user_id)
            if not member: continue
            
            member_role_id = await database.get_setting(guild.id, 'member_role_id')
            unverified_role_id = await database.get_setting(guild.id, 'unverified_role_id')

            if member_role_id and unverified_role_id:
                try:
                    member_role = guild.get_role(member_role_id)
                    unverified_role = guild.get_role(unverified_role_id)
                    if member_role and unverified_role:
                        await member.add_roles(member_role, reason="OAuth Verification Success")
                        await member.remove_roles(unverified_role, reason="OAuth Verification Success")
                        await database.delete_verification_link(state)
                except Exception as e:
                    log.error(f"Error granting roles via verification: {e}")

    @check_verifications.before_loop
    async def before_check_verifications(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is not None or message.author.bot or not message.content.isdigit() or len(message.content) != 6:
            return

        user = message.author
        code = message.content

        for guild in user.mutual_guilds:
            stored_code = await database.get_gmail_code(guild.id, user.id)
            if stored_code and stored_code == code:
                log.info(f"Found matching Gmail code for user {user.id} in guild {guild.id}")
                
                member_role_id = await database.get_setting(guild.id, 'member_role_id')
                unverified_role_id = await database.get_setting(guild.id, 'unverified_role_id')
                member_role = guild.get_role(member_role_id)
                unverified_role = guild.get_role(unverified_role_id)
                member = guild.get_member(user.id)

                if not member_role or not unverified_role or not member:
                    await message.channel.send("❌ Verification failed. Roles may not be configured correctly in the server.")
                    return

                try:
                    await member.add_roles(member_role, reason="Gmail DM verification success.")
                    await member.remove_roles(unverified_role, reason="Gmail DM verification success.")
                    await database.delete_gmail_code(guild.id, user.id)
                    await message.channel.send(f"✅ You have been successfully verified in **{guild.name}**!")
                    return
                except discord.Forbidden:
                    await message.channel.send(f"❌ Verification failed in **{guild.name}**. I don't have permission to manage your roles there.")
                    return
        
        await message.channel.send("❌ That code is incorrect or has expired. Please start the verification process again in your server.")

    @app_commands.command(name="setup_verification", description="Sends the verification message.")
    @utils.is_bot_admin()
    async def setup_verification(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel_id = await database.get_setting(interaction.guild.id, 'verification_channel_id')
        if not channel_id:
            return await interaction.followup.send("Verification channel not set. Use `/settings` first.", ephemeral=True)
        
        channel = self.bot.get_channel(channel_id)
        embed = discord.Embed(title="Server Verification", description="To gain access to the server, click the button below and complete the required action.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        view = VerificationButton(self.bot)
        
        await channel.send(embed=embed, view=view)
        await interaction.followup.send(f"✅ Verification message sent to {channel.mention}!", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))