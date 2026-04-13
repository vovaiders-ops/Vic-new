import os
import json
import sqlite3
import asyncio

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Конфигурация
TOKEN = os.getenv("TOKEN")
ADMIN_IDS = {123456789, 987654321}  # пример ID администраторов
if not TOKEN:
    raise RuntimeError("TOKEN не задан")

# Инициализация SQLite БД
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

# Хранение состояния
USER = {}    # state викторины для каждого пользователя
ADMIN = {}   # state для каждого администратора
TIMERS = {}  # таймеры для вопросов {user_id: asyncio.Task}

# Хелперы
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def get_quizzes():
    return conn.execute("SELECT * FROM quizzes").fetchall()

def get_questions(quiz_id):
    return conn.execute("SELECT * FROM questions WHERE quiz_id=? ORDER BY position", (quiz_id,)).fetchall()

def reset_user(uid):
    # сброс состояния пользователя и отмена таймера
    if uid in USER:
        USER.pop(uid)
    if uid in TIMERS:
        TIMERS[uid].cancel()
        TIMERS.pop(uid, None)

# Команда /start – выводит список викторин
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    quizzes = get_quizzes()
    if not quizzes:
        await update.message.reply_text("❌ Пока нет викторин")
        return
    text = "📚 ВИКТОРИНЫ:\n"
    for idx, q in enumerate(quizzes, start=1):
        text += f"{idx}. {q['name']}\n"
    text += "\nНапишите название викторины для старта\n⏱ На каждый вопрос — 20 секунд."
    await update.message.reply_text(text)

# Обработчик сообщений
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    # Админ-флоу
    if uid in ADMIN:
        await admin_flow(update, context)
        return
    # Пользователь: выбирает викторину
    quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()
    if quiz:
        USER[uid] = {"quiz_id": quiz["id"], "q": 0, "score": 0, "answered": False, "started": False}
        kb = ReplyKeyboardMarkup([["▶ Начать", "⬅ Назад"]], resize_keyboard=True)
        desc = quiz["description"] or "(описание не задано)"
        await update.message.reply_text(
            f"📖 {desc}\n\nНажмите ▶ «Начать» или «⬅ Назад» для отмены.", 
            reply_markup=kb
        )
        return
    # Назад к списку викторин
    if text == "⬅ Назад" and uid in USER and not USER[uid]["started"]:
        reset_user(uid)
        await start(update, context)
        return
    # Начать викторину
    if text.startswith("▶") and uid in USER and not USER[uid]["started"]:
        USER[uid]["started"] = True
        await send_question(update, uid)
        return
    # Ответ на вопрос
    if uid in USER and USER[uid].get("started", False):
        if uid in TIMERS:
            TIMERS[uid].cancel()
            TIMERS.pop(uid, None)
        state = USER[uid]
        if state.get("answered"):
            return  # уже отвечено на текущий вопрос
        qs = get_questions(state["quiz_id"])
        if state["q"] >= len(qs):
            return
        q = qs[state["q"]]
        if text.lower() == q["answer"].lower():
            state["score"] += 1
            await update.message.reply_text("✅ Верно")
        else:
            await update.message.reply_text(f"❌ Неверно\nПравильный ответ: {q['answer']}")
        state["answered"] = True
        state["q"] += 1
        await send_question(update, uid)
        return
    # По умолчанию
    await update.message.reply_text("Используйте /start для выбора викторины.")

# Отправка следующего вопроса
async def send_question(update: Update, uid: int):
    state = USER.get(uid)
    if not state:
        return
    qs = get_questions(state["quiz_id"])
    if state["q"] >= len(qs):
        # Викторина закончена
        await update.message.reply_text(f"🏁 Конец викторины. Ваш результат: {state['score']}/{len(qs)}")
        conn.execute("INSERT INTO results VALUES (?,?)", (uid, state["score"]))
        conn.commit()
        reset_user(uid)
        return
    q = qs[state["q"]]
    state["answer"] = q["answer"]
    state["answered"] = False
    options = json.loads(q["options"])
    kb = ReplyKeyboardMarkup([[opt] for opt in options], resize_keyboard=True)
    if q["photo"]:
        await update.message.reply_photo(q["photo"], caption=q["question"], reply_markup=kb)
    else:
        await update.message.reply_text(q["question"], reply_markup=kb)
    # Запуск таймера
    if uid in TIMERS:
        TIMERS[uid].cancel()
    TIMERS[uid] = asyncio.create_task(question_timer(update, uid, 20))

