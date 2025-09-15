import discord
from discord.ext import commands
import os
import logging
from dotenv import load_dotenv
import asyncio

# --- Bot Components ---
import database
import config
from web_server import app
from cogs.verification import VerificationButton
from cogs.reporting import ReportTriggerView

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)-8s] %(name)-12s: %(message)s", datefmt="%Y-m-d %H:%M:%S")
log = logging.getLogger(__name__)

class MyBot(commands.Bot):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # --- Configuration for LIVE HOSTING (Reverse Proxy) ---
        # The bot listens on an internal port, and the main web server will forward traffic to it.
        self.loop.create_task(app.run_task(host='0.0.0.0', port=8080))
        log.info("Started background web server task for live hosting on port 5000.")
        
        await database.initialize_database()
        
        self.add_view(ReportTriggerView(bot=self))
        self.add_view(VerificationButton(bot=self))
        log.info("Registered persistent UI views.")

        cogs_to_load = [
            "cogs.settings", "cogs.events", "cogs.moderation",
            "cogs.verification", "cogs.reaction_roles", "cogs.reporting",
            "cogs.temp_vc", "cogs.submissions", "cogs.tasks", "cogs.ranking",
        ]
        for cog in cogs_to_load:
            try:
                await self.load_extension(cog)
                log.info(f"Successfully loaded extension: {cog}")
            except Exception as e:
                log.error(f"Failed to load extension {cog}: {e}", exc_info=True)
        
        log.info("Syncing application commands...")
        synced = await self.tree.sync()
        log.info(f"Synced {len(synced)} commands globally.")
        
    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        log.info("Bot is ready! 🚀")
        activity = discord.Activity(name=config.BOT_CONFIG["ACTIVITY_NAME"], type=discord.ActivityType.watching)
        await self.change_presence(activity=activity)
        log.info(f"Set activity to: Watching {config.BOT_CONFIG['ACTIVITY_NAME']}")

if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    intents.voice_states = True
    
    bot = MyBot(intents=intents)
    bot.run(TOKEN)