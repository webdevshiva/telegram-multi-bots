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
ADMIN_IDS = [5298223577, 2120581499]  # Add more admin IDs here
FSUB_CHANNEL_IDS = [-1002114224580, -1003627956964, -1003680807119, -1002440964326, -1003541438177]  # Add more channel IDs here

# Welcome Image
WELCOME_IMAGE = ""
DEV_CREDITS = "\n\n\n\n👨‍💻 *Developed by:* [VoidXdevs](https://t.me/devXvoid)"

# ================= MONGODB SETUP =================
client = AsyncIOMotorClient(MONGO_URI, maxPoolSize=50, minPoolSize=10)
db = client.shein_coupon_bot

# Collections
users_collection = db.users
coupons_collection = db.coupons
backup_logs_collection = db.backup_logs

# Create Indexes (FAST QUERIES)
async def create_indexes():
    await users_collection.create_index("user_id", unique=True)
    await coupons_collection.create_index([("category", 1), ("status", 1), ("created_at", 1)])
    await coupons_collection.create_index([("code", 1), ("category", 1)], unique=True)
    logger.info("✅ Database indexes created")

# Caching for faster access
user_cache = {}
link_cache = {}
stock_cache = {}
CACHE_TTL = 300  # 5 minutes

# Global Memory
user_captcha = {}
pending_referrals = {}
processing_users = set()  # Using set for faster lookups

# IST Time Helper
def get_ist_time():
    IST = pytz.timezone('Asia/Kolkata')
    return datetime.now(IST).strftime("%Y-%m-%d %I:%M:%S %p")

# ================= MONGODB FUNCTIONS (OPTIMIZED) =================
async def get_user_data(user_id: int, use_cache=True):
    if use_cache and user_id in user_cache:
        return user_cache[user_id]
    
    user = await users_collection.find_one({"user_id": user_id})
    if user and use_cache:
        user_cache[user_id] = user
    return user

async def update_user_balance(user_id: int, amount: int):
    result = await users_collection.update_one(
        {"user_id": user_id},
        {"$inc": {"balance": amount}},
        upsert=True
    )
    
    # Clear cache
    if user_id in user_cache:
        del user_cache[user_id]
    
    return result.modified_count or result.upserted_id

async def register_user_mongo(user_id: int, first_name: str, referrer_id: Optional[int] = None):
    existing_user = await get_user_data(user_id, use_cache=False)
    if existing_user:
        return False
    
    user_data = {
        "user_id": user_id,
        "first_name": first_name,
        "balance": 0,
        "referrer_id": referrer_id,
        "referral_count": 0,  # NEW: Track referral count
        "joined_date": datetime.now().strftime("%Y-%m-%d"),
        "created_at": datetime.now()
    }
    await users_collection.insert_one(user_data)
    
    # Update cache
    user_cache[user_id] = user_data
    
    # Update referrer's referral count if exists
    if referrer_id:
        await users_collection.update_one(
            {"user_id": referrer_id},
            {"$inc": {"referral_count": 1}}
        )
        # Clear referrer cache
        if referrer_id in user_cache:
            del user_cache[referrer_id]
    
    return True

async def get_stock_count(category: str, use_cache=True):
    cache_key = f"stock_{category}"
    current_time = datetime.now().timestamp()
    
    if use_cache and cache_key in stock_cache:
        data, timestamp = stock_cache[cache_key]
        if current_time - timestamp < CACHE_TTL:
            return data
    
    count = await coupons_collection.count_documents({
        "category": category,
        "status": "unused"
    })
    
    if use_cache:
        stock_cache[cache_key] = (count, current_time)
    
    return count

