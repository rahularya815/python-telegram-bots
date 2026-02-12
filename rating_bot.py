import logging
import os
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CallbackQueryHandler, filters

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')

# --- WEB SERVER (KEEP-ALIVE) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- BOT CODE ---
# New Structure: { message_id: { user_id: { 'score': 5, 'name': 'John' } } }
post_votes = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def get_keyboard():
    # Row 1: 1-5
    row1 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]
    # Row 2: 6-10
    row2 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(6, 11)]
    # Row 3: The "Who Voted?" button (Admin tool)
    row3 = [InlineKeyboardButton("👁️ See Who Voted", callback_data="check_voters")]
    
    return InlineKeyboardMarkup([row1, row2, row3])

async def add_rating_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    message = update.channel_post
    try:
        # Initialize storage for this message
        post_votes[message.message_id] = {}
        
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
    
    # --- HANDLE "CHECK VOTERS" BUTTON ---
    if query.data == "check_voters":
        if message_id not in post_votes or not post_votes[message_id]:
            await query.answer("No one has voted yet!", show_alert=True)
            return

        # Create a list of names and scores
        # Format: "John: 5"
        voter_list = []
        for uid, data in post_votes[message_id].items():
            name = data['name']
            score = data['score']
            voter_list.append(f"{name}: {score}")
        
        # Join them with newlines
        result_text = "Users who clicked:\n" + "\n".join(voter_list)
        
        # Show as a popup alert (only the clicker sees this)
        await query.answer(result_text, show_alert=True)
        return

    # --- HANDLE RATING BUTTONS ---
    new_score = int(query.data)
    
    # Initialize if missing
    if message_id not in post_votes:
        post_votes[message_id] = {}

    # Store Score AND Name
    # We use user.first_name, but you could use user.username or user.full_name
    user_name = user.username if user.username else user.first_name
    post_votes[message_id][user.id] = {
        'score': new_score,
        'name': user_name
    }

    await query.answer(f"You rated this {new_score}/10")

    # Calculate Average
    # Extract just the scores from the dictionary
    scores = [v['score'] for v in post_votes[message_id].values()]
    total_votes = len(scores)
    average_score = sum(scores) / total_votes
    filled_blocks = int(round(average_score))
    progress_bar = "■" * filled_blocks + "□" * (10 - filled_blocks)

    new_text = (
        f"📊 **Rating: {average_score:.1f} / 10**\n"
        f"{progress_bar}\n"
        f"({total_votes} votes)"
    )

    # Only edit if text changed (prevents Telegram API errors)
    if new_text != query.message.text:
        await query.edit_message_text(
            text=new_text,
            reply_markup=get_keyboard(),
            parse_mode='Markdown'
        )

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN is missing.")
        exit(1)

    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
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
