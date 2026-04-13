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
if not TOKEN:
    raise RuntimeError("TOKEN is not set")

ADMIN_IDS = {465313785, 1935484494}

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

def is_admin(uid):
    return uid in ADMIN_IDS


def get_quizzes():
    return conn.execute("SELECT * FROM quizzes").fetchall()


def get_questions(quiz_id):
    return conn.execute(
        "SELECT * FROM questions WHERE quiz_id=? ORDER BY position",
        (quiz_id,)
    ).fetchall()


# =====================
# USER FLOW
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = get_quizzes()

    if not quizzes:
        await update.message.reply_text("📭 Викторин пока нет")
        return

    kb = [[q["name"]] for q in quizzes]

    await update.message.reply_text(
        "📚 Выбери викторину\n⏱ 20 секунд на вопрос",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def send_question(update: Update):
    uid = update.effective_user.id
    st = USER[uid]

    qs = get_questions(st["quiz_id"])

    if st["q"] >= len(qs):
        await update.message.reply_text(
            f"🏁 Результат: {st['score']}/{len(qs)}"
        )

        conn.execute("INSERT INTO results VALUES (?,?)", (uid, st["score"]))
        conn.commit()

        USER.pop(uid)
        return

    q = qs[st["q"]]

    st["answered"] = False
    st["answer"] = q["answer"]

    options = json.loads(q["options"])
    kb = ReplyKeyboardMarkup([[o] for o in options], resize_keyboard=True)

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=kb)
    else:
        await update.message.reply_text(q["question"], reply_markup=kb)

    st["timer_task"] = asyncio.create_task(timer(update, uid))


async def timer(update, uid):
    await asyncio.sleep(20)

    if uid in USER and not USER[uid]["answered"]:
        USER[uid]["q"] += 1
        await update.message.reply_text("⏰ Время вышло")
        await send_question(update)


async def handle_answer(update: Update):
    uid = update.effective_user.id
    st = USER[uid]

    # 🔐 ЗАЩИТА ОТ ПОВТОРНОГО ОТВЕТА
    if st["answered"]:
        return

    st["answered"] = True

    # отменяем таймер
    if "timer_task" in st:
        st["timer_task"].cancel()

    text = update.message.text.lower()
    correct = st["answer"].lower()

    if text == correct:
        st["score"] += 1
        await update.message.reply_text("✅ Верно")
    else:
        await update.message.reply_text(f"❌ Неверно: {st['answer']}")

    st["q"] += 1
    await send_question(update)


# =====================
# ADMIN (STABLE MENU)
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        return await update.message.reply_text("❌ Нет доступа")

    ADMIN[uid] = {"step": "menu"}

    await update.message.reply_text(
        "🛠 АДМИН МЕНЮ\n\n"
        "1 - создать викторину\n"
        "2 - список викторин\n"
        "3 - удалить викторину\n"
        "4 - статистика"
    )


# =====================
# MAIN HANDLER
# =====================

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # ADMIN FLOW
    if uid in ADMIN:
        await admin_flow(update)
        return

    # START QUIZ
    quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()

    if quiz:
        USER[uid] = {
            "quiz_id": quiz["id"],
            "q": 0,
            "score": 0,
            "answered": False
        }

        await update.message.reply_text(
            f"📖 {quiz['description']}",
            reply_markup=ReplyKeyboardRemove()
        )

        await send_question(update)
        return

    if uid in USER:
        await handle_answer(update)
        return

    await update.message.reply_text("Используй /start")


# =====================
# ADMIN FLOW (FIXED)
# =====================

async def admin_flow(update: Update):
    uid = update.effective_user.id
    text = update.message.text

    st = ADMIN[uid]

    # 1 - create quiz
    if text == "1":
        st["step"] = "create_quiz"
        return await update.message.reply_text("Название викторины:")

    if st["step"] == "create_quiz":
        conn.execute("INSERT INTO quizzes(name) VALUES(?)", (text,))
        conn.commit()
        st["step"] = "menu"
        return await update.message.reply_text("✔ создано")

    # 2 - list
    if text == "2":
        quizzes = get_quizzes()
        msg = "\n".join([q["name"] for q in quizzes]) or "пусто"
        return await update.message.reply_text(msg)

    # 3 - delete
    if text == "3":
        st["step"] = "delete"
        quizzes = get_quizzes()
        kb = [[q["name"]] for q in quizzes]
        return await update.message.reply_text(
            "Выбери для удаления",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )

    if st["step"] == "delete":
        conn.execute("DELETE FROM quizzes WHERE name=?", (text,))
        conn.commit()
        st["step"] = "menu"
        return await update.message.reply_text("🗑 удалено")

    # 4 - stats
    if text == "4":
        total_users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM results").fetchone()[0]
        avg = conn.execute("SELECT AVG(score) FROM results").fetchone()[0]

        return await update.message.reply_text(
            f"📊 СТАТИСТИКА\n\n"
            f"👥 пользователи: {total_users}\n"
            f"📈 средний балл: {round(avg or 0, 2)}"
        )


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
