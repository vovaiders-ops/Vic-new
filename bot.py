import os
import json
import sqlite3

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =====================
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN is not set")

ADMIN_IDS = {465313785}

TG_CHANNEL = "https://t.me/videt_i_slyshat"
VK_PAGE = "https://vk.com/art_in_church"

BACK = "🔙 Назад"
CHANGE = "📚 Выбрать другую викторину"

# =====================
# DB
# =====================

conn = sqlite3.connect("quiz.db", check_same_thread=False)
conn.row_factory = sqlite3.Row

conn.execute("""
CREATE TABLE IF NOT EXISTS quizzes (
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT,
description TEXT DEFAULT ''
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS questions (
id INTEGER PRIMARY KEY AUTOINCREMENT,
quiz_id INTEGER,
question TEXT,
options TEXT,
answer TEXT
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS stats (
user_id INTEGER PRIMARY KEY,
games INTEGER DEFAULT 0,
score INTEGER DEFAULT 0
)
""")

conn.commit()

# =====================
USER = {}
ADMIN = {}

# =====================
def txt(update):
    return update.message.text.strip() if update.message and update.message.text else None


def quizzes():
    return conn.execute("SELECT * FROM quizzes").fetchall()


def questions(qid):
    return conn.execute(
        "SELECT * FROM questions WHERE quiz_id=?",
        (qid,)
    ).fetchall()


# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER.pop(update.effective_user.id, None)

    qz = quizzes()
    if not qz:
        return await update.message.reply_text("❌ нет викторин")

    kb = [[f"{q['id']} - {q['name']}"] for q in qz]

    await update.message.reply_text(
        "📚 выбери викторину:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


# =====================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = txt(update)

    if not text:
        return

    # 📚 смена викторины
    if text == CHANGE:
        USER.pop(uid, None)
        return await start(update, context)

    # 🔙 назад
    if text == BACK:
        USER.pop(uid, None)
        ADMIN.pop(uid, None)
        return await update.message.reply_text("↩ вышли", reply_markup=ReplyKeyboardRemove())

    # ADMIN
    if uid in ADMIN:
        return await admin(update, context)

    # выбор викторины
    if uid not in USER:
        if " - " not in text:
            return await update.message.reply_text("нажми /start")

        try:
            qid = int(text.split(" - ")[0])
        except:
            return await update.message.reply_text("ошибка")

        USER[uid] = {"qid": qid, "i": 0, "score": 0, "ans": None}

        await update.message.reply_text("▶ старт", reply_markup=ReplyKeyboardRemove())
        return await send(update)

    return await answer(update)


# =====================
async def send(update):
    uid = update.effective_user.id
    st = USER.get(uid)

    qs = questions(st["qid"])

    if st["i"] >= len(qs):
        return await finish(update)

    q = qs[st["i"]]
    st["ans"] = q["answer"]

    try:
        opts = json.loads(q["options"])
    except:
        opts = []

    kb = ReplyKeyboardMarkup([[o] for o in opts], resize_keyboard=True)

    await update.message.reply_text(q["question"], reply_markup=kb)


# =====================
async def answer(update):
    uid = update.effective_user.id
    st = USER.get(uid)
    text = txt(update)

    if not st:
        return

    if text.lower().strip() == st["ans"].lower().strip():
        st["score"] += 1
        await update.message.reply_text("✅")
    else:
        await update.message.reply_text(f"❌ {st['ans']}")

    st["i"] += 1
    await send(update)


# =====================
async def finish(update):
    uid = update.effective_user.id
    st = USER.pop(uid, None)

    conn.execute("""
        INSERT INTO stats(user_id, games, score)
        VALUES (?,1,?)
        ON CONFLICT(user_id) DO UPDATE SET
        games = games + 1,
        score = score + excluded.score
    """, (uid, st["score"]))
    conn.commit()

    kb = ReplyKeyboardMarkup([[CHANGE]], resize_keyboard=True)

    await update.message.reply_text(
        f"🏁 результат: {st['score']}\n\nTG: {TG_CHANNEL}\nVK: {VK_PAGE}",
        reply_markup=kb
    )


# =====================
# ADMIN
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if uid not in ADMIN_IDS:
        return await update.message.reply_text("нет доступа")

    ADMIN[uid] = True
    await update.message.reply_text("🛠 админ режим")


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
