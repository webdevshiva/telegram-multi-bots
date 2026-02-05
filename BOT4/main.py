import asyncio
import telebot
from telebot.async_telebot import AsyncTeleBot
from telebot import types
import json
import random
import os
import time
import threading
import zipfile
from datetime import datetime
import pytz

# ================= CONFIGURATION =================

BOT4_TOKEN = os.getenv("BOT4_TOKEN", "")

# 👥 Multiple Admins (Add IDs separated by comma)
ADMIN_IDS = [5298223577]  # Add more IDs like [id1, id2, id3]

# 📢 Multiple Force Subscribe Channels (ONLY IDs)
FSUB_CHANNEL_IDS = [-1003627956964]  # Add more channel IDs like [id1, id2, id3]

LOG_CHANNEL_ID = -1002686058050

# Welcome Image
WELCOME_IMAGE = "https://raw.githubusercontent.com/DevXShiva/Save-Restricted-Bot/refs/heads/main/logo.png"

# Credits
DEV_CREDITS = "\n\n👨‍💻 *Developed by:* [VoidXdevs](https://t.me/devXvoid)\n📜 *Source Code:* [Click Here](https://t.me/devXvoid)"

# ================= SYSTEM SETUP =================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "BOT_DATA")

# JSON File Paths
USERS_FILE = os.path.join(DATA_DIR, "users.json")
COUPONS_FILE = os.path.join(DATA_DIR, "coupons.json")

# Ensure Data Folder Exists
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# Initialize JSON files if not exist or if empty
def initialize_json_files():
    # Fix for empty JSON files
    if not os.path.exists(USERS_FILE) or os.path.getsize(USERS_FILE) == 0:
        with open(USERS_FILE, 'w') as f: 
            json.dump({}, f)
        print("Initialized users.json (empty)")
    
    if not os.path.exists(COUPONS_FILE) or os.path.getsize(COUPONS_FILE) == 0:
        with open(COUPONS_FILE, 'w') as f: 
            json.dump({"500": [], "1000": [], "2000": [], "4000": []}, f)
        print("Initialized coupons.json with empty categories")

initialize_json_files()

# Create async bot instance
bot = AsyncTeleBot(API_TOKEN)

# Global Memory
user_captcha = {}
pending_referrals = {}
processing_users = [] # Security Lock
data_lock = threading.Lock() # JSON Write Lock
link_cache = {} # Cache for FSub links

# IST Time Helper
def get_ist_time():
    IST = pytz.timezone('Asia/Kolkata')
    return datetime.now(IST).strftime("%Y-%m-%d %I:%M:%S %p")

# ================= JSON DATABASE FUNCTIONS =================

def read_json(filename):
    with data_lock:
        try:
            # Check if file exists and has content
            if not os.path.exists(filename) or os.path.getsize(filename) == 0:
                # Return default data based on filename
                if "coupons" in filename:
                    return {"500": [], "1000": [], "2000": [], "4000": []}
                else:
                    return {}
            
            with open(filename, 'r') as f:
                data = f.read().strip()
                if not data:  # If file is empty after stripping whitespace
                    if "coupons" in filename:
                        return {"500": [], "1000": [], "2000": [], "4000": []}
                    else:
                        return {}
                return json.loads(data)
        except json.JSONDecodeError as e:
            print(f"JSON Error in {filename}: {e}. Returning default data.")
            # Return default data
            if "coupons" in filename:
                return {"500": [], "1000": [], "2000": [], "4000": []}
            else:
                return {}
        except Exception as e:
            print(f"Error reading {filename}: {e}")
            # Return default data
            if "coupons" in filename:
                return {"500": [], "1000": [], "2000": [], "4000": []}
            else:
                return {}