async def question_timer(update: Update, uid: int, sec: int):
    await asyncio.sleep(sec)
    if uid in USER and not USER[uid]["answered"]:
        await update.message.reply_text("⏰ Время вышло")
        USER[uid]["q"] += 1
        await send_question(update, uid)

# Команда /admin – вход в админ-панель
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Нет доступа")
        return
    ADMIN[uid] = {"step": "menu"}
    kb = ReplyKeyboardMarkup([["1", "2", "3", "0"]], resize_keyboard=True)
    text = (
        "🛠 АДМИН-ПАНЕЛЬ\n"
        "1 - создать викторину\n"
        "2 - список викторин\n"
        "3 - статистика\n"
        "0 - выход"
    )
    await update.message.reply_text(text, reply_markup=kb)

# Логика админ-панели
async def admin_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    state = ADMIN[uid]

    # Главное меню админа
    if state["step"] == "menu":
        if text == "1":
            state["step"] = "create_quiz"
            await update.message.reply_text("Введите название новой викторины:")
            return
        if text == "2":
            quizzes = get_quizzes()
            if not quizzes:
                await update.message.reply_text("❌ Нет викторин")
                return
            msg = "📚 Список викторин:\n"
            for i, q in enumerate(quizzes, 1):
                msg += f"{i}. {q['name']}\n"
            msg += "Введите номер или название викторины для выбора:"
            state["step"] = "select_quiz"
            await update.message.reply_text(msg)
            return
        if text == "3":
            total = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
            unique = conn.execute("SELECT COUNT(DISTINCT user_id) FROM results").fetchone()[0]
            avg = conn.execute("SELECT AVG(score) FROM results").fetchone()[0] or 0
            await update.message.reply_text(
                f"📊 Статистика:\n"
                f"Проходили всего: {total}\n"
                f"Уникальных пользователей: {unique}\n"
                f"Средний балл: {round(avg,2)}"
            )
            return
        if text == "0":
            ADMIN.pop(uid, None)
            await update.message.reply_text("Выход из админ-панели", reply_markup=ReplyKeyboardRemove())
            return

    # Создание викторины
    if state["step"] == "create_quiz":
        name = text
        try:
            conn.execute("INSERT INTO quizzes(name) VALUES(?)", (name,))
            conn.commit()
            await update.message.reply_text("✔ Викторина создана")
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ Такая викторина уже существует")
        state["step"] = "menu"
        return

    # Выбор викторины для редактирования
    if state["step"] == "select_quiz":
        quizzes = get_quizzes()
        quiz = None
        # по номеру
        try:
            num = int(text)
            if 1 <= num <= len(quizzes):
                quiz = quizzes[num-1]
        except:
            quiz = conn.execute("SELECT * FROM quizzes WHERE name=?", (text,)).fetchone()
        if not quiz:
            await update.message.reply_text("❌ Викторина не найдена. Попробуйте снова.")
            return
        state["quiz_id"] = quiz["id"]
        state["quiz_name"] = quiz["name"]
        state["step"] = "quiz_menu"
        kb = ReplyKeyboardMarkup([["1","2","3"],["4","5","6"],["7","0"]], resize_keyboard=True)
        await update.message.reply_text(
            f"📂 Викторина: {quiz['name']}\n"
            "1 - добавить вопрос\n"
            "2 - список вопросов\n"
            "3 - изменить вопрос\n"
            "4 - удалить вопрос\n"
            "5 - переместить вопрос\n"
            "6 - изменить описание\n"
            "7 - удалить викторину\n"
            "0 - назад",
            reply_markup=kb
        )
        return

    # Меню выбранной викторины
    if state["step"] == "quiz_menu":
        if text == "0":
            state["step"] = "menu"
            await admin(update, context)
            return
        if text == "1":
            state["step"] = "q_text"
            await update.message.reply_text("Введите текст вопроса:")
            return
        if text == "2":
            qs = get_questions(state["quiz_id"])
            if not qs:
                await update.message.reply_text("❌ Вопросов нет")
            else:
                msg = "\n".join([f"{q['id']}. {q['question']}" for q in qs])
                await update.message.reply_text(f"📋 Вопросы:\n{msg}")
            return
        if text == "3":
            qs = get_questions(state["quiz_id"])
            if not qs:
                await update.message.reply_text("❌ Вопросов нет")
                return
            state["step"] = "edit_select"
            await update.message.reply_text("Введите ID вопроса для редактирования:")
            return
        if text == "4":
            qs = get_questions(state["quiz_id"])
            if not qs:
                await update.message.reply_text("❌ Вопросов нет")
                return
            state["step"] = "delete_select"
            await update.message.reply_text("Введите ID вопроса для удаления:")
            return
        if text == "5":
            qs = get_questions(state["quiz_id"])
            if not qs:
                await update.message.reply_text("❌ Вопросов нет")
                return
            state["step"] = "move_select"
            await update.message.reply_text("Введите ID вопроса для перемещения:")
            return
        if text == "6":
            state["step"] = "edit_desc"
            await update.message.reply_text("Введите новое описание викторины:")
            return
        if text == "7":
            quiz_name = state.get("quiz_name", "")
            state["step"] = "delete_quiz"
            await update.message.reply_text(f"Удалить викторину '{quiz_name}' навсегда? Введите ДА для подтверждения.")
            return

    # Добавление вопроса
    if state["step"] == "q_text":
        state["question"] = text
        state["step"] = "q_options"
        await update.message.reply_text("Введите варианты ответов через запятую:")
        return
    if state["step"] == "q_options":
        opts = [o.strip() for o in text.split(",") if o.strip()]
        if len(opts) < 2:
            await update.message.reply_text("❌ Нужно минимум два варианта. Попробуйте снова:")
            return
        state["options"] = opts
        state["step"] = "q_answer"
        await update.message.reply_text("Введите правильный ответ:")
        return
    if state["step"] == "q_answer":
        ans = text.strip()
        if ans not in state["options"]:
            await update.message.reply_text("❌ Ответ должен быть из списка вариантов. Попробуйте снова:")
            return
        state["answer"] = ans
        state["step"] = "q_photo"
        await update.message.reply_text("Отправьте фото или введите 'skip':")
        return
    if state["step"] == "q_photo":
        photo_id = None
        if update.message.photo:
            photo_id = update.message.photo[-1].file_id
        pos = conn.execute("SELECT COUNT(*) FROM questions WHERE quiz_id=?", (state["quiz_id"],)).fetchone()[0]
        conn.execute("INSERT INTO questions(quiz_id, question, options, answer, photo, position) VALUES (?,?,?,?,?,?)",
                     (state["quiz_id"], state["question"], json.dumps(state["options"]), state["answer"], photo_id, pos))
        conn.commit()
        await update.message.reply_text("✔ Вопрос добавлен")
        state["step"] = "quiz_menu"
        return

    # Редактирование вопроса
    if state["step"] == "edit_select":
        try:
            qid = int(text)
        except:
            await update.message.reply_text("❌ Некорректный ID. Попробуйте снова:")
            return
        q = conn.execute("SELECT * FROM questions WHERE id=? AND quiz_id=?", (qid, state["quiz_id"])).fetchone()
        if not q:
            await update.message.reply_text("❌ Вопрос с таким ID не найден.")
            return
        state["question_id"] = qid
        state["step"] = "edit_menu"
        await update.message.reply_text(
            "Что изменить?\n"
            "1 - текст\n"
            "2 - варианты\n"
            "3 - ответ\n"
            "4 - фото\n"
            "0 - отмена"
        )
        return
    if state["step"] == "edit_menu":
        if text == "0":
            state["step"] = "quiz_menu"
            await update.message.reply_text("Отмена")
            return
        if text == "1":
            state["step"] = "edit_text"
            await update.message.reply_text("Введите новый текст вопроса:")
            return
        if text == "2":
            state["step"] = "edit_options"
            await update.message.reply_text("Введите новые варианты через запятую:")
            return
        if text == "3":
            state["step"] = "edit_answer"
            await update.message.reply_text("Введите новый правильный ответ:")
            return
        if text == "4":
            state["step"] = "edit_photo"
            await update.message.reply_text("Отправьте новое фото или введите 'skip':")
            return
        await update.message.reply_text("Выберите 1-4 или 0 для отмены")
        return
    if state["step"] == "edit_text":
        conn.execute("UPDATE questions SET question=? WHERE id=?", (text, state["question_id"]))
        conn.commit()
        await update.message.reply_text("✔ Текст обновлен")
        state["step"] = "quiz_menu"
        return
    if state["step"] == "edit_options":
        opts = [o.strip() for o in text.split(",") if o.strip()]
        if len(opts) < 2:
            await update.message.reply_text("❌ Нужно минимум два варианта. Попробуйте снова:")
            return
        conn.execute("UPDATE questions SET options=? WHERE id=?", (json.dumps(opts), state["question_id"]))
        conn.commit()
        await update.message.reply_text("✔ Варианты обновлены")
        state["step"] = "quiz_menu"
        return
    if state["step"] == "edit_answer":
        q = conn.execute("SELECT options FROM questions WHERE id=?", (state["question_id"],)).fetchone()
        opts = json.loads(q["options"])
        if text not in opts:
            await update.message.reply_text("❌ Ответ должен быть из вариантов. Введите снова:")
            return
        conn.execute("UPDATE questions SET answer=? WHERE id=?", (text, state["question_id"]))
        conn.commit()
        await update.message.reply_text("✔ Правильный ответ обновлен")
        state["step"] = "quiz_menu"
        return
    if state["step"] == "edit_photo":
        if update.message.photo:
            new_photo = update.message.photo[-1].file_id
            conn.execute("UPDATE questions SET photo=? WHERE id=?", (new_photo, state["question_id"]))
            conn.commit()
            await update.message.reply_text("✔ Фото обновлено")
        else:
            await update.message.reply_text("Оставлено старое фото")
        state["step"] = "quiz_menu"
        return

    # Удаление вопроса
    if state["step"] == "delete_select":
        try:
            qid = int(text)
        except:
            await update.message.reply_text("❌ Некорректный ID. Попробуйте снова:")
            return
        q = conn.execute("SELECT * FROM questions WHERE id=? AND quiz_id=?", (qid, state["quiz_id"])).fetchone()
        if not q:
            await update.message.reply_text("❌ Вопрос не найден.")
            return
        state["question_id"] = qid
        state["step"] = "delete_confirm"
        await update.message.reply_text("Введите ДА для подтверждения удаления:")
        return
    if state["step"] == "delete_confirm":
        if text.lower() == "да":
            conn.execute("DELETE FROM questions WHERE id=?", (state["question_id"],))
            conn.commit()
            await update.message.reply_text("🗑 Вопрос удален")
        else:
            await update.message.reply_text("Отмена удаления")
        state["step"] = "quiz_menu"
        return

    # Перемещение вопроса
    if state["step"] == "move_select":
        try:
            qid = int(text)
        except:
            await update.message.reply_text("❌ Некорректный ID. Попробуйте снова:")
            return
        q = conn.execute("SELECT * FROM questions WHERE id=? AND quiz_id=?", (qid, state["quiz_id"])).fetchone()
        if not q:
            await update.message.reply_text("❌ Вопрос не найден.")
            return
        state["question_id"] = qid
        state["step"] = "move_dir"
        await update.message.reply_text("Введите 'вверх' или 'вниз':")
        return
    if state["step"] == "move_dir":
        direction = text.lower()
        qid = state["question_id"]
        q = conn.execute("SELECT * FROM questions WHERE id=? AND quiz_id=?", (qid, state["quiz_id"])).fetchone()
        if not q:
            state["step"] = "quiz_menu"
            return
        pos = q["position"]
        if direction == "вверх":
            swap = conn.execute(
                "SELECT id, position FROM questions WHERE quiz_id=? AND position<? ORDER BY position DESC LIMIT 1",
                (state["quiz_id"], pos)
            ).fetchone()
        else:
            swap = conn.execute(
                "SELECT id, position FROM questions WHERE quiz_id=? AND position>? ORDER BY position ASC LIMIT 1",
                (state["quiz_id"], pos)
            ).fetchone()
        if not swap:
            await update.message.reply_text("❌ Нельзя переместить дальше")
        else:
            conn.execute("UPDATE questions SET position=? WHERE id=?", (swap["position"], qid))
            conn.execute("UPDATE questions SET position=? WHERE id=?", (pos, swap["id"]))
            conn.commit()
            await update.message.reply_text("✔ Вопрос перемещен")
        state["step"] = "quiz_menu"
        return

    # Редактирование описания викторины
    if state["step"] == "edit_desc":
        desc = text
        conn.execute("UPDATE quizzes SET description=? WHERE id=?", (desc, state["quiz_id"]))
        conn.commit()
        await update.message.reply_text("✔ Описание обновлено")
        state["step"] = "quiz_menu"
        return

    # Удаление викторины
    if state["step"] == "delete_quiz":
        if text.lower() == "да":
            conn.execute("DELETE FROM questions WHERE quiz_id=?", (state["quiz_id"],))
            conn.execute("DELETE FROM quizzes WHERE id=?", (state["quiz_id"],))
            conn.commit()
            await update.message.reply_text("🗑 Викторина удалена")
            state["step"] = "menu"
        else:
            await update.message.reply_text("Отмена удаления")
            state["step"] = "quiz_menu"
        return
