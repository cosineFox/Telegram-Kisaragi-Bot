import logging
import sys
import sqlite3
import os
import datetime
import asyncio
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.abspath("../Telegram-Kisaragi-Bot"))
from dotenv import load_dotenv

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

DB_PATH = "../Telegram-Kisaragi-Bot/bot/conversations.sqlite3"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_message TEXT,
    bot_response TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

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

active_talk_sessions = {}
executor = ThreadPoolExecutor()
client = Client(host='http://localhost:11434', timeout=60)

def save_conversation(user_id, user_message, bot_response):
    cursor.execute("""
        INSERT INTO conversation (user_id, user_message, bot_response)
        VALUES (?, ?, ?)
    """, (user_id, user_message, bot_response))
    conn.commit()

async def save_conversation_async(user_id, user_message, bot_response):
    await asyncio.get_event_loop().run_in_executor(
        None,
        save_conversation,
        user_id, user_message, bot_response
    )

def get_conversation_history(user_id, limit=5):
    cursor.execute("""
        SELECT user_message, bot_response FROM conversation
        WHERE user_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """, (user_id, limit))
    rows = cursor.fetchall()
    history = []
    for usr_msg, bot_msg in reversed(rows):
        history.append({'role': 'user', 'content': usr_msg})
        history.append({'role': 'assistant', 'content': bot_msg})
    return history

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
        xp += 10  # You can adjust the XP gain here
        if xp >= 100:  # You can adjust the level-up threshold here
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

async def query_model(user_message: str, user_id: str) -> str:
    history = get_conversation_history(user_id)  # Retrieve user conversation history

    try:
        response = client.chat(
            model="smallthinker:3b",
            messages=[
                {"role": "system", "content": "You are Kisaragi, a playful fox-girl maid who loves helping Master with tasks. Stay polite, charming, and maintain your personality."},
                *history,
                {"role": "user", "content": user_message}
            ],
            stream=False
        )
        return response.message.content

    except Exception as e:
        logging.error(f"Error querying Smallthinker model: {e}")
        return "Sorry, I encountered an error processing your request."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        # Ignore updates without messages or text
        return

    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "Anonymous"
    user_message = update.message.text

    # Update XP whenever a message is processed
    update_xp(user_id, username)

    if user_id in active_talk_sessions.get(str(update.effective_chat.id), set()):
        # Indicate the bot is typing
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        bot_response = await query_model(user_message, user_id)
        await save_conversation_async(user_id, user_message, bot_response)
        await update.message.reply_text(f"**{bot_response}**", parse_mode="markdown")

async def talk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if session_id not in active_talk_sessions:
        active_talk_sessions[session_id] = set()

    active_talk_sessions[session_id].add(user_id)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="I'm ready to chat, Master! (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧"
    )

async def endtalk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if session_id in active_talk_sessions and user_id in active_talk_sessions[session_id]:
        active_talk_sessions[session_id].remove(user_id)
        if not active_talk_sessions[session_id]:
            del active_talk_sessions[session_id]

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="It was a great talk, Master! (´｡• ᵕ •｡`)")
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="You're not in a conversation with me, Master! Use /talk to start chatting! (・・；)")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    leaderboard_data = get_leaderboard()
    if leaderboard_data:
        message = "🏆 Leaderboard 🏆\n"
        for rank, (username, level, xp) in enumerate(leaderboard_data, start=1):
            message += f"{rank}. {username}: Level {level}, {xp}/100 XP\n"
        await update.message.reply_text(message)
    else:
        await update.message.reply_text("No leaderboard data available yet!")

if __name__ == '__main__':
    try:
        application = ApplicationBuilder().token(TOKEN).build()

        application.add_handler(CommandHandler('talk', talk))
        application.add_handler(CommandHandler('endtalk', endtalk))
        application.add_handler(CommandHandler('leaderboard', leaderboard))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        application.run_polling()
    finally:
        executor.shutdown(wait=True)
