import os
import json
import sqlite3
import asyncio

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =====================
# CONFIG
# =====================

TOKEN = os.getenv("TOKEN")
ADMIN_IDS = {465313785, 1935484494}

if not TOKEN:
    raise RuntimeError("TOKEN is not set")

# =====================
# DB
# =====================

conn = sqlite3.connect("quiz.db", check_same_thread=False)
conn.row_factory = sqlite3.Row

conn.execute("""
CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    description TEXT DEFAULT ''
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id INTEGER,
    question TEXT,
    options TEXT,
    answer TEXT,
    photo TEXT,
    position INTEGER DEFAULT 0
)
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS results (
    user_id INTEGER,
    score INTEGER
)
""")

conn.commit()

# =====================
# STATE
# =====================

USER = {}
ADMIN = {}

# =====================
# HELPERS
# =====================

def is_admin(uid: int):
    return uid in ADMIN_IDS


def get_quizzes():
    return conn.execute("SELECT * FROM quizzes").fetchall()


def get_questions(quiz_id):
    return conn.execute(
        "SELECT * FROM questions WHERE quiz_id=? ORDER BY position",
        (quiz_id,)
    ).fetchall()


def reset_user(uid):
    if uid in USER:
        USER.pop(uid)


# =====================
# START
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = get_quizzes()

    if not quizzes:
        await update.message.reply_text("❌ Пока нет викторин")
        return

    kb = [[q["name"]] for q in quizzes]

    await update.message.reply_text(
        "📚 Выбери викторину\n⏱ 20 секунд на вопрос",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


# =====================
# USER FLOW
# =====================

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # ADMIN
    if uid in ADMIN:
        await admin_flow(update, context)
        return

    quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()

    # выбрать викторину
    if quiz:
        USER[uid] = {
            "quiz_id": quiz["id"],
            "q": 0,
            "score": 0,
            "answered": False
        }

        kb = [["⬅ назад"]]

        await update.message.reply_text(
            f"📖 {quiz['description']}\n\nНажми чтобы начать",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return

    # назад
    if text == "⬅ назад":
        reset_user(uid)
        await start(update, context)
        return

    # ответ
    if uid in USER:
        state = USER[uid]

        if state.get("answered"):
            return

        qs = get_questions(state["quiz_id"])

        if state["q"] >= len(qs):
            return

        q = qs[state["q"]]

        if text.lower() == q["answer"].lower():
            state["score"] += 1
            await update.message.reply_text("✅ Верно")
        else:
            await update.message.reply_text(f"❌ Неверно\nОтвет: {q['answer']}")

        state["answered"] = True
        return

    await update.message.reply_text("Выбери викторину")


# =====================
# QUESTIONS
# =====================

async def send_question(update: Update, uid: int):
    state = USER[uid]
    qs = get_questions(state["quiz_id"])

    if state["q"] >= len(qs):
        await update.message.reply_text(
            f"🏁 Результат: {state['score']}/{len(qs)}\n\nПодпишись на канал"
        )
        conn.execute("INSERT INTO results VALUES (?,?)", (uid, state["score"]))
        conn.commit()
        reset_user(uid)
        return

    q = qs[state["q"]]
    state["answered"] = False
    state["answer"] = q["answer"]

    options = json.loads(q["options"])
    kb = ReplyKeyboardMarkup([[o] for o in options], resize_keyboard=True)

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=kb)
    else:
        await update.message.reply_text(q["question"], reply_markup=kb)

    asyncio.create_task(timer(update, uid, 20))


async def timer(update, uid, sec):
    await asyncio.sleep(sec)

    if uid not in USER:
        return

    if USER[uid].get("answered"):
        return

    await update.message.reply_text("⏰ Время вышло")

    USER[uid]["q"] += 1
    await send_question(update, uid)


# =====================
# ADMIN
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("❌ Нет доступа")
        return

    ADMIN[uid] = {"step": "menu"}

    kb = [
        ["➕ Викторина", "✏️ Описание"],
        ["📚 Список", "🗑 Удалить"]
    ]

    await update.message.reply_text(
        "🛠 Админ панель",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    state = ADMIN[uid]

    # create quiz
    if text == "➕ Викторина":
        state["step"] = "quiz_name"
        await update.message.reply_text("Название викторины:")
        return

    if state.get("step") == "quiz_name":
        conn.execute("INSERT INTO quizzes(name) VALUES(?)", (text,))
        conn.commit()
        state["step"] = "menu"
        await update.message.reply_text("✔ создано")
        return

    # edit description
    if text == "✏️ Описание":
        state["step"] = "desc_select"
        await update.message.reply_text("Напиши название викторины:")
        return

    if state.get("step") == "desc_select":
        state["quiz_name"] = text
        state["step"] = "desc_text"
        await update.message.reply_text("Новое описание:")
        return

    if state.get("step") == "desc_text":
        conn.execute(
            "UPDATE quizzes SET description=? WHERE name=?",
            (text, state["quiz_name"])
        )
        conn.commit()
        state["step"] = "menu"
        await update.message.reply_text("✔ обновлено")
        return

    # list
    if text == "📚 Список":
        quizzes = get_quizzes()
        msg = "\n".join([q["name"] for q in quizzes]) or "пусто"
        await update.message.reply_text(msg)
        return

    # delete
    if text == "🗑 Удалить":
        conn.execute("DELETE FROM quizzes")
        conn.execute("DELETE FROM questions")
        conn.commit()
        await update.message.reply_text("✔ удалено всё")
        return


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
