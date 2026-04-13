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
    quiz_id INTEGER,
    score INTEGER
)
""")

conn.commit()

# =====================
# STATE
# =====================

USER = {}
ADMIN = {}
ACTIVE_TIMERS = {}

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

    if not quizzes:
        await update.message.reply_text("❌ нет викторин")
        return

    kb = [[q["name"]] for q in quizzes]

    await update.message.reply_text(
        "📚 Выбери викторину\n⏱ 20 секунд на вопрос",
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
            "q_index": 0,
            "score": 0,
            "locked": False
        }

        await update.message.reply_text(
            f"📖 {quiz['description']}\n\nСтарт!",
            reply_markup=ReplyKeyboardRemove()
        )

        await send_question(update, uid)
        return

    # ANSWER
    if uid in USER:
        await handle_answer(update, uid)
        return

    await update.message.reply_text("выбери викторину")


# =====================
# QUIZ ENGINE (FIXED)
# =====================

async def send_question(update: Update, uid: int):
    state = USER.get(uid)
    if not state:
        return

    qs = get_questions(state["quiz_id"])

    if state["q_index"] >= len(qs):
        await update.message.reply_text(
            f"🏁 результат: {state['score']}/{len(qs)}"
        )

        conn.execute(
            "INSERT INTO results VALUES (?,?,?)",
            (uid, state["quiz_id"], state["score"])
        )
        conn.commit()

        USER.pop(uid, None)
        return

    q = qs[state["q_index"]]

    state["current_qid"] = q["id"]
    state["answer"] = q["answer"]
    state["locked"] = False

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

    # CANCEL OLD TIMER
    if uid in ACTIVE_TIMERS:
        ACTIVE_TIMERS[uid].cancel()

    ACTIVE_TIMERS[uid] = asyncio.create_task(timer(update, uid, q["id"]))


async def timer(update, uid, qid):
    try:
        await asyncio.sleep(20)

        state = USER.get(uid)
        if state and state.get("current_qid") == qid and not state["locked"]:
            await update.message.reply_text("⏰ время вышло")
            state["q_index"] += 1
            await send_question(update, uid)

    except asyncio.CancelledError:
        pass


async def handle_answer(update: Update, uid: int):
    state = USER[uid]

    if state["locked"]:
        return

    state["locked"] = True

    text = update.message.text.lower()
    correct = state["answer"].lower()

    if text == correct:
        state["score"] += 1
        await update.message.reply_text("✅ верно")
    else:
        await update.message.reply_text(f"❌ неверно\nответ: {state['answer']}")

    state["q_index"] += 1
    await send_question(update, uid)


# =====================
# ADMIN (stable skeleton)
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("❌ нет доступа")
        return

    ADMIN[uid] = {"step": "menu"}

    kb = [
        ["➕ викторина"],
        ["📚 список"]
    ]

    await update.message.reply_text(
        "🛠 админка",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    st = ADMIN[uid]

    if text == "➕ викторина":
        st["step"] = "quiz_name"
        await update.message.reply_text("название?")
        return

    if st.get("step") == "quiz_name":
        conn.execute("INSERT INTO quizzes(name, description) VALUES (?,?)", (text, ""))
        conn.commit()
        st["step"] = "menu"
        await update.message.reply_text("✔ создано")
        return


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
