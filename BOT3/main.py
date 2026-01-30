import os
import json
import random
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode

# ================= CONFIGURATION =================
BOT3_TOKEN = os.getenv("BOT3_TOKEN", "YOUR_BOT3_TOKEN_HERE")
DIVIDER = "◈───────────────────◈"
FOOTER = "\n\n───\n📱 **Developed By [𝐒𝐇𝐈𝐕𝐀 𝐂𝐇𝐀𝐔𝐃𝐇𝐀𝐑𝐘](https://t.me/theprofessorreport_bot)**"
STATS_FILE = "stats.json"

matches_cache = {}

# ================= DATA PERSISTENCE =================
def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_stats(uid, name):
    stats = load_stats()
    uid = str(uid)
    if uid not in stats:
        stats[uid] = {"name": name, "wins": 0}
    stats[uid]["wins"] += 1
    with open(STATS_FILE, "w") as f:
        json.dump(stats, f, indent=4)

# ================= ENGINE START =================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    intro = (f"{DIVIDER}\n        🏏 **APEX CRICKET WORLD**\n{DIVIDER}\n\n"
             f"Welcome! Hand-Cricket on Telegram.\n\n"
             f"🏆 **Rules:** 1 Over Match | 2 Wickets Max.")
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 VS CPU", callback_data=f"mode_cpu_{update.effective_chat.id}"),
         InlineKeyboardButton("👥 VS FRIEND", callback_data=f"mode_duel_{update.effective_chat.id}")]
    ])
    
    await update.message.reply_text(intro + FOOTER, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    uid, data = str(user.id), query.data.split('_')
    action, chat_id = data[0], data[-1]

    # Leaderboard Logic
    if action == "show":
        stats = load_stats()
        sorted_stats = sorted(stats.items(), key=lambda x: x[1]['wins'], reverse=True)[:10]
        lb_text = f"{DIVIDER}\n🏆 **TOP 10 PLAYERS**\n{DIVIDER}\n\n"
        if not sorted_stats:
            lb_text += "No records yet. Play a match!"
        for i, (user_id, data) in enumerate(sorted_stats, 1):
            lb_text += f"{i}. {data['name']} — {data['wins']} Wins\n"
        
        await query.edit_message_text(lb_text + FOOTER, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 BACK", callback_data=f"back_{chat_id}")]]), parse_mode=ParseMode.MARKDOWN)
        return

    if action == "back":
        await start_command(update, context)
        return

    if action == "mode":
        matches_cache[chat_id] = {
            "players": [uid] if data[1]=="duel" else [uid, "cpu"],
            "names": {uid: user.first_name, "cpu": "APEX AI"},
            "score": 0, "wickets": 0, "overs": 0, "balls": 0, "choices": {},
            "state": "toss", "cpu_mode": data[1]=="cpu", "total_overs": 1, "max_wickets": 2
        }
        
        if data[1] == "cpu":
            matches_cache[chat_id]["toss_caller"] = uid
            # Player hamesha toss call karega
            await query.edit_message_text(f"{DIVIDER}\n    🪙 **TOSS TIME (CPU)**\n{DIVIDER}\n\n{user.first_name}, call Heads or Tails (You'll win!):", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("HEADS", callback_data=f"th_{chat_id}"), InlineKeyboardButton("TAILS", callback_data=f"tt_{chat_id}")]]) , parse_mode=ParseMode.MARKDOWN)
        else:
            await query.edit_message_text(f"{DIVIDER}\n    👥 **WAITING FOR OPPONENT**\n{DIVIDER}\n\nAsk your friend to join below.", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ JOIN MATCH", callback_data=f"j_{chat_id}")]]) , parse_mode=ParseMode.MARKDOWN)
        return

    m = matches_cache.get(chat_id)
    if not m: return

    # Join logic
    if action == "j" and uid not in m["players"]:
        m["players"].append(uid)
        m["names"][uid] = user.first_name
        m["toss_caller"] = random.choice(m["players"])
        await query.edit_message_text(f"🪙 **TOSS CALL**\n\n{m['names'][m['toss_caller']]}, make your call!", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("HEADS", callback_data=f"th_{chat_id}"), InlineKeyboardButton("TAILS", callback_data=f"tt_{chat_id}")]]) , parse_mode=ParseMode.MARKDOWN)
        return

    # Toss Logic (Forced Win for Player in CPU Mode)
    if action in ["th", "tt"] and uid == m["toss_caller"]:
        if m["cpu_mode"]:
            m["toss_winner"] = uid # Forced win
        else:
            m["toss_winner"] = uid if (random.choice([0,1])==1) else [p for p in m["players"] if p != uid][0]
            
        await query.edit_message_text(f"🎊 {m['names'][m['toss_winner']]} won the toss!\n\nSelect Strategy:", 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏏 BAT", callback_data=f"tb_{chat_id}"), InlineKeyboardButton("🎯 BOWL", callback_data=f"tw_{chat_id}")]]) , parse_mode=ParseMode.MARKDOWN)
        return

    if action in ["tb", "tw"] and uid == m["toss_winner"]:
        p1, p2 = m["players"][0], m["players"][1]
        if action == "tb": m["bat_f"], m["bowl_f"] = uid, (p2 if uid==p1 else p1)
        else: m["bowl_f"], m["bat_f"] = uid, (p2 if uid==p1 else p1)
        m.update({"current_batsman": m["bat_f"], "current_bowler": m["bowl_f"], "state": "inning1"})
        await update_scorecard(query, m, chat_id)
        return

    # Gameplay logic
    if action.startswith('n'):
        if uid not in [m["current_batsman"], m["current_bowler"]]: return
        if uid in m["choices"]: return
        m["choices"][uid] = int(action[1])
        if m["cpu_mode"]: m["choices"]["cpu"] = random.randint(1,6)
        if len(m["choices"]) == 2: await resolve_ball(query, m, chat_id)
        else: await update_scorecard(query, m, chat_id, waiting_for=True)

# ================= CORE LOGIC =================

async def resolve_ball(query, m, cid):
    b_id, bo_id = m["current_batsman"], m["current_bowler"]
    b1, b2 = m["choices"][b_id], m["choices"][bo_id]
    m["choices"] = {}
    m["balls"] += 1
    if m["balls"] == 6: m["overs"] += 1; m["balls"] = 0
    
    last_action = f"🎯 WICKET! ({b1} vs {b2})" if b1 == b2 else f"✨ {b1} RUNS! ({b1} vs {b2})"
    if b1 == b2: m["wickets"] += 1
    else: m["score"] += b1

    chase_success = (m["state"] == "inning2" and m["score"] >= m["target"])
    inning_over = (m["wickets"] >= m["max_wickets"] or m["overs"] >= m["total_overs"])

    if chase_success:
        await end_match(query, m, cid, b_id, "CHASE COMPLETED!")
    elif inning_over:
        if m["state"] == "inning1":
            m.update({"target": m["score"]+1, "state": "inning2", "current_batsman": m["bowl_f"], "current_bowler": m["bat_f"], "score": 0, "wickets": 0, "overs": 0, "balls": 0})
            await query.edit_message_text(f"🏁 **INNINGS OVER**\nTarget: {m['target']}", reply_markup=get_num_kb(cid), parse_mode=ParseMode.MARKDOWN)
        else:
            await end_match(query, m, cid, bo_id, "DEFENDED SUCCESSFULLY!")
    else:
        await update_scorecard(query, m, cid, last_ball=last_action)

async def update_scorecard(query, m, cid, last_ball=None, waiting_for=False):
    bat, bowl = m["current_batsman"], m["current_bowler"]
    status = f"📊 **SCORECARD**\n"
    if last_ball: status += f"🎤 {last_ball}\n"
    status += f"🏏 {m['names'][bat]} | 🎯 {m['names'][bowl]}\n📈 {m['score']}/{m['wickets']} ({m['overs']}.{m['balls']}/{m['total_overs']})"
    if m["state"] == "inning2": status += f"\n🚩 Need {m['target'] - m['score']} runs"
    await query.edit_message_text(status + FOOTER, reply_markup=get_num_kb(cid), parse_mode=ParseMode.MARKDOWN)

async def end_match(query, m, cid, winner, reason):
    # Stats update for real players
    if winner != "cpu":
        save_stats(winner, m["names"][winner])
        
    status = (f"🏆 **MATCH OVER**\n👑 **WINNER:** {m['names'][winner]}\n📝 {reason}")
    await query.edit_message_text(status + FOOTER, parse_mode=ParseMode.MARKDOWN)
    matches_cache.pop(str(cid), None)

def get_num_kb(cid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(str(i), callback_data=f"n{i}_{cid}") for i in range(1,4)],
        [InlineKeyboardButton(str(i), callback_data=f"n{i}_{cid}") for i in range(4,7)],
        [InlineKeyboardButton("🏳️ SURRENDER", callback_data=f"surrender_{cid}")]
    ])

async def start_bot3():
    app = ApplicationBuilder().token(BOT3_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cricket", start_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("✅ PRO BOT ONLINE")
    
    await app.bot.initialize()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
