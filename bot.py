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

ADMIN_IDS = {465313785}

TG_CHANNEL = "https://t.me/videt_i_slyshat"
VK_PAGE = "https://vk.com/art_in_church"

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
CREATE TABLE IF NOT EXISTS stats (
    user_id INTEGER PRIMARY KEY,
    games INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0
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


def get_text(update):
    return update.message.text.strip() if update.message and update.message.text else None


def get_quizzes():
    return conn.execute("SELECT * FROM quizzes").fetchall()


def get_questions(qid):
    return conn.execute(
        "SELECT * FROM questions WHERE quiz_id=? ORDER BY position",
        (qid,)
    ).fetchall()


# =====================
# START
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    qs = get_quizzes()

    if not qs:
        await update.message.reply_text("❌ Викторин пока нет")
        return

    kb = [[f"{q['id']} - {q['name']}"] for q in qs]

    await update.message.reply_text(
        "📚 Выберите викторину:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


# =====================
# MAIN HANDLER
# =====================

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = get_text(update)

    if uid in ADMIN:
        await admin_router(update)
        return

    # выбор викторины
    if uid not in USER:
        if text and " - " in text:
            quiz_id = int(text.split(" - ")[0])

            quiz = conn.execute(
                "SELECT * FROM quizzes WHERE id=?",
                (quiz_id,)
            ).fetchone()

            if quiz:
                USER[uid] = {
                    "quiz_id": quiz_id,
                    "q": 0,
                    "score": 0
                }

                await update.message.reply_text(
                    f"📖 {quiz['description']}\n\n▶ Начинаем!",
                    reply_markup=ReplyKeyboardRemove()
                )

                await send_question(update, context)
                return

        await update.message.reply_text("Выбери викторину через /start")
        return

    # ответы
    await handle_answer(update, context)


# =====================
# QUESTIONS
# =====================

async def send_question(update, context):
    uid = update.effective_user.id
    state = USER[uid]

    qs = get_questions(state["quiz_id"])

    if state["q"] >= len(qs):
        return await finish_quiz(update, context)

    q = qs[state["q"]]

    state["answer"] = q["answer"]

    options = json.loads(q["options"])
    kb = ReplyKeyboardMarkup([[o] for o in options], resize_keyboard=True)

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=kb)
    else:
        await update.message.reply_text(q["question"], reply_markup=kb)


async def handle_answer(update, context):
    uid = update.effective_user.id
    state = USER[uid]
    text = get_text(update)

    if not text:
        return

    if text.lower() == state["answer"].lower():
        state["score"] += 1
        await update.message.reply_text("✅ Верно")
    else:
        await update.message.reply_text(f"❌ Неверно\nОтвет: {state['answer']}")

    state["q"] += 1
    await send_question(update, context)


# =====================
# FINISH + STATS
# =====================

def save_stats(uid, score):
    conn.execute("""
        INSERT INTO stats(user_id, games, score)
        VALUES (?,1,?)
        ON CONFLICT(user_id) DO UPDATE SET
        games = games + 1,
        score = score + excluded.score
    """, (uid, score))

    conn.commit()


async def finish_quiz(update, context):
    uid = update.effective_user.id
    state = USER[uid]

    score = state["score"]

    save_stats(uid, score)

    del USER[uid]

    kb = ReplyKeyboardMarkup(
        [["📚 Выбрать другую викторину"]],
        resize_keyboard=True
    )

    await update.message.reply_text(
        f"""🏁 ВИКТОРИНА ЗАВЕРШЕНА

Ваш результат: {score}

━━━━━━━━━━━━""",
        reply_markup=kb
    )


# =====================
# ADMIN
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("Нет доступа")
        return

    ADMIN[uid] = {"mode": "menu"}

    await update.message.reply_text("""
🛠 ADMIN

1 - создать викторину
2 - список
3 - выйти
4 - добавить вопрос
""")


async def admin_router(update):
    uid = update.effective_user.id
    text = get_text(update)
    st = ADMIN[uid]

    # MENU
    if st["mode"] == "menu":

        if text == "1":
            st["mode"] = "create_quiz"
            await update.message.reply_text("Название викторины:")
            return

        if text == "2":
            qs = get_quizzes()
            msg = "📚 Викторины:\n\n"
            for q in qs:
                msg += f"{q['id']} - {q['name']}\n"
            await update.message.reply_text(msg)
            return

        if text == "3":
            ADMIN.pop(uid)
            await update.message.reply_text("Выход")
            return

        if text == "4":
            st["mode"] = "add_question_quiz"
            await update.message.reply_text("ID викторины:")
            return

    # CREATE QUIZ
    if st["mode"] == "create_quiz":
        conn.execute(
            "INSERT INTO quizzes(name, description) VALUES (?,?)",
            (text, "Описание викторины")
        )
        conn.commit()

        st["mode"] = "menu"
        await update.message.reply_text("✔ Создано")

    # ADD QUESTION FLOW
    if st["mode"] == "add_question_quiz":
        st["quiz_id"] = int(text)
        st["mode"] = "add_question_text"
        await update.message.reply_text("Вопрос:")
        return

    if st["mode"] == "add_question_text":
        st["question"] = text
        st["mode"] = "add_question_photo"
        await update.message.reply_text("Отправь фото или напиши 'нет'")
        return

    if st["mode"] == "add_question_photo":

        if update.message.photo:
            st["photo"] = update.message.photo[-1].file_id
        else:
            st["photo"] = None

        st["mode"] = "add_options"
        await update.message.reply_text("Варианты через запятую:")
        return

    if st["mode"] == "add_options":
        st["options"] = json.dumps(text.split(","))
        st["mode"] = "add_answer"
        await update.message.reply_text("Правильный ответ:")
        return

    if st["mode"] == "add_answer":
        conn.execute("""
            INSERT INTO questions(quiz_id, question, options, answer, photo)
            VALUES (?,?,?,?,?)
        """, (
            st["quiz_id"],
            st["question"],
            st["options"],
            text,
            st.get("photo")
        ))

        conn.commit()

        st["mode"] = "menu"
        await update.message.reply_text("✔ Вопрос добавлен")


# =====================
# BACK BUTTON
# =====================

async def back_to_quizzes(update, context):
    return await start(update, context)


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))

app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
