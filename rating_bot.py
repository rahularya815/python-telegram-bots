import logging
import os
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CallbackQueryHandler, filters

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv('BOT_TOKEN')

# --- WEB SERVER (KEEP-ALIVE) ---
# This mimics a website so Render allows the deploy
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_web_server():
    # Render assigns a port automatically in the environment variable 'PORT'
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# --- BOT CODE ---
post_votes = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def get_keyboard():
    row1 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(6, 11)]
    return InlineKeyboardMarkup([row1, row2])

async def add_rating_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    message = update.channel_post
    try:
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
    user_id = query.from_user.id
    new_score = int(query.data)
    message_id = query.message.message_id
    
    await query.answer(f"You rated this {new_score}/10")

    if message_id not in post_votes:
        post_votes[message_id] = {}
    post_votes[message_id][user_id] = new_score

    scores = list(post_votes[message_id].values())
    total_votes = len(scores)
    average_score = sum(scores) / total_votes
    filled_blocks = int(round(average_score))
    progress_bar = "■" * filled_blocks + "□" * (10 - filled_blocks)

    new_text = (
        f"📊 **Rating: {average_score:.1f} / 10**\n"
        f"{progress_bar}\n"
        f"({total_votes} votes)"
    )

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

    # 1. Start the dummy web server in a separate thread
    # This satisfies Render's requirement to bind to a port
    server_thread = threading.Thread(target=run_web_server)
    server_thread.daemon = True
    server_thread.start()

    # 2. Start the Bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Updated Filters for v20+
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
