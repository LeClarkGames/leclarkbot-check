import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
import random
from collections import defaultdict
import time

import database
import config 

log = logging.getLogger(__name__)

# Rank requirements. The 'xp' is the total XP needed to achieve that rank.
RANKS = {
    1: {"name": "Rank 1", "xp": 50},
    2: {"name": "Rank 2", "xp": 150},
    3: {"name": "Rank 3", "xp": 300},
    4: {"name": "Rank 4", "xp": 500},
    5: {"name": "Rank 5", "xp": 750},
    6: {"name": "Rank 6", "xp": 900},
    7: {"name": "Rank 7", "xp": 1000},
    8: {"name": "Rank 8", "xp": 1200},
    9: {"name": "Rank 9", "xp": 1500},
    10: {"name": "The Legend", "xp": 2000}, # Given a finite number for max rank
}

def get_rank_info(xp):
    """Helper function to determine a user's current rank and progress."""
    current_rank_num = 0
    # Default to the first rank's requirement
    xp_for_next_rank = RANKS[1]['xp']
    
    # Find the user's current rank by checking which XP requirement they have met
    for rank_num, rank_data in RANKS.items():
        if xp < rank_data['xp']:
            xp_for_next_rank = rank_data['xp']
            break
        current_rank_num = rank_num

    # Get the name of the current rank
    current_rank_name = RANKS.get(current_rank_num, {}).get('name', "Unranked")
    
    # --- FIXED LOGIC ---
    # Determine the starting XP for the current rank's progress bar.
    # If unranked, the progress starts from 0.
    # Otherwise, it starts from the XP required for the rank they are currently at.
    if current_rank_num == 0:
        xp_for_current_rank_start = 0
    else:
        xp_for_current_rank_start = RANKS[current_rank_num]['xp']
        # For the final rank, the "next rank" XP is the same as the start, so the bar is full.
        if current_rank_num == 10:
             xp_for_next_rank = RANKS[current_rank_num]['xp']
    
    return current_rank_name, xp_for_current_rank_start, xp_for_next_rank


class RankingCog(commands.Cog, name="Ranking"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.xp_cooldowns = defaultdict(int)
        self.cooldown_seconds = 60
        self.voice_xp_loop.start()

    def cog_unload(self):
        self.voice_xp_loop.cancel()

    @tasks.loop(minutes=5)
    async def voice_xp_loop(self):
        """Grants XP to active members in voice channels."""
        for guild in self.bot.guilds:
            for channel in guild.voice_channels:
                # Grant XP only to users who are not server-deafened or server-muted
                active_members = [m for m in channel.members if not m.bot and not m.voice.deaf and not m.voice.mute]
                if len(active_members) >= 2:
                    for member in active_members:
                        xp_to_add = random.randint(5, 10)
                        await database.update_user_xp(guild.id, member.id, xp_to_add)

    @voice_xp_loop.before_loop
    async def before_voice_xp_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
            
        # Check if user is on cooldown
        user_key = (message.guild.id, message.author.id)
        last_message_time = self.xp_cooldowns.get(user_key, 0)
        current_time = time.time()

        if current_time - last_message_time > self.cooldown_seconds:
            # Grant XP and update cooldown
            xp_to_add = random.randint(15, 25)
            await database.update_user_xp(message.guild.id, message.author.id, xp_to_add)
            self.xp_cooldowns[user_key] = current_time

    @app_commands.command(name="rank", description="Check your or another member's rank and XP.")
    @app_commands.describe(member="The member to check the rank of (optional).")
    async def rank(self, interaction: discord.Interaction, member: discord.Member = None):
        target_member = member or interaction.user
        
        user_xp, rank_pos = await database.get_user_rank(interaction.guild.id, target_member.id)
        
        if user_xp is None:
            await interaction.response.send_message(f"{target_member.display_name} is not yet ranked.", ephemeral=True)
            return

        # Use the corrected function
        rank_name, prev_xp, next_xp = get_rank_info(user_xp)

        progress_needed = next_xp - prev_xp
        progress_made = user_xp - prev_xp
        
        # Avoid division by zero if a rank level has 0 xp difference
        progress_percent = (progress_made / progress_needed) * 100 if progress_needed > 0 else 100
        
        bar_length = 10
        filled_blocks = int(bar_length * progress_percent / 100)
        empty_blocks = bar_length - filled_blocks
        progress_bar = '▓' * filled_blocks + '░' * empty_blocks

        embed = discord.Embed(
            title=f"Rank for {target_member.display_name}",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        embed.set_thumbnail(url=target_member.display_avatar.url)
        embed.add_field(name="Server Rank", value=f"#{rank_pos}", inline=True)
        embed.add_field(name="Level", value=rank_name, inline=True)
        embed.add_field(name="Total XP", value=f"{user_xp}", inline=True)
        
        if rank_name != "The Legend":
             embed.add_field(name="Progress to Next Rank", value=f"`{progress_bar}`\n{user_xp} / {next_xp} XP", inline=False)
        else:
             embed.add_field(name="Progress", value="**Max Rank Reached!** 🌟", inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Shows the server's top 10 most active members.")
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        top_users = await database.get_leaderboard(interaction.guild.id, limit=10)
        
        if not top_users:
            await interaction.followup.send("There is no one on the leaderboard yet!")
            return
            
        embed = discord.Embed(
            title=f"🏆 Leaderboard for {interaction.guild.name}",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        
        description_lines = []
        for i, (user_id, xp) in enumerate(top_users):
            member = interaction.guild.get_member(user_id)
            rank_name, _, _ = get_rank_info(xp)
            # Use a crown for the first place user
            rank_icon = "👑" if i == 0 else f"**{i+1}.**"

            if member:
                description_lines.append(f"{rank_icon} {member.mention} - `{xp}` XP ({rank_name})")
            else:
                description_lines.append(f"{rank_icon} *Unknown User ({user_id})* - `{xp}` XP ({rank_name})")

        embed.description = "\n".join(description_lines)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(RankingCog(bot))
