import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
from collections import defaultdict

import database
import config
import utils

log = logging.getLogger(__name__)

# --- Helper Function to Build the Panel ---
async def get_panel_embed_and_view(guild: discord.Guild, bot: commands.Bot):
    """Generates the panel embed and view based on the database state."""
    status = await database.get_setting(guild.id, 'submission_status') or 'closed'
    
    embed_color = config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
    
    if status.startswith('koth'):
        title = "⚔️ King of the Hill Panel"
        koth_queue_count = await database.get_submission_queue_count(guild.id, submission_type='koth')
        
        desc = f"**Mode:** King of the Hill\n**Submissions:** `{'OPEN' if status == 'koth_open' else 'CLOSED'}`\n**Queue:** `{koth_queue_count}` challengers pending."

        if status == 'koth_tiebreaker':
            desc = "**Mode:** King of the Hill\n**Submissions:** `TIEBREAKER DUEL`"
            tiebreaker_users_str = await database.get_setting(guild.id, 'koth_tiebreaker_users') or ""
            tiebreaker_user_ids = [int(uid) for uid in tiebreaker_users_str.split(',') if uid]
            
            mentions = []
            for user_id in tiebreaker_user_ids:
                if user := guild.get_member(user_id):
                    mentions.append(user.mention)
            if mentions:
                desc += f"\n\nWaiting for final submissions from {', '.join(mentions)}."
        
        if king_user_id := await database.get_setting(guild.id, 'koth_king_id'):
            if king_user := guild.get_member(king_user_id):
                desc += f"\n\n**Current King:** {king_user.mention}"
        
        cog = bot.get_cog("Submissions")
        session_stats = cog.current_koth_session.get(guild.id, {})
        if status == 'koth_open' and session_stats:
            desc += "\n\n**Leaderboard (Current Battle):**\n"
            sorted_session = sorted(session_stats.items(), key=lambda item: item[1]['points'], reverse=True)
            for i, (user_id, stats) in enumerate(sorted_session[:5]):
                user = guild.get_member(user_id)
                desc += f"`{i+1}.` {user.display_name if user else 'Unknown'}: `{stats['points']}` pts (`{stats['wins']}` wins)\n"
    else:
        title = "🎵 Music Submission Control Panel"
        is_open = status == 'open'
        queue_count = await database.get_submission_queue_count(guild.id)
        desc = f"Submissions are currently **{'OPEN' if is_open else 'CLOSED'}**.\n\n**Queue:** `{queue_count}` tracks pending."
        embed_color = config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"] if is_open else config.BOT_CONFIG["EMBED_COLORS"]["ERROR"]

    embed = discord.Embed(title=title, description=desc, color=embed_color)
    view = SubmissionPanelView(bot, status)
    return embed, view

# --- Views for Specific Interactions ---

class KOTHBattleView(discord.ui.View):
    """View for when a KOTH battle is actively happening."""
    def __init__(self, bot: commands.Bot, king_data: dict, challenger_data: dict, is_tiebreaker: bool = False):
        super().__init__(timeout=None)
        self.bot = bot
        self.king_data = king_data
        self.challenger_data = challenger_data
        self.is_tiebreaker = is_tiebreaker
        self.cog = bot.get_cog("Submissions")

    async def _handle_vote(self, interaction: discord.Interaction, winner: str):
        if not await utils.has_mod_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission to vote.", ephemeral=True)
        
        await interaction.response.defer()
        
        winner_data = self.king_data if winner == 'king' else self.challenger_data
        loser_data = self.challenger_data if winner == 'king' else self.king_data
        
        if self.is_tiebreaker:
            await interaction.message.delete()
            await self.cog.finalize_koth_battle(interaction, winner_data['user_id'])
            return

        await database.update_koth_battle_results(interaction.guild.id, winner_data['user_id'], loser_data['user_id'])
        
        # Update in-memory session stats
        session_stats = self.cog.current_koth_session[interaction.guild.id]
        winner_id = winner_data['user_id']
        session_stats.setdefault(winner_id, {'points': 0, 'wins': 0})['points'] += 1
        session_stats.setdefault(winner_id, {'points': 0, 'wins': 0})['wins'] += 1

        # Mark both submissions as reviewed
        await database.update_submission_status(self.king_data['submission_id'], 'reviewed', interaction.user.id)
        await database.update_submission_status(self.challenger_data['submission_id'], 'reviewed', interaction.user.id)
        
        # Update king in the database
        await database.update_setting(interaction.guild.id, 'koth_king_id', winner_data['user_id'])
        await database.update_setting(interaction.guild.id, 'koth_king_submission_id', winner_data['submission_id'])

        await interaction.message.delete()
        
        if panel_message := await self.cog.get_panel_message(interaction.guild):
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)
        
        winner_user = interaction.guild.get_member(winner_id)
        winner_message = await interaction.followup.send(f"👑 **{winner_user.display_name if winner_user else 'Someone'}** wins the round and remains King!")
        self.cog.koth_battle_messages[interaction.guild.id].append(winner_message.id)

