import logging
import sys
import sqlite3
import os
import datetime
import time

sys.path.append(os.path.abspath("../Telegram-Kisaragi-Bot"))
from dotenv import load_dotenv

# PTB v20+ imports
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, JobQueue
)

from ollama import chat, ChatResponse, Client

load_dotenv("../Telegram-Kisaragi-Bot/bot/tekkit.env")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not found in .env file.")
    sys.exit(1)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.ERROR)
print("Bot is running...")

# ──────────────────────────────────────────────────────────────────────────
#                          Database Setup
# ──────────────────────────────────────────────────────────────────────────
DB_PATH = "../Telegram-Kisaragi-Bot/bot/conversations.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    user_message TEXT,
    bot_response TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

active_talk_sessions = {}
last_activity = {}

def save_conversation(user_id, user_message, bot_response):
    cursor.execute("""
        INSERT INTO conversation (session_id, user_message, bot_response)
        VALUES (?, ?, ?)
    """, (user_id, user_message, bot_response))
    conn.commit()

def get_conversation_history(user_id, limit=5):
    cursor.execute("""
        SELECT user_message, bot_response FROM conversation
        WHERE session_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    history = []
    for usr_msg, bot_msg in reversed(rows):
        history.append({'role': 'user', 'content': usr_msg})
        history.append({'role': 'assistant', 'content': bot_msg})
    return history

# ──────────────────────────────────────────────────────────────────────────
#                      Ollama Query + Context
# ──────────────────────────────────────────────────────────────────────────
async def query_ollama_with_context(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: str,
    user_message: str,
    model: str = "smallthinker:3b"
) -> str:
    conversation_history = get_conversation_history(user_id)
    conversation_history.append({'role': 'user', 'content': user_message})

    client = Client(host='http://localhost:11434', timeout=60)
    retries = 3
    delay = 1

    while retries > 0:
        try:
            # Show "typing" action
            await context.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)

            # Query Ollama
            response: ChatResponse = client.chat(
                model=model,
                messages=conversation_history,
                stream=False
            )
            bot_response = response.message.content

            # Save the conversation
            save_conversation(user_id, user_message, bot_response)
            return bot_response

        except Exception as e:
            print(f"Error querying Ollama: {str(e)}")
            retries -= 1
            if retries > 0:
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2
            else:
                return "Sorry, there was an error processing your request."

    return "Sorry, there was an error processing your request."

# ──────────────────────────────────────────────────────────────────────────
#                           Rank System
# ──────────────────────────────────────────────────────────────────────────
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
    return rank_cursor.fetchall()

# ──────────────────────────────────────────────────────────────────────────
#                          Bot Command Handlers
# ──────────────────────────────────────────────────────────────────────────
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leaderboard_data = get_leaderboard()
    if leaderboard_data:
        message = "🏆 Leaderboard 🏆\n"
        for rank, (username, level, xp) in enumerate(leaderboard_data, start=1):
            message += f"{rank}. {username}: Level {level}, {xp}/100 XP\n"
        await update.message.reply_text(message)
    else:
        await update.message.reply_text("No leaderboard data available yet!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Hi, I'm Kisaragi! Use /talk to start chatting, /endtalk when you’re done, and /rank to see your rank!"
    )

async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if session_id not in active_talk_sessions:
        active_talk_sessions[session_id] = []
    active_talk_sessions[session_id].append(user_id)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="I'm ready to chat! (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧"
    )

async def check_idle_users(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now()
    for (session_id, user_id), last_active in list(last_activity.items()):
        if now - last_active > datetime.timedelta(minutes=5):
            try:
                active_talk_sessions[session_id].remove(user_id)
                del last_activity[(session_id, user_id)]
                await context.bot.send_message(
                    chat_id=session_id,
                    text=f"Ending /talk session for {user_id} due to inactivity."
                )
            except (KeyError, ValueError):
                pass

# ──────────────────────────────────────────────────────────────────────────
#                       handle_message (only one!)
# ──────────────────────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "Anonymous"
    user_message = update.message.text
    session_id = str(update.effective_chat.id)

    # Always update XP
    update_xp(user_id, username)

    # Only respond if user is in /talk
    if user_id in active_talk_sessions.get(session_id, []):
        last_activity[(session_id, user_id)] = datetime.datetime.now()

        # Call the async query function
        bot_response = await query_ollama_with_context(
            update, context, user_id, user_message
        )
        formatted_response = f"**{bot_response}**"
        await update.message.reply_text(formatted_response, parse_mode="markdown")

async def rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    rank_info = get_user_rank(user_id)
    await update.message.reply_text(rank_info)

async def endtalk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if session_id in active_talk_sessions:
        try:
            active_talk_sessions[session_id].remove(user_id)
        except ValueError:
            pass
        if not active_talk_sessions[session_id]:
            del active_talk_sessions[session_id]

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="It was a great talk!"
    )

# ──────────────────────────────────────────────────────────────────────────
#                        Main Application
# ──────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('talk', talk))
    application.add_handler(CommandHandler('endtalk', endtalk))
    application.add_handler(CommandHandler('rank', rank))
    application.add_handler(CommandHandler('leaderboard', leaderboard))

    # Catch all non-command text
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue: JobQueue = application.job_queue
    job_queue.run_repeating(check_idle_users, interval=60, first=60)

    application.run_polling()