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

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("TOKEN is not set")

ADMIN_IDS = {465313785, 1935484494}

# =========================
# DB
# =========================

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

# =========================
# STATE
# =========================

USER = {}
ADMIN = {}

# =========================
# HELPERS
# =========================

def is_admin(uid):
    return uid in ADMIN_IDS


def get_quizzes():
    return conn.execute("SELECT * FROM quizzes").fetchall()


def get_questions(quiz_id):
    return conn.execute(
        "SELECT * FROM questions WHERE quiz_id=? ORDER BY position",
        (quiz_id,)
    ).fetchall()


# =========================
# USER FLOW
# =========================

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


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # admin
    if uid in ADMIN:
        await admin_flow(update, context)
        return

    quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()

    if quiz:
        USER[uid] = {
            "quiz_id": quiz["id"],
            "q": 0,
            "score": 0
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

    await update.message.reply_text("Выбери викторину")


# =========================
# QUIZ ENGINE
# =========================

async def send_question(update: Update):
    uid = update.effective_user.id
    state = USER[uid]

    qs = get_questions(state["quiz_id"])

    if state["q"] >= len(qs):
        await update.message.reply_text(
            f"🏁 Результат: {state['score']}/{len(qs)}\n\n👉 Подписка: @your_channel"
        )

        conn.execute(
            "INSERT INTO results VALUES (?,?,?)",
            (uid, state["quiz_id"], state["score"])
        )
        conn.commit()

        USER.pop(uid)
        return

    q = qs[state["q"]]
    state["answer"] = q["answer"]

    options = json.loads(q["options"])
    kb = ReplyKeyboardMarkup([[o] for o in options], resize_keyboard=True)

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=kb)
    else:
        await update.message.reply_text(q["question"], reply_markup=kb)

    asyncio.create_task(timer(update, uid))


async def timer(update, uid):
    await asyncio.sleep(20)

    if uid in USER:
        await update.message.reply_text("⏰ время вышло")
        USER[uid]["q"] += 1
        await send_question(update)


async def handle_answer(update: Update):
    uid = update.effective_user.id
    state = USER[uid]

    text = update.message.text.lower()
    correct = state["answer"].lower()

    if text == correct:
        state["score"] += 1
        await update.message.reply_text("✅ верно")
    else:
        await update.message.reply_text(f"❌ неверно\nОтвет: {state['answer']}")

    state["q"] += 1
    await send_question(update)


# =========================
# ADMIN MENU
# =========================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("❌ нет доступа")
        return

    ADMIN[uid] = {"step": "menu"}

    kb = [
        ["➕ создать викторину"],
        ["📚 список"],
        ["📊 статистика"]
    ]

    await update.message.reply_text(
        "🛠 АДМИНКА",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    st = ADMIN[uid]

    # CREATE QUIZ
    if text == "➕ создать викторину":
        st["step"] = "quiz_name"
        await update.message.reply_text("введи название")
        return

    if st.get("step") == "quiz_name":
        conn.execute("INSERT INTO quizzes(name, description) VALUES (?,?)", (text, "без описания"))
        conn.commit()
        st["step"] = "menu"
        await update.message.reply_text("✔ создано")
        return

    # LIST
    if text == "📚 список":
        qs = get_quizzes()
        kb = [[q["name"]] for q in qs]
        st["step"] = "select_quiz"

        await update.message.reply_text(
            "выбери викторину",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return

    # SELECT QUIZ
    if st.get("step") == "select_quiz":
        q = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()
        st["quiz_id"] = q["id"]
        st["step"] = "quiz_menu"

        kb = [
            ["➕ вопрос"],
            ["📋 вопросы"],
            ["🗑 удалить викторину"],
            ["✏ описание"]
        ]

        await update.message.reply_text(
            "📂 управление",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )
        return

    # ADD QUESTION
    if text == "➕ вопрос":
        st["step"] = "q_text"
        await update.message.reply_text("вопрос?")
        return

    if st.get("step") == "q_text":
        st["q"] = text
        st["step"] = "q_opts"
        await update.message.reply_text("варианты через запятую")
        return

    if st.get("step") == "q_opts":
        st["opts"] = [x.strip() for x in text.split(",")]
        st["step"] = "q_ans"
        await update.message.reply_text("правильный ответ")
        return

    if st.get("step") == "q_ans":
        st["ans"] = text
        st["step"] = "q_photo"
        await update.message.reply_text("фото или skip")
        return

    if st.get("step") == "q_photo":
        photo = update.message.photo[-1].file_id if update.message.photo else None

        pos = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE quiz_id=?",
            (st["quiz_id"],)
        ).fetchone()[0]

        conn.execute("""
            INSERT INTO questions(quiz_id, question, options, answer, photo, position)
            VALUES (?,?,?,?,?,?)
        """, (
            st["quiz_id"],
            st["q"],
            json.dumps(st["opts"]),
            st["ans"],
            photo,
            pos
        ))

        conn.commit()
        st["step"] = "quiz_menu"

        await update.message.reply_text("✔ добавлено")
        return

    # DELETE QUIZ
    if text == "🗑 удалить викторину":
        conn.execute("DELETE FROM quizzes WHERE id=?", (st["quiz_id"],))
        conn.execute("DELETE FROM questions WHERE quiz_id=?", (st["quiz_id"],))
        conn.commit()
        await update.message.reply_text("удалено")
        st["step"] = "menu"
        return

    # STATS
    if text == "📊 статистика":
        count = conn.execute("SELECT COUNT(DISTINCT user_id) FROM results").fetchone()[0]
        await update.message.reply_text(f"👥 пользователей: {count}")
        return


# =========================
# RUN
# =========================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
