import aiosqlite
import logging
from datetime import datetime

log = logging.getLogger(__name__)
DB_FILE = "bot_database.db"
db_conn = None

async def get_db_connection():
    """Gets a connection to the SQLite database."""
    global db_conn
    if db_conn:
        return db_conn
    try:
        db_conn = await aiosqlite.connect(DB_FILE)
        await db_conn.execute("PRAGMA journal_mode=WAL;")
        log.info("Successfully connected to the SQLite database.")
        return db_conn
    except Exception as e:
        log.critical(f"Could not connect to the SQLite database: {e}")
        return None

async def initialize_database():
    """Initializes and updates the database schema if needed."""
    conn = await get_db_connection()
    if not conn: return
    async with conn.cursor() as cursor:
        await cursor.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY, log_channel_id INTEGER, report_channel_id INTEGER,
                verification_channel_id INTEGER, unverified_role_id INTEGER, member_role_id INTEGER,
                verification_message_id INTEGER, admin_role_ids TEXT, mod_role_ids TEXT,
                mod_chat_channel_id INTEGER, temp_vc_hub_id INTEGER, temp_vc_category_id INTEGER,
                submission_channel_id INTEGER, review_channel_id INTEGER, submission_status TEXT DEFAULT 'closed',
                review_panel_message_id INTEGER, announcement_channel_id INTEGER, last_milestone_count INTEGER DEFAULT 0,
                koth_submission_channel_id INTEGER, koth_winner_role_id INTEGER, verification_mode TEXT DEFAULT 'captcha'
            )
        """)
        await cursor.execute("CREATE TABLE IF NOT EXISTS warnings (warning_id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, log_message_id INTEGER)")
        await cursor.execute("CREATE TABLE IF NOT EXISTS reaction_roles (message_id INTEGER NOT NULL, emoji TEXT NOT NULL, role_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, PRIMARY KEY (message_id, emoji))")
        await cursor.execute("CREATE TABLE IF NOT EXISTS temporary_vcs (channel_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, text_channel_id INTEGER)")
        await cursor.execute("CREATE TABLE IF NOT EXISTS music_submissions ( submission_id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, track_url TEXT NOT NULL, status TEXT NOT NULL, submitted_at TIMESTAMP NOT NULL, reviewer_id INTEGER, submission_type TEXT DEFAULT 'regular' )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS koth_leaderboard ( user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, points INTEGER DEFAULT 0, PRIMARY KEY (user_id, guild_id) )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS ranking ( user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, xp INTEGER DEFAULT 0, PRIMARY KEY (user_id, guild_id) )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS bad_words ( word_id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, word TEXT NOT NULL )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS verification_links ( state TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, status TEXT DEFAULT 'pending', verified_account TEXT, server_name TEXT, bot_avatar_url TEXT )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS gmail_verification ( user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, verification_code TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, guild_id) )")
        
        # --- Schema Updates for KOTH & Leaderboard ---
        await cursor.execute("PRAGMA table_info(koth_leaderboard)")
        koth_columns = [row[1] for row in await cursor.fetchall()]
        if 'wins' not in koth_columns: await cursor.execute("ALTER TABLE koth_leaderboard ADD COLUMN wins INTEGER NOT NULL DEFAULT 0")
        if 'losses' not in koth_columns: await cursor.execute("ALTER TABLE koth_leaderboard ADD COLUMN losses INTEGER NOT NULL DEFAULT 0")
        if 'streak' not in koth_columns: await cursor.execute("ALTER TABLE koth_leaderboard ADD COLUMN streak INTEGER NOT NULL DEFAULT 0")

        # --- NEW: Schema updates for persistent KOTH state ---
        await cursor.execute("PRAGMA table_info(guild_settings)")
        settings_columns = [row[1] for row in await cursor.fetchall()]
        if 'koth_king_id' not in settings_columns:
            await cursor.execute("ALTER TABLE guild_settings ADD COLUMN koth_king_id INTEGER")
        if 'koth_king_submission_id' not in settings_columns:
            await cursor.execute("ALTER TABLE guild_settings ADD COLUMN koth_king_submission_id INTEGER")
        if 'koth_tiebreaker_users' not in settings_columns:
            await cursor.execute("ALTER TABLE guild_settings ADD COLUMN koth_tiebreaker_users TEXT")

    await conn.commit()
    log.info("Database tables initialized/updated successfully.")

# --- SETTINGS FUNCTIONS ---
async def get_setting(guild_id, setting_name):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute(f"SELECT {setting_name} FROM guild_settings WHERE guild_id = ?", (guild_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def update_setting(guild_id, setting_name, value):
    conn = await get_db_connection()
    sql = f"INSERT INTO guild_settings (guild_id, {setting_name}) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET {setting_name} = excluded.{setting_name}"
    await conn.execute(sql, (guild_id, value))
    await conn.commit()

async def get_all_settings(guild_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        if not row: return {}
        columns = [description[0] for description in cursor.description]
        return dict(zip(columns, row))

# --- WARNINGS FUNCTIONS ---
async def add_warning(guild_id, user_id, log_message_id):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO warnings (guild_id, user_id, log_message_id) VALUES (?, ?, ?)", (guild_id, user_id, log_message_id))
    await conn.commit()

async def get_warnings_count(guild_id, user_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def clear_warnings(guild_id, user_id):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    await conn.commit()

# --- REACTION ROLES FUNCTIONS ---
async def add_reaction_role(guild_id, message_id, emoji, role_id):
    conn = await get_db_connection()
    await conn.execute("INSERT OR REPLACE INTO reaction_roles (guild_id, message_id, emoji, role_id) VALUES (?, ?, ?, ?)", (guild_id, message_id, emoji, role_id))
    await conn.commit()

async def get_reaction_role(message_id, emoji):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT role_id FROM reaction_roles WHERE message_id = ? AND emoji = ?", (message_id, emoji))
        result = await cursor.fetchone()
        return result[0] if result else None

# --- TEMP VC FUNCTIONS ---
async def add_temp_vc(channel_id, owner_id, text_channel_id=None):
    conn = await get_db_connection()
    await conn.execute("INSERT OR REPLACE INTO temporary_vcs (channel_id, owner_id, text_channel_id) VALUES (?, ?, ?)", (channel_id, owner_id, text_channel_id))
    await conn.commit()

async def remove_temp_vc(channel_id):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM temporary_vcs WHERE channel_id = ?", (channel_id,))
    await conn.commit()

async def get_temp_vc_owner(channel_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT owner_id FROM temporary_vcs WHERE channel_id = ?", (channel_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def get_temp_vc_text_channel_id(channel_id):
    """Gets the associated text channel ID for a temporary VC."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT text_channel_id FROM temporary_vcs WHERE channel_id = ?", (channel_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def update_temp_vc_owner(channel_id, new_owner_id):
    conn = await get_db_connection()
    await conn.execute("UPDATE temporary_vcs SET owner_id = ? WHERE channel_id = ?", (new_owner_id, channel_id))

# --- SUBMISSION FUNCTIONS ---
async def add_submission(guild_id, user_id, track_url, submission_type='regular'):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("INSERT INTO music_submissions (guild_id, user_id, track_url, status, submitted_at, submission_type) VALUES (?, ?, ?, ?, ?, ?)",(guild_id, user_id, track_url, "pending", datetime.utcnow(), submission_type))
        submission_id = cursor.lastrowid
    await conn.commit()
    return submission_id

async def get_user_submission_count(guild_id, user_id, submission_type='regular'):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM music_submissions WHERE guild_id = ? AND user_id = ? AND submission_type = ?", (guild_id, user_id, submission_type))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def get_submission_queue_count(guild_id, submission_type='regular', status="pending"):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM music_submissions WHERE guild_id = ? AND submission_type = ? AND status = ?", (guild_id, submission_type, status))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def get_total_reviewed_count(guild_id, submission_type='regular'):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(DISTINCT submission_id) FROM music_submissions WHERE guild_id = ? AND submission_type = ? AND status = 'reviewed'", (guild_id, submission_type))
        result = await cursor.fetchone()
        return result[0] if result else 0
        
