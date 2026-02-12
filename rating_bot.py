import logging
import os
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from telegram.error import BadRequest

# --- CONFIGURATION ---
# Get the token from Environment Variables (Best Practice)
# On your local machine, you can replace os.getenv with "YOUR_TOKEN_HERE"
BOT_TOKEN = os.getenv('BOT_TOKEN')

# --- WEB SERVER (KEEP-ALIVE) ---
# This mimics a website so hosting platforms like Render allow the deploy
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    # Render assigns a port automatically in the environment variable 'PORT'
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- BOT STORAGE & LOGGING ---
# Structure: { message_id: { user_id: { 'score': 5, 'name': 'John Doe' } } }
post_votes = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- HELPER FUNCTIONS ---

def get_keyboard():
    """Generates the 1-10 rating buttons + the 'See Who Voted' button."""
    # Row 1: 1-5
    row1 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]
    # Row 2: 6-10
    row2 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(6, 11)]
    # Row 3: The "See Who Voted" button
    row3 = [InlineKeyboardButton("👁️ See Who Voted", callback_data="check_voters")]
    
    return InlineKeyboardMarkup([row1, row2, row3])

# --- BOT HANDLERS ---

async def add_rating_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered when a new post is sent to the Channel.
    Adds the rating keyboard to the post.
    """
    if not update.channel_post:
        return
    
    message = update.channel_post
    
    try:
        # Initialize storage for this message
        post_votes[message.message_id] = {}
        
        # Send the rating panel as a reply to the post
        await context.bot.send_message(
            chat_id=message.chat_id,
            text="📊 **Rate this post:**\nNo ratings yet.",
            reply_to_message_id=message.message_id,
            reply_markup=get_keyboard(),
            parse_mode='Markdown'
        )
        logging.info(f"Added rating buttons to message {message.message_id}")
    except Exception as e:
        logging.error(f"Error sending rating buttons: {e}")

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles clicks on BOTH the rating numbers (1-10) and the 'See Who Voted' button.
    """
    query = update.callback_query
    user = query.from_user
    message_id = query.message.message_id
    chat_id = query.message.chat_id

    # Initialize storage if it doesn't exist (e.g., bot restarted)
    if message_id not in post_votes:
        post_votes[message_id] = {}

    # ======================================================
    # PATH 1: USER CLICKED "SEE WHO VOTED"
    # ======================================================
    if query.data == "check_voters":
        
        # 1. Check if the user is an Admin
        is_admin = False
        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
            if member.status in ['creator', 'administrator']:
                is_admin = True
        except Exception as e:
            logging.error(f"Error checking admin status: {e}")
            # Fail safely: assume not admin if check fails
            is_admin = False

        # 2. Check if user has voted
        has_voted = user.id in post_votes[message_id]
        
        # 3. Access Control Logic
        # - Admins can pass regardless of voting status.
        # - Regular users MUST vote first.
        if not is_admin and not has_voted:
            await query.answer("🔒 You must vote first to see who else voted!", show_alert=True)
            return

        # 4. Generate the List
        if not post_votes[message_id]:
             await query.answer("No one has voted yet.", show_alert=True)
             return

        voter_lines = []
        for uid, data in post_votes[message_id].items():
            name = data.get('name', 'Unknown')
            score = data.get('score', '?')
            
            if is_admin:
                # ADMIN VIEW: Names + Scores
                voter_lines.append(f"• {name}: {score}")
            else:
                # USER VIEW: Names Only
                voter_lines.append(f"• {name}")
        
        # Header text
        header = f"👮 Admin View ({len(voter_lines)} votes):" if is_admin else f"👥 Voters ({len(voter_lines)}):"
        result_text = f"{header}\n" + "\n".join(voter_lines)
        
        # Truncate if too long (Telegram alert limit is small)
        if len(result_text) > 200:
            result_text = result_text[:190] + "\n... (list truncated)"

        await query.answer(result_text, show_alert=True)
        return

    # ======================================================
    # PATH 2: USER CLICKED A RATING BUTTON (1-10)
    # ======================================================
    
    new_score = int(query.data)

    # Construct Display Name
    user_name = user.first_name
    if user.last_name:
        user_name += f" {user.last_name}"
    
    # Save the vote
    post_votes[message_id][user.id] = {
        'score': new_score,
        'name': user_name
    }

    # Acknowledge the click
    await query.answer(f"You rated this {new_score}/10")

    # Calculate Statistics
    scores = [v['score'] for v in post_votes[message_id].values()]
    total_votes = len(scores)
    average_score = sum(scores) / total_votes
    
    # Create Progress Bar
    filled_blocks = int(round(average_score))
    progress_bar = "■" * filled_blocks + "□" * (10 - filled_blocks)

    new_text = (
        f"📊 **Rating: {average_score:.1f} / 10**\n"
        f"{progress_bar}\n"
        f"({total_votes} votes)"
    )

    # Edit the message ONLY if the text is different
    # (Telegram API throws an error if you try to edit a message to the same content)
    if new_text != query.message.text:
        try:
            await query.edit_message_text(
                text=new_text,
                reply_markup=get_keyboard(),
                parse_mode='Markdown'
            )
        except BadRequest as e:
            # "Message is not modified" error is harmless
            if "Message is not modified" not in str(e):
                logging.error(f"Error updating message: {e}")

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing. Set it in your environment variables.")
        exit(1)

    # 1. Start the dummy web server in a separate thread
    # This satisfies Render's requirement to bind to a port
    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    # 2. Start the Bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Filter for Channel Posts only (Text, Photo, Video, Docs, Animation)
    channel_post_filter = filters.ChatType.CHANNEL & (
        filters.TEXT | 
        filters.PHOTO | 
        filters.VIDEO | 
        filters.Document.ALL | 
        filters.ANIMATION
    )

    application.add_handler(MessageHandler(channel_post_filter, add_rating_buttons))
    application.add_handler(CallbackQueryHandler(handle_vote))

    print("Bot is running...")
    application.run_polling()
