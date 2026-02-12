import logging
import os
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CallbackQueryHandler, CommandHandler, filters
from telegram.error import BadRequest
from pymongo import MongoClient

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')

# --- DATABASE SETUP ---
if not MONGO_URI:
    print("Error: MONGO_URI is missing.")

try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client['telegram_bot_db']
    votes_collection = db['post_votes']
    print("✅ Connected to MongoDB successfully!")
except Exception as e:
    print(f"❌ Failed to connect to MongoDB: {e}")

# --- WEB SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running with Leaderboard!"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- HELPER FUNCTIONS ---

def get_keyboard():
    row1 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(6, 11)]
    row3 = [InlineKeyboardButton("👁️ See Who Voted", callback_data="check_voters")]
    return InlineKeyboardMarkup([row1, row2, row3])

# --- BOT COMMANDS ---

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Shows the top 5 highest-rated posts.
    Usage: /top
    """
    try:
        # 1. Fetch all posts from MongoDB
        all_posts = list(votes_collection.find())
        
        # 2. Calculate averages for each post
        ranked_posts = []
        for post in all_posts:
            votes = post.get('votes', {})
            title = post.get('title', 'Unknown Post')
            message_id = post.get('_id')
            chat_id = post.get('chat_id')
            
            if not votes:
                continue

            # Calculate score
            scores = [v['score'] for v in votes.values()]
            avg_score = sum(scores) / len(scores)
            vote_count = len(scores)

            # Store tuple: (Average Score, Vote Count, Title, Link Info)
            ranked_posts.append({
                'avg': avg_score,
                'count': vote_count,
                'title': title,
                'link': f"https://t.me/c/{str(chat_id)[4:]}/{message_id}" # Construct generic private link
            })

        # 3. Sort: Primary by Score (Desc), Secondary by Vote Count (Desc)
        ranked_posts.sort(key=lambda x: (x['avg'], x['count']), reverse=True)

        # 4. Take top 5
        top_5 = ranked_posts[:5]

        if not top_5:
            await update.message.reply_text("No rated posts found yet!")
            return

        # 5. Build the Message
        msg_lines = ["🏆 **Top Rated Posts** 🏆\n"]
        for i, post in enumerate(top_5, 1):
            # Format: 1. Python Tutorial - ⭐ 9.5 (20 votes)
            line = (
                f"{i}. [{post['title']}]({post['link']})\n"
                f"   ⭐ **{post['avg']:.1f}/10** ({post['count']} votes)"
            )
            msg_lines.append(line)

        await update.message.reply_text("\n\n".join(msg_lines), parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error in /top command: {e}")
        await update.message.reply_text("An error occurred while fetching the leaderboard.")

# --- BOT HANDLERS ---

async def add_rating_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    
    message = update.channel_post
    
    try:
        # EXTRACT TITLE (Text or Caption)
        # We grab the first 30 chars to use as the "Title" in the leaderboard
        post_text = message.text or message.caption or "Media Post"
        short_title = post_text[:30] + "..." if len(post_text) > 30 else post_text
        
        # SAVE TO DB
        initial_data = {
            '_id': message.message_id,
            'chat_id': message.chat_id,
            'title': short_title,  # <--- NEW: Saving the title
            'votes': {}
        }
        
        try:
            votes_collection.insert_one(initial_data)
        except Exception:
            pass 
        
        await context.bot.send_message(
            chat_id=message.chat_id,
            text="📊 **Rate this post:**\nNo ratings yet.",
            reply_to_message_id=message.message_id,
            reply_markup=get_keyboard(),
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Error sending rating buttons: {e}")

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    message_id = query.message.message_id
    chat_id = query.message.chat_id

    # 1. FETCH DATA
    post_data = votes_collection.find_one({'_id': message_id})
    if not post_data:
        post_data = {'_id': message_id, 'chat_id': chat_id, 'votes': {}}
        votes_collection.insert_one(post_data)

    current_votes = post_data.get('votes', {})

    # ======================================================
    # PATH 1: "SEE WHO VOTED"
    # ======================================================
    if query.data == "check_voters":
        is_admin = False
        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
            if member.status in ['creator', 'administrator']:
                is_admin = True
        except Exception:
            is_admin = False

        user_id_str = str(user.id)
        has_voted = user_id_str in current_votes
        
        if not is_admin and not has_voted:
            await query.answer("🔒 You must vote first to see who else voted!", show_alert=True)
            return

        if not current_votes:
             await query.answer("No one has voted yet.", show_alert=True)
             return

        voter_lines = []
        for uid_str, data in current_votes.items():
            name = data.get('name', 'Unknown')
            score = data.get('score', '?')
            if is_admin:
                voter_lines.append(f"• {name}: {score}")
            else:
                voter_lines.append(f"• {name}")
        
        header = f"👮 Admin View ({len(voter_lines)} votes):" if is_admin else f"👥 Voters ({len(voter_lines)}):"
        result_text = f"{header}\n" + "\n".join(voter_lines)
        if len(result_text) > 200:
            result_text = result_text[:190] + "..."
        await query.answer(result_text, show_alert=True)
        return

    # ======================================================
    # PATH 2: RATING (1-10)
    # ======================================================
    new_score = int(query.data)
    user_name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
    
    # Update DB
    user_id_str = str(user.id)
    votes_collection.update_one(
        {'_id': message_id},
        {'$set': {f'votes.{user_id_str}': {'score': new_score, 'name': user_name}}}
    )

    await query.answer(f"You rated this {new_score}/10")

    # Re-calc average
    updated_post = votes_collection.find_one({'_id': message_id})
    updated_votes = updated_post.get('votes', {})
    scores = [v['score'] for v in updated_votes.values()]
    total_votes = len(scores)
    average_score = sum(scores) / total_votes if total_votes > 0 else 0
    
    filled_blocks = int(round(average_score))
    progress_bar = "■" * filled_blocks + "□" * (10 - filled_blocks)

    new_text = (
        f"📊 **Rating: {average_score:.1f} / 10**\n"
        f"{progress_bar}\n"
        f"({total_votes} votes)"
    )

    if new_text != query.message.text:
        try:
            await query.edit_message_text(
                text=new_text,
                reply_markup=get_keyboard(),
                parse_mode='Markdown'
            )
        except BadRequest:
            pass

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing.")
        exit(1)

    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    channel_post_filter = filters.ChatType.CHANNEL & (
        filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION
    )

    application.add_handler(MessageHandler(channel_post_filter, add_rating_buttons))
    application.add_handler(CallbackQueryHandler(handle_vote))
    
    # NEW: Add the /top command handler
    application.add_handler(CommandHandler("top", cmd_top))

    print("Bot is running...")
    application.run_polling()
