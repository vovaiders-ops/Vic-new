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

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def get_quizzes():
    return conn.execute("SELECT * FROM quizzes").fetchall()


def get_questions(quiz_id):
    return conn.execute(
        "SELECT * FROM questions WHERE quiz_id=? ORDER BY position",
        (quiz_id,)
    ).fetchall()


# =====================
# USER PART
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = get_quizzes()

    kb = [[q["name"]] for q in quizzes]

    await update.message.reply_text(
        "📚 Выбери викторину\n⏱ На каждый вопрос 20 секунд",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # ADMIN FLOW
    if uid in ADMIN:
        await admin_flow(update, context)
        return

    # START QUIZ
    quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()

    if quiz:
        USER[uid] = {
            "quiz_id": quiz["id"],
            "q": 0,
            "score": 0
        }

        await update.message.reply_text(
            f"📖 {quiz['description']}\n\nСтартуем!",
            reply_markup=ReplyKeyboardRemove()
        )

        await send_question(update)
        return

    # ANSWER FLOW
    if uid in USER:
        await handle_answer(update)
        return

    await update.message.reply_text("Выбери викторину из меню")


# =====================
# QUIZ LOGIC
# =====================

async def send_question(update: Update):
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

    options = json.loads(q["options"])

    kb = ReplyKeyboardMarkup([[o] for o in options], resize_keyboard=True)

    if q["photo"]:
        await update.message.reply_photo(
            q["photo"],
            caption=q["question"],
            reply_markup=kb
        )
    else:
        await update.message.reply_text(
            q["question"],
            reply_markup=kb
        )

    asyncio.create_task(question_timer(update, uid, 20))


async def question_timer(update, uid, sec):
    await asyncio.sleep(sec)

    if uid in USER:
        await update.message.reply_text("⏰ Время вышло")
        USER[uid]["q"] += 1
        await send_question(update)


async def handle_answer(update: Update):
    uid = update.effective_user.id
    state = USER[uid]

    text = update.message.text.lower()
    correct = state["answer"].lower()

    if text == correct:
        state["score"] += 1
        await update.message.reply_text("✅ Верно")
    else:
        await update.message.reply_text(f"❌ Неверно\nОтвет: {state['answer']}")

    state["q"] += 1
    await send_question(update)


# =====================
# ADMIN PANEL
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
    state = ADMIN[uid]

    # CREATE QUIZ
    if text == "➕ Викторина":
        state["step"] = "create_quiz"
        await update.message.reply_text("Название викторины:")
        return

    if state["step"] == "create_quiz":
        conn.execute(
            "INSERT INTO quizzes(name, description) VALUES (?,?)",
            (text, "Описание")
        )
        conn.commit()

        state["step"] = "menu"
        await update.message.reply_text("✔ Создано")
        return

    # LIST QUIZZES
    if text == "📚 Список":
        quizzes = get_quizzes()

        kb = [[q["name"]] for q in quizzes]

        state["step"] = "select_quiz"

        await update.message.reply_text(
            "Выбери викторину",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return

    # SELECT QUIZ
    if state["step"] == "select_quiz":
        quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()

        state["quiz_id"] = quiz["id"]
        state["step"] = "quiz_menu"

        kb = [["➕ Вопрос", "📋 Вопросы"]]

        await update.message.reply_text(
            "📂 Управление",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return

    # ADD QUESTION
    if text == "➕ Вопрос":
        state["step"] = "q_text"
        await update.message.reply_text("Вопрос?")
        return

    if state["step"] == "q_text":
        state["question"] = text
        state["step"] = "q_options"
        await update.message.reply_text("Варианты через запятую")
        return

    if state["step"] == "q_options":
        state["options"] = [x.strip() for x in text.split(",")]
        state["step"] = "q_answer"
        await update.message.reply_text("Правильный ответ")
        return

    if state["step"] == "q_answer":
        state["answer"] = text
        state["step"] = "q_photo"
        await update.message.reply_text("Фото (отправь или напиши skip)")
        return

    if state["step"] == "q_photo":

        photo = None

        if update.message.photo:
            photo = update.message.photo[-1].file_id

        pos = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE quiz_id=?",
            (state["quiz_id"],)
        ).fetchone()[0]

        conn.execute("""
            INSERT INTO questions(quiz_id, question, options, answer, photo, position)
            VALUES (?,?,?,?,?,?)
        """, (
            state["quiz_id"],
            state["question"],
            json.dumps(state["options"]),
            state["answer"],
            photo,
            pos
        ))

        conn.commit()

        state["step"] = "quiz_menu"
        await update.message.reply_text("✔ Добавлено")
        return


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
