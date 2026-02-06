#!/usr/bin/env python3
"""
SHEIN REFER COUPON BOT
Complete single file with MongoDB integration
"""

import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Telegram imports
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# MongoDB imports
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# ================= CONFIGURATION =================
# Load from environment variables
BOT4_TOKEN = os.getenv("BOT4_TOKEN", "")
MONGO_URI = os.getenv("MONGO_URI", "")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", 0))
PORT = int(os.getenv("PORT", "8080"))

# Multiple Admins and Force Subscribe Channels
ADMIN_IDS = [5298223577, 2120581499]  # Add more admin IDs here
FSUB_CHANNEL_IDS = [
    -1002114224580,
    -1003627956964,
    -1003680807119,
    -1002440964326,
    -1003541438177
]  # Force subscribe channels

# Conversation states
WAITING_CODES = 1

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= DATABASE SETUP =================
class Database:
    def __init__(self):
        self.client = None
        self.db = None
        self.connect()
    
    def connect(self):
        """Connect to MongoDB"""
        try:
            self.client = MongoClient(MONGO_URI)
            self.db = self.client.shein_bot
            # Create collections if not exists
            self.db.users.create_index("user_id", unique=True)
            self.db.coupons.create_index("code", unique=True)
            self.db.redeemed.create_index([("user_id", 1), ("code", 1)], unique=True)
            self.db.admin_logs.create_index("timestamp")
            logger.info("✅ Connected to MongoDB")
        except ConnectionFailure as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            raise
    
    # ========== USER MANAGEMENT ==========
    def get_user(self, user_id: int):
        """Get user data"""
        return self.db.users.find_one({"user_id": user_id})
    
    def create_user(self, user_id: int, username: str, first_name: str, last_name: str = ""):
        """Create new user"""
        user_data = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "balance": 0.0,
            "referral_count": 0,
            "referral_code": str(uuid4())[:8],
            "created_at": datetime.now(),
            "last_active": datetime.now(),
            "is_banned": False
        }
        self.db.users.insert_one(user_data)
        return user_data
    
    def update_user_activity(self, user_id: int):
        """Update user's last active time"""
        self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"last_active": datetime.now()}}
        )
    
    def increment_balance(self, user_id: int, amount: float):
        """Increase user balance"""
        self.db.users.update_one(
            {"user_id": user_id},
            {"$inc": {"balance": amount}}
        )
    
    def get_user_balance(self, user_id: int):
        """Get user balance"""
        user = self.get_user(user_id)
        return user.get("balance", 0.0) if user else 0.0
    
    # ========== COUPON MANAGEMENT ==========
    def get_coupon_stock(self):
        """Get current coupon stock"""
        stock = {
            "500": 0,
            "1000": 0,
            "2000": 0,
            "4000": 0
        }
        
        coupons = self.db.coupons.find({"is_used": False})
        for coupon in coupons:
            amount = str(coupon.get("amount", 0))
            if amount in stock:
                stock[amount] += 1
        
        return stock
    
    def add_coupons(self, amount: int, codes: List[str]):
        """Add new coupon codes to database"""
        added = 0
        for code in codes:
            code = code.strip()
            if not code:
                continue
            
            coupon_data = {
                "code": code,
                "amount": amount,
                "is_used": False,
                "added_at": datetime.now(),
                "used_by": None,
                "used_at": None
            }
            
            try:
                self.db.coupons.insert_one(coupon_data)
                added += 1
            except:
                continue
        
        return added
    
    def get_available_coupon(self, amount: int):
        """Get an available coupon of specific amount"""
        return self.db.coupons.find_one({
            "amount": amount,
            "is_used": False
        })
    
    def mark_coupon_used(self, code: str, user_id: int):
        """Mark coupon as used"""
        result = self.db.coupons.update_one(
            {"code": code, "is_used": False},
            {
                "$set": {
                    "is_used": True,
                    "used_by": user_id,
                    "used_at": datetime.now()
                }
            }
        )
        
        if result.modified_count > 0:
            # Record redemption
            self.db.redeemed.insert_one({
                "user_id": user_id,
                "code": code,
                "redeemed_at": datetime.now()
            })
            return True
        return False
    
    # ========== REDEMPTION HISTORY ==========
    def get_user_redemptions(self, user_id: int):
        """Get user's redemption history"""
        return list(self.db.redeemed.find({"user_id": user_id}).sort("redeemed_at", -1))
    
    def get_redemption_count(self, user_id: int):
        """Get user's redemption count"""
        return self.db.redeemed.count_documents({"user_id": user_id})
    
    # ========== ADMIN LOGS ==========
    def log_admin_action(self, admin_id: int, action: str, details: str = ""):
        """Log admin actions"""
        self.db.admin_logs.insert_one({
            "admin_id": admin_id,
            "action": action,
            "details": details,
            "timestamp": datetime.now()
        })
    
    def get_stats(self):
        """Get bot statistics"""
        total_users = self.db.users.count_documents({})
        active_today = self.db.users.count_documents({
            "last_active": {"$gte": datetime.now().replace(hour=0, minute=0, second=0)}
        })
        total_coupons = self.db.coupons.count_documents({})
        used_coupons = self.db.coupons.count_documents({"is_used": True})
        
        return {
            "total_users": total_users,
            "active_today": active_today,
            "total_coupons": total_coupons,
            "used_coupons": used_coupons,
            "available_coupons": total_coupons - used_coupons
        }

