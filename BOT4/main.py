import os
import random
import asyncio
import logging
import pytz
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
    Application
)

# ================= LOGGING SETUP =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= CONFIGURATION =================
BOT4_TOKEN = os.getenv("BOT4_TOKEN", "")
MONGO_URI = os.getenv("MONGO_URI", "")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", ""))
PORT = int(os.getenv("PORT", "8080"))

# Multiple Admins and Force Subscribe Channels
ADMIN_IDS = [5298223577,2120581499]  # Add more admin IDs here
FSUB_CHANNEL_IDS = [-1002114224580,-1003627956964,-1003680807119,-1002440964326,-1003838483796]  # Add more channel IDs here

# Welcome Image
WELCOME_IMAGE = "https://raw.githubusercontent.com/DevXShiva/Save-Restricted-Bot/refs/heads/main/logo.png"
DEV_CREDITS = "\n\n\n\n👨‍💻 *Developed by:* [VoidXdevs](https://t.me/devXvoid)"

# ================= MONGODB SETUP =================
client = AsyncIOMotorClient(MONGO_URI)
db = client.shein_coupon_bot

# Collections
users_collection = db.users
coupons_collection = db.coupons
backup_logs_collection = db.backup_logs

# Global Memory
user_captcha = {}
pending_referrals = {}
processing_users = []
link_cache = {}

# IST Time Helper
def get_ist_time():
    IST = pytz.timezone('Asia/Kolkata')
    return datetime.now(IST).strftime("%Y-%m-%d %I:%M:%S %p")

# ================= MONGODB FUNCTIONS =================
async def get_user_data(user_id: int):
    user = await users_collection.find_one({"user_id": user_id})
    return user

async def update_user_balance(user_id: int, amount: int):
    await users_collection.update_one(
        {"user_id": user_id},
        {"$inc": {"balance": amount}},
        upsert=True
    )

async def register_user_mongo(user_id: int, first_name: str, referrer_id: Optional[int] = None):
    existing_user = await get_user_data(user_id)
    if not existing_user:
        user_data = {
            "user_id": user_id,
            "first_name": first_name,
            "balance": 0,
            "referrer_id": referrer_id,
            "joined_date": datetime.now().strftime("%Y-%m-%d"),
            "created_at": datetime.now()
        }
        await users_collection.insert_one(user_data)
        return True
    return False

async def get_stock_count(category: str):
    pipeline = [
        {"$match": {"category": category, "status": "unused"}},
        {"$count": "count"}
    ]
    result = await coupons_collection.aggregate(pipeline).to_list(length=1)
    return result[0]["count"] if result else 0

async def add_coupons_mongo(category: str, codes_list: List[str]):
    added = 0
    for code in codes_list:
        if code:
            existing = await coupons_collection.find_one({
                "category": category,
                "code": code
            })
            if not existing:
                coupon_data = {
                    "category": category,
                    "code": code,
                    "status": "unused",
                    "used_by": None,
                    "used_at": None,
                    "created_at": datetime.now()
                }
                await coupons_collection.insert_one(coupon_data)
                added += 1
    return added

async def redeem_coupon_mongo(category: str, user_id: int, cost: int):
    # Find an unused coupon
    coupon = await coupons_collection.find_one_and_update(
        {"category": category, "status": "unused"},
        {"$set": {"status": "used", "used_by": user_id, "used_at": datetime.now()}},
        sort=[("created_at", 1)]  # Get oldest first
    )
    
    if coupon:
        # Update user balance
        await users_collection.update_one(
            {"user_id": user_id},
            {"$inc": {"balance": -cost}}
        )
        return coupon["code"]
    return None

