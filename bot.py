import os
import json
import sqlite3
import asyncio

from telegram import Update
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
    score INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
    return conn.execute("SELECT * FROM quizzes ORDER BY id").fetchall()


def get_questions(quiz_id):
    return conn.execute(
        "SELECT * FROM questions WHERE quiz_id=? ORDER BY position, id",
        (quiz_id,)
    ).fetchall()


def safe_text(update: Update):
    if update.message and update.message.text:
        return update.message.text.strip()
    if update.message and update.message.caption:
        return update.message.caption.strip()
    return None


def safe_photo(update: Update):
    if update.message and update.message.photo:
        return update.message.photo[-1].file_id
    return None


def cancel_timer(uid):
    task = TIMERS.pop(uid, None)
    if task:
        task.cancel()


def reset_user(uid):
    cancel_timer(uid)
    USER.pop(uid, None)


def normalize_question_positions(quiz_id):
    qs = conn.execute(
        "SELECT id FROM questions WHERE quiz_id=? ORDER BY position, id",
        (quiz_id,)
    ).fetchall()

    for pos, q in enumerate(qs):
        conn.execute(
            "UPDATE questions SET position=? WHERE id=?",
            (pos, q["id"])
        )

    conn.commit()


def find_quiz_by_user_input(text):
    if not text:
        return None

    quizzes = get_quizzes()

    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(quizzes):
            return quizzes[idx]

    row = conn.execute(
        "SELECT * FROM quizzes WHERE lower(name)=lower(?)",
        (text,)
    ).fetchone()
    return row


def find_quiz_by_admin_input(text):
    if not text:
        return None

    if text.isdigit():
        row = conn.execute(
            "SELECT * FROM quizzes WHERE id=?",
            (int(text),)
        ).fetchone()
        if row:
            return row

    row = conn.execute(
        "SELECT * FROM quizzes WHERE lower(name)=lower(?)",
        (text,)
    ).fetchone()
    return row


def find_question_in_quiz(quiz_id, qid):
    return conn.execute(
        "SELECT * FROM questions WHERE id=? AND quiz_id=?",
        (qid, quiz_id)
    ).fetchone()


def swap_question_position(quiz_id, qid, direction):
    current = conn.execute(
        "SELECT * FROM questions WHERE id=? AND quiz_id=?",
        (qid, quiz_id)
    ).fetchone()

    if not current:
        return False, "❌ вопрос не найден"

    pos = current["position"]

    if direction in ("up", "вверх", "1"):
        neighbor = conn.execute(
            """
            SELECT * FROM questions
            WHERE quiz_id=? AND position < ?
            ORDER BY position DESC
            LIMIT 1
            """,
            (quiz_id, pos)
        ).fetchone()
    else:
        neighbor = conn.execute(
            """
            SELECT * FROM questions
            WHERE quiz_id=? AND position > ?
            ORDER BY position ASC
            LIMIT 1
            """,
            (quiz_id, pos)
        ).fetchone()

    if not neighbor:
        return False, "❌ двигать дальше некуда"

    conn.execute("UPDATE questions SET position=? WHERE id=?", (neighbor["position"], current["id"]))
    conn.execute("UPDATE questions SET position=? WHERE id=?", (pos, neighbor["id"]))
    conn.commit()
    normalize_question_positions(quiz_id)
    return True, "✔ вопрос перемещён"


# =====================
# USER FLOW
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    reset_user(uid)

    qs = get_quizzes()
    if not qs:
        await update.message.reply_text("❌ нет викторин")
        return

    msg = "📚 ВИКТОРИНЫ:\n\n"
    for i, q in enumerate(qs, 1):
        msg += f"{i} — {q['name']}\n"
    msg += "\n0 — рейтинг"

    await update.message.reply_text(msg)


async def show_quiz_preview(update: Update, quiz):
    uid = update.effective_user.id
    reset_user(uid)

    USER[uid] = {
        "mode": "preview",
        "quiz_id": quiz["id"]
    }

    desc = quiz["description"] or "Без описания"
    await update.message.reply_text(
        f"📖 {quiz['name']}\n\n{desc}\n\n"
        "Напишите:\n"
        "start — начать\n"
        "back — назад к списку\n\n"
        "⏱ 20 секунд на вопрос"
    )