def write_json(filename, data):
    with data_lock:
        try:
            with open(filename, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error writing {filename}: {e}")

def get_user_data(user_id):
    users = read_json(USERS_FILE)
    return users.get(str(user_id))

def update_user_balance(user_id, amount):
    users = read_json(USERS_FILE)
    if str(user_id) in users:
        users[str(user_id)]["balance"] += amount
        write_json(USERS_FILE, users)

def register_user_json(user_id, first_name, referrer_id=None):
    users = read_json(USERS_FILE)
    if str(user_id) not in users:
        users[str(user_id)] = {
            "first_name": first_name,
            "balance": 0,
            "referrer_id": referrer_id,
            "joined_date": datetime.now().strftime("%Y-%m-%d")
        }
        write_json(USERS_FILE, users)
        return True
    return False

def get_stock_count(category):
    coupons = read_json(COUPONS_FILE)
    # Ensure coupons is a dictionary
    if not isinstance(coupons, dict):
        coupons = {"500": [], "1000": [], "2000": [], "4000": []}
    
    # Count unused coupons
    if category in coupons:
        count = sum(1 for c in coupons[category] if isinstance(c, dict) and c.get("status") == "unused")
        return count
    return 0

def add_coupons_json(category, codes_list):
    coupons = read_json(COUPONS_FILE)
    # Ensure coupons is a dictionary
    if not isinstance(coupons, dict):
        coupons = {"500": [], "1000": [], "2000": [], "4000": []}
    
    # Ensure category exists
    if category not in coupons:
        coupons[category] = []
    
    added = 0
    # Get existing codes safely
    existing_codes = set()
    for c in coupons[category]:
        if isinstance(c, dict) and "code" in c:
            existing_codes.add(c["code"])
    
    for code in codes_list:
        if code and code not in existing_codes:
            coupons[category].append({"code": code, "status": "unused", "used_by": None})
            added += 1
            
    write_json(COUPONS_FILE, coupons)
    return added

# ================= BACKUP SYSTEM =================
async def backup_job():
    while True:
        await asyncio.sleep(7200)  # Every 2 hours
        try:
            timestamp = get_ist_time().replace(":", "-").replace(" ", "_")
            zip_filename = f"Backup_{timestamp}.zip"
            zip_path = os.path.join(BASE_DIR, zip_filename)

            # Create Zip of BOT_DATA
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(DATA_DIR):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, BASE_DIR)
                        zipf.write(file_path, arcname)
            
            # Send to Log Channel
            with open(zip_path, 'rb') as doc:
                caption = (
                    "🗂 **System Auto Backup (JSON)**\n"
                    f"📅 Time (IST): {get_ist_time()}\n"
                    f"📁 Files: users.json, coupons.json\n"
                    f"🤖 Bot: @{bot.user.username if hasattr(bot, 'user') else 'Bot'}"
                )
                await bot.send_document(LOG_CHANNEL_ID, doc, caption=caption, parse_mode="Markdown")
            
            os.remove(zip_path)
            print(f"✅ Backup sent at {timestamp}")
            
        except Exception as e:
            print(f"❌ Backup Failed: {e}")

# ================= HELPER FUNCTIONS =================

async def get_channel_invite_link(chat_id):
    # Check cache first
    if chat_id in link_cache:
        return link_cache[chat_id]
    
    try:
        # Try to generate or get link
        chat = await bot.get_chat(chat_id)
        if chat.invite_link:
            link = chat.invite_link
        else:
            link = await bot.export_chat_invite_link(chat_id)
        
        link_cache[chat_id] = link
        return link
    except Exception as e:
        print(f"Error fetching link for {chat_id}: {e}")
        return f"https://t.me/c/{str(chat_id)[4:]}" if str(chat_id).startswith("-100") else "https://t.me/"

async def is_joined(user_id):
    try:
        for chat_id in FSUB_CHANNEL_IDS:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status not in ['creator', 'administrator', 'member']:
                return False
        return True
    except:
        return False

async def send_log(log_type, user_id, first_name, details=""):
    try:
        user_link = f"[{first_name}](tg://user?id={user_id})"
        time_now = get_ist_time()
        
        if log_type == "new_user":
            msg = (
                "#NewUser Joined 🚀\n\n"
                f"👤 Name: {user_link}\n"
                f"🆔 ID: `{user_id}`\n"
                f"🕒 Time: `{time_now}`\n"
                f"🤖 Bot: @{bot.user.username if hasattr(bot, 'user') else 'Bot'}"
            )
        elif log_type == "withdraw":
            msg = (
                "#NewWithdraw Request 💸\n\n"
                f"👤 User: {user_link}\n"
                f"🆔 ID: `{user_id}`\n"
                f"{details}\n"
                f"🕒 Time: `{time_now}`\n"
                f"🤖 Bot: @{bot.user.username if hasattr(bot, 'user') else 'Bot'}"
            )
            
        await bot.send_message(LOG_CHANNEL_ID, msg, parse_mode="Markdown")
    except Exception as e:
        print(f"Log Error: {e}")