# ================= BACKUP SYSTEM =================
async def backup_job():
    while True:
        await asyncio.sleep(7200)  # Every 2 hours
        try:
            # Export collections to JSON-like format
            users = await users_collection.find().to_list(length=None)
            coupons = await coupons_collection.find().to_list(length=None)
            
            backup_data = {
                "timestamp": datetime.now(),
                "ist_time": get_ist_time(),
                "users_count": len(users),
                "coupons_count": len(coupons),
                "users_sample": users[:10] if users else [],
                "coupons_sample": coupons[:10] if coupons else []
            }
            
            # Save to backup collection
            await backup_logs_collection.insert_one(backup_data)
            
            # Send log to channel
            from telegram.constants import ParseMode
            app = Application.builder().token(BOT4_TOKEN).build()
            await app.initialize()
            
            backup_msg = (
                "🗂 **System Auto Backup (MongoDB)**\n"
                f"📅 Time (IST): {get_ist_time()}\n"
                f"👥 Total Users: {len(users)}\n"
                f"🎟 Total Coupons: {len(coupons)}\n"
                f"🤖 Bot: @{(await app.bot.get_me()).username}"
            )
            
            await app.bot.send_message(
                LOG_CHANNEL_ID, 
                backup_msg, 
                parse_mode=ParseMode.MARKDOWN
            )
            
            await app.shutdown()
            print(f"✅ Backup completed at {get_ist_time()}")
            
        except Exception as e:
            print(f"❌ Backup Failed: {e}")

# ================= HELPER FUNCTIONS =================
async def get_channel_invite_link(chat_id: int, app: Application):
    if chat_id in link_cache:
        return link_cache[chat_id]
    
    try:
        chat = await app.bot.get_chat(chat_id)
        if chat.invite_link:
            link = chat.invite_link
        else:
            link = await app.bot.export_chat_invite_link(chat_id)
        
        link_cache[chat_id] = link
        return link
    except Exception as e:
        logger.error(f"Error fetching link for {chat_id}: {e}")
        return f"https://t.me/c/{str(chat_id)[4:]}" if str(chat_id).startswith("-100") else "https://t.me/"

async def is_joined(user_id: int, app: Application):
    try:
        for chat_id in FSUB_CHANNEL_IDS:
            member = await app.bot.get_chat_member(chat_id, user_id)
            if member.status not in ['creator', 'administrator', 'member']:
                return False
        return True
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        return False