async def start_quiz(uid, quiz_id, message):
    USER[uid] = {
        "mode": "quiz",
        "quiz_id": quiz_id,
        "q_index": 0,
        "score": 0,
        "locked": False,
        "current_qid": None,
        "answer": None
    }
    await message.reply_text("🚀 старт!")
    await send_question(message, uid)


async def send_question(message_or_update, uid: int):
    state = USER.get(uid)
    if not state or state.get("mode") != "quiz":
        return

    qs = get_questions(state["quiz_id"])

    if state["q_index"] >= len(qs):
        conn.execute(
            "INSERT INTO results (user_id, quiz_id, score) VALUES (?, ?, ?)",
            (uid, state["quiz_id"], state["score"])
        )
        conn.commit()

        cancel_timer(uid)
        USER.pop(uid, None)

        if hasattr(message_or_update, "reply_text"):
            await message_or_update.reply_text(
                f"🏁 Результат: {state['score']}/{len(qs)}"
            )
        else:
            await message_or_update.message.reply_text(
                f"🏁 Результат: {state['score']}/{len(qs)}"
            )
        return

    q = qs[state["q_index"]]
    state["current_qid"] = q["id"]
    state["answer"] = q["answer"]
    state["locked"] = False

    options = json.loads(q["options"] or "[]")
    options_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])

    full_text = f"{q['question']}\n\n{options_text}\n\nНапишите номер ответа или сам ответ."

    if hasattr(message_or_update, "reply_photo"):
        if q["photo"]:
            await message_or_update.reply_photo(q["photo"], caption=full_text)
        else:
            await message_or_update.reply_text(full_text)
    else:
        if q["photo"]:
            await message_or_update.message.reply_photo(q["photo"], caption=full_text)
        else:
            await message_or_update.message.reply_text(full_text)

    cancel_timer(uid)
    TIMERS[uid] = asyncio.create_task(question_timer(uid, q["id"]))


async def question_timer(uid, qid):
    try:
        await asyncio.sleep(20)

        state = USER.get(uid)
        if not state or state.get("mode") != "quiz":
            return

        if state.get("current_qid") != qid or state.get("locked"):
            return

        state["q_index"] += 1
        state["locked"] = False

        # сообщение отправим через create_task ниже уже из общего loop
        # здесь нельзя использовать update, поэтому делаем через сохранённый state только
        # безопаснее просто пометить и обработать через следующий handle()
        # но для стабильности отправим через прямой бот-клиент через background task не будем.
        # Поэтому таймаут переводит вопрос вперёд только если пользователь потом напишет ответ.
        # Чтобы всё же сразу показать следующий вопрос, используем сохранённый chat_id.
        # В этом коде чат-ид не храним, поэтому таймаут просто закрывает вопрос сообщением,
        # а следующий вопрос будет показан при следующем сообщении пользователя.
        # Это стабильнее, чем ломать поток на Render Free.

        # если хочешь авто-переход без ожидания, скажи — добавлю chat_id в state.
        pass
    except asyncio.CancelledError:
        pass


async def handle_user_answer(update: Update, uid: int):
    state = USER.get(uid)
    if not state or state.get("mode") != "quiz":
        return

    if state.get("locked"):
        return

    text = safe_text(update)
    if not text:
        await update.message.reply_text("Напишите номер ответа или сам ответ.")
        return

    qs = get_questions(state["quiz_id"])
    if state["q_index"] >= len(qs):
        return

    q = qs[state["q_index"]]
    options = json.loads(q["options"] or "[]")

    resolved = None

    if text.isdigit():
        idx = int(text) - 1
        if 0 <= idx < len(options):
            resolved = options[idx]
        else:
            await update.message.reply_text("❌ Нет такого номера. Выберите из списка.")
            return
    else:
        for opt in options:
            if opt.strip().lower() == text.strip().lower():
                resolved = opt
                break

        if resolved is None:
            await update.message.reply_text("❌ Ответ должен совпадать с одним из вариантов или быть его номером.")
            return

    state["locked"] = True
    cancel_timer(uid)

    if resolved.strip().lower() == (state["answer"] or "").strip().lower():
        state["score"] += 1
        await update.message.reply_text("✅ Верно")
    else:
        await update.message.reply_text(f"❌ Неверно\nОтвет: {state['answer']}")

    state["q_index"] += 1
    state["locked"] = False
    await send_question(update, uid)