# Initialize database
db = Database()

# ================= HELPER FUNCTIONS =================
async def send_log_message(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send message to log channel"""
    try:
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=message,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send log message: {e}")

async def check_user_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is subscribed to all required channels"""
    if not FSUB_CHANNEL_IDS:
        return True
    
    for channel_id in FSUB_CHANNEL_IDS:
        try:
            member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            logger.error(f"Error checking subscription for channel {channel_id}: {e}")
            return False
    
    return True

def get_main_keyboard():
    """Get main reply keyboard"""
    keyboard = [
        ["🔗 My Link", "💎 Balance"],
        ["🎟 Coupon Stock", "💸 Withdraw"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    """Get admin keyboard"""
    keyboard = [
        ["👑 Admin Panel"],
        ["🔗 My Link", "💎 Balance"],
        ["🎟 Coupon Stock", "💸 Withdraw"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def format_stock_message(stock):
    """Format stock message"""
    return (
        "🎟 <b>Coupon Stock</b>\n\n"
        f"• 500 Coupons: {stock['500']}\n"
        f"• 1000 Coupons: {stock['1000']}\n"
        f"• 2000 Coupons: {stock['2000']}\n"
        f"• 4000 Coupons: {stock['4000']}"
    )

# ================= BOT HANDLERS =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    user_id = user.id
    
    # Check if user exists
    user_data = db.get_user(user_id)
    if not user_data:
        # Create new user
        user_data = db.create_user(
            user_id=user_id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name or ""
        )
        
        # Send new user notification to log channel
        log_message = (
            "#NewUser Joined 🚀\n\n"
            f"👤 Name: {user.first_name}\n"
            f"🆔 ID: {user_id}\n"
            f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
        )
        await send_log_message(context, log_message)
    
    # Update activity
    db.update_user_activity(user_id)
    
    # Check subscription
    is_subscribed = await check_user_subscription(user_id, context)
    
    if not is_subscribed:
        # Create channel list message
        channels_text = "\n".join([f"• Channel {i+1}" for i in range(len(FSUB_CHANNEL_IDS))])
        
        # Create inline keyboard with channel links
        keyboard = []
        for i, channel_id in enumerate(FSUB_CHANNEL_IDS):
            try:
                chat = await context.bot.get_chat(channel_id)
                keyboard.append([InlineKeyboardButton(
                    f"📢 Join Channel {i+1}", 
                    url=f"https://t.me/{chat.username}" if chat.username else f"https://t.me/c/{str(chat.id)[4:]}"
                )])
            except:
                continue
        
        keyboard.append([InlineKeyboardButton("✅ I've Joined", callback_data="check_join")])
        
        await update.message.reply_text(
            "⚠️ <b>Please join our channels to use the bot!</b>\n\n"
            f"{channels_text}\n\n"
            "After joining, click the button below:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Send welcome message
    if user_id in ADMIN_IDS:
        reply_markup = get_admin_keyboard()
    else:
        reply_markup = get_main_keyboard()
    
    await update.message.reply_text(
        "🤖 <b>Welcome to SHEIN REFER COUPON BOT!</b>\n\n"
        "🛍️ Buy Fast Now, Buy Discount\n"
        "🎁 Great Offers and Discounts\n"
        "🚀 Order Now\n"
        "💰 Avail Cash on Delivery\n"
        "🔄 Easy Returns and Exchange\n\n"
        "<i>Use the buttons below to navigate:</i>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle join check callback"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Check subscription again
    is_subscribed = await check_user_subscription(user_id, context)
    
    if is_subscribed:
        if user_id in ADMIN_IDS:
            reply_markup = get_admin_keyboard()
        else:
            reply_markup = get_main_keyboard()
        
        await query.edit_message_text(
            "✅ <b>Channel verified!</b>\n\n"
            "Now you can use all features of the bot.",
            parse_mode="HTML"
        )
        
        await context.bot.send_message(
            chat_id=user_id,
            text="🤖 <b>Welcome to SHEIN REFER COUPON BOT!</b>\n\n"
                 "Use the buttons below to navigate:",
            parse_mode="HTML",
            reply_markup=reply_markup
        )
    else:
        await query.answer("❌ Please join all channels first!", show_alert=True)

async def handle_my_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle My Link button"""
    user = update.effective_user
    user_data = db.get_user(user.id)
    
    if not user_data:
        await update.message.reply_text("❌ User not found. Please use /start first.")
        return
    
    referral_code = user_data.get("referral_code", str(uuid4())[:8])
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start={referral_code}"
    
    message = (
        "🔗 <b>Your Referral Link</b>\n\n"
        f"{referral_link}\n\n"
        "🎉 <b>Invite friends & earn rewards</b>\n"
        "Get 1 ❤️ for every verified join\n\n"
        "<i>Share this link to start earning!</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={referral_link}&text=Get%20FREE%20SHEIN%20Coupons%20🎁")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    
    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )

async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Balance button"""
    user_id = update.effective_user.id
    user_data = db.get_user(user_id)
    
    if not user_data:
        await update.message.reply_text("❌ User not found. Please use /start first.")
        return
    
    balance = user_data.get("balance", 0.0)
    redemption_count = db.get_redemption_count(user_id)
    
    message = (
        "💎 <b>Balance</b>\n\n"
        f"<b>Total:</b> {balance:.1f} 💎\n"
        f"<b>Redeem:</b> {redemption_count}\n\n"
        "<i>Redeem History:</i>"
    )
    
    # Get recent redemptions
    redemptions = db.get_user_redemptions(user_id)[:5]  # Last 5 redemptions
    
    if redemptions:
        for i, redemption in enumerate(redemptions, 1):
            code = redemption.get("code", "N/A")
            time = redemption.get("redeemed_at", datetime.now()).strftime("%Y-%m-%d %H:%M")
            message += f"\n{i}. {code} - {time}"
    else:
        message += "\nNo redemptions yet."
    
    await update.message.reply_text(message, parse_mode="HTML")

async def handle_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Coupon Stock button"""
    stock = db.get_coupon_stock()
    
    message = format_stock_message(stock)
    await update.message.reply_text(message, parse_mode="HTML")

async def handle_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Withdraw button"""
    user_id = update.effective_user.id
    balance = db.get_user_balance(user_id)
    
    if balance <= 0:
        await update.message.reply_text(
            "❌ <b>Insufficient Balance!</b>\n\n"
            "Your balance is 0.0 💎\n"
            "Invite friends to earn more coins.",
            parse_mode="HTML"
        )
        return
    
    # Show withdrawal options
    keyboard = [
        [
            InlineKeyboardButton("1 💎 = 500 ₪", callback_data="redeem_500"),
            InlineKeyboardButton("4 💎 = 1000 ₪", callback_data="redeem_1000")
        ],
        [
            InlineKeyboardButton("15 💎 = 2000 ₪", callback_data="redeem_2000"),
            InlineKeyboardButton("25 💎 = 4000 ₪", callback_data="redeem_4000")
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    
    message = (
        "💸 <b>Withdraw</b>\n\n"
        f"<b>Total Balance:</b> {balance:.1f} 💎\n"
        "<b>Select amount to withdraw:</b>"
    )
    
    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle redeem callback"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    amount_map = {
        "redeem_500": 500,
        "redeem_1000": 1000,
        "redeem_2000": 2000,
        "redeem_4000": 4000
    }
    
    amount = amount_map.get(data)
    if not amount:
        return
    
    # Check user balance
    balance = db.get_user_balance(user_id)
    cost_map = {500: 1, 1000: 4, 2000: 15, 4000: 25}
    cost = cost_map.get(amount, 1)
    
    if balance < cost:
        await query.edit_message_text(
            f"❌ <b>Insufficient Balance!</b>\n\n"
            f"Required: {cost} 💎\n"
            f"Your balance: {balance:.1f} 💎\n\n"
            "Invite friends to earn more coins.",
            parse_mode="HTML"
        )
        return
    
    # Get available coupon
    coupon = db.get_available_coupon(amount)
    
    if not coupon:
        await query.edit_message_text(
            f"❌ <b>Coupon Out of Stock!</b>\n\n"
            f"{amount} ₪ coupons are currently unavailable.\n"
            "Please try again later or choose different amount.",
            parse_mode="HTML"
        )
        return
    
    # Mark coupon as used and deduct balance
    if db.mark_coupon_used(coupon["code"], user_id):
        db.increment_balance(user_id, -cost)
        
        # Send success message
        await query.edit_message_text(
            f"✅ <b>Coupon Redeemed Successfully!</b>\n\n"
            f"🎟 <b>Code:</b> <code>{coupon['code']}</code>\n"
            f"💰 <b>Amount:</b> {amount} ₪\n"
            f"💸 <b>Deducted:</b> {cost} 💎\n"
            f"💎 <b>Remaining Balance:</b> {balance - cost:.1f} 💎\n\n"
            "<i>Use this code on SHEIN app/website</i>",
            parse_mode="HTML"
        )
        
        # Log the redemption
        log_message = (
            f"🎟 <b>New Redemption</b>\n\n"
            f"👤 User: {query.from_user.first_name} (ID: {user_id})\n"
            f"💰 Amount: {amount} ₪\n"
            f"🔢 Code: {coupon['code']}\n"
            f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
        )
        await send_log_message(context, log_message)
    else:
        await query.edit_message_text(
            "❌ <b>Error processing coupon!</b>\n\n"
            "Please try again later.",
            parse_mode="HTML"
        )

async def back_to_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back to main callback"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id in ADMIN_IDS:
        reply_markup = get_admin_keyboard()
    else:
        reply_markup = get_main_keyboard()
    
    await query.edit_message_text(
        "🤖 <b>Main Menu</b>\n\n"
        "Use the buttons below to navigate:",
        parse_mode="HTML"
    )
    
    await context.bot.send_message(
        chat_id=user_id,
        text="⬇️ <b>Navigation Menu</b>",
        parse_mode="HTML",
        reply_markup=reply_markup
    )

# ================= ADMIN HANDLERS =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin command"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Access denied!")
        return
    
    # Admin panel keyboard
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
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
            InlineKeyboardButton("🔄 Reload Data", callback_data="admin_reload")
        ],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main")]
    ]
    
    stats = db.get_stats()
    
    message = (
        "👑 <b>Admin Panel</b>\n\n"
        f"👥 <b>Total Users:</b> {stats['total_users']}\n"
        f"🟢 <b>Active Today:</b> {stats['active_today']}\n"
        f"🎟 <b>Total Coupons:</b> {stats['total_coupons']}\n"
        f"✅ <b>Used Coupons:</b> {stats['used_coupons']}\n"
        f"🔄 <b>Available:</b> {stats['available_coupons']}\n\n"
        "<i>Select an option:</i>"
    )
    
    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_add_coupons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin add coupons callback"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Access denied!")
        return
    
    # Get amount from callback data
    data = query.data
    amount_map = {
        "admin_add_500": 500,
        "admin_add_1000": 1000,
        "admin_add_2000": 2000,
        "admin_add_4000": 4000
    }
    
    amount = amount_map.get(data)
    context.user_data["admin_coupon_amount"] = amount
    
    await query.edit_message_text(
        f"👑 <b>Add {amount} ₪ Coupons</b>\n\n"
        "Please send coupon codes (one per line):\n\n"
        "<i>Example:</i>\n"
        "<code>SHEIN500ABC</code>\n"
        "<code>SHEIN500XYZ</code>\n\n"
        "Send /cancel to cancel.",
        parse_mode="HTML"
    )
    
    return WAITING_CODES

async def admin_receive_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive coupon codes from admin"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return ConversationHandler.END
    
    amount = context.user_data.get("admin_coupon_amount", 500)
    text = update.message.text
    
    # Split codes by newline
    codes = text.strip().split('\n')
    
    # Add coupons to database
    added_count = db.add_coupons(amount, codes)
    
    # Log admin action
    db.log_admin_action(user_id, f"add_coupons_{amount}", f"Added {added_count} coupons")
    
    # Send confirmation
    await update.message.reply_text(
        f"✅ <b>Successfully added {added_count} coupon(s)!</b>\n\n"
        f"💰 Amount: {amount} ₪\n"
        f"🎟 Added: {added_count} codes\n"
        f"📊 Failed: {len(codes) - added_count} (duplicates)\n\n"
        "Updated stock:",
        parse_mode="HTML"
    )
    
    # Show updated stock
    stock = db.get_coupon_stock()
    stock_message = format_stock_message(stock)
    await update.message.reply_text(stock_message, parse_mode="HTML")
    
    # Log to channel
    log_message = (
        f"👑 <b>Admin Action</b>\n\n"
        f"👤 Admin: {update.effective_user.first_name} (ID: {user_id})\n"
        f"🎟 Added: {added_count} x {amount} ₪ coupons\n"
        f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
    )
    await send_log_message(context, log_message)
    
    return ConversationHandler.END

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin stats callback"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ Access denied!")
        return
    
    stats = db.get_stats()
    stock = db.get_coupon_stock()
    
    message = (
        "📊 <b>Bot Statistics</b>\n\n"
        f"👥 <b>Total Users:</b> {stats['total_users']}\n"
        f"🟢 <b>Active Today:</b> {stats['active_today']}\n"
        f"🎟 <b>Total Coupons:</b> {stats['total_coupons']}\n"
        f"✅ <b>Used Coupons:</b> {stats['used_coupons']}\n"
        f"🔄 <b>Available:</b> {stats['available_coupons']}\n\n"
        "<b>Coupon Stock:</b>\n"
        f"• 500 ₪: {stock['500']}\n"
        f"• 1000 ₪: {stock['1000']}\n"
        f"• 2000 ₪: {stock['2000']}\n"
        f"• 4000 ₪: {stock['4000']}\n\n"
        f"<i>Last updated: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="back_to_admin")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_stats")]
    ]
    
    await query.edit_message_text(
        message,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_reload_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin reload callback"""
    query = update.callback_query
    await query.answer("✅ Data reloaded!", show_alert=True)

async def back_to_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back to admin panel callback"""
    query = update.callback_query
    await query.answer()
    
    # Return to admin panel
    await admin_command(update, context)

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel admin operation"""
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages (for button texts)"""
    text = update.message.text
    
    if text == "🔗 My Link":
        await handle_my_link(update, context)
    elif text == "💎 Balance":
        await handle_balance(update, context)
    elif text == "🎟 Coupon Stock":
        await handle_stock(update, context)
    elif text == "💸 Withdraw":
        await handle_withdraw(update, context)
    elif text == "👑 Admin Panel":
        await admin_command(update, context)

# ================= MAIN FUNCTION =================
async def start_bot4():
    """Start Bot 4 with python-telegram-bot"""
    # Build application
    app = Application.builder() \
        .token(BOT4_TOKEN) \
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
            WAITING_CODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_receive_codes)]
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
        allow_reentry=True
    )
    
    # Add all handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    
    # Callback query handlers
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(handle_redeem, pattern="^redeem_"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_reload_callback, pattern="^admin_reload$"))
    app.add_handler(CallbackQueryHandler(back_to_main_callback, pattern="^back_to_main$"))
    app.add_handler(CallbackQueryHandler(back_to_admin_callback, pattern="^back_to_admin$"))
    
    # Admin conversation handler
    app.add_handler(admin_conv_handler)
    
    # Message handlers for buttons
    app.add_handler(MessageHandler(filters.Regex("^(🔗 My Link|💎 Balance|🎟 Coupon Stock|💸 Withdraw|👑 Admin Panel)$"), handle_message))
    
    # Initialize and start
    await app.initialize()
    await app.start()
    
    logger.info("🤖 Bot 4 Started Successfully")
    logger.info(f"👑 Admins: {len(ADMIN_IDS)} users")
    logger.info(f"📢 Force Sub Channels: {len(FSUB_CHANNEL_IDS)} channels")
    
    # Get bot info
    bot_info = await app.bot.get_me()
    logger.info(f"🤖 Bot Username: @{bot_info.username}")
    
    # Send startup message to log channel
    startup_message = (
        "🚀 <b>Bot Started Successfully!</b>\n\n"
        f"🤖 Bot: @{bot_info.username}\n"
        f"👑 Admins: {len(ADMIN_IDS)}\n"
        f"📢 Channels: {len(FSUB_CHANNEL_IDS)}\n"
        f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}"
    )
    await send_log_message(app, startup_message)
    
    # Start polling
    await app.updater.start_polling()

async def post_init(application: Application):
    """Post initialization"""
    logger.info("✅ Bot initialized successfully")
