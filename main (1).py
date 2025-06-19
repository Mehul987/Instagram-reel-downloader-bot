import os
import logging
import sqlite3
import asyncio
import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

# --- Configuration and Setup ---

# Set up logging to see errors and bot activity
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load sensitive info from environment variables (Secrets)
try:
    TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
    OWNER_ID = int(os.environ['OWNER_ID'])
except KeyError:
    logger.error("Error: Make sure TELEGRAM_BOT_TOKEN and OWNER_ID are set in your environment variables.")
    exit()

DB_NAME = 'users.db'

# --- Database Functions ---

def setup_database():
    """Creates the database and the users table if they don't exist."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Create table with user_id as a unique primary key
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("Database setup complete.")

def add_user_to_db(user_id: int):
    """Adds a new user to the database. Ignores if the user already exists."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # INSERT OR IGNORE prevents errors if the user_id is already in the table
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def get_all_user_ids():
    """Retrieves a list of all user IDs from the database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    # The result is a list of tuples, so we extract the first element of each tuple
    user_ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return user_ids

def get_total_user_count():
    """Gets the total number of users in the database."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command, welcomes the user, and saves their ID."""
    user = update.effective_user
    add_user_to_db(user.id)
    await update.message.reply_html(
        f"👋 नमस्ते, {user.mention_html()}!\n\n"
        "मैं एक इंस्टाग्राम डाउनलोडर बॉट हूँ। मुझे कोई भी रील, वीडियो या फोटो का लिंक भेजें और मैं उसे आपके लिए डाउनलोड कर दूँगा।\n\n"
        "सहायता के लिए /help कमांड का उपयोग करें।"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /help command."""
    await update.message.reply_text(
        "बस मुझे किसी भी पब्लिक इंस्टाग्राम रील, वीडियो या फोटो का लिंक भेजें।\n\n"
        "👑 **मालिक के लिए कमांड:**\n"
        "/stats - कुल यूजर्स की संख्या देखें।\n"
        "/broadcast <message> - सभी यूजर्स को संदेश भेजें।"
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only command to get bot usage statistics."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ आप इस कमांड का उपयोग करने के लिए अधिकृत नहीं हैं।")
        return

    total_users = get_total_user_count()
    await update.message.reply_text(f"📊 **बॉट आँकड़े**\n\nकुल यूनिक यूजर्स: {total_users}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only command to broadcast a message to all users."""
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ आप इस कमांड का उपयोग करने के लिए अधिकृत नहीं हैं।")
        return

    # The message to broadcast is the text after the /broadcast command
    message_to_broadcast = " ".join(context.args)
    if not message_to_broadcast:
        await update.message.reply_text("⚠️ उपयोग: /broadcast <आपका संदेश यहाँ>")
        return

    user_ids = get_all_user_ids()
    await update.message.reply_text(f"📢 ब्रॉडकास्ट शुरू हो रहा है... {len(user_ids)} यूजर्स को संदेश भेजा जाएगा।")

    success_count = 0
    fail_count = 0
    for user_id in user_ids:
        try:
            await context.bot.send_message(chat_id=user_id, text=message_to_broadcast, parse_mode=ParseMode.HTML)
            success_count += 1
        except Forbidden:
            # User has blocked the bot
            fail_count += 1
            logger.warning(f"Failed to send message to {user_id}: User blocked the bot.")
        except BadRequest as e:
            # Other errors, e.g., chat not found
            fail_count += 1
            logger.error(f"Failed to send message to {user_id}: {e}")
        
        # A small delay to avoid hitting Telegram's rate limits
        await asyncio.sleep(0.1)

    await update.message.reply_text(
        f"✅ ब्रॉडकास्ट पूरा हुआ!\n\n"
        f"सफलतापूर्वक भेजा गया: {success_count}\n"
        f"विफल रहा: {fail_count}"
    )

# --- Core Functionality (Downloader) ---

async def download_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming messages, downloads content from Instagram links."""
    url = update.message.text
    if "instagram.com" not in url:
        # Silently ignore messages that are not links to avoid spamming
        return

    processing_msg = await update.message.reply_text("🔄 आपका लिंक प्रोसेस हो रहा है, कृपया प्रतीक्षा करें...")

    file_path = None
    try:
        ydl_opts = {
            'outtmpl': f'downloads/%(id)s.%(ext)s',
            'format': 'best',
            'quiet': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await processing_msg.edit_text("📥 कंटेंट डाउनलोड हो रहा है...")
            info_dict = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info_dict)

        await processing_msg.edit_text("📤 टेलीग्राम पर अपलोड किया जा रहा है...")

        # Determine if it's a video or photo based on extension
        file_ext = os.path.splitext(file_path)[1].lower()
        caption_text = "✨ लीजिए, आपका कंटेंट तैयार है!\n\nद्वारा: @YourBotUsername" # अपना यूजरनेम यहाँ डालें

        if file_ext in ['.mp4', '.mov', '.mkv', '.webm']:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=open(file_path, 'rb'),
                caption=caption_text,
                supports_streaming=True
            )
        elif file_ext in ['.jpg', '.jpeg', '.png', '.webp']:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open(file_path, 'rb'),
                caption=caption_text
            )
        else:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=open(file_path, 'rb'),
                caption=caption_text
            )

        # Delete the processing message after successful upload
        await processing_msg.delete()

    except Exception as e:
        logger.error(f"Error processing link {url}: {e}")
        error_message = (
            "❌ एक त्रुटि हुई।\n\n"
            "यह हो सकता है क्योंकि:\n"
            "- यह एक प्राइवेट अकाउंट है।\n"
            "- लिंक गलत या हटा दिया गया है।\n"
            "- यह कंटेंट इस देश में उपलब्ध नहीं है।"
        )
        await processing_msg.edit_text(error_message)
    
    finally:
        # Clean up the downloaded file from the server
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Cleaned up file: {file_path}")

# --- Main Bot Execution ---

def main() -> None:
    """Start the bot."""
    # Create the 'downloads' directory if it doesn't exist
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
        
    # Set up the database
    setup_database()
    
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))

    # Register message handler for Instagram links
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_content))

    # Start the Bot
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()