async def send_log(log_type: str, user_id: int, first_name: str, app: Application, details: str = ""):
    try:
        user_link = f"[{first_name}](tg://user?id={user_id})"
        time_now = get_ist_time()
        
        if log_type == "new_user":
            msg = (
                "#NewUser Joined 🚀\n\n"
                f"👤 Name: {user_link}\n"
                f"🆔 ID: `{user_id}`\n"
                f"🕒 Time: `{time_now}`\n"
                f"🤖 Bot: @{(await app.bot.get_me()).username}"
            )
        elif log_type == "withdraw":
            msg = (
                "#NewWithdraw Request 💸\n\n"
                f"👤 User: {user_link}\n"
                f"🆔 ID: `{user_id}`\n"
                f"{details}\n"
                f"🕒 Time: `{time_now}`\n"
                f"🤖 Bot: @{(await app.bot.get_me()).username}"
            )
        
        await app.bot.send_message(
            LOG_CHANNEL_ID,
            msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Log Error: {e}")

# ================= COMMAND HANDLERS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    # Check Force Subscribe
    if not await is_joined(user_id, context.application):
        keyboard = []
        for i, chat_id in enumerate(FSUB_CHANNEL_IDS, 1):
            link = await get_channel_invite_link(chat_id, context.application)
            keyboard.append([InlineKeyboardButton(f"📢 Join Channel {i}", url=link)])
        
        keyboard.append([InlineKeyboardButton("✅ I've Joined All", callback_data="check_join")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⚠️ **Action Required**\n\nTo use this bot, you must join all our channels.",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    # Check if existing user
    user_data = await get_user_data(user_id)
    if user_data:
        keyboard = [
            ["🔗 My Link", "💎 Balance"],
            ["💸 Withdraw", "🎟 Coupon Stock"]
        ]
        reply_markup = {
            "keyboard": keyboard,
            "resize_keyboard": True,
            "one_time_keyboard": False
        }
        await update.message.reply_text(
            "👇 Select option",
            reply_markup=reply_markup
        )
        return

    # Referral Logic
    args = context.args
    if args:
        referrer = args[0]
        if referrer.isdigit() and int(referrer) != user_id:
            pending_referrals[user_id] = int(referrer)

    # Captcha
    n1, n2 = random.randint(1, 9), random.randint(1, 9)
    user_captcha[user_id] = n1 + n2
    
    await update.message.reply_text(
        f"🔒 *CAPTCHA*\n{n1} + {n2} = ??\n\nSend answer to verify.",
        parse_mode="Markdown"
    )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if await is_joined(user_id, context.application):
        await query.message.delete()
        await start_command(update, context)
    else:
        await query.answer("❌ You haven't joined all channels!", show_alert=True)

async def handle_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    if user_id not in user_captcha:
        return
    
    try:
        answer = int(update.message.text)
        if answer == user_captcha[user_id]:
            del user_captcha[user_id]
            await update.message.reply_text("✅ Correct answer!")
            
            # Register user
            referrer_id = pending_referrals.get(user_id)
            is_new = await register_user_mongo(user_id, first_name, referrer_id)
            
            if is_new:
                await send_log("new_user", user_id, first_name, context.application)
                
                # Give referral reward
                if referrer_id:
                    await update_user_balance(referrer_id, 1)
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            "🎉 New Referral! You got +1 💎 Diamond."
                        )
                    except:
                        pass
            
            # Welcome message
            caption = (
                "👋 Welcome to SHEIN Refer Coupon Bot!\n"
                "Invite friends & earn rewards.\n"
                f"{DEV_CREDITS}"
            )
            
            await update.message.reply_photo(
                photo=WELCOME_IMAGE,
                caption=caption,
                parse_mode="Markdown"
            )
            
            # Show main menu
            keyboard = [
                ["🔗 My Link", "💎 Balance"],
                ["💸 Withdraw", "🎟 Coupon Stock"]
            ]
            reply_markup = {
                "keyboard": keyboard,
                "resize_keyboard": True,
                "one_time_keyboard": False
            }
            await update.message.reply_text(
                "👇 Select option",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text("❌ Wrong answer. Try again.")
    except ValueError:
        await update.message.reply_text("Please send a number.")

async def handle_my_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={user_id}"
    
    text = (
        "🔗 *Your Referral Link*\n"
        f"`{link}`\n"
        f"Get 1 💎 for every verified join.\n{DEV_CREDITS}"
    )
    
    keyboard = [[InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={link}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    balance = user_data["balance"] if user_data else 0
    
    await update.message.reply_text(
        f"💎 *Balance*\nTotal: {balance}.0 💎",
        parse_mode="Markdown"
    )

async def handle_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stock_500 = await get_stock_count("500")
    stock_1000 = await get_stock_count("1000")
    stock_2000 = await get_stock_count("2000")
    stock_4000 = await get_stock_count("4000")
    
    text = (
        "🎟 *Live Coupon Stock*\n\n"
        f"📦 500: {stock_500}\n"
        f"📦 1000: {stock_1000}\n"
        f"📦 2000: {stock_2000}\n"
        f"📦 4000: {stock_4000}"
    )
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    balance = user_data["balance"] if user_data else 0
    
    text = f"💸 *Withdraw*\nTotal Balance: {balance}.0 💎\nSelect amount to withdraw:"
    
    keyboard = [
        [
            InlineKeyboardButton("1 💎 500 🎟", callback_data="redeem_500_1"),
            InlineKeyboardButton("4 💎 1000 🎟", callback_data="redeem_1000_4")
        ],
        [
            InlineKeyboardButton("15 💎 2000 🎟", callback_data="redeem_2000_15"),
            InlineKeyboardButton("25 💎 4000 🎟", callback_data="redeem_4000_25")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    first_name = query.from_user.first_name
    
    if user_id in processing_users:
        await query.answer("⏳ Processing... Please wait.", show_alert=True)
        return
    
    processing_users.append(user_id)
    
    try:
        data = query.data.split("_")
        category = data[1]
        cost = int(data[2])
        
        # Check balance
        user_data = await get_user_data(user_id)
        if not user_data or user_data["balance"] < cost:
            await query.answer("❌ Not enough diamonds!", show_alert=True)
            return
        
        # Check stock
        stock_count = await get_stock_count(category)
        if stock_count == 0:
            await query.answer("⚠️ Out of Stock! Contact Admin.", show_alert=True)
            return
        
        # Redeem coupon
        coupon_code = await redeem_coupon_mongo(category, user_id, cost)
        
        if coupon_code:
            # Send coupon to user
            msg = (
                "✅ *Redemption Successful!*\n\n"
                f"🎟 Category: {category} Coupons\n"
                f"🔐 Code: `{coupon_code}`\n\n"
                "⚠️ Copy and use it immediately!"
                f"{DEV_CREDITS}"
            )
            await query.message.reply_text(msg, parse_mode="Markdown")
            
            # Log
            details = f"🎟 Type: {category} Coupon\n💎 Cost: {cost} Diamonds"
            await send_log("withdraw", user_id, first_name, context.application, details)
        else:
            await query.answer("❌ Error redeeming coupon!", show_alert=True)
            
    except Exception as e:
        logger.error(f"Redeem error: {e}")
        await query.message.reply_text("❌ Error occurred. Contact Admin.")
    finally:
        if user_id in processing_users:
            processing_users.remove(user_id)

# ================= ADMIN HANDLERS =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    
    keyboard = [
        [
            InlineKeyboardButton("➕ Add 500 Coupons", callback_data="admin_add_500"),
            InlineKeyboardButton("➕ Add 1000 Coupons", callback_data="admin_add_1000")
        ],
        [
            InlineKeyboardButton("➕ Add 2000 Coupons", callback_data="admin_add_2000"),
            InlineKeyboardButton("➕ Add 4000 Coupons", callback_data="admin_add_4000")
        ],
        [
            InlineKeyboardButton("📊 View Stats", callback_data="admin_stats"),
            InlineKeyboardButton("🔄 Reload Data", callback_data="admin_reload")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👨‍💻 **Admin Panel**\nSelect option:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def admin_add_coupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Unauthorized!", show_alert=True)
        return
    
    category = query.data.split("_")[2]  # admin_add_500 -> 500
    
    # Store category in context for next step
    context.user_data["admin_category"] = category
    
    await query.message.reply_text(
        f"Send codes for **{category}** category (Space separated or New lines).",
        parse_mode="Markdown"
    )
    
    return "WAITING_CODES"

async def admin_receive_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    category = context.user_data.get("admin_category")
    if not category:
        return ConversationHandler.END
    
    raw_text = update.message.text
    if not raw_text:
        await update.message.reply_text("❌ No codes provided!")
        return ConversationHandler.END
    
    codes = raw_text.replace('\n', ' ').split(' ')
    codes = [c.strip() for c in codes if c.strip()]
    
    added_count = await add_coupons_mongo(category, codes)
    
    await update.message.reply_text(
        f"✅ Successfully added **{added_count}** coupons to **{category}** category.",
        parse_mode="Markdown"
    )
    
    return ConversationHandler.END

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Unauthorized!", show_alert=True)
        return
    
    # Get stats
    users_count = await users_collection.count_documents({})
    coupons_count = await coupons_collection.count_documents({})
    
    stock_500 = await get_stock_count("500")
    stock_1000 = await get_stock_count("1000")
    stock_2000 = await get_stock_count("2000")
    stock_4000 = await get_stock_count("4000")
    
    total_balance = await users_collection.aggregate([
        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
    ]).to_list(length=1)
    
    total_balance = total_balance[0]["total"] if total_balance else 0
    
    # Admin list
    admin_list = "\n".join([f"  • `{admin_id}`" for admin_id in ADMIN_IDS])
    
    # FSub channels list
    fsub_list = "\n".join([f"  • `{channel_id}`" for channel_id in FSUB_CHANNEL_IDS])
    
    stats_text = (
        "📊 **Bot Statistics**\n\n"
        f"👥 Total Users: `{users_count}`\n"
        f"💎 Total Diamonds: `{total_balance}`\n\n"
        "🎟 **Coupon Stock**\n"
        f"  500: `{stock_500}`\n"
        f"  1000: `{stock_1000}`\n"
        f"  2000: `{stock_2000}`\n"
        f"  4000: `{stock_4000}`\n\n"
        "👑 **Admins**\n"
        f"{admin_list}\n\n"
        "📢 **Force Subscribe Channels**\n"
        f"{fsub_list}"
    )
    
    await query.message.reply_text(stats_text, parse_mode="Markdown")

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# ================= WEB SERVER =================
async def web_start():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot Alive"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', PORT).start()
    logger.info(f"🌐 Web server started on port {PORT}")

# ================= MAIN BOT SETUP =================
async def post_init(app: Application):
    """Run after bot is initialized"""
    await web_start()
    
    try:
        await client.admin.command('ping')
        logger.info("✅ Connected to MongoDB")
        
        bot_username = (await app.bot.get_me()).username
        await app.bot.send_message(
            LOG_CHANNEL_ID,
            f"🟢 **Bot Restarted & Online**\n🤖 Bot: @{bot_username}\n📅 Time: {get_ist_time()}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"MongoDB connection error: {e}")

# Export the start function for runner.py
async def start_bot4():
    """Start Bot 4 with python-telegram-bot"""
    # Build application
    app = ApplicationBuilder() \
        .token(BOT4_TOKEN) \
        .post_init(post_init) \
        .build()
    
    # Add conversation handlers for admin
    admin_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_add_coupons, pattern="^admin_add_500$"),
            CallbackQueryHandler(admin_add_coupons, pattern="^admin_add_1000$"),
            CallbackQueryHandler(admin_add_coupons, pattern="^admin_add_2000$"),
            CallbackQueryHandler(admin_add_coupons, pattern="^admin_add_4000$")
        ],
        states={
            "WAITING_CODES": [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_codes)]
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)]
    )
    
    # Add all handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(handle_redeem, pattern="^redeem_"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: u.answer("✅ Data reloaded!", show_alert=True), pattern="^admin_reload$"))
    app.add_handler(admin_conv_handler)
    
    # Add message handlers
    app.add_handler(MessageHandler(filters.Regex("^🔗 My Link$"), handle_my_link))
    app.add_handler(MessageHandler(filters.Regex("^💎 Balance$"), handle_balance))
    app.add_handler(MessageHandler(filters.Regex("^🎟 Coupon Stock$"), handle_stock))
    app.add_handler(MessageHandler(filters.Regex("^💸 Withdraw$"), handle_withdraw))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_captcha))
    
    # Start backup job
    asyncio.create_task(backup_job())
    
    # Initialize and start
    await app.initialize()
    await app.start()
    
    logger.info("🤖 Bot 4 Started Successfully")
    logger.info(f"👑 Admins: {len(ADMIN_IDS)} users")
    logger.info(f"📢 Force Sub Channels: {len(FSUB_CHANNEL_IDS)} channels")
    
    # Start polling
    await app.updater.start_polling()
    
    # Run forever
    await asyncio.Event().wait()

    # ================= EXPORT FOR RUNNER =================
    __all__ = ['start_bot4']