class ReviewItemView(discord.ui.View):
    """View for a single track being reviewed in regular mode."""
    def __init__(self, bot: commands.Bot, submission_id: int):
        super().__init__(timeout=18000) # 5 hours
        self.bot = bot
        self.submission_id = submission_id
        self.cog = bot.get_cog("Submissions")

    @discord.ui.button(label="✔️ Mark as Reviewed", style=discord.ButtonStyle.success)
    async def mark_reviewed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission to review tracks.", ephemeral=True)
        
        await database.update_submission_status(self.submission_id, "reviewed", interaction.user.id)
        await interaction.message.delete()
        await interaction.response.send_message("✅ Track marked as reviewed.", ephemeral=True)
        
        panel_message = await self.cog.get_panel_message(interaction.guild)
        if panel_message:
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)


# --- The New Dynamic Control Panel View ---

class SubmissionPanelView(discord.ui.View):
    """
    A dynamic view that shows different buttons based on the submission status.
    """
    def __init__(self, bot: commands.Bot, status: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.cog = bot.get_cog("Submissions")

        if status == 'koth_tiebreaker':
            self.add_button("🛑 Cancel Tiebreaker", self.stop_koth_battle, discord.ButtonStyle.danger)
            return

        if status == 'closed':
            self.add_button("Start Submissions", self.start_submissions, discord.ButtonStyle.success)
            self.add_button("📊 Statistics", self.statistics, discord.ButtonStyle.secondary)
            self.add_button("Switch to KOTH Mode", self.switch_to_koth, discord.ButtonStyle.secondary)
        elif status == 'open':
            self.add_button("▶️ Play the Queue", self.play_queue, discord.ButtonStyle.primary)
            self.add_button("⏹️ Stop Submissions", self.stop_submissions, discord.ButtonStyle.danger)
            self.add_button("📊 Statistics", self.statistics, discord.ButtonStyle.secondary)
        elif status == 'koth_closed':
            self.add_button("Start KOTH Battle", self.start_koth_battle, discord.ButtonStyle.success)
            self.add_button("📊 KOTH Stats", self.koth_stats, discord.ButtonStyle.secondary)
            self.add_button("Switch to Regular Mode", self.switch_to_regular, discord.ButtonStyle.secondary)
        elif status == 'koth_open':
            self.add_button("▶️ Play KOTH Queue", self.play_koth_queue, discord.ButtonStyle.primary)
            self.add_button("⏹️ Stop KOTH Battle", self.stop_koth_battle, discord.ButtonStyle.danger)
            self.add_button("📊 KOTH Stats", self.koth_stats, discord.ButtonStyle.secondary)

    def add_button(self, label, callback, style=discord.ButtonStyle.secondary, emoji=None):
        button = discord.ui.Button(label=label, style=style, emoji=emoji)
        button.callback = callback
        self.add_item(button)

    async def _update_panel(self, interaction: discord.Interaction):
        async with self.cog.panel_update_locks[interaction.guild.id]:
            panel_message = await self.cog.get_panel_message(interaction.guild)
            if panel_message:
                embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
                try:
                    await panel_message.edit(embed=embed, view=view)
                except discord.NotFound:
                    log.warning(f"Failed to update panel for guild {interaction.guild.id}, message not found.")
            
    # --- REGULAR MODE CALLBACKS ---

    async def start_submissions(self, interaction: discord.Interaction):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        await database.update_setting(interaction.guild.id, 'submission_status', 'open')
        await self._update_panel(interaction)
        sub_channel_id = await database.get_setting(interaction.guild.id, 'submission_channel_id')
        if sub_channel_id and (channel := self.bot.get_channel(sub_channel_id)):
            await channel.send("@everyone Submissions are now **OPEN**! Please send your audio files here.")
        await interaction.followup.send("✅ Submissions are now open.", ephemeral=True)

    async def play_queue(self, interaction: discord.Interaction):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
        next_track = await database.get_next_submission(interaction.guild.id, submission_type='regular')
        if not next_track: return await interaction.response.send_message("The submission queue is empty!", ephemeral=True)
        
        sub_id, user_id, url = next_track
        await database.update_submission_status(sub_id, "reviewing", interaction.user.id)
        user = interaction.guild.get_member(user_id)
        embed = discord.Embed(title="🎵 Track for Review", description=f"Submitted by: {user.mention if user else 'N/A'}", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        await interaction.response.send_message(embed=embed, content=url, view=ReviewItemView(self.bot, sub_id))

    async def stop_submissions(self, interaction: discord.Interaction):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        
        conn = await database.get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) FROM music_submissions WHERE guild_id = ? AND submission_type = ? AND status = 'reviewed'", (interaction.guild.id, 'regular'))
            session_reviewed_count = (await cursor.fetchone())[0]

        await database.clear_session_submissions(interaction.guild.id, 'regular')
        await database.update_setting(interaction.guild.id, 'submission_status', 'closed')
        await self._update_panel(interaction)
        
        sub_channel_id = await database.get_setting(interaction.guild.id, 'submission_channel_id')
        if sub_channel_id and (channel := self.bot.get_channel(sub_channel_id)):
            await channel.send("Submissions are now **CLOSED**! Thanks to everyone who sent in their tracks.")
        await interaction.followup.send(f"✅ Session closed. A total of **{session_reviewed_count}** tracks were reviewed in this session.", ephemeral=True)

    async def statistics(self, interaction: discord.Interaction):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        reviewed_count = await database.get_total_reviewed_count(interaction.guild.id, 'regular')
        embed = discord.Embed(title="📊 Regular Submission Statistics (All-Time)", description=f"A total of **{reviewed_count}** tracks have been permanently reviewed in this server.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        await interaction.followup.send(embed=embed)

    async def switch_to_koth(self, interaction: discord.Interaction):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        await database.update_setting(interaction.guild.id, 'submission_status', 'koth_closed')
        await self._update_panel(interaction)
        await interaction.followup.send("✅ Switched to King of the Hill mode.", ephemeral=True)

    # --- KOTH MODE CALLBACKS ---
    
    async def start_koth_battle(self, interaction: discord.Interaction):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        
        self.cog.current_koth_session.pop(interaction.guild.id, None)

        if winner_role_id := await database.get_setting(interaction.guild.id, 'koth_winner_role_id'):
            if role := interaction.guild.get_role(winner_role_id):
                for member in role.members:
                    await member.remove_roles(role, reason="New KOTH battle started.")

        await database.update_setting(interaction.guild.id, 'submission_status', 'koth_open')
        await self._update_panel(interaction)
        
        if koth_channel_id := await database.get_setting(interaction.guild.id, 'koth_submission_channel_id'):
            if channel := self.bot.get_channel(koth_channel_id):
                await channel.send("@everyone King of the Hill submissions are **OPEN**! Submit your best track to enter the battle!")
        await interaction.followup.send("✅ King of the Hill battle has started!", ephemeral=True)

    async def play_koth_queue(self, interaction: discord.Interaction):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods only.", ephemeral=True)
        guild_id = interaction.guild.id

        king_id = await database.get_setting(guild_id, 'koth_king_id')
        challenger_track = await database.get_next_submission(guild_id, 'koth')

        if not king_id:
            if not challenger_track: return await interaction.response.send_message("The KOTH queue is empty! Need at least one challenger.", ephemeral=True)
            
            sub_id, user_id, url = challenger_track
            await database.update_setting(guild_id, 'koth_king_id', user_id)
            await database.update_setting(guild_id, 'koth_king_submission_id', sub_id)
            await database.update_submission_status(sub_id, 'reviewing', interaction.user.id)
            
            king_user = interaction.guild.get_member(user_id)
            embed = discord.Embed(title="👑 New King of the Hill!", description=f"**{king_user.display_name}** is the new King!", color=config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"])
            await interaction.response.send_message(content=url, embed=embed)
            self.cog.koth_battle_messages[guild_id].append((await interaction.original_response()).id)
            await self._update_panel(interaction)
        else:
            if not challenger_track: return await interaction.response.send_message("No more challengers in the queue!", ephemeral=True)
            
            c_sub_id, c_user_id, c_url = challenger_track
            await database.update_submission_status(c_sub_id, 'reviewing', interaction.user.id)
            
            king_sub_id = await database.get_setting(guild_id, 'koth_king_submission_id')
            conn = await database.get_db_connection()
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT track_url FROM music_submissions WHERE submission_id = ?", (king_sub_id,))
                king_url_result = await cursor.fetchone()
                king_url = king_url_result[0] if king_url_result else "Track URL not found"

            king_data = {"user_id": king_id, "submission_id": king_sub_id, "track_url": king_url}
            challenger_data = {"user_id": c_user_id, "submission_id": c_sub_id, "track_url": c_url}
            
            king_user = interaction.guild.get_member(king_id)
            challenger_user = interaction.guild.get_member(c_user_id)
            
            embed = discord.Embed(title="⚔️ BATTLE TIME! ⚔️", color=discord.Color.gold())
            embed.add_field(name=f"👑 The King: {king_user.display_name if king_user else 'Unknown'}", value=f"Track: {king_url}", inline=False)
            embed.add_field(name=f"⚔️ The Challenger: {challenger_user.display_name if challenger_user else 'Unknown'}", value=f"Track: {c_url}", inline=False)
            
            await interaction.response.send_message(embed=embed, view=KOTHBattleView(self.bot, king_data, challenger_data))

    async def stop_koth_battle(self, interaction: discord.Interaction):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        
        guild_id = interaction.guild.id
        session_stats = self.cog.current_koth_session.get(guild_id, {})
        sorted_session = sorted(session_stats.items(), key=lambda item: item[1]['points'], reverse=True)

        is_tie = len(sorted_session) > 1 and sorted_session[0][1]['points'] > 0 and sorted_session[0][1]['points'] == sorted_session[1][1]['points']

        if is_tie:
            user1_id, user2_id = sorted_session[0][0], sorted_session[1][0]
            await database.update_setting(guild_id, 'koth_tiebreaker_users', f"{user1_id},{user2_id}")
            self.cog.tiebreaker_submissions.pop(guild_id, None) # Clear old tiebreaker submissions
            await database.update_setting(guild_id, 'submission_status', 'koth_tiebreaker')
            await self._update_panel(interaction)

            user1 = interaction.guild.get_member(user1_id)
            user2 = interaction.guild.get_member(user2_id)
            if (koth_channel_id := await database.get_setting(guild_id, 'koth_submission_channel_id')) and (channel := self.bot.get_channel(koth_channel_id)):
                await channel.send(f"**⚔️ TIEBREAKER! ⚔️**\n{user1.mention} and {user2.mention}, submit one final track!")
            await interaction.followup.send("A tiebreaker has been initiated!", ephemeral=True)
        else:
            winner_id = sorted_session[0][0] if sorted_session else await database.get_setting(guild_id, 'koth_king_id')
            await self.cog.finalize_koth_battle(interaction, winner_id)

    async def koth_stats(self, interaction: discord.Interaction):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        
        leaderboard = await database.get_koth_leaderboard(interaction.guild.id)
        if not leaderboard: return await interaction.followup.send("No KOTH statistics found yet.", ephemeral=True)

        desc = "All-time points for King of the Hill battles:\n\n"
        for i, (user_id, points, wins, losses, streak) in enumerate(leaderboard[:10]):
            user = interaction.guild.get_member(user_id)
            user_display = user.display_name if user else f'Unknown User ({user_id})'
            desc += f"`{i+1}.` **{user_display}**: `{points}` pts (**W/L:** `{wins}/{losses}`, **Streak:** `{streak}`)\n"
        
        embed = discord.Embed(title="⚔️ KOTH Leaderboard (All-Time)", description=desc, color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        await interaction.followup.send(embed=embed)

    async def switch_to_regular(self, interaction: discord.Interaction):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        await database.update_setting(interaction.guild.id, 'submission_status', 'closed')
        await self._update_panel(interaction)
        await interaction.followup.send("✅ Switched back to regular submission mode.", ephemeral=True)

# --- Main Cog ---

class SubmissionsCog(commands.Cog, name="Submissions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel_update_locks = defaultdict(asyncio.Lock)
        self.koth_battle_messages = defaultdict(list)
        self.current_koth_session = defaultdict(dict)
        self.tiebreaker_submissions = defaultdict(dict)

    async def finalize_koth_battle(self, interaction: discord.Interaction, winner_id: int | None):
        guild_id = interaction.guild.id
        
        if review_channel_id := await database.get_setting(guild_id, 'review_channel_id'):
            if review_channel := self.bot.get_channel(review_channel_id):
                message_ids_to_delete = self.koth_battle_messages.pop(guild_id, [])
                for msg_id in message_ids_to_delete:
                    try:
                        await (await review_channel.fetch_message(msg_id)).delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass
        
        session_stats = self.current_koth_session.get(guild_id, {})
        sorted_session = sorted(session_stats.items(), key=lambda item: item[1]['points'], reverse=True)
        public_desc = "**Final Battle Leaderboard:**\n"
        if sorted_session:
            for i, (user_id, stats) in enumerate(sorted_session):
                user = interaction.guild.get_member(user_id)
                public_desc += f"`{i+1}.` {user.display_name if user else 'Unknown User'}: `{stats['points']}` points (`{stats['wins']}` wins)\n"
        else:
            public_desc += "No points were scored in this battle."

        public_embed = discord.Embed(title="🏆 King of the Hill Results 🏆", description=public_desc, color=discord.Color.gold())
        
        if winner_id and (winner := interaction.guild.get_member(winner_id)):
            public_embed.description = f"Congratulations to the battle winner, {winner.mention}!\n\n" + public_desc
            if winner_role_id := await database.get_setting(guild_id, 'koth_winner_role_id'):
                if role := interaction.guild.get_role(winner_role_id):
                    await winner.add_roles(role, reason="KOTH Winner")
        
        if koth_channel_id := await database.get_setting(guild_id, 'koth_submission_channel_id'):
            if channel := self.bot.get_channel(koth_channel_id):
                await channel.send(embed=public_embed)

        # --- NEW: Clear KOTH state from the database ---
        await database.clear_session_submissions(guild_id, 'koth')
        await database.update_setting(guild_id, 'submission_status', 'koth_closed')
        await database.update_setting(guild_id, 'koth_king_id', None)
        await database.update_setting(guild_id, 'koth_king_submission_id', None)
        await database.update_setting(guild_id, 'koth_tiebreaker_users', None)
        
        # Clear in-memory session data
        self.current_koth_session.pop(guild_id, None)
        self.tiebreaker_submissions.pop(guild_id, None)

        panel_message = await self.get_panel_message(interaction.guild)
        if panel_message:
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)

        if interaction.response.is_done():
            await interaction.followup.send("✅ KOTH battle stopped. Results posted.", ephemeral=True)
        else:
            await interaction.response.send_message("✅ KOTH battle stopped. Results posted.", ephemeral=True)

    async def get_panel_message(self, guild: discord.Guild) -> discord.Message | None:
        panel_id = await database.get_setting(guild.id, 'review_panel_message_id')
        channel_id = await database.get_setting(guild.id, 'review_channel_id')
        if not panel_id or not channel_id: return None
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if channel: return await channel.fetch_message(panel_id)
        except (discord.NotFound, discord.Forbidden): return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return
        
        status = await database.get_setting(message.guild.id, 'submission_status')
        submission_channel_id = await database.get_setting(message.guild.id, 'submission_channel_id')
        koth_submission_channel_id = await database.get_setting(message.guild.id, 'koth_submission_channel_id')
        
        submission_type = None
        if status == 'open' and message.channel.id == submission_channel_id:
            submission_type = 'regular'
        elif status == 'koth_open' and message.channel.id == koth_submission_channel_id:
            submission_type = 'koth'
        elif status == 'koth_tiebreaker' and message.channel.id == koth_submission_channel_id:
            tiebreaker_users_str = await database.get_setting(message.guild.id, 'koth_tiebreaker_users') or ""
            if str(message.author.id) in tiebreaker_users_str:
                if message.author.id not in self.tiebreaker_submissions.get(message.guild.id, {}):
                    if message.attachments and any(att.content_type and att.content_type.startswith("audio/") for att in message.attachments):
                        self.tiebreaker_submissions[message.guild.id][message.author.id] = message.attachments[0].url
                        await message.add_reaction("⚔️")
                        
                        if len(self.tiebreaker_submissions[message.guild.id]) == 2:
                            user_ids = list(self.tiebreaker_submissions[message.guild.id].keys())
                            track_urls = list(self.tiebreaker_submissions[message.guild.id].values())
                            
                            p1_data = {"user_id": user_ids[0], "submission_id": -1, "track_url": track_urls[0]}
                            p2_data = {"user_id": user_ids[1], "submission_id": -1, "track_url": track_urls[1]}
                            
                            p1_user = message.guild.get_member(user_ids[0])
                            p2_user = message.guild.get_member(user_ids[1])
                            
                            embed = discord.Embed(title="⚔️ FINAL BATTLE! ⚔️", color=discord.Color.red())
                            embed.add_field(name=f"Duelist 1: {p1_user.display_name if p1_user else 'Unknown'}", value=f"Track: {track_urls[0]}", inline=False)
                            embed.add_field(name=f"Duelist 2: {p2_user.display_name if p2_user else 'Unknown'}", value=f"Track: {track_urls[1]}", inline=False)
                            
                            if (review_channel_id := await database.get_setting(message.guild.id, 'review_channel_id')) and (review_channel := self.bot.get_channel(review_channel_id)):
                                await review_channel.send(embed=embed, view=KOTHBattleView(self.bot, p1_data, p2_data, is_tiebreaker=True))
            return

        if submission_type and message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("audio/"):
                    submission_id = await database.add_submission(message.guild.id, message.author.id, attachment.url, submission_type)
                    await message.add_reaction("✅")
                    
                    if submission_type == 'regular':
                        total_user_subs = await database.get_user_submission_count(message.guild.id, message.author.id, 'regular')
                        if total_user_subs == 1:
                            await database.prioritize_submission(submission_id)
                            log.info(f"Prioritized first-time submission from {message.author.id}")
                            try:
                                await message.author.send(f"✅ Since it's your first time submitting in **{message.guild.name}**, your track has been moved to the front of the queue!")
                            except discord.Forbidden:
                                pass

                    if submission_type == 'koth':
                        session_stats = self.current_koth_session[message.guild.id]
                        user_id = message.author.id
                        session_stats.setdefault(user_id, {'points': 0, 'wins': 0, 'submissions': 0})['submissions'] += 1

                    async with self.panel_update_locks[message.guild.id]:
                        panel_message = await self.get_panel_message(message.guild)
                        if panel_message:
                            embed, view = await get_panel_embed_and_view(message.guild, self.bot)
                            await panel_message.edit(embed=embed, view=view)
                    break 

    @app_commands.command(name="setup_submission_panel", description="Posts the interactive panel for managing music submissions.")
    @utils.is_bot_admin()
    async def setup_submission_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        if not (review_channel_id := await database.get_setting(interaction.guild.id, 'review_channel_id')):
            return await interaction.followup.send("❌ The review channel is not set. Use `/settings submission_system` first.")
        if not (review_channel := self.bot.get_channel(review_channel_id)):
            return await interaction.followup.send("❌ Could not find the configured review channel.")
        
        if old_panel := await self.get_panel_message(interaction.guild):
            try:
                await old_panel.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
                
        embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
        
        try:
            panel_message = await review_channel.send(embed=embed, view=view)
            await database.update_setting(interaction.guild.id, 'review_panel_message_id', panel_message.id)
            await database.update_setting(interaction.guild.id, 'submission_status', 'closed')
            await interaction.followup.send(f"✅ Submission panel has been posted in {review_channel.mention}.")
        except discord.Forbidden:
            await interaction.followup.send(f"❌ I don't have permission to send messages in {review_channel.mention}.")

async def setup(bot: commands.Bot):
    await bot.add_cog(SubmissionsCog(bot))