async def get_next_submission(guild_id, submission_type='regular'):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT submission_id, user_id, track_url FROM music_submissions WHERE guild_id = ? AND status = 'pending' AND submission_type = ? ORDER BY submitted_at ASC LIMIT 1", (guild_id, submission_type))
        return await cursor.fetchone()

async def update_submission_status(submission_id, status, reviewer_id=None):
    conn = await get_db_connection()
    await conn.execute("UPDATE music_submissions SET status = ?, reviewer_id = ? WHERE submission_id = ?", (status, reviewer_id, submission_id))
    await conn.commit()

async def clear_session_submissions(guild_id, submission_type='regular'):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM music_submissions WHERE guild_id = ? AND submission_type = ? AND status != 'reviewed'", (guild_id, submission_type))
    await conn.commit()

async def prioritize_submission(submission_id):
    conn = await get_db_connection()
    await conn.execute("UPDATE music_submissions SET submitted_at = '1970-01-01 00:00:00' WHERE submission_id = ?", (submission_id,))
    await conn.commit()

# --- KOTH FUNCTIONS ---
async def get_koth_leaderboard(guild_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT user_id, points, wins, losses, streak FROM koth_leaderboard WHERE guild_id = ? ORDER BY points DESC", (guild_id,))
        return await cursor.fetchall()

async def update_koth_battle_results(guild_id, winner_id, loser_id):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO koth_leaderboard (guild_id, user_id, points, wins, losses, streak) VALUES (?, ?, 1, 1, 0, 1) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + 1, wins = wins + 1, streak = streak + 1", (guild_id, winner_id))
    await conn.execute("INSERT INTO koth_leaderboard (guild_id, user_id, points, wins, losses, streak) VALUES (?, ?, 0, 0, 1, 0) ON CONFLICT(guild_id, user_id) DO UPDATE SET losses = losses + 1, streak = 0", (guild_id, loser_id))
    await conn.commit()

async def reset_koth_leaderboard(guild_id):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM koth_leaderboard WHERE guild_id = ?", (guild_id,))
    await conn.commit()

# --- BAD WORD FILTER FUNCTIONS ---
async def add_bad_word(guild_id, word):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO bad_words (guild_id, word) VALUES (?, ?)", (guild_id, word.lower()))
    await conn.commit()
    return True

async def remove_bad_word(guild_id, word):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("DELETE FROM bad_words WHERE guild_id = ? AND word = ?", (guild_id, word.lower()))
        await conn.commit()
        return cursor.rowcount > 0

async def get_bad_words(guild_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT word FROM bad_words WHERE guild_id = ?", (guild_id,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

# --- RANKING SYSTEM FUNCTIONS ---
async def update_user_xp(guild_id, user_id, xp_to_add):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO ranking (guild_id, user_id, xp) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = xp + excluded.xp", (guild_id, user_id, xp_to_add))
    await conn.commit()

async def get_user_rank(guild_id, user_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT xp FROM ranking WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        result = await cursor.fetchone()
        if not result: return None, None
        user_xp = result[0]
        await cursor.execute("SELECT COUNT(*) FROM ranking WHERE guild_id = ? AND xp > ?", (guild_id, user_xp))
        rank_result = await cursor.fetchone()
        rank = rank_result[0] + 1
        return user_xp, rank

async def get_leaderboard(guild_id, limit=10):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT user_id, xp FROM ranking WHERE guild_id = ? ORDER BY xp DESC LIMIT ?", (guild_id, limit))
        return await cursor.fetchall()

# --- OAUTH & GMAIL VERIFICATION FUNCTIONS ---
async def create_verification_link(state, guild_id, user_id, server_name, bot_avatar_url):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO verification_links (state, guild_id, user_id, server_name, bot_avatar_url) VALUES (?, ?, ?, ?, ?)", (state, guild_id, user_id, server_name, bot_avatar_url))
    await conn.commit()

async def complete_verification(state, account_name):
    conn = await get_db_connection()
    await conn.execute("UPDATE verification_links SET status = 'verified', verified_account = ? WHERE state = ?", (account_name, state))
    await conn.commit()

async def get_completed_verifications():
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT state, guild_id, user_id FROM verification_links WHERE status = 'verified'")
        return await cursor.fetchall()

async def delete_verification_link(state):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM verification_links WHERE state = ?", (state,))
    await conn.commit()

async def store_gmail_code(guild_id, user_id, code):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO gmail_verification (guild_id, user_id, verification_code) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET verification_code = excluded.verification_code, created_at = CURRENT_TIMESTAMP", (guild_id, user_id, code))
    await conn.commit()

async def get_gmail_code(guild_id, user_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT verification_code FROM gmail_verification WHERE guild_id = ? AND user_id = ? AND created_at > datetime('now', '-10 minutes')", (guild_id, user_id))
        result = await cursor.fetchone()
        return result[0] if result else None

async def delete_gmail_code(guild_id, user_id):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM gmail_verification WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    await conn.commit()