# ================= ADMIN PANEL =================
@bot.message_handler(commands=['admin'])
async def admin_panel(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Add 500 Coupons", callback_data="add_500"),
        types.InlineKeyboardButton("➕ Add 1000 Coupons", callback_data="add_1000"),
        types.InlineKeyboardButton("➕ Add 2000 Coupons", callback_data="add_2000"),
        types.InlineKeyboardButton("➕ Add 4000 Coupons", callback_data="add_4000")
    )
    markup.add(types.InlineKeyboardButton("📊 View Stats", callback_data="view_stats"))
    markup.add(types.InlineKeyboardButton("🔄 Reload Data", callback_data="reload_data"))
    await bot.send_message(message.chat.id, "👨‍💻 **Admin Panel**\nSelect option:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("add_"))
async def ask_coupons(call):
    if call.from_user.id not in ADMIN_IDS:
        await bot.answer_callback_query(call.id, "❌ Unauthorized!", show_alert=True)
        return
    
    category = call.data.split("_")[1]
    msg = await bot.send_message(call.message.chat.id, f"Send codes for **{category}** category (Space separated or New lines).", parse_mode="Markdown")
    
    # Register next step handler
    @bot.message_handler(func=lambda m: m.chat.id == call.message.chat.id, content_types=['text'])
    async def handle_coupon_response(m):
        if m.from_user.id not in ADMIN_IDS:
            return
        
        raw_text = m.text
        if not raw_text:
            await bot.send_message(m.chat.id, "❌ No codes provided!")
            return
        
        codes = raw_text.replace('\n', ' ').split(' ')
        codes = [c.strip() for c in codes if c.strip()]
        
        added_count = add_coupons_json(category, codes)
        await bot.send_message(m.chat.id, f"✅ Successfully added **{added_count}** coupons to **{category}** category.", parse_mode="Markdown")
        
        # Remove the handler after use
        bot.remove_message_handler(handle_coupon_response)

@bot.callback_query_handler(func=lambda call: call.data == "view_stats")
async def view_stats(call):
    if call.from_user.id not in ADMIN_IDS:
        await bot.answer_callback_query(call.id, "❌ Unauthorized!", show_alert=True)
        return
    
    # Read all data
    users = read_json(USERS_FILE)
    coupons = read_json(COUPONS_FILE)
    
    # Ensure coupons is dictionary
    if not isinstance(coupons, dict):
        coupons = {"500": [], "1000": [], "2000": [], "4000": []}
    
    # Calculate stats
    total_users = len(users)
    total_balance = sum(user.get("balance", 0) for user in users.values())
    
    stock_500 = get_stock_count("500")
    stock_1000 = get_stock_count("1000")
    stock_2000 = get_stock_count("2000")
    stock_4000 = get_stock_count("4000")
    
    # Admin list
    admin_list = "\n".join([f"  • `{admin_id}`" for admin_id in ADMIN_IDS])
    
    # FSub channels list
    fsub_list = "\n".join([f"  • `{channel_id}`" for channel_id in FSUB_CHANNEL_IDS])
    
    stats_text = (
        "📊 **Bot Statistics**\n\n"
        f"👥 Total Users: `{total_users}`\n"
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
    
    await bot.send_message(call.message.chat.id, stats_text, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "reload_data")
async def reload_data(call):
    if call.from_user.id not in ADMIN_IDS:
        await bot.answer_callback_query(call.id, "❌ Unauthorized!", show_alert=True)
        return
    
    initialize_json_files()
    await bot.answer_callback_query(call.id, "✅ Data files reloaded!", show_alert=True)

# ================= USER FLOW =================

def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("🔗 My Link", "💎 Balance", "💸 Withdraw", "🎟 Coupon Stock")
    return markup

@bot.message_handler(commands=['start'])
async def send_welcome(message):
    user_id = message.from_user.id
    
    # Dynamic FSub Check for multiple channels
    if not await is_joined(user_id):
        markup = types.InlineKeyboardMarkup(row_width=1)
        for i, chat_id in enumerate(FSUB_CHANNEL_IDS, 1):
            link = await get_channel_invite_link(chat_id)
            markup.add(types.InlineKeyboardButton(f"📢 Join Channel {i}", url=link))
            
        markup.add(types.InlineKeyboardButton("✅ I've Joined All", callback_data="check_join"))
        await bot.send_message(user_id, "⚠️ **Action Required**\n\nTo use this bot, you must join all our channels.", reply_markup=markup, parse_mode="Markdown")
        return

    # Check Old User
    user_data = get_user_data(user_id)
    if user_data:
        await bot.send_message(user_id, "👇 Select option", reply_markup=main_menu())
        return

    # Referral Logic
    args = message.text.split()
    if len(args) > 1:
        referrer = args[1]
        if referrer.isdigit() and int(referrer) != user_id:
            pending_referrals[user_id] = int(referrer)

    # Captcha
    n1, n2 = random.randint(1, 9), random.randint(1, 9)
    user_captcha[user_id] = n1 + n2
    await bot.send_message(user_id, f"🔒 *CAPTCHA*\n{n1} + {n2} = ??\n\nSend answer to verify.", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "check_join")
async def recheck_join(call):
    if await is_joined(call.from_user.id):
        await bot.delete_message(call.message.chat.id, call.message.message_id)
        await send_welcome(call.message)
    else:
        await bot.answer_callback_query(call.id, "❌ You haven't joined all channels!", show_alert=True)

@bot.message_handler(func=lambda m: m.from_user.id in user_captcha)
async def check_captcha(message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    
    try:
        if int(message.text) == user_captcha[user_id]:
            del user_captcha[user_id]
            await bot.send_message(user_id, "✅ Correct answer!")
            
            referrer_id = pending_referrals.get(user_id)
            is_new = register_user_json(user_id, first_name, referrer_id)
            
            if is_new:
                await send_log("new_user", user_id, first_name)
                
                if referrer_id:
                    update_user_balance(referrer_id, 1)
                    try:
                        await bot.send_message(referrer_id, "🎉 New Referral! You got +1 💎 Diamond.")
                    except:
                        pass
            
            # Welcome Message
            caption = (
                "👋 Welcome to SHEIN Refer Coupon Bot!\n"
                "Invite friends & earn rewards.\n"
                f"{DEV_CREDITS}"
            )
            await bot.send_photo(user_id, WELCOME_IMAGE, caption=caption, parse_mode="Markdown")
            await bot.send_message(user_id, "👇 Select option", reply_markup=main_menu())
        else:
            await bot.send_message(user_id, "❌ Wrong answer. Try again.")
    except ValueError:
        await bot.send_message(user_id, "Please send a number.")

# ================= MENU HANDLERS =================

@bot.message_handler(func=lambda m: m.text == "🔗 My Link")
async def my_link(m):
    bot_user = await bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={m.from_user.id}"
    text = (
        "🔗 *Your Referral Link*\n"
        f"`{link}`\n"
        f"Get 1 💎 for every verified join.\n{DEV_CREDITS}"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={link}"))
    await bot.send_message(m.from_user.id, text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "💎 Balance")
async def balance(m):
    data = get_user_data(m.from_user.id)
    bal = data["balance"] if data else 0
    await bot.send_message(m.from_user.id, f"💎 *Balance*\nTotal: {bal}.0 💎", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🎟 Coupon Stock")
async def stock(m):
    text = (
        "🎟 *Live Coupon Stock*\n\n"
        f"📦 500: {get_stock_count('500')}\n"
        f"📦 1000: {get_stock_count('1000')}\n"
        f"📦 2000: {get_stock_count('2000')}\n"
        f"📦 4000: {get_stock_count('4000')}"
    )
    await bot.send_message(m.from_user.id, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "💸 Withdraw")
async def withdraw_menu(m):
    data = get_user_data(m.from_user.id)
    bal = data["balance"] if data else 0
    
    text = f"💸 *Withdraw*\nTotal Balance: {bal}.0 💎\nSelect amount to withdraw:"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("1 💎 500 🎟", callback_data="redeem_500_1"))
    markup.add(types.InlineKeyboardButton("6 💎 1000 🎟", callback_data="redeem_1000_6"))
    markup.add(types.InlineKeyboardButton("15 💎 2000 🎟", callback_data="redeem_2000_15"))
    markup.add(types.InlineKeyboardButton("25 💎 4000 🎟", callback_data="redeem_4000_25"))
    await bot.send_message(m.from_user.id, text, parse_mode="Markdown", reply_markup=markup)

# ================= REDEEM (JSON & SECURITY) =================

@bot.callback_query_handler(func=lambda call: call.data.startswith("redeem_"))
async def process_redeem(call):
    user_id = call.from_user.id
    
    if user_id in processing_users:
        await bot.answer_callback_query(call.id, "⏳ Processing... Please wait.", show_alert=True)
        return
    processing_users.append(user_id) 
    
    try:
        first_name = call.from_user.first_name
        data = call.data.split("_")
        category = data[1]
        cost = int(data[2])
        
        # READ JSON SAFELY
        users = read_json(USERS_FILE)
        coupons = read_json(COUPONS_FILE)
        
        # Ensure coupons is dictionary
        if not isinstance(coupons, dict):
            coupons = {"500": [], "1000": [], "2000": [], "4000": []}
        
        # 1. Balance Check
        user_data = users.get(str(user_id), {})
        user_bal = user_data.get("balance", 0) if isinstance(user_data, dict) else 0
        
        if user_bal < cost:
            await bot.answer_callback_query(call.id, "❌ Not enough diamonds!", show_alert=True)
            return 

        # 2. Stock Check - FIXED: Ensure coupons is a dictionary
        found_coupon_index = -1
        if category in coupons:
            available_coupons = coupons[category]
        else:
            available_coupons = []
            
        for idx, cp in enumerate(available_coupons):
            if isinstance(cp, dict) and cp.get("status") == "unused":
                found_coupon_index = idx
                break
        
        if found_coupon_index == -1:
            await bot.answer_callback_query(call.id, "⚠️ Out of Stock! Contact Admin.", show_alert=True)
            return 
            
        # 3. Transaction (Atomic Write)
        coupon_code = available_coupons[found_coupon_index].get("code", "")
        
        # Update Memory
        if str(user_id) in users and isinstance(users[str(user_id)], dict):
            users[str(user_id)]["balance"] = users[str(user_id)].get("balance", 0) - cost
        
        # Update coupon status
        if category in coupons and found_coupon_index < len(coupons[category]):
            coupons[category][found_coupon_index]["status"] = "used"
            coupons[category][found_coupon_index]["used_by"] = user_id
        
        # Write to Disk
        write_json(USERS_FILE, users)
        write_json(COUPONS_FILE, coupons)
        
        # 4. Send Coupon
        msg = (
            "✅ *Redemption Successful!*\n\n"
            f"🎟 Category: {category} Coupons\n"
            f"🔐 Code: `{coupon_code}`\n\n"
            "⚠️ Copy and use it immediately!"
            f"{DEV_CREDITS}"
        )
        await bot.send_message(user_id, msg, parse_mode="Markdown")
        await bot.answer_callback_query(call.id, "Success!")
        
        # 5. Log
        details = f"🎟 Type: {category} Coupon\n💎 Cost: {cost} Diamonds"
        await send_log("withdraw", user_id, first_name, details)
            
    except Exception as e:
        await bot.send_message(user_id, "❌ Error occurred. Contact Admin.")
        print(f"❌ Error in redeem: {e}")
        
    finally:
        # UNLOCK USER
        if user_id in processing_users:
            processing_users.remove(user_id)

# ================= MULTI-BOT SUPPORT =================
async def start_bot4():
    """Alternative async startup function"""
    # You can create multiple bot instances like this
    bot2_token = os.getenv("BOT2_TOKEN", API_TOKEN)  # Fallback to main token
    
    if bot4_token == API_TOKEN:
        print("⚠️ BOT2_TOKEN not set, using main token")
        return
    
    bot4 = AsyncTeleBot(bot4_token)
    
    # Add handlers for bot2 if needed
    @bot2.message_handler(commands=['start'])
    async def start4(message):
        await bot4.send_message(message.chat.id, "🤖 This is Bot 2!")
    
    print(f"🤖 Bot 2 Started with token: {bot4_token[:10]}...")
    await bot4.polling(non_stop=True)

# ================= MAIN STARTUP =================
async def main():
    """Main async function to run all bots"""
    print("🤖 Bot Started by VoidXdevs (Async JSON Mode)...")
    print(f"📁 Data stored in: {DATA_DIR}")
    print(f"👑 Admins: {len(ADMIN_IDS)} users")
    print(f"📢 FSub Channels: {len(FSUB_CHANNEL_IDS)} channels")
    print("✅ All JSON files initialized")
    
    # Start backup job in background
    asyncio.create_task(backup_job())
    
    # Get bot info
    bot_user = await bot.get_me()
    print(f"🤖 Bot Username: @{bot_user.username}")
    print(f"🤖 Bot ID: {bot_user.id}")
    
    # Start polling
    await bot.polling(non_stop=True)
