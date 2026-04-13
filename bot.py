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
# 🔧 НАСТРОЙКИ
# =====================

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN is not set")

ADMIN_IDS = {465313785}

TG_CHANNEL = "https://t.me/videt_i_slyshat"
VK_PAGE = "https://vk.com/art_in_church"

BACK_BTN = "🔙 Назад"

# =====================
# 🗄 БАЗА
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
    photo TEXT
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
# 🧠 STATE
# =====================

USER = {}
ADMIN = {}

# =====================
# 🧰 HELPERS
# =====================

def get_text(update):
    return update.message.text.strip() if update.message and update.message.text else None


def is_admin(uid):
    return uid in ADMIN_IDS


def get_quizzes():
    return conn.execute("SELECT * FROM quizzes").fetchall()


def get_questions(qid):
    return conn.execute(
        "SELECT * FROM questions WHERE quiz_id=?",
        (qid,)
    ).fetchall()


# =====================
# /start
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = get_quizzes()

    if not quizzes:
        await update.message.reply_text("❌ Нет викторин")
        return

    kb = [[f"{q['id']} - {q['name']}"] for q in quizzes]

    await update.message.reply_text(
        "📚 Выберите викторину:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


# =====================
# BACK
# =====================

async def go_back(update, context):
    uid = update.effective_user.id

    if uid in USER:
        USER.pop(uid, None)
        await update.message.reply_text("↩ Возврат в меню", reply_markup=ReplyKeyboardRemove())
        await start(update, context)
        return

    if uid in ADMIN:
        ADMIN.pop(uid, None)
        await update.message.reply_text("↩ Выход из админки")
        return


# =====================
# MAIN HANDLER
# =====================

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = get_text(update)

    if not text:
        return

    if text == BACK_BTN:
        await go_back(update, context)
        return

    if uid in ADMIN:
        await admin_router(update, context)
        return

    if uid not in USER:
        if " - " in text:
            try:
                quiz_id = int(text.split(" - ")[0])
            except:
                return await update.message.reply_text("❌ Ошибка")

            quiz = conn.execute(
                "SELECT * FROM quizzes WHERE id=?",
                (quiz_id,)
            ).fetchone()

            if not quiz:
                return await update.message.reply_text("❌ Нет викторины")

            USER[uid] = {
                "quiz_id": quiz_id,
                "q": 0,
                "score": 0,
                "answer": None
            }

            await update.message.reply_text(
                f"📖 {quiz['description']}\n\n▶ Старт!",
                reply_markup=ReplyKeyboardRemove()
            )

            await send_question(update, context)
            return

        return await update.message.reply_text("👉 нажми /start")

    await handle_answer(update, context)


# =====================
# QUESTIONS
# =====================

async def send_question(update, context):
    uid = update.effective_user.id
    state = USER[uid]

    questions = get_questions(state["quiz_id"])

    if state["q"] >= len(questions):
        return await finish_quiz(update, context)

    q = questions[state["q"]]
    state["answer"] = q["answer"]

    try:
        options = json.loads(q["options"])
    except:
        options = []

    kb = ReplyKeyboardMarkup([[o] for o in options], resize_keyboard=True)

    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=kb)
    else:
        await update.message.reply_text(q["question"], reply_markup=kb)


# =====================
# ANSWERS
# =====================

async def handle_answer(update, context):
    uid = update.effective_user.id
    state = USER.get(uid)
    text = get_text(update)

    if not state:
        return

    correct = state.get("answer")

    if correct and text.strip().lower() == correct.strip().lower():
        state["score"] += 1
        await update.message.reply_text("✅ Верно")
    else:
        await update.message.reply_text(f"❌ Неверно\nОтвет: {correct}")

    state["q"] += 1
    await send_question(update, context)


# =====================
# FINISH
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
    state = USER.pop(uid, None)

    if not state:
        return

    save_stats(uid, state["score"])

    kb = ReplyKeyboardMarkup(
        [["📚 Выбрать другую викторину"]],
        resize_keyboard=True
    )

    await update.message.reply_text(
        f"""🏁 Готово!

🎯 Результат: {state['score']}

━━━━━━━━━━
📲 TG: {TG_CHANNEL}
📘 VK: {VK_PAGE}
""",
        reply_markup=kb
    )


# =====================
# ADMIN MENU
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        return await update.message.reply_text("❌ нет доступа")

    ADMIN[uid] = {"mode": "menu"}

    await update.message.reply_text("""
🛠 АДМИН

1 - создать викторину
2 - список викторин
3 - выход
4 - добавить вопрос
5 - удалить вопрос
6 - редактировать вопрос
""", reply_markup=ReplyKeyboardRemove())


# =====================
# ADMIN ROUTER
# =====================

async def admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = get_text(update)
    st = ADMIN.get(uid)

    if not st:
        return

    mode = st["mode"]

    # ========= MENU =========
    if mode == "menu":

        if text == "1":
            st["mode"] = "create_quiz"
            return await update.message.reply_text("Название?")

        if text == "2":
            qs = get_quizzes()
            msg = "\n".join([f"{q['id']} - {q['name']}" for q in qs])
            return await update.message.reply_text(msg or "пусто")

        if text == "3":
            ADMIN.pop(uid, None)
            return await update.message.reply_text("выход")

        if text == "4":
            st["mode"] = "add_quiz_id"
            return await update.message.reply_text("ID викторины?")

        if text == "5":
            st["mode"] = "delete"
            return await update.message.reply_text("ID викторины для удаления вопроса?")

        if text == "6":
            st["mode"] = "edit_qid"
            return await update.message.reply_text("ID викторины для редактирования?")

    # ========= CREATE =========
    if mode == "create_quiz":
        conn.execute("INSERT INTO quizzes(name) VALUES (?)", (text,))
        conn.commit()
        st["mode"] = "menu"
        return await update.message.reply_text("создано")

    # ========= ADD QUESTION =========
    if mode == "add_quiz_id":
        st["quiz_id"] = int(text)
        st["mode"] = "add_q"
        return await update.message.reply_text("вопрос?")

    if mode == "add_q":
        st["question"] = text
        st["mode"] = "add_options"
        return await update.message.reply_text("варианты через ,")

    if mode == "add_options":
        st["options"] = json.dumps([x.strip() for x in text.split(",")])
        st["mode"] = "add_answer"
        return await update.message.reply_text("ответ?")

    if mode == "add_answer":
        conn.execute("""
            INSERT INTO questions(quiz_id, question, options, answer)
            VALUES (?,?,?,?)
        """, (st["quiz_id"], st["question"], st["options"], text))
        conn.commit()
        st["mode"] = "menu"
        return await update.message.reply_text("готово")

    # ========= EDIT =========
    if mode == "edit_qid":
        st["edit_qid"] = int(text)
        st["mode"] = "edit_select"
        return await update.message.reply_text("что менять? question/options/answer")

    if mode == "edit_select":
        st["field"] = text
        st["mode"] = "edit_value"
        return await update.message.reply_text("новое значение?")

    if mode == "edit_value":
        conn.execute(f"""
            UPDATE questions
            SET {st['field']}=?
            WHERE id=?
        """, (text, st["edit_qid"]))
        conn.commit()
        st["mode"] = "menu"
        return await update.message.reply_text("обновлено")


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))

app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