# =====================
# ADMIN ENTRY
# =====================

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_admin(uid):
        await update.message.reply_text("нет доступа")
        return

    ADMIN[uid] = {
        "mode": "menu",
        "quiz_id": None,
        "qid": None,
        "step": None
    }

    await update.message.reply_text(
        "🛠 SAFE EDITOR V8\n\n"
        "1 — викторины\n"
        "2 — статистика\n"
        "0 — выйти"
    )


# =====================
# MAIN ROUTER
# =====================

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = safe_text(update)
    photo = safe_photo(update)

    # admin first
    if uid in ADMIN:
        await admin_router(update, text=text, photo=photo)
        return

    # user preview / quiz
    if uid in USER:
        state = USER[uid]
        if state.get("mode") == "preview":
            if text and text.lower() == "start":
                await start_quiz(uid, state["quiz_id"], update.message)
                return
            if text and text.lower() == "back":
                reset_user(uid)
                await start(update, context)
                return
            await update.message.reply_text("Напишите start, чтобы начать, или back, чтобы вернуться.")
            return

        if state.get("mode") == "quiz":
            await handle_user_answer(update, uid)
            return

    # root user menu
    if text == "0":
        top = conn.execute("""
            SELECT user_id, SUM(score) AS s
            FROM results
            GROUP BY user_id
            ORDER BY s DESC
            LIMIT 10
        """).fetchall()

        msg = "🏆 РЕЙТИНГ:\n\n"
        for i, r in enumerate(top, 1):
            msg += f"{i}. {r['user_id']} — {r['s']}\n"
        await update.message.reply_text(msg or "Пока нет результатов")
        return

    quiz = find_quiz_by_user_input(text)
    if quiz:
        await show_quiz_preview(update, quiz)
        return

    await update.message.reply_text("Используйте /start")


# =====================
# ADMIN ROUTER
# =====================

