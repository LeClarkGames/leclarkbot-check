from quart import Quart, request, render_template
import os
import httpx
import aiosqlite
from dotenv import load_dotenv

load_dotenv()

app = Quart(__name__)

# --- CONFIGURATION ---
# Load credentials and URIs from the environment file for security and flexibility.
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")

# Dynamically build the redirect URIs from the base URL
TWITCH_REDIRECT_URI = f"{APP_BASE_URL}/callback/twitch"
YOUTUBE_REDIRECT_URI = f"{APP_BASE_URL}/callback/youtube"

DB_FILE = "bot_database.db"

# --- HELPER FUNCTION ---
async def get_verification_data(state: str):
    """Gets the server name and bot avatar for the success page."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            async with conn.execute("SELECT server_name, bot_avatar_url FROM verification_links WHERE state = ?", (state,)) as cursor:
                data = await cursor.fetchone()
                if data:
                    return {"server_name": data[0], "bot_avatar_url": data[1]}
    except Exception as e:
        print(f"Error fetching verification data: {e}")
    # Fallback in case of an error
    return {"server_name": "your Discord server", "bot_avatar_url": ""}

# --- WEB ROUTES ---
@app.route('/')
async def home():
    """A simple homepage to confirm the server is running."""
    return "Web server for LeClark Bot is active."

@app.route('/callback/twitch')
async def callback_twitch():
    """Handles the OAuth2 callback from Twitch."""
    auth_code = request.args.get('code')
    state = request.args.get('state')
    if not auth_code or not state:
        return "Error: Missing authorization code or state.", 400

    token_url = "https://id.twitch.tv/oauth2/token"
    token_params = {
        "client_id": TWITCH_CLIENT_ID, "client_secret": TWITCH_CLIENT_SECRET,
        "code": auth_code, "grant_type": "authorization_code", "redirect_uri": TWITCH_REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, params=token_params)
    token_data = response.json()
    if 'access_token' not in token_data:
        return "Error: Could not retrieve access token from Twitch.", 400
    
    access_token = token_data['access_token']
    user_url = "https://api.twitch.tv/helix/users"
    headers = {"Authorization": f"Bearer {access_token}", "Client-Id": TWITCH_CLIENT_ID}
    async with httpx.AsyncClient() as client:
        user_response = await client.get(user_url, headers=headers)
    user_data = user_response.json()
    if not user_data.get('data'):
        return "Error: Could not retrieve user data from Twitch.", 400
    account_name = user_data['data'][0]['login']

    try:
        template_data = await get_verification_data(state)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("UPDATE verification_links SET status = 'verified', verified_account = ? WHERE state = ? AND status = 'pending'", (account_name, state))
            await conn.commit()
        return await render_template("success.html", account_name=account_name, **template_data)
    except Exception as e:
        print(f"Database error during Twitch callback: {e}")
        return "An internal server error occurred.", 500

@app.route('/callback/youtube')
async def callback_youtube():
    """Handles the OAuth2 callback from Google/YouTube."""
    auth_code = request.args.get('code')
    state = request.args.get('state')
    if not auth_code or not state:
        return "Error: Missing authorization code or state.", 400

    token_url = "https://oauth2.googleapis.com/token"
    token_params = {
        "client_id": YOUTUBE_CLIENT_ID, "client_secret": YOUTUBE_CLIENT_SECRET,
        "code": auth_code, "grant_type": "authorization_code", "redirect_uri": YOUTUBE_REDIRECT_URI,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=token_params)
    token_data = response.json()
    if 'access_token' not in token_data:
        return "Error: Could not retrieve access token from Google.", 400
    
    access_token = token_data['access_token']
    user_url = "https://www.googleapis.com/oauth2/v2/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        user_response = await client.get(user_url, headers=headers)
    user_data = user_response.json()
    if 'name' not in user_data:
        return "Error: Could not retrieve user data from Google.", 400
    account_name = user_data['name']
    
    try:
        template_data = await get_verification_data(state)
        # --- FIXED THE TYPO HERE (was DB_File) ---
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("UPDATE verification_links SET status = 'verified', verified_account = ? WHERE state = ? AND status = 'pending'", (account_name, state))
            await conn.commit()
        return await render_template("success.html", account_name=account_name, **template_data)
    except Exception as e:
        print(f"Database error during YouTube callback: {e}")
        return "An internal server error occurred.", 500

