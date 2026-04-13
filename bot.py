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
TIME_LIMIT = 20

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
TIMERS = {}

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


def cancel_timer(uid):
    if uid in TIMERS:
        TIMERS[uid].cancel()
        del TIMERS[uid]

# =====================
# USER SIDE
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = get_quizzes()

    if not quizzes:
        await update.message.reply_text("Пока нет викторин")
        return

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
            "answered": False
        }

        await update.message.reply_text(
            f"📖 {quiz['description']}\n\nСтарт!",
            reply_markup=ReplyKeyboardRemove()
        )

        await send_question(update)
        return

    if uid in USER:
        await handle_answer(update)
        return

    await update.message.reply_text("Выбери викторину через /start")


async def send_question(update: Update):
    uid = update.effective_user.id
    state = USER[uid]

    qs = get_questions(state["quiz_id"])

    if state["q"] >= len(qs):
        conn.execute("INSERT INTO results VALUES (?,?)", (uid, state["score"]))
        conn.commit()

        await update.message.reply_text(
            f"🏁 Итог: {state['score']}/{len(qs)}\n\nПодписывайся: @your_channel"
        )

        USER.pop(uid, None)
        return

    q = qs[state["q"]]
    state["answer"] = q["answer"]
    state["answered"] = False

    options = json.loads(q["options"])
    kb = ReplyKeyboardMarkup([[o] for o in options], resize_keyboard=True)

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=kb)
    else:
        await update.message.reply_text(q["question"], reply_markup=kb)

    cancel_timer(uid)
    TIMERS[uid] = asyncio.create_task(timer(update, uid))


async def timer(update, uid):
    await asyncio.sleep(TIME_LIMIT)

    if uid in USER and not USER[uid]["answered"]:
        USER[uid]["q"] += 1
        await update.message.reply_text("⏰ время вышло")
        await send_question(update)


async def handle_answer(update: Update):
    uid = update.effective_user.id
    state = USER[uid]

    if state["answered"]:
        return

    state["answered"] = True
    cancel_timer(uid)

    text = update.message.text.lower()
    correct = state["answer"].lower()

    if text == correct:
        state["score"] += 1
        await update.message.reply_text("✅ верно")
    else:
        await update.message.reply_text(f"❌ неверно\nОтвет: {state['answer']}")

    state["q"] += 1
    await send_question(update)

# =====================
# ADMIN SIDE (MENU)
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("нет доступа")
        return

    ADMIN[uid] = {"step": "menu"}

    kb = [
        ["1 - создать викторину"],
        ["2 - список викторин"],
        ["3 - статистика"],
        ["0 - выход"]
    ]

    await update.message.reply_text(
        "🛠 АДМИН ПАНЕЛЬ",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    state = ADMIN[uid]

    # EXIT
    if text == "0 - выход":
        ADMIN.pop(uid, None)
        await update.message.reply_text("выход", reply_markup=ReplyKeyboardRemove())
        return

    # CREATE QUIZ
    if text == "1 - создать викторину":
        state["step"] = "create_quiz"
        await update.message.reply_text("название викторины:")
        return

    if state["step"] == "create_quiz":
        conn.execute("INSERT INTO quizzes(name) VALUES (?)", (text,))
        conn.commit()
        state["step"] = "menu"
        await update.message.reply_text("создано")
        return

    # LIST QUIZZES
    if text == "2 - список викторин":
        qs = get_quizzes()
        kb = [[q["name"]] for q in qs]

        state["step"] = "select_quiz"

        await update.message.reply_text(
            "выбери викторину",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return

    if state["step"] == "select_quiz":
        quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()

        state["quiz_id"] = quiz["id"]
        state["step"] = "quiz_menu"

        kb = [
            ["1 добавить вопрос"],
            ["2 список вопросов"],
            ["3 удалить викторину"]
        ]

        await update.message.reply_text(
            "📂 управление",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return

    # QUIZ MENU
    if text == "1 добавить вопрос":
        state["step"] = "q_text"
        await update.message.reply_text("вопрос?")
        return

    if state["step"] == "q_text":
        state["question"] = text
        state["step"] = "q_options"
        await update.message.reply_text("варианты через запятую")
        return

    if state["step"] == "q_options":
        state["options"] = [x.strip() for x in text.split(",")]
        state["step"] = "q_answer"
        await update.message.reply_text("правильный ответ")
        return

    if state["step"] == "q_answer":
        state["answer"] = text
        state["step"] = "q_photo"
        await update.message.reply_text("отправь фото или напиши skip")
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

        await update.message.reply_text("добавлено")
        return

    # DELETE QUIZ
    if text == "3 удалить викторину":
        conn.execute("DELETE FROM quizzes WHERE id=?", (state["quiz_id"],))
        conn.execute("DELETE FROM questions WHERE quiz_id=?", (state["quiz_id"],))
        conn.commit()

        await update.message.reply_text("удалено")
        state["step"] = "menu"
        return

    # STATS
    if text == "3 - статистика":
        count = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        await update.message.reply_text(f"игроков всего: {count}")
        return


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