async def add_coupons_mongo(category: str, codes_list: List[str]):
    # Clear stock cache for this category
    cache_key = f"stock_{category}"
    if cache_key in stock_cache:
        del stock_cache[cache_key]
    
    added = 0
    batch = []
    
    for code in codes_list:
        if code:
            batch.append({
                "category": category,
                "code": code.strip(),
                "status": "unused",
                "used_by": None,
                "used_at": None,
                "created_at": datetime.now()
            })
    
    if batch:
        try:
            result = await coupons_collection.insert_many(batch, ordered=False)
            added = len(result.inserted_ids)
        except Exception as e:
            # Handle duplicates
            logger.warning(f"Some coupons were duplicates: {e}")
            # Insert one by one for non-duplicates
            for coupon in batch:
                try:
                    await coupons_collection.insert_one(coupon)
                    added += 1
                except:
                    continue
    
    return added

async def redeem_coupon_mongo(category: str, user_id: int, cost: int):
    # Clear stock cache
    cache_key = f"stock_{category}"
    if cache_key in stock_cache:
        del stock_cache[cache_key]
    
    # Find and update in single atomic operation
    coupon = await coupons_collection.find_one_and_update(
        {"category": category, "status": "unused"},
        {"$set": {"status": "used", "used_by": user_id, "used_at": datetime.now()}},
        sort=[("created_at", 1)],
        return_document=True
    )
    
    if coupon:
        # Update user balance in single operation
        await users_collection.update_one(
            {"user_id": user_id},
            {"$inc": {"balance": -cost}}
        )
        
        # Clear user cache
        if user_id in user_cache:
            del user_cache[user_id]
        
        return coupon["code"]
    return None

# ================= BACKUP SYSTEM =================
async def backup_job():
    while True:
        await asyncio.sleep(7200)  # Every 2 hours
        try:
            # Get counts only (faster than fetching all documents)
            users_count = await users_collection.count_documents({})
            coupons_count = await coupons_collection.count_documents({})
            
            # Get recent samples only
            recent_users = await users_collection.find().sort("_id", -1).limit(5).to_list(length=5)
            recent_coupons = await coupons_collection.find().sort("_id", -1).limit(5).to_list(length=5)
            
            backup_data = {
                "timestamp": datetime.now(),
                "ist_time": get_ist_time(),
                "users_count": users_count,
                "coupons_count": coupons_count,
                "users_sample": recent_users,
                "coupons_sample": recent_coupons
            }
            
            # Save to backup collection
            await backup_logs_collection.insert_one(backup_data)
            
            # Send log to channel (no need to create new app instance)
            backup_msg = (
                "🗂 **System Auto Backup (MongoDB)**\n"
                f"📅 Time (IST): {get_ist_time()}\n"
                f"👥 Total Users: {users_count}\n"
                f"🎟 Total Coupons: {coupons_count}\n"
                f"🔄 Cache Stats: Users={len(user_cache)}, Stock={len(stock_cache)}"
            )
            
            # Use global app reference if available, otherwise skip
            logger.info(backup_msg)
            
        except Exception as e:
            logger.error(f"❌ Backup Failed: {e}")

# ================= HELPER FUNCTIONS (OPTIMIZED) =================
async def get_channel_invite_link(chat_id: int, app: Application):
    if chat_id in link_cache:
        return link_cache[chat_id]
    
    try:
        # Try to create invite link if not exists
        chat = await app.bot.get_chat(chat_id)
        link = f"https://t.me/c/{str(chat_id)[4:]}" if str(chat_id).startswith("-100") else f"https://t.me/{chat.username}"
        link_cache[chat_id] = link
        return link
    except Exception as e:
        logger.error(f"Error fetching link for {chat_id}: {e}")
        return f"https://t.me/c/{str(chat_id)[4:]}" if str(chat_id).startswith("-100") else "https://t.me/"

