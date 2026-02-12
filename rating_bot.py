import logging
import os
import threading
import certifi  # Fixes SSL errors on Render
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CallbackQueryHandler, CommandHandler, filters
from telegram.error import BadRequest
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')

# --- LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- DATABASE CONNECTION ---
votes_collection = None

if not MONGO_URI:
    logging.error("❌ Error: MONGO_URI is missing. Set it in your environment variables.")
else:
    try:
        # Create a new client and connect to the server
        # tlsCAFile=certifi.where() is CRITICAL for Render to trust MongoDB
        mongo_client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
        
        # Send a ping to confirm a successful connection
        mongo_client.admin.command('ping')
        
        db = mongo_client['telegram_bot_db']
        votes_collection = db['post_votes']
        logging.info("✅ Connected to MongoDB successfully!")
        
    except ServerSelectionTimeoutError as e:
        logging.error(f"❌ Failed to connect to MongoDB (Timeout): {e}")
    except Exception as e:
        logging.error(f"❌ Failed to connect to MongoDB (General Error): {e}")

# --- WEB SERVER (KEEP-ALIVE) ---
app = Flask(__name__)

@app.route('/')
def home():
    status = "Connected to DB" if votes_collection is not None else "DB Connection Failed"
    return f"Bot is running! Status: {status}"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- HELPER FUNCTIONS ---

def get_keyboard():
    """Generates the rating buttons (1-10) and the View button."""
    row1 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(6, 11)]
    row3 = [InlineKeyboardButton("👁️ See Who Voted", callback_data="check_voters")]
    return InlineKeyboardMarkup([row1, row2, row3])

# --- COMMAND HANDLERS ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message."""
    await update.message.reply_text(
        "👋 Hi! I am the Rating Bot.\n"
        "I automatically add rating buttons to new posts in your channel.\n\n"
        "Type /help to see what else I can do!"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends instructions on how to use the bot."""
    help_text = (
        "🤖 **How to use this Bot:**\n\n"
        "1. **Add me to your Channel** as an Admin.\n"
        "2. **Post anything** (text, photo, video).\n"
        "3. I will automatically add **Rating Buttons (1-10)**.\n\n"
        "**Commands:**\n"
        "🏆 `/top` - See the highest-rated posts.\n"
        "ℹ️ `/help` - Show this message.\n"
        "👁️ **See Who Voted** - Click the button on a post to see the voters list (Admins see scores, Users see names)."
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the top 5 highest-rated posts."""
    if votes_collection is None:
        await update.message.reply_text("❌ Database error: Cannot fetch leaderboard.")
        return

    try:
        # 1. Fetch all posts
        all_posts = list(votes_collection.find())
        
        # 2. Process and calculate scores
        ranked_posts = []
        for post in all_posts:
            votes = post.get('votes', {})
            title = post.get('title', 'Unknown Post')
            message_id = post.get('_id')
            chat_id = post.get('chat_id')
            
            if not votes:
                continue

            scores = [v['score'] for v in votes.values()]
            avg_score = sum(scores) / len(scores)
            vote_count = len(scores)

            # Construct Link (Works for Public Channels. Private require invite links)
            # We strip the "-100" prefix from channel ID for the link
            cid_str = str(chat_id).replace("-100", "")
            link = f"https://t.me/c/{cid_str}/{message_id}"

            ranked_posts.append({
                'avg': avg_score,
                'count': vote_count,
                'title': title,
                'link': link
            })

        # 3. Sort by Score (Desc), then by Vote Count (Desc)
        ranked_posts.sort(key=lambda x: (x['avg'], x['count']), reverse=True)

        # 4. Take top 5
        top_5 = ranked_posts[:5]

        if not top_5:
            await update.message.reply_text("No rated posts found yet!")
            return

        # 5. Build Message
        msg_lines = ["🏆 **Top Rated Posts** 🏆\n"]
        for i, post in enumerate(top_5, 1):
            line = (
                f"{i}. [{post['title']}]({post['link']})\n"
                f"   ⭐ **{post['avg']:.1f}/10** ({post['count']} votes)"
            )
            msg_lines.append(line)

        await update.message.reply_text("\n\n".join(msg_lines), parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error in /top command: {e}")
        await update.message.reply_text("An error occurred while fetching the leaderboard.")

# --- ACTION HANDLERS ---

async def add_rating_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when a NEW post is sent to the Channel."""
    if not update.channel_post:
        return
    
    message = update.channel_post
    
    if votes_collection is None:
        logging.error("Cannot save post: Database not connected.")
        return

    try:
        # Extract Title (First 30 chars of text or caption)
        post_text = message.text or message.caption or "Media Post"
        # Sanitize text for Markdown (escape brackets if needed) but keep simple for DB
        short_title = post_text[:30] + "..." if len(post_text) > 30 else post_text
        
        # Initial Data
        initial_data = {
            '_id': message.message_id,
            'chat_id': message.chat_id,
            'title': short_title,
            'votes': {}
        }
        
        # Try insert (ignore if exists)
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
    """Handles clicks on voting buttons AND the 'See Who Voted' button."""
    query = update.callback_query
    user = query.from_user
    message_id = query.message.message_id
    chat_id = query.message.chat_id

    if votes_collection is None:
        await query.answer("❌ Database Error. Try again later.", show_alert=True)
        return

    # 1. Fetch Data
    post_data = votes_collection.find_one({'_id': message_id})
    if not post_data:
        # Create on the fly if missing (e.g. old post)
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
        
        # Access Rule: Admins can always see. Users must vote first.
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
        
        # Truncate for Alert box limit (~200 chars)
        if len(result_text) > 200:
            result_text = result_text[:190] + "..."
            
        await query.answer(result_text, show_alert=True)
        return

    # ======================================================
    # PATH 2: RATING (1-10)
    # ======================================================
    new_score = int(query.data)
    user_name = f"{user.first_name} {user.last_name}" if user.last_name else user.first_name
    
    # Update DB using $set to modify specific field
    user_id_str = str(user.id)
    votes_collection.update_one(
        {'_id': message_id},
        {'$set': {f'votes.{user_id_str}': {'score': new_score, 'name': user_name}}}
    )

    await query.answer(f"You rated this {new_score}/10")

    # Re-calculate average from DB
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

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    if not BOT_TOKEN:
        print("❌ Error: BOT_TOKEN is missing.")
        exit(1)

    # 1. Start Web Server (Daemon thread)
    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    # 2. Start Bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Filter for Channel Posts
    channel_post_filter = filters.ChatType.CHANNEL & (
        filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION
    )

    # Add Handlers
    application.add_handler(MessageHandler(channel_post_filter, add_rating_buttons))
    application.add_handler(CallbackQueryHandler(handle_vote))
    application.add_handler(CommandHandler("top", cmd_top))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("start", cmd_start))

    print("🤖 Bot is running...")
    application.run_polling()
