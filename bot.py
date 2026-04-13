import os
import json
import sqlite3
import asyncio

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("TOKEN")

if not TOKEN:
    raise RuntimeError("TOKEN not set")

ADMIN_IDS = {465313785, 1935484494}

# ================= DB =================

conn = sqlite3.connect("quiz.db", check_same_thread=False)
conn.row_factory = sqlite3.Row

def db():
    return conn

def init_db():
    cur = db().cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS quizzes (
        name TEXT PRIMARY KEY
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quiz TEXT,
        text TEXT,
        options TEXT,
        answer TEXT,
        photo TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        score INTEGER DEFAULT 0
    )
    """)

    conn.commit()

init_db()

# ================= STATE =================

USER = {}
ADMIN = {}

# ================= HELPERS =================

def is_admin(uid):
    return uid in ADMIN_IDS

def norm(t):
    return (t or "").strip().lower()

def get_quizzes():
    cur = db().cursor()
    cur.execute("SELECT name FROM quizzes")
    return [r["name"] for r in cur.fetchall()]

def get_questions(quiz):
    cur = db().cursor()
    cur.execute("SELECT * FROM questions WHERE quiz=?", (quiz,))
    return cur.fetchall()

# ================= USER =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = get_quizzes()

    if not quizzes:
        await update.message.reply_text("Пока нет викторин")
        return

    kb = ReplyKeyboardMarkup([[q] for q in quizzes], resize_keyboard=True)
    await update.message.reply_text("Выбери викторину:", reply_markup=kb)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    if uid in ADMIN:
        await admin_flow(update)
        return

    if text in get_quizzes():
        USER[uid] = {"quiz": text, "q": 0, "score": 0}
        await send_question(update)
        return

    if uid in USER:
        await answer(update)
        return


async def send_question(update: Update):
    uid = update.effective_user.id
    state = USER[uid]

    qs = get_questions(state["quiz"])

    if state["q"] >= len(qs):
        score = state["score"]

        db().execute(
            "INSERT OR IGNORE INTO users(user_id, score) VALUES(?,0)", (uid,)
        )
        db().execute(
            "UPDATE users SET score = score + ? WHERE user_id=?",
            (score, uid),
        )
        conn.commit()

        await update.message.reply_text(
            f"🎉 Готово! {score}/{len(qs)}\n\nПодпишись 👉 @your_channel",
            reply_markup=ReplyKeyboardRemove(),
        )

        USER.pop(uid)
        return

    q = qs[state["q"]]

    kb = ReplyKeyboardMarkup(
        [[o] for o in json.loads(q["options"])], resize_keyboard=True
    )

    state["answer"] = q["answer"]

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["text"], reply_markup=kb)
    else:
        await update.message.reply_text(q["text"], reply_markup=kb)

    asyncio.create_task(timer(update, uid))


async def timer(update, uid):
    await asyncio.sleep(20)

    if uid not in USER:
        return

    state = USER[uid]
    await update.message.reply_text(f"⏰ Время вышло!\nОтвет: {state['answer']}")

    state["q"] += 1
    await send_question(update)


async def answer(update: Update):
    uid = update.effective_user.id
    state = USER[uid]

    if norm(update.message.text) == norm(state["answer"]):
        state["score"] += 1
        await update.message.reply_text("✅ Верно")
    else:
        await update.message.reply_text(f"❌ Неверно\n{state['answer']}")

    state["q"] += 1
    await send_question(update)

# ================= ADMIN =================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    ADMIN[update.effective_user.id] = {"step": "quiz"}
    await update.message.reply_text("Название викторины:")


async def admin_flow(update: Update):
    uid = update.effective_user.id
    text = update.message.text
    state = ADMIN[uid]

    if state["step"] == "quiz":
        state["quiz"] = text
        db().execute("INSERT OR IGNORE INTO quizzes VALUES(?)", (text,))
        conn.commit()

        state["step"] = "question"
        await update.message.reply_text("Вопрос:")
        return

    if state["step"] == "question":
        state["question"] = text
        state["step"] = "options"
        await update.message.reply_text("Варианты через запятую:")
        return

    if state["step"] == "options":
        state["options"] = [x.strip() for x in text.split(",")]
        state["step"] = "answer"
        await update.message.reply_text("Правильный ответ:")
        return

    if state["step"] == "answer":
        state["answer"] = text
        state["step"] = "photo"
        await update.message.reply_text("Отправь фото или напиши 'нет'")
        return

    if state["step"] == "photo":
        photo = None

        if update.message.photo:
            photo = update.message.photo[-1].file_id

        db().execute(
            "INSERT INTO questions VALUES(NULL,?,?,?,?,?)",
            (
                state["quiz"],
                state["question"],
                json.dumps(state["options"]),
                state["answer"],
                photo,
            ),
        )
        conn.commit()

        state["step"] = "question"
        await update.message.reply_text("✅ Добавлено")

# ================= MAIN =================

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(MessageHandler(filters.ALL, handle))

    print("BOT STARTED 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()