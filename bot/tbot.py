import logging
import sys
import sqlite3
import os
import datetime
sys.path.append(os.path.abspath("../Telegram-Kisaragi-Bot"))
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
from ollama import chat, ChatResponse, Client

# Load environment variables
load_dotenv("../Telegram-Kisaragi-Bot/bot/tekkit.env")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not found in .env file.")
    exit()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.ERROR)
print("Bot is running...")

# Initialize SQLite database
DB_PATH = "../Telegram-Kisaragi-Bot/bot/conversations.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,  -- Changed to user_id
    user_message TEXT,
    bot_response TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# Dictionary to track active `/talk` mode for users AND the user they are talking to
active_talk_sessions = {}
last_activity = {}  # Dictionary to track last activity time

# Save conversation to the database
def save_conversation(user_id, user_message, bot_response):  # Changed session_id to user_id
    cursor.execute("""
    INSERT INTO conversation (session_id, user_message, bot_response)
    VALUES (?, ?, ?)
    """, (user_id, user_message, bot_response))
    conn.commit()

# Retrieve conversation history
def get_conversation_history(user_id, limit=5):  # Changed session_id to user_id
    cursor.execute("""
    SELECT user_message, bot_response FROM conversation
    WHERE session_id = ?
    ORDER BY timestamp DESC
    LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    history = []
    for user_message, bot_response in reversed(rows):
        history.append({'role': 'user', 'content': user_message})
        history.append({'role': 'assistant', 'content': bot_response})
    return history

# Query Ollama with context
def query_ollama_with_context(user_id, user_message, model="smallthinker:3b"):
    conversation_history = get_conversation_history(user_id)
    conversation_history.append({'role': 'user', 'content': user_message})

    client = Client(host='http://localhost:11434', timeout=60)  # Define Client here
    retries = 3
    delay = 1

    while retries > 0:
        try:
            response: ChatResponse = client.chat(model=model, messages=conversation_history, stream=False)
            bot_response = response.message.content
            save_conversation(user_id, user_message, bot_response)
            return bot_response
        except Exception as e:
            print(f"Error querying Ollama: {str(e)}")
            retries -= 1
            if retries > 0:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                return "Sorry, there was an error processing your request."

    return "Sorry, there was an error processing your request."


#rank system
RANK_DB_PATH = "../Telegram-Kisaragi-Bot/bot/ranks.sqlite3"
rank_conn = sqlite3.connect(RANK_DB_PATH, check_same_thread=False)
rank_cursor = rank_conn.cursor()

rank_cursor.execute("""
CREATE TABLE IF NOT EXISTS user_ranks (
    user_id TEXT PRIMARY KEY,
    username TEXT,
    xp INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1
)
""")
rank_conn.commit()

def add_or_update_user(user_id, username):
    rank_cursor.execute("""
    INSERT INTO user_ranks (user_id, username, xp, level)
    VALUES (?, ?, 0, 1)
    ON CONFLICT(user_id) DO NOTHING
    """, (user_id, username))
    rank_conn.commit()

def update_xp(user_id, username):
    add_or_update_user(user_id, username)
    rank_cursor.execute("""
    SELECT xp, level FROM user_ranks WHERE user_id = ?
    """, (user_id,))
    result = rank_cursor.fetchone()
    if result:
        xp, level = result
        xp += 10
        if xp >= 100:
            xp = 0
            level += 1
        rank_cursor.execute("""
        UPDATE user_ranks SET xp = ?, level = ? WHERE user_id = ?
        """, (xp, level, user_id))
        rank_conn.commit()

def get_user_rank(user_id):
    rank_cursor.execute("""
    SELECT username, xp, level FROM user_ranks WHERE user_id = ?
    """, (user_id,))
    result = rank_cursor.fetchone()
    if result:
        username, xp, level = result
        return f"{username}, you are level {level} with {xp}/100 XP."
    else:
        return "You have no rank yet. Start messaging to gain XP!"

def get_leaderboard(limit=10):
    rank_cursor.execute("""
    SELECT username, level, xp FROM user_ranks
    ORDER BY level DESC, xp DESC
    LIMIT ?
    """, (limit,))
    rows = rank_cursor.fetchall()
    return rows

# Commented out print statements
# async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     print(f"Command triggered: /leaderboard by {update.effective_user.username}")
#     leaderboard_data = get_leaderboard()
#     if leaderboard_data:
#         message = "🏆 Leaderboard 🏆\n"
#         for rank, (username, level, xp) in enumerate(leaderboard_data, start=1):
#             message += f"{rank}. {username}: Level {level}, {xp}/100 XP\n"
#         await update.message.reply_text(message)
#     else:
#         await update.message.reply_text("No leaderboard data available yet!")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leaderboard_data = get_leaderboard()
    if leaderboard_data:
        message = "🏆 Leaderboard 🏆\n"
        for rank, (username, level, xp) in enumerate(leaderboard_data, start=1):
            message += f"{rank}. {username}: Level {level}, {xp}/100 XP\n"
        await update.message.reply_text(message)
    else:
        await update.message.reply_text("No leaderboard data available yet!")

# Commented out print statements
# async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     print(f"Command triggered: /start by {update.effective_user.username}")
#     await context.bot.send_message(chat_id=update.effective_chat.id, text="Hi I'm a Suzu! Use /talk to start a conversation with me and /end_talk once you're done talking to me. You can also check /rank to see your rank in this chat!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Hi I'm a Kisaragi! Use /talk to start a conversation with me and /end_talk once you're done talking to me. You can also check /rank to see your rank in this chat!")

# Talk Command
async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # print(f"Command triggered: /talk by {update.effective_user.username}")  # Commented out print
    session_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if session_id not in active_talk_sessions:
        active_talk_sessions[session_id] = []

    active_talk_sessions[session_id].append(user_id)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm ready to chat!  (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧")

# Function to check for idle users and end their /talk sessions
async def check_idle_users(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now()
    for (session_id, user_id), last_active in list(last_activity.items()):  # Iterate over a copy
        if now - last_active > datetime.timedelta(minutes=5):
            try:
                active_talk_sessions[session_id].remove(user_id)
                del last_activity[(session_id, user_id)]
                await context.bot.send_message(chat_id=session_id, text=f"Ending /talk session for {user_id} due to inactivity.")
            except (KeyError, ValueError):
                pass

# Handle Messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "Anonymous"
    user_message = update.message.text
    session_id = str(update.effective_chat.id)

    # Always update XP, regardless of /talk
    update_xp(user_id, username)

    if user_id in active_talk_sessions.get(session_id, []):
        last_activity[(session_id, user_id)] = datetime.datetime.now()
        bot_response = query_ollama_with_context(user_id, user_message)
        await update.message.reply_text(bot_response)

# Commented out print statements
# async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     print(f"Command triggered: /rank by {update.effective_user.username}")
#     user_id = str(update.effective_user.id)
#     rank_info = get_user_rank(user_id)
#     await update.message.reply_text(rank_info)

async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    rank_info = get_user_rank(user_id)
    await update.message.reply_text(rank_info)

# End Talk Command
async def endtalk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # print(f"Command triggered: /end_talk by {update.effective_user.username}")  # Commented out print
    session_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if session_id in active_talk_sessions:
        try:
            active_talk_sessions[session_id].remove(user_id)
        except ValueError:
            pass

    if session_id in active_talk_sessions and not active_talk_sessions[session_id]:
        del active_talk_sessions[session_id]

    await context.bot.send_message(chat_id=update.effective_chat.id, text="It was a great talk!")

# Main Program
if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('talk', talk))
    application.add_handler(CommandHandler('endtalk', endtalk))
    application.add_handler(CommandHandler('rank', rank))
    application.add_handler(CommandHandler('leaderboard', leaderboard))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue: JobQueue = application.job_queue

    job_queue.run_repeating(check_idle_users, interval=60, first=60)

    application.run_polling()