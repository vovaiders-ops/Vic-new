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
# USER
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = get_quizzes()

    kb = [[q["name"]] for q in quizzes]

    await update.message.reply_text(
        "📚 Выбери викторину\n⏱ 20 секунд на вопрос",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    if uid in ADMIN:
        await admin_flow(update, context)
        return

    quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()

    if quiz:
        USER[uid] = {
            "quiz_id": quiz["id"],
            "q": 0,
            "score": 0,
            "active": True
        }

        await update.message.reply_text(
            f"📖 {quiz['description']}\n\nСтарт!",
            reply_markup=ReplyKeyboardRemove()
        )

        await send_question(update, context)
        return

    if uid in USER:
        await handle_answer(update, context)
        return

    await update.message.reply_text("Выбери викторину")


# =====================
# QUIZ LOGIC (FIXED)
# =====================

async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = USER[uid]

    qs = get_questions(state["quiz_id"])

    if state["q"] >= len(qs):
        await update.message.reply_text(
            f"🏁 Результат: {state['score']}/{len(qs)}\n\nПодпишись: @your_channel"
        )
        conn.execute("INSERT INTO results VALUES (?,?)", (uid, state["score"]))
        conn.commit()

        USER.pop(uid)
        return

    q = qs[state["q"]]

    state["answer"] = q["answer"]
    state["question_id"] = q["id"]
    state["active"] = True

    options = json.loads(q["options"])

    kb = ReplyKeyboardMarkup([[o] for o in options], resize_keyboard=True)

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=kb)
    else:
        await update.message.reply_text(q["question"], reply_markup=kb)

    # JOB TIMER (SAFE)
    context.job_queue.run_once(
        timeout_job,
        20,
        data={"uid": uid, "qid": q["id"]}
    )


async def timeout_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    uid = job.data["uid"]
    qid = job.data["qid"]

    if uid not in USER:
        return

    state = USER[uid]

    # если уже ответили — игнор
    if not state.get("active") or state.get("question_id") != qid:
        return

    state["q"] += 1
    state["active"] = False

    await context.bot.send_message(uid, "⏰ Время вышло")
    await send_question_fake(context, uid)


async def send_question_fake(context, uid):
    # безопасный перескок вопроса
    if uid not in USER:
        return

    update = type("obj", (), {"effective_user": type("obj", (), {"id": uid}), "message": None})
    await send_question(update, context)


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = USER[uid]

    if not state.get("active"):
        return

    text = (update.message.text or "").strip().lower()
    correct = (state.get("answer") or "").strip().lower()

    state["active"] = False

    if text == correct:
        state["score"] += 1
        await update.message.reply_text("✅ Верно")
    else:
        await update.message.reply_text(f"❌ Неверно\nОтвет: {state['answer']}")

    state["q"] += 1
    await send_question(update, context)


# =====================
# ADMIN (SAFE)
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("❌ Нет доступа")
        return

    ADMIN[uid] = {"step": "menu"}

    kb = [["➕ Викторина", "📚 Список"]]

    await update.message.reply_text(
        "🛠 Админка",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    state = ADMIN.get(uid, {"step": "menu"})

    ADMIN[uid] = state

    if text == "➕ Викторина":
        state["step"] = "create_quiz"
        await update.message.reply_text("Название:")
        return

    if state["step"] == "create_quiz":
        conn.execute("INSERT INTO quizzes(name, description) VALUES (?,?)", (text, "Описание"))
        conn.commit()

        state["step"] = "menu"
        await update.message.reply_text("✔ Создано")
        return


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
