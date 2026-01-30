import os
import logging
import requests
import re
import asyncio
import tempfile
from telegram import Update, ChatMember, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================= LOGGING =================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
API_BASE_URL = "https://lakshitop.vercel.app/?url="
DEVELOPER = "𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘"
DEVELOPER_LINK = "https://t.me/theprofessorreport_bot"

DIVIDER = "◈───────────────────◈"
FOOTER = f"\n\n───\n📱 **Developed By [{DEVELOPER}]({DEVELOPER_LINK})**"

FORCE_SUB_CHANNELS = []  # optional

# ================= HELPERS =================
def extract_instagram_url(text: str):
    patterns = [
        r"(https?://(?:www\.)?instagram\.com/(?:p|reel|reels|stories)/[^\s]+)",
        r"(https?://(?:www\.)?instagr\.am/(?:p|reel|reels|stories)/[^\s]+)",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None


def download_from_api(insta_url: str):
    try:
        r = requests.get(f"{API_BASE_URL}{insta_url}", timeout=30)
        if r.status_code != 200:
            return None, "API Error"

        data = r.json()
        download_url = data.get("result", {}).get("download_url")
        if not download_url:
            return None, "No download URL"

        vr = requests.get(download_url, stream=True, timeout=60)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as f:
            for chunk in vr.iter_content(8192):
                f.write(chunk)
            return f.name, "OK"
    except Exception as e:
        return None, str(e)


async def update_progress_bar(message, step, total=5):
    bar = "█" * step + "░" * (total - step)
    text = (
        f"{DIVIDER}\n"
        f"🔄 **DOWNLOAD IN PROGRESS**\n"
        f"{DIVIDER}\n\n"
        f"┃{bar}┃\n"
        f"📋 Step {step}/{total}\n"
        f"{FOOTER}"
    )
    try:
        await message.edit_text(text, parse_mode="Markdown")
    except:
        pass


# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{DIVIDER}\n"
        f"👋 **WELCOME**\n"
        f"{DIVIDER}\n\n"
        f"🎬 Send any Instagram video / reel link\n"
        f"{FOOTER}",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    url = extract_instagram_url(update.message.text)
    if not url:
        await update.message.reply_text("❌ Invalid Instagram URL")
        return

    msg = await update.message.reply_text("🔄 Starting download…")
    await update_progress_bar(msg, 1)

    path, status = await asyncio.to_thread(download_from_api, url)
    if not path:
        await msg.edit_text(f"❌ Failed: {status}")
        return

    await update_progress_bar(msg, 5)
    await msg.delete()

    with open(path, "rb") as f:
        await update.message.reply_video(
            video=f,
            caption=f"✅ Downloaded\n{FOOTER}",
            parse_mode="Markdown",
            supports_streaming=True,
        )

    os.unlink(path)


# ================= ENTRY POINT =================
async def start_bot1():
    app = (
        Application.builder()
        .token(os.getenv("BOT1_TOKEN"))
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.bot.initialize()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
