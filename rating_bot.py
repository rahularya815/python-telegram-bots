import logging
import os
import threading
import certifi
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CallbackQueryHandler, CommandHandler, filters
from telegram.error import BadRequest
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- DATABASE SETUP ---
votes_collection = None

if not MONGO_URI:
    logging.error("❌ MONGO_URI is missing from environment variables.")
else:
    try:
        # tlsCAFile=certifi.where() solves the SSL handshake error on Render
        mongo_client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
        mongo_client.admin.command('ping')
        db = mongo_client['telegram_bot_db']
        votes_collection = db['post_votes']
        logging.info("✅ Connected to MongoDB successfully!")
    except Exception as e:
        logging.error(f"❌ Failed to connect to MongoDB: {e}")

# --- WEB SERVER (KEEP-ALIVE) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- HELPER FUNCTIONS ---

def get_keyboard():
    row1 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(6, 11)]
    row3 = [InlineKeyboardButton("👁️ See Who Voted", callback_data="check_voters")]
    return InlineKeyboardMarkup([row1, row2, row3])

# --- COMMAND HANDLERS ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👋 Welcome! I am your Channel Rating Bot.\n"
        "I add rating buttons to your channel posts automatically.\n\n"
        "Type /help to see available commands."
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **Bot Help & Instructions**\n\n"
        "• **Rating:** I automatically add 1-10 buttons to new channel posts.\n"
        "• **Privacy:** Users see names of voters; Admins see names + scores.\n"
        "• **Requirement:** Users must vote before they can see the voter list.\n\n"
        "**Commands:**\n"
        "🏆 `/top` - View the 5 highest-rated posts.\n"
        "ℹ️ `/help` - Show this message."
    )
    await update.effective_message.reply_text(help_text, parse_mode='Markdown')

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if votes_collection is None:
        await update.effective_message.reply_text("❌ Database not connected.")
        return

    try:
        all_posts = list(votes_collection.find())
        ranked_posts = []

        for post in all_posts:
            votes = post.get('votes', {})
            if not votes: continue

            scores = [v['score'] for v in votes.values()]
            avg_score = sum(scores) / len(scores)
            
            # Construct link (stripping -100 from channel ID)
            chat_id = str(post.get('chat_id')).replace("-100", "")
            link = f"https://t.me/c/{chat_id}/{post.get('_id')}"

            ranked_posts.append({
                'avg': avg_score,
                'count': len(scores),
                'title': post.get('title', 'Unknown Post'),
                'link': link
            })

        ranked_posts.sort(key=lambda x: (x['avg'], x['count']), reverse=True)
        top_5 = ranked_posts[:5]

        if not top_5:
            await update.effective_message.reply_text("No votes recorded yet.")
            return

        msg = ["🏆 **Top Rated Posts** 🏆\n"]
        for i, p in enumerate(top_5, 1):
            msg.append(f"{i}. [{p['title']}]({p['link']})\n   ⭐ **{p['avg']:.1f}/10** ({p['count']} votes)")

        await update.effective_message.reply_text("\n\n".join(msg), parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Top error: {e}")

# --- MESSAGE HANDLERS ---

async def add_rating_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or votes_collection is None:
        return
    
    msg = update.channel_post
    text = msg.text or msg.caption or "Media Post"
    title = text[:30] + "..." if len(text) > 30 else text

    try:
        votes_collection.insert_one({
            '_id': msg.message_id,
            'chat_id': msg.chat_id,
            'title': title,
            'votes': {}
        })
        
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text="📊 **Rate this post:**",
            reply_to_message_id=msg.message_id,
            reply_markup=get_keyboard(),
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Button error: {e}")

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    msg_id = query.message.message_id
    chat_id = query.message.chat_id

    post_data = votes_collection.find_one({'_id': msg_id})
    if not post_data: return

    votes = post_data.get('votes', {})

    if query.data == "check_voters":
        # Check admin status
        is_admin = False
        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
            is_admin = member.status in ['creator', 'administrator']
        except: pass

        if not is_admin and str(user.id) not in votes:
            await query.answer("🔒 Vote first to see the list!", show_alert=True)
            return

        lines = []
        for uid, data in votes.items():
            name = data.get('name', 'User')
            lines.append(f"• {name}" + (f": {data['score']}" if is_admin else ""))
        
        res = ("👮 Admin View:\n" if is_admin else "👥 Voters:\n") + "\n".join(lines)
        await query.answer(res[:195], show_alert=True)
        return

    # Process actual vote
    score = int(query.data)
    user_name = f"{user.first_name} {user.last_name or ''}".strip()
    
    votes_collection.update_one(
        {'_id': msg_id},
        {'$set': {f'votes.{user.id}': {'score': score, 'name': user_name}}}
    )

    await query.answer(f"Rated {score}/10")

    # Update UI
    updated = votes_collection.find_one({'_id': msg_id})
    v_list = [v['score'] for v in updated['votes'].values()]
    avg = sum(v_list) / len(v_list)
    bar = "■" * int(round(avg)) + "□" * (10 - int(round(avg)))

    new_text = f"📊 **Rating: {avg:.1f} / 10**\n{bar}\n({len(v_list)} votes)"
    if new_text != query.message.text:
        try:
            await query.edit_message_text(new_text, reply_markup=get_keyboard(), parse_mode='Markdown')
        except: pass

if __name__ == '__main__':
    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # 1. Commands (Work everywhere)
    app_bot.add_handler(CommandHandler("start", cmd_start))
    app_bot.add_handler(CommandHandler("help", cmd_help))
    app_bot.add_handler(CommandHandler("top", cmd_top))

    # 2. Callbacks
    app_bot.add_handler(CallbackQueryHandler(handle_vote))

    # 3. Channel Posts (Ignore commands)
    c_filter = filters.ChatType.CHANNEL & (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION) & ~filters.COMMAND
    app_bot.add_handler(MessageHandler(c_filter, add_rating_buttons))

    print("🤖 Bot is live...")
    app_bot.run_polling()