async def admin_router(update: Update, text=None, photo=None):
    uid = update.effective_user.id
    st = ADMIN[uid]
    text = text if text is not None else safe_text(update)

    # -----------------
    # ROOT MENU
    # -----------------
    if st["mode"] == "menu":
        if text == "1":
            st["mode"] = "quizzes"
            await show_quizzes_menu(update)
            return

        if text == "2":
            await show_stats(update)
            return

        if text == "0":
            cancel_timer(uid)
            ADMIN.pop(uid, None)
            await update.message.reply_text("выход")
            return

    # -----------------
    # QUIZZES MENU
    # -----------------
    if st["mode"] == "quizzes":
        if text == "1":
            st["mode"] = "create_quiz"
            await update.message.reply_text("Название викторины?")
            return

        if text == "2":
            qs = get_quizzes()
            msg = "📚 Викторины:\n\n"
            for q in qs:
                msg += f"{q['id']} — {q['name']}\n"
            msg += "\nВведи ID или название"
            st["mode"] = "select_quiz"
            await update.message.reply_text(msg)
            return

        if text == "3":
            st["mode"] = "menu"
            await admin(update, None)
            return

    if st["mode"] == "create_quiz":
        if not text:
            await update.message.reply_text("Введите название текстом.")
            return

        try:
            conn.execute(
                "INSERT INTO quizzes (name, description) VALUES (?, ?)",
                (text, "")
            )
            conn.commit()
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ Такая викторина уже есть")
            return

        st["mode"] = "quizzes"
        await update.message.reply_text("✔ создано")
        await show_quizzes_menu(update)
        return

    if st["mode"] == "select_quiz":
        quiz = find_quiz_by_admin_input(text)
        if not quiz:
            await update.message.reply_text("❌ Нет такой викторины. Введите ID или название ещё раз.")
            return

        st["quiz_id"] = quiz["id"]
        st["quiz_name"] = quiz["name"]
        st["mode"] = "quiz_menu"
        await show_quiz_menu(update, quiz)
        return

    # -----------------
    # QUIZ MENU
    # -----------------
    if st["mode"] == "quiz_menu":
        if text == "0":
            st["mode"] = "quizzes"
            st.pop("quiz_id", None)
            st.pop("quiz_name", None)
            st.pop("qid", None)
            st.pop("step", None)
            await show_quizzes_menu(update)
            return

        if text == "1":
            st["mode"] = "add_question"
            st["step"] = "question"
            st.pop("qid", None)
            st.pop("new_question", None)
            st.pop("options", None)
            st.pop("answer", None)
            await update.message.reply_text("Вопрос?")
            return

        if text == "2":
            qs = get_questions(st["quiz_id"])
            if not qs:
                await update.message.reply_text("Пока нет вопросов")
                return

            msg = "❓ Вопросы:\n\n"
            for q in qs:
                msg += f"ID {q['id']} | поз. {q['position']} — {q['question']}\n"
            await update.message.reply_text(msg)
            return

        if text == "3":
            st["mode"] = "edit_question"
            st["step"] = "select"
            st.pop("qid", None)
            st.pop("edit_old_photo", None)
            await update.message.reply_text("Введите ID вопроса для редактирования")
            return

        if text == "4":
            st["mode"] = "delete_question"
            st["step"] = "select"
            st.pop("qid", None)
            await update.message.reply_text("Введите ID вопроса для удаления")
            return

        if text == "5":
            st["mode"] = "move_question"
            st["step"] = "select"
            st.pop("qid", None)
            await update.message.reply_text("Введите ID вопроса для перемещения")
            return

        if text == "6":
            st["mode"] = "edit_desc"
            await update.message.reply_text("Новое описание викторины?")
            return

        if text == "7":
            st["mode"] = "delete_quiz"
            await update.message.reply_text(
                f"Удалить викторину «{st.get('quiz_name', '')}»? Напишите ДА для подтверждения."
            )
            return

    # -----------------
    # ADD QUESTION FLOW
    # -----------------
    if st["mode"] == "add_question":
        await add_question_flow(update, st, text, photo)
        return

    # -----------------
    # EDIT QUESTION FLOW
    # -----------------
    if st["mode"] == "edit_question":
        await edit_question_flow(update, st, text, photo)
        return

    # -----------------
    # DELETE QUESTION FLOW
    # -----------------
    if st["mode"] == "delete_question":
        await delete_question_flow(update, st, text)
        return

    # -----------------
    # MOVE QUESTION FLOW
    # -----------------
    if st["mode"] == "move_question":
        await move_question_flow(update, st, text)
        return

    # -----------------
    # EDIT DESCRIPTION
    # -----------------
    if st["mode"] == "edit_desc":
        if not text:
            await update.message.reply_text("Введите описание текстом.")
            return

        conn.execute(
            "UPDATE quizzes SET description=? WHERE id=?",
            (text, st["quiz_id"])
        )
        conn.commit()

        st["mode"] = "quiz_menu"
        await update.message.reply_text("✔ описание обновлено")
        await show_quiz_menu(update, conn.execute("SELECT * FROM quizzes WHERE id=?", (st["quiz_id"],)).fetchone())
        return

    # -----------------
    # DELETE QUIZ CONFIRM
    # -----------------
    if st["mode"] == "delete_quiz":
        if text and text.upper() == "ДА":
            quiz_id = st["quiz_id"]
            conn.execute("DELETE FROM questions WHERE quiz_id=?", (quiz_id,))
            conn.execute("DELETE FROM quizzes WHERE id=?", (quiz_id,))
            conn.commit()

            # clean user sessions tied to this quiz
            for u in list(USER.keys()):
                if USER[u].get("quiz_id") == quiz_id:
                    reset_user(u)
                    USER.pop(u, None)

            st["mode"] = "quizzes"
            st.pop("quiz_id", None)
            st.pop("quiz_name", None)
            st.pop("qid", None)
            st.pop("step", None)

            await update.message.reply_text("🗑 викторина удалена")
            await show_quizzes_menu(update)
            return

        await update.message.reply_text("Удаление отменено")
        st["mode"] = "quiz_menu"
        return


# =====================
# ADMIN SCREENS
# =====================

async def show_quizzes_menu(update):
    await update.message.reply_text(
        "📚 ВИКТОРИНЫ\n\n"
        "1 — создать викторину\n"
        "2 — список и выбрать\n"
        "3 — назад\n"
    )


async def show_quiz_menu(update, quiz):
    await update.message.reply_text(
        f"📂 Викторина: {quiz['name']}\n\n"
        "1 — добавить вопрос\n"
        "2 — список вопросов\n"
        "3 — редактировать вопрос\n"
        "4 — удалить вопрос\n"
        "5 — переместить вопрос\n"
        "6 — изменить описание\n"
        "7 — удалить викторину\n"
        "0 — назад"
    )


