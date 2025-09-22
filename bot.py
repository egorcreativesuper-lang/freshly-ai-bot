import logging
import sqlite3
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError
import json

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔐 Загрузка токена из переменной окружения (безопасно!)
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    logger.error("❌ Токен не найден! Установите TELEGRAM_BOT_TOKEN в настройках хостинга.")
    exit(1)

# Инициализация планировщика
scheduler = BackgroundScheduler()
scheduler.start()

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            purchase_date TEXT NOT NULL,
            expiration_days INTEGER NOT NULL,
            added_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Загрузка рецептов
def load_recipes():
    try:
        with open('recipes.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("Файл recipes.json не найден")
        return []

RECIPES = load_recipes()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я — Freshly Bot 🤖\n"
        "Я помогу тебе не выбрасывать еду — и никто не узнает, что у тебя в холодильнике.\n\n"
        "📸 Отправь мне фото продукта — и я скажу, когда он испортится.\n"
        "📋 Команды:\n"
        "/list — показать все продукты\n"
        "/clear — удалить все продукты"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    photo_file = await update.message.photo[-1].get_file()
    photo_path = f"photo_{user_id}.jpg"
    await photo_file.download_to_drive(photo_path)

    # 🔍 Заглушка — распознавание продукта
    product_name = "Молоко"

    # 📅 Сохраняем продукт
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    purchase_date = datetime.now().strftime('%Y-%m-%d')
    expiration_days = 7
    added_at = datetime.now().isoformat()

    cursor.execute('''
        INSERT INTO products (user_id, name, purchase_date, expiration_days, added_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, product_name, purchase_date, expiration_days, added_at))
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # ⏰ Планируем уведомление
    notify_time = datetime.now() + timedelta(days=expiration_days - 1)
    job_id = f"notify_{product_id}"
    scheduler.add_job(
        send_notification,
        'date',
        run_date=notify_time,
        args=[context.bot, user_id, product_name],
        id=job_id
    )

    await update.message.reply_text(
        f"✅ Распознал: *{product_name}*\n"
        f"📅 Куплено: {purchase_date}\n"
        f"⏳ Истекает: через {expiration_days} дней\n"
        "🔔 Напомню за 1 день!",
        parse_mode='Markdown'
    )

async def send_notification(bot, user_id, product_name):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"⚠️ *{product_name}* испортится завтра!\n"
                 "Попробуй сделать творог? 🥛",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Рецепт", callback_data=f"recipe_{product_name}")],
                [InlineKeyboardButton("Пропустить", callback_data="ignore")]
            ])
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("recipe_"):
        product_name = query.data.split("_", 1)[1]
        recipe = next((r for r in RECIPES if r['name'] == product_name), None)
        if recipe:
            steps = "\n".join(f"{i+1}. {step}" for i, step in enumerate(recipe['steps']))
            await query.edit_message_text(
                f"👩‍🍳 *{recipe['name']}*\n"
                f"⏱️ Время: {recipe['time_minutes']} мин\n"
                f"🍽️ Порций: {recipe['servings']}\n\n"
                f"*Ингредиенты:*\n{', '.join(recipe['ingredients'])}\n\n"
                f"*Шаги:*\n{steps}",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("Рецепт не найден 😔")

    elif query.data == "ignore":
        await query.edit_message_text("Хорошо, напомню в следующий раз 😉")

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, purchase_date, expiration_days, added_at FROM products WHERE user_id = ?', (user_id,))
    products = cursor.fetchall()
    conn.close()

    if not products:
        await update.message.reply_text("📦 Пока нет продуктов. Отправь фото — и я добавлю!")
        return

    text = "📋 *Твои продукты:*\n\n"
    for name, purchase_date, exp_days, added_at in products:
        expires_at = datetime.strptime(purchase_date, '%Y-%m-%d') + timedelta(days=exp_days)
        days_left = (expires_at - datetime.now()).days
        if days_left <= 0:
            status = "🔴 Истекает сегодня!"
        elif days_left == 1:
            status = "🟠 Истекает завтра"
        elif days_left <= 3:
            status = f"🟡 Истекает через {days_left} дня"
        else:
            status = f"🟢 Ещё {days_left} дней"
        text += f"• *{name}* — {status}\n"

    await update.message.reply_text(text, parse_mode='Markdown')

async def clear_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

    # 🗑️ Удаляем все запланированные уведомления
    for job in scheduler.get_jobs():
        if job.id.startswith(f"notify_") and str(user_id) in str(job.args):
            try:
                scheduler.remove_job(job.id)
            except JobLookupError:
                pass

    await update.message.reply_text("🗑️ Все продукты удалены!")

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_products))
    application.add_handler(CommandHandler("clear", clear_products))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("🚀 Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
