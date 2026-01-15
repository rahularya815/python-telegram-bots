

# --- CONFIGURATION ---
# Replace 'YOUR_BOT_TOKEN_HERE' with the token you got from BotFather

BOT_TOKEN = os.getenv('BOT_TOKEN')

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CallbackQueryHandler, filters



# --- DATA STORAGE ---
# NOTE: In a real production app, use a database (SQLite/PostgreSQL). 
# This dictionary stores votes in memory: { message_id: { user_id: score } }
post_votes = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def get_keyboard():
    """Creates the 1-10 rating buttons layout"""
    # Row 1: 1 to 5
    row1 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(1, 6)]
    # Row 2: 6 to 10
    row2 = [InlineKeyboardButton(str(i), callback_data=str(i)) for i in range(6, 11)]
    return InlineKeyboardMarkup([row1, row2])

async def add_rating_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggered when a new post is added to the channel.
    Sends a message with rating buttons.
    """
    if not update.channel_post:
        return

    message = update.channel_post
    
    try:
        # Send the rating message replying to the content
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
    """
    Triggered when a user clicks a rating button.
    Calculates the new average and updates the message text.
    """
    query = update.callback_query
    user_id = query.from_user.id
    new_score = int(query.data)
    message_id = query.message.message_id
    
    # 1. Answer the query (stops the button loading animation)
    await query.answer(f"You rated this {new_score}/10")

    # 2. Update the vote data
    if message_id not in post_votes:
        post_votes[message_id] = {}
    
    # Check if this user is changing their vote or voting for the first time
    post_votes[message_id][user_id] = new_score

    # 3. Calculate Average
    scores = list(post_votes[message_id].values())
    total_votes = len(scores)
    average_score = sum(scores) / total_votes

    # 4. Generate the progress bar (visual representation)
    # E.g., 7.5/10 -> [■■■■■■■□□□]
    filled_blocks = int(round(average_score))
    progress_bar = "■" * filled_blocks + "□" * (10 - filled_blocks)

    # 5. Edit the message text with the new stats
    new_text = (
        f"📊 **Rating: {average_score:.1f} / 10**\n"
        f"{progress_bar}\n"
        f"({total_votes} votes)"
    )

    # Only edit if the text is different (prevents errors if user clicks same button twice)
    if new_text != query.message.text:
        await query.edit_message_text(
            text=new_text,
            reply_markup=get_keyboard(), # Keep buttons there so others can vote
            parse_mode='Markdown'
        )

if __name__ == '__main__':
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Filter: Listen to channel posts (Text, Photo, Video, Documents)
    # Note: filters.Document.ALL and filters.ANIMATION are used for newer versions
    channel_post_filter = filters.ChatType.CHANNEL & (
        filters.TEXT | 
        filters.PHOTO | 
        filters.VIDEO | 
        filters.Document.ALL | 
        filters.ANIMATION
    )

    # Handler 1: Detect new posts and add buttons
    application.add_handler(MessageHandler(channel_post_filter, add_rating_buttons))

    # Handler 2: Detect button clicks (CallbackQuery)
    application.add_handler(CallbackQueryHandler(handle_vote))

    print("Bot is running... Press Ctrl+C to stop.")
    application.run_polling()