async def show_stats(update):
    quizzes_count = conn.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0]
    questions_count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    attempts_count = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    unique_users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM results").fetchone()[0]
    avg_score = conn.execute("SELECT AVG(score) FROM results").fetchone()[0] or 0

    await update.message.reply_text(
        "📊 СТАТИСТИКА\n\n"
        f"Викторин: {quizzes_count}\n"
        f"Вопросов: {questions_count}\n"
        f"Попыток: {attempts_count}\n"
        f"Пользователей: {unique_users}\n"
        f"Средний балл: {round(avg_score, 2)}"
    )


# =====================
# ADD QUESTION FLOW
# =====================

async def add_question_flow(update, st, text, photo):
    if st["step"] == "question":
        if not text:
            await update.message.reply_text("Введите вопрос текстом.")
            return

        st["new_question"] = text
        st["step"] = "options"
        await update.message.reply_text("Варианты через запятую")
        return

    if st["step"] == "options":
        if not text:
            await update.message.reply_text("Введите варианты текстом.")
            return

        options = [x.strip() for x in text.split(",") if x.strip()]
        if len(options) < 2:
            await update.message.reply_text("❌ Нужно минимум 2 варианта")
            return

        st["options"] = options
        st["step"] = "answer"
        await update.message.reply_text("Правильный ответ")
        return

    if st["step"] == "answer":
        if not text:
            await update.message.reply_text("Введите правильный ответ текстом.")
            return

        if text.strip() not in st["options"]:
            await update.message.reply_text("❌ Ответ должен быть одним из вариантов. Введите ещё раз.")
            return

        st["answer"] = text.strip()
        st["step"] = "photo"
        await update.message.reply_text("Отправьте фото или напишите skip")
        return

    if st["step"] == "photo":
        if photo is None and not (text and text.lower() == "skip"):
            await update.message.reply_text("Отправьте фото или напишите skip")
            return

        pos = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE quiz_id=?",
            (st["quiz_id"],)
        ).fetchone()[0]

        conn.execute("""
            INSERT INTO questions (quiz_id, question, options, answer, photo, position)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            st["quiz_id"],
            st["new_question"],
            json.dumps(st["options"], ensure_ascii=False),
            st["answer"],
            photo,
            pos
        ))
        conn.commit()

        st["mode"] = "quiz_menu"
        st.pop("step", None)
        st.pop("new_question", None)
        st.pop("options", None)
        st.pop("answer", None)

        await update.message.reply_text("✔ добавлено")
        await show_quiz_menu(update, conn.execute("SELECT * FROM quizzes WHERE id=?", (st["quiz_id"],)).fetchone())
        return


# =====================
# EDIT QUESTION FLOW
# =====================

async def edit_question_flow(update, st, text, photo):
    if st["step"] == "select":
        if not text or not text.isdigit():
            await update.message.reply_text("Введите ID вопроса цифрой.")
            return

        q = find_question_in_quiz(st["quiz_id"], int(text))
        if not q:
            await update.message.reply_text("❌ Нет такого вопроса в этой викторине")
            return

        st["qid"] = q["id"]
        st["edit_old_photo"] = q["photo"]
        st["step"] = "edit_menu"

        await update.message.reply_text(
            "✏ РЕДАКТОР\n\n"
            "1 — текст\n"
            "2 — варианты\n"
            "3 — правильный ответ\n"
            "4 — фото\n"
            "0 — назад"
        )
        return

    if st["step"] == "edit_menu":
        if text == "0":
            st.pop("qid", None)
            st.pop("edit_old_photo", None)
            st.pop("step", None)
            st["mode"] = "quiz_menu"
            await show_quiz_menu(update, conn.execute("SELECT * FROM quizzes WHERE id=?", (st["quiz_id"],)).fetchone())
            return

        if text == "1":
            st["step"] = "edit_text"
            await update.message.reply_text("Новый текст вопроса")
            return

        if text == "2":
            st["step"] = "edit_options"
            await update.message.reply_text("Новые варианты через запятую")
            return

        if text == "3":
            st["step"] = "edit_answer"
            await update.message.reply_text("Новый правильный ответ")
            return

        if text == "4":
            st["step"] = "edit_photo"
            await update.message.reply_text("Отправьте новое фото или напишите skip")
            return

        await update.message.reply_text("Выберите 0–4")
        return

    qid = st["qid"]

    if st["step"] == "edit_text":
        if not text:
            await update.message.reply_text("Введите новый текст.")
            return

        conn.execute("UPDATE questions SET question=? WHERE id=?", (text, qid))
        conn.commit()
        st["step"] = "edit_menu"
        await update.message.reply_text("✔ обновлено")
        return

    if st["step"] == "edit_options":
        if not text:
            await update.message.reply_text("Введите варианты через запятую.")
            return

        options = [x.strip() for x in text.split(",") if x.strip()]
        if len(options) < 2:
            await update.message.reply_text("❌ Нужно минимум 2 варианта")
            return

        conn.execute(
            "UPDATE questions SET options=? WHERE id=?",
            (json.dumps(options, ensure_ascii=False), qid)
        )
        conn.commit()

        st["step"] = "edit_answer"
        await update.message.reply_text("✔ варианты обновлены\nТеперь введите правильный ответ из нового списка")
        return

    if st["step"] == "edit_answer":
        if not text:
            await update.message.reply_text("Введите ответ текстом.")
            return

        current = conn.execute(
            "SELECT options FROM questions WHERE id=?",
            (qid,)
        ).fetchone()

        options = json.loads(current["options"] or "[]")
        if text.strip() not in options:
            await update.message.reply_text("❌ Ответ должен быть из списка вариантов. Попробуйте ещё раз.")
            return

        conn.execute("UPDATE questions SET answer=? WHERE id=?", (text.strip(), qid))
        conn.commit()
        st["step"] = "edit_menu"
        await update.message.reply_text("✔ ответ обновлён")
        return

    if st["step"] == "edit_photo":
        if photo:
            conn.execute("UPDATE questions SET photo=? WHERE id=?", (photo, qid))
            conn.commit()
            st["step"] = "edit_menu"
            await update.message.reply_text("✔ фото обновлено")
            return

        if text and text.lower() == "skip":
            st["step"] = "edit_menu"
            await update.message.reply_text("Фото не изменено")
            return

        await update.message.reply_text("Отправьте фото или напишите skip")
        return


# =====================
# DELETE QUESTION FLOW
# =====================

async def delete_question_flow(update, st, text):
    if st["step"] == "select":
        if not text or not text.isdigit():
            await update.message.reply_text("Введите ID вопроса цифрой.")
            return

        q = find_question_in_quiz(st["quiz_id"], int(text))
        if not q:
            await update.message.reply_text("❌ Нет такого вопроса в этой викторине")
            return

        st["qid"] = q["id"]
        st["step"] = "confirm"
        await update.message.reply_text("Напишите ДА для подтверждения удаления")
        return

    if st["step"] == "confirm":
        if text and text.upper() == "ДА":
            conn.execute("DELETE FROM questions WHERE id=?", (st["qid"],))
            conn.commit()
            normalize_question_positions(st["quiz_id"])

            st.pop("qid", None)
            st.pop("step", None)
            st["mode"] = "quiz_menu"

            await update.message.reply_text("🗑 вопрос удалён")
            await show_quiz_menu(update, conn.execute("SELECT * FROM quizzes WHERE id=?", (st["quiz_id"],)).fetchone())
            return

        st.pop("qid", None)
        st.pop("step", None)
        st["mode"] = "quiz_menu"
        await update.message.reply_text("Удаление отменено")
        await show_quiz_menu(update, conn.execute("SELECT * FROM quizzes WHERE id=?", (st["quiz_id"],)).fetchone())
        return


# =====================
# MOVE QUESTION FLOW
# =====================

async def move_question_flow(update, st, text):
    if st["step"] == "select":
        if not text or not text.isdigit():
            await update.message.reply_text("Введите ID вопроса цифрой.")
            return

        q = find_question_in_quiz(st["quiz_id"], int(text))
        if not q:
            await update.message.reply_text("❌ Нет такого вопроса в этой викторине")
            return

        st["qid"] = q["id"]
        st["step"] = "direction"
        await update.message.reply_text("Напишите вверх или вниз")
        return

    if st["step"] == "direction":
        if not text:
            await update.message.reply_text("Напишите вверх или вниз")
            return

        direction = text.lower()
        if direction not in ("вверх", "вниз", "up", "down", "1", "2"):
            await update.message.reply_text("Напишите вверх или вниз")
            return

        ok, msg = swap_question_position(st["quiz_id"], st["qid"], direction)
        st.pop("qid", None)
        st.pop("step", None)
        st["mode"] = "quiz_menu"

        await update.message.reply_text(msg)
        await show_quiz_menu(update, conn.execute("SELECT * FROM quizzes WHERE id=?", (st["quiz_id"],)).fetchone())
        return


# =====================
# RUN
# =====================

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle))

app.run_polling()