async def is_joined(user_id: int, app: Application):
    # Check cache first (assume user stays joined)
    cache_key = f"join_{user_id}"
    if cache_key in link_cache:
        return True
    
    try:
        # Check all channels concurrently for faster verification
        tasks = []
        for chat_id in FSUB_CHANNEL_IDS:
            tasks.append(app.bot.get_chat_member(chat_id, user_id))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception) or result.status not in ['creator', 'administrator', 'member']:
                return False
        
        # Cache positive result
        link_cache[cache_key] = True
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
            parse_mode="Markdown",
            disable_notification=True  # Reduce notifications
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
            ["💸 Withdraw", "🎟 Coupon Stock"],
            ["👥 My Referrals"]  # NEW: Referral stats button
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

    # Referral Logic (FIXED)
    args = context.args
    if args and args[0].isdigit():
        referrer_id = int(args[0])
        if referrer_id != user_id:
            # Verify referrer exists
            referrer_data = await get_user_data(referrer_id)
            if referrer_data:
                pending_referrals[user_id] = referrer_id
                logger.info(f"Referral detected: {user_id} referred by {referrer_id}")

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
                
                # Give referral reward (FIXED LOGIC)
                if referrer_id:
                    await update_user_balance(referrer_id, 1)
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            f"🎉 New Referral! {first_name} joined using your link.\nYou got +1 💎 Diamond."
                        )
                    except:
                        pass
            
            # Welcome message
            caption = (
                "👋 Welcome to SHEIN Refer Coupon Bot!\n"
                "Invite friends & earn rewards.\n"
            )
            
            await update.message.reply_text(
                caption=caption,
                parse_mode="Markdown"
            )
            
            # Show main menu
            keyboard = [
                ["🔗 My Link", "💎 Balance"],
                ["💸 Withdraw", "🎟 Coupon Stock"],
                ["👥 My Referrals"]  # NEW
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
    
    # Get referral stats
    user_data = await get_user_data(user_id)
    referral_count = user_data.get("referral_count", 0) if user_data else 0
    
    text = (
        "🔗 *Your Referral Link*\n"
        f"`{link}`\n\n"
        f"📊 **Referral Stats**\n"
        f"• Total Referrals: {referral_count}\n"
        f"• Earn 1 💎 per referral\n\n"
        f"Invite friends using above link!"
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
    
    if user_data:
        balance = user_data.get("balance", 0)
        referral_count = user_data.get("referral_count", 0)
        
        text = (
            f"💎 *Balance*\n"
            f"Total: {balance}.0 💎\n\n"
            f"📊 *Referral Stats*\n"
            f"Referrals: {referral_count}\n"
            f"Earned: {referral_count} 💎 from referrals"
        )
    else:
        text = "You are not registered. Use /start"
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    
    if not user_data:
        await update.message.reply_text("You are not registered. Use /start")
        return
    
    referral_count = user_data.get("referral_count", 0)
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={user_id}"
    
    text = (
        "👥 *My Referrals*\n\n"
        f"📊 **Stats**\n"
        f"• Total Referrals: {referral_count}\n"
        f"• Earned Diamonds: {referral_count} 💎\n\n"
        f"🔗 **Your Referral Link**\n"
        f"`{link}`\n\n"
        f"*How it works:*\n"
        f"1. Share your link with friends\n"
        f"2. When they join and verify\n"
        f"3. You get 1 💎 per referral\n"
        f"4. Use diamonds to redeem coupons!"
    )
    
    keyboard = [
        [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={link}")],
        [InlineKeyboardButton("💎 Balance", callback_data="balance_menu"),
         InlineKeyboardButton("🎟 Coupons", callback_data="coupon_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def handle_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Get all stock counts concurrently for faster response
    categories = ["500", "1000", "2000", "4000"]
    tasks = [get_stock_count(cat) for cat in categories]
    results = await asyncio.gather(*tasks)
    
    stock_500, stock_1000, stock_2000, stock_4000 = results
    
    text = (
        "🎟 *Live Coupon Stock*\n\n"
        f"📦 500: {stock_500}\n"
        f"📦 1000: {stock_1000}\n"
        f"📦 2000: {stock_2000}\n"
        f"📦 4000: {stock_4000}\n\n"
        f"*Last Updated:* {get_ist_time()}"
    )
    
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = await get_user_data(user_id)
    
    if not user_data:
        await update.message.reply_text("You are not registered. Use /start")
        return
    
    balance = user_data.get("balance", 0)
    
    text = f"💸 *Withdraw*\nTotal Balance: {balance}.0 💎\nSelect amount to withdraw:"
    
    keyboard = [
        [
            InlineKeyboardButton("1 💎 500 🎟", callback_data="redeem_500_1"),
            InlineKeyboardButton("4 💎 1000 🎟", callback_data="redeem_1000_4")
        ],
        [
            InlineKeyboardButton("15 💎 2000 🎟", callback_data="redeem_2000_15"),
            InlineKeyboardButton("25 💎 4000 🎟", callback_data="redeem_4000_25")
        ],
        [InlineKeyboardButton("👥 My Referrals", callback_data="my_referrals")]
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
    
    processing_users.add(user_id)
    
    try:
        data = query.data.split("_")
        category = data[1]
        cost = int(data[2])
        
        # Check balance
        user_data = await get_user_data(user_id)
        if not user_data or user_data.get("balance", 0) < cost:
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
        processing_users.discard(user_id)

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
        ],
        [
            InlineKeyboardButton("🗑 Clear Cache", callback_data="admin_clear_cache"),
            InlineKeyboardButton("📈 Referral Stats", callback_data="admin_ref_stats")
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
    
    # Get stats concurrently for faster response
    users_count_task = users_collection.count_documents({})
    coupons_count_task = coupons_collection.count_documents({})
    stock_500_task = get_stock_count("500", use_cache=False)
    stock_1000_task = get_stock_count("1000", use_cache=False)
    stock_2000_task = get_stock_count("2000", use_cache=False)
    stock_4000_task = get_stock_count("4000", use_cache=False)
    total_balance_task = users_collection.aggregate([
        {"$group": {"_id": None, "total": {"$sum": "$balance"}}}
    ]).to_list(length=1)
    
    results = await asyncio.gather(
        users_count_task, coupons_count_task, stock_500_task,
        stock_1000_task, stock_2000_task, stock_4000_task, total_balance_task
    )
    
    users_count, coupons_count, stock_500, stock_1000, stock_2000, stock_4000, total_balance = results
    
    total_balance = total_balance[0]["total"] if total_balance else 0
    
    # Admin list
    admin_list = "\n".join([f"  • `{admin_id}`" for admin_id in ADMIN_IDS])
    
    # FSub channels list
    fsub_list = "\n".join([f"  • `{channel_id}`" for channel_id in FSUB_CHANNEL_IDS])
    
    # Cache stats
    cache_stats = f"Users: {len(user_cache)}, Stock: {len(stock_cache)}, Links: {len(link_cache)}"
    
    stats_text = (
        "📊 **Bot Statistics**\n\n"
        f"👥 Total Users: `{users_count}`\n"
        f"💎 Total Diamonds: `{total_balance}`\n"
        f"🎟 Total Coupons: `{coupons_count}`\n"
        f"🗃 Cache Stats: `{cache_stats}`\n\n"
        "🎟 **Coupon Stock**\n"
        f"  500: `{stock_500}`\n"
        f"  1000: `{stock_1000}`\n"
        f"  2000: `{stock_2000}`\n"
        f"  4000: `{stock_4000}`\n\n"
        "👑 **Admins**\n"
        f"{admin_list}\n\n"
        "📢 **Force Subscribe Channels**\n"
        f"{fsub_list}\n\n"
        f"*Updated:* {get_ist_time()}"
    )
    
    await query.message.reply_text(stats_text, parse_mode="Markdown")

async def admin_ref_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Unauthorized!", show_alert=True)
        return
    
    # Get top referrers
    pipeline = [
        {"$match": {"referral_count": {"$gt": 0}}},
        {"$sort": {"referral_count": -1}},
        {"$limit": 10},
        {"$project": {
            "user_id": 1,
            "first_name": 1,
            "referral_count": 1,
            "balance": 1
        }}
    ]
    
    top_referrers = await users_collection.aggregate(pipeline).to_list(length=10)
    
    # Get total referrals
    total_ref_pipeline = [
        {"$group": {"_id": None, "total_referrals": {"$sum": "$referral_count"}}}
    ]
    total_ref_result = await users_collection.aggregate(total_ref_pipeline).to_list(length=1)
    total_referrals = total_ref_result[0]["total_referrals"] if total_ref_result else 0
    
    # Format top referrers
    referrers_text = ""
    for i, user in enumerate(top_referrers, 1):
        referrers_text += (
            f"{i}. {user.get('first_name', 'Unknown')} "
            f"(ID: `{user['user_id']}`)\n"
            f"   Referrals: {user.get('referral_count', 0)} | "
            f"Balance: {user.get('balance', 0)} 💎\n"
        )
    
    stats_text = (
        "📈 **Referral Statistics**\n\n"
        f"📊 Total Referrals: `{total_referrals}`\n\n"
        "🏆 **Top 10 Referrers**\n"
        f"{referrers_text if referrers_text else 'No referrals yet.'}\n\n"
        f"*Updated:* {get_ist_time()}"
    )
    
    await query.message.reply_text(stats_text, parse_mode="Markdown")

async def admin_clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Unauthorized!", show_alert=True)
        return
    
    # Clear all caches
    user_cache.clear()
    stock_cache.clear()
    link_cache.clear()
    
    await query.answer("✅ All caches cleared!", show_alert=True)
    await query.message.reply_text("🗑 *Cache Status*\nAll caches have been cleared.", parse_mode="Markdown")

async def admin_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.answer("❌ Unauthorized!", show_alert=True)
        return
    
    # Clear caches
    user_cache.clear()
    stock_cache.clear()
    
    await query.answer("✅ Data reloaded from database!", show_alert=True)

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
        
        # Create indexes
        await create_indexes()
        
        bot_username = (await app.bot.get_me()).username
        await app.bot.send_message(
            LOG_CHANNEL_ID,
            f"🟢 **Bot Restarted & Online**\n🤖 Bot: @{bot_username}\n📅 Time: {get_ist_time()}\n⚡ Optimized Version",
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
    app.add_handler(CallbackQueryHandler(admin_ref_stats, pattern="^admin_ref_stats$"))
    app.add_handler(CallbackQueryHandler(admin_clear_cache, pattern="^admin_clear_cache$"))
    app.add_handler(CallbackQueryHandler(admin_reload, pattern="^admin_reload$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: u.answer("✅", show_alert=True), pattern="^my_referrals$"))
    app.add_handler(admin_conv_handler)
    
    # Add message handlers
    app.add_handler(MessageHandler(filters.Regex("^🔗 My Link$"), handle_my_link))
    app.add_handler(MessageHandler(filters.Regex("^💎 Balance$"), handle_balance))
    app.add_handler(MessageHandler(filters.Regex("^🎟 Coupon Stock$"), handle_stock))
    app.add_handler(MessageHandler(filters.Regex("^💸 Withdraw$"), handle_withdraw))
    app.add_handler(MessageHandler(filters.Regex("^👥 My Referrals$"), handle_referrals))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_captcha))
    
    # Start backup job
    asyncio.create_task(backup_job())
    
    # Initialize and start
    await app.initialize()
    await app.start()
    
    logger.info("🤖 Bot 4 Started Successfully (Optimized Version)")
    logger.info(f"👑 Admins: {len(ADMIN_IDS)} users")
    logger.info(f"📢 Force Sub Channels: {len(FSUB_CHANNEL_IDS)} channels")
    logger.info("⚡ Performance Optimizations: Caching, Indexing, Async Operations")
    
    # Start polling
    await app.updater.start_polling()
    
    # Run forever
    await asyncio.Event().wait()

# ================= EXPORT FOR RUNNER =================
__all__ = ['start_bot4']


