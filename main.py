import asyncio
import sqlite3
import os
from datetime import datetime
import pytz
import logging

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# === ЛОГИ ===
logging.basicConfig(level=logging.INFO)

# === НАСТРОЙКИ ===
import logging
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")

logging.info("BOT_TOKEN repr: %r; type: %s", TOKEN, type(TOKEN))

TOKEN = os.getenv('BOT_TOKEN')
ADMINS = [1920657547, 363720024]          # 🔐 ID администратора
CHANNEL_ID = -1003281573197   # 📢 ID канала
TIMEZONE = "Europe/Moscow"

#bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

# === БАЗА ДАННЫХ ===
conn = sqlite3.connect("posts.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER,
    text TEXT,
    image_path TEXT,
    post_time TEXT
)
""")
conn.commit()
#=======Проверка админа ==========
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

# === ГЛАВНОЕ МЕНЮ ===
def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗓 Мои посты", callback_data="list_posts")],
        [InlineKeyboardButton(text="➕ Добавить пост", callback_data="help_add")]
    ])


# === ФУНКЦИЯ ПУБЛИКАЦИИ ===
async def publish_post(post_id: int):
    try:
        with sqlite3.connect("posts.db", check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT text, image_path FROM posts WHERE id = ?", (post_id,))
            post = cursor.fetchone()

            if not post:
                logging.warning(f"Пост {post_id} не найден.")
                return

            text, image_path = post
            if not os.path.exists(image_path):
                logging.warning(f"Фото {image_path} не найдено.")
                return

            photo = FSInputFile(image_path)

            try:
                await bot.send_photo(chat_id=CHANNEL_ID, photo=photo, caption=text)
            except Exception as e:
                logging.error(f"Ошибка публикации в канал: {e}")
                return

            cursor.execute("DELETE FROM posts WHERE id = ?", (post_id,))
            conn.commit()
            os.remove(image_path)
            logging.info(f"✅ Пост {post_id} опубликован и удалён из очереди.")
    except Exception as e:
        logging.error(f"Ошибка публикации поста {post_id}: {e}")

# === КНОПКИ ===
def make_posts_keyboard(posts):
    buttons = [
        [InlineKeyboardButton(text=f"#{pid} | {ptime[:16]} | {text[:25]}...", callback_data=f"post_{pid}")]
        for pid, ptime, text in posts
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def post_details_kb(post_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit_{post_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{post_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="list_posts")],
        ##[InlineKeyboardButton(text="", callback_data="list_posts")]
    ])

def edit_menu(pid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data=f"edit_text_{pid}")],
        [InlineKeyboardButton(text="🕒 Изменить время", callback_data=f"edit_time_{pid}")],
        [InlineKeyboardButton(text="🖼 Изменить фото", callback_data=f"edit_photo_{pid}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"post_{pid}")]
    ])

# === START ===
@dp.message(Command("start"))
async def start(message: types.Message):
    
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У тебя нет доступа.")
        return
    await message.answer("Привет, админ 👋", reply_markup=main_menu())

# === ИНСТРУКЦИЯ ДОБАВЛЕНИЯ ===
@dp.callback_query(F.data == "help_add")
async def help_add(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Чтобы запланировать пост:\n"
        "Отправь фото с подписью:\n"
        "```\n2025-11-01 14:30\nТекст поста\n```",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )
    await callback.answer()

# === МОД РЕДАКТИРОВАНИЯ ===
edit_mode = {}

# === ОБРАБОТКА ФОТО (добавление + редактирование) ===
@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if message.from_user.id not in [*ADMINS, *edit_mode.keys()]:
        return


    # --- редактирование фото ---
    if message.from_user.id in edit_mode and edit_mode[message.from_user.id]["mode"] == "photo":
        pid = edit_mode[message.from_user.id]["post_id"]
        file = await bot.get_file(message.photo[-1].file_id)
        new_path = f"images/{file.file_unique_id}.jpg"
        await bot.download_file(file.file_path, new_path)
        cursor.execute("UPDATE posts SET image_path = ? WHERE id = ?", (new_path, pid))
        conn.commit()
        del edit_mode[message.from_user.id]
        await message.answer(f"✅ Фото поста #{pid} обновлено.", reply_markup=main_menu())
        return

    # --- добавление поста ---
    if not message.caption:
        return await message.answer(
            "Добавь подпись с временем публикации:\n"
            "```\n2025-11-01 14:30\nТекст поста\n```",
            parse_mode="Markdown"
        )

    parts = message.caption.split("\n", 1)
    time_str = parts[0].strip()
    text = parts[1].strip() if len(parts) > 1 else ""

    if len(time_str) < 16:
        return await message.answer("⏰ Формат: `ГГГГ-ММ-ДД ЧЧ:ММ`", parse_mode="Markdown")

    try:
        tz = pytz.timezone(TIMEZONE)
        post_time = tz.localize(datetime.strptime(time_str, "%Y-%m-%d %H:%M"))
    except ValueError:
        return await message.answer("⏰ Неверный формат даты. Используй `ГГГГ-ММ-ДД ЧЧ:ММ`.")

    file = await bot.get_file(message.photo[-1].file_id)
    os.makedirs("images", exist_ok=True)
    image_path = f"images/{file.file_unique_id}.jpg"
    await bot.download_file(file.file_path, image_path)

    cursor.execute(
        "INSERT INTO posts (admin_id, text, image_path, post_time) VALUES (?, ?, ?, ?)",
        (message.from_user.id, text, image_path, post_time.isoformat())
    )
    conn.commit()
    post_id = cursor.lastrowid

    scheduler.add_job(publish_post, "date", run_date=post_time, args=[post_id])
    await message.answer(f"✅ Пост #{post_id} запланирован на {post_time.strftime('%Y-%m-%d %H:%M')}",
          reply_markup=main_menu())

# === СПИСОК ПОСТОВ ===
@dp.callback_query(F.data == "list_posts")
async def list_posts(callback: types.CallbackQuery):
    cursor.execute("SELECT id, post_time, text FROM posts ORDER BY post_time")
    posts = cursor.fetchall()

    text = "📭 Нет запланированных постов." if not posts else "🗓 Запланированные посты:"
    reply_markup = main_menu() if not posts else make_posts_keyboard(posts)

    try:
        # Если сообщение было фото — просто отправляем новое
        if callback.message.photo:
            await callback.message.answer(text, reply_markup=reply_markup)
        else:
            await callback.message.edit_text(text, reply_markup=reply_markup)
    except Exception as e:
        logging.warning(f"Ошибка при возврате к списку постов: {e}")
        await callback.message.answer(text, reply_markup=reply_markup)

    await callback.answer()


# === ДЕТАЛИ ПОСТА ===
@dp.callback_query(F.data.startswith("post_"))
async def show_post(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[1])
    cursor.execute("SELECT text, post_time, image_path FROM posts WHERE id = ?", (pid,))
    post = cursor.fetchone()
    if not post:
        return await callback.answer("❌ Пост не найден", show_alert=True)

    text, ptime, image_path = post
    caption = f"🆔 Пост #{pid}\n🕒 {ptime}\n\n{text}"

    if os.path.exists(image_path):
        try:
            photo = FSInputFile(image_path)
            media = InputMediaPhoto(media=photo, caption=caption)
            await callback.message.edit_media(media=media, reply_markup=post_details_kb(pid))
        except Exception as e:
            logging.error(f"Ошибка показа фото: {e}")
            await callback.message.edit_text(caption, reply_markup=post_details_kb(pid))
    else:
        await callback.message.edit_text(caption, reply_markup=post_details_kb(pid))

    await callback.answer()

# === УДАЛЕНИЕ ПОСТА ===
@dp.callback_query(F.data.startswith("delete_"))
async def delete_post(callback: types.CallbackQuery):
    pid = int(callback.data.split("_")[1])

    # Удаляем фото из системы
    cursor.execute("SELECT image_path FROM posts WHERE id = ?", (pid,))
    row = cursor.fetchone()
    if row and os.path.exists(row[0]):
        os.remove(row[0])

    # Удаляем запись из БД
    cursor.execute("DELETE FROM posts WHERE id = ?", (pid,))
    conn.commit()

    # Если сообщение с фото — отправляем новое
    if callback.message.photo:
        await callback.message.answer(f"🗑 Пост #{pid} удалён.", reply_markup=main_menu())
    else:
        await callback.message.edit_text(f"🗑 Пост #{pid} удалён.", reply_markup=main_menu())

    await callback.answer()


# === РЕДАКТИРОВАНИЕ ===
@dp.callback_query(F.data.startswith("edit_"))
async def edit_post(callback: types.CallbackQuery):
    parts = callback.data.split("_")

    if len(parts) == 2:
        pid = int(parts[1])
        # Проверяем, фото ли это сообщение
        if callback.message.photo:
            # Отправляем новое сообщение, вместо редактирования
            await callback.message.answer(
                f"Редактирование поста #{pid}:",
                reply_markup=edit_menu(pid)
            )
        else:
            # Можно безопасно редактировать текст
            await callback.message.edit_text(
                f"Редактирование поста #{pid}:",
                reply_markup=edit_menu(pid)
            )
        await callback.answer()
        return

    elif len(parts) == 3:
        mode, pid = parts[1], int(parts[2])
        edit_mode[callback.from_user.id] = {"mode": mode, "post_id": pid}

        text = ""
        if mode == "text":
            text = f"✏️ Отправь новый текст для поста #{pid}:"
        elif mode == "time":
            text = (
                f"🕒 Введи новое время публикации для поста #{pid}:\n"
                f"Формат: `2025-11-01 14:00`"
            )
        elif mode == "photo":
            text = f"🖼 Отправь новое фото для поста #{pid}:"

        # Здесь тоже проверяем тип сообщения
        if callback.message.photo:
            await callback.message.answer(text, parse_mode="Markdown")
        else:
            await callback.message.edit_text(text, parse_mode="Markdown")

        await callback.answer()


# === РЕДАКТИРОВАНИЕ ТЕКСТА / ВРЕМЕНИ ===
@dp.message(F.text)
async def handle_edit_text(message: types.Message):
    if message.from_user.id not in edit_mode:
        return

    mode = edit_mode[message.from_user.id]
    pid = mode["post_id"]

    if mode["mode"] == "text":
        cursor.execute("UPDATE posts SET text = ? WHERE id = ?", (message.text, pid))
        conn.commit()
        del edit_mode[message.from_user.id]
        await message.answer(f"✅ Текст поста #{pid} обновлён.", reply_markup=main_menu())

    elif mode["mode"] == "time":
        try:
            tz = pytz.timezone(TIMEZONE)
            new_time = tz.localize(datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M"))
            cursor.execute("UPDATE posts SET post_time = ? WHERE id = ?", (new_time.isoformat(), pid))
            conn.commit()
            scheduler.add_job(publish_post, "date", run_date=new_time, args=[pid])
            del edit_mode[message.from_user.id]
            await message.answer(f"✅ Время поста #{pid} изменено на {new_time.strftime('%Y-%m-%d %H:%M')}", reply_markup=main_menu())
        except ValueError:
            await message.answer("⏰ Неверный формат. Используй `ГГГГ-ММ-ДД ЧЧ:ММ`.")

# === НАЗАД В ГЛАВНОЕ МЕНЮ ===
@dp.callback_query(F.data == "back_main")
async def back_main(callback: types.CallbackQuery):
    try:
        # Проверяем, фото ли это сообщение
        if callback.message.photo:
            await callback.message.answer("Главное меню:", reply_markup=main_menu())
        else:
            await callback.message.edit_text("Главное меню:", reply_markup=main_menu())
    except Exception as e:
        # На случай, если Telegram всё равно не примет редактирование
        await callback.message.answer("Главное меню:", reply_markup=main_menu())
        logging.warning(f"Ошибка при возврате в главное меню: {e}")
    await callback.answer()


# === ВОССТАНОВЛЕНИЕ ЗАДАЧ ===
async def restore_jobs():
    cursor.execute("SELECT id, post_time FROM posts")
    for pid, ptime in cursor.fetchall():
        post_time = datetime.fromisoformat(ptime)
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        if post_time > now:
            scheduler.add_job(publish_post, "date", run_date=post_time, args=[pid])
            logging.info(f"🔁 Восстановлена задача для поста #{pid}")





# === ЗАПУСК ===
async def main():
    try:
        scheduler.start()
        await restore_jobs()
        logging.info("✅ Scheduler запущен")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Ошибка в main: {e}")


@dp.message(F.forward_from_chat)
async def forwarded_message(message: types.Message):
    chat = message.forward_from_chat
    await message.answer(f"📢 ID канала: `{chat.id}`\nНазвание: {chat.title}", parse_mode="Markdown")


if __name__ == "__main__":
    asyncio.run(main())
