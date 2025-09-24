import logging
import sqlite3
import os
import random
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError
import json
from flask import Flask
import threading
import asyncio

# Состояния для ConversationHandler
PHOTO_RECOGNITION, CHOOSING_PRODUCT_NAME, CHOOSING_PURCHASE_DATE, CHOOSING_EXPIRATION_DATE = range(4)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка токена и URL
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    logger.error("❌ Токен не найден! Добавьте переменную TELEGRAM_BOT_TOKEN в Amvera → Переменные окружения")
    exit(1)

WEBHOOK_URL = os.getenv('WEBHOOK_URL')
if not WEBHOOK_URL:
    logger.error("❌ WEBHOOK_URL не задан! Укажите публичный URL вашего приложения на Amvera.")
    exit(1)

# Инициализация планировщика
scheduler = BackgroundScheduler()
scheduler.start()

# Инициализация базы данных SQLite
def init_db():
    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                purchase_date TEXT NOT NULL,
                expiration_days INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                notified BOOLEAN DEFAULT FALSE
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON products(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_expires_at ON products(expires_at)')
        conn.commit()
        cursor.close()
        conn.close()
        logger.info("✅ База данных SQLite инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        exit(1)

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

# Вспомогательная функция для парсинга даты
def parse_date(date_str: str):
    date_formats = ['%Y-%m-%d', '%Y.%m.%d', '%d.%m.%Y', '%d-%m-%Y']
    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None

# Вспомогательные функции
async def recognize_product(photo_path: str) -> str:
    """Заглушка для распознавания продукта"""
    products = ["Молоко", "Хлеб", "Яйца", "Сыр", "Йогурт", "Мясо", "Рыба", "Овощи", "Фрукты"]
    return random.choice(products)

def get_main_menu_keyboard():
    keyboard = [
        ["📸 Добавить по фото", "✍️ Добавить вручную"],
        ["📋 Мои продукты", "🚨 Просроченные"],
        ["🗑️ Очистить всё", "ℹ️ Помощь"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_cancel_keyboard():
    keyboard = [["❌ Отмена", "🏠 Главное меню"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# --- Диалог добавления продукта ВРУЧНУЮ ---
async def start_add_manually(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "✏️ *Шаг 1/3: Название продукта*\n\n"
        "Введите название продукта (например, 'Молоко', 'Сыр Моцарелла'):\n\n"
        "_Или нажмите '❌ Отмена' / '🏠 Главное меню'_",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_PRODUCT_NAME

async def choose_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        await cancel(update, context)
        return ConversationHandler.END

    if not user_input:
        await update.message.reply_text("Пожалуйста, введите корректное название продукта.")
        return CHOOSING_PRODUCT_NAME

    context.user_data['product_name'] = user_input
    await update.message.reply_text(
        "📅 *Шаг 2/3: Дата покупки*\n\n"
        "Введите дату покупки в одном из форматов:\n"
        "• ГГГГ-ММ-ДД (2025-09-23)\n"
        "• ГГГГ.ММ.ДД (2025.09.23)\n"
        "• ДД.ММ.ГГГГ (23.09.2025)\n\n"
        "_Или нажмите '❌ Отмена' / '🏠 Главное меню'_",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_PURCHASE_DATE

async def choose_purchase_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        await cancel(update, context)
        return ConversationHandler.END

    parsed_date = parse_date(user_input)
    if parsed_date is None:
        await update.message.reply_text(
            "😔 *Неверный формат даты.*\n"
            "Пожалуйста, введите дату в одном из форматов:\n"
            "• ГГГГ-ММ-ДД (2025-09-23)\n"
            "• ГГГГ.ММ.ДД (2025.09.23)\n"
            "• ДД.ММ.ГГГГ (23.09.2025)",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return CHOOSING_PURCHASE_DATE

    context.user_data['purchase_date'] = parsed_date.isoformat()
    await update.message.reply_text(
        "📆 *Шаг 3/3: Дата истечения срока*\n\n"
        "Введите дату истечения срока в том же формате:\n"
        "• ГГГГ-ММ-ДД (2025-10-07)\n"
        "• ГГГГ.ММ.ДД (2025.10.07)\n"
        "• ДД.ММ.ГГГГ (07.10.2025)\n\n"
        "_Или нажмите '❌ Отмена' / '🏠 Главное меню'_",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_EXPIRATION_DATE

async def choose_expiration_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        await cancel(update, context)
        return ConversationHandler.END

    parsed_date = parse_date(user_input)
    if parsed_date is None:
        await update.message.reply_text(
            "😔 *Неверный формат даты.*\n"
            "Пожалуйста, введите дату в одном из форматов:\n"
            "• ГГГГ-ММ-ДД (2025-10-07)\n"
            "• ГГГГ.ММ.ДД (2025.10.07)\n"
            "• ДД.ММ.ГГГГ (07.10.2025)",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return CHOOSING_EXPIRATION_DATE

    today = datetime.now().date()
    if parsed_date < today:
        await update.message.reply_text(
            "❌ *Ошибка:* Дата истечения не может быть в прошлом.\n"
            "Пожалуйста, введите корректную дату.",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return CHOOSING_EXPIRATION_DATE

    context.user_data['expires_at'] = parsed_date.isoformat()

    purchase_date = datetime.strptime(context.user_data['purchase_date'], '%Y-%m-%d').date()
    expiration_days = (parsed_date - purchase_date).days

    if expiration_days < 0:
        await update.message.reply_text(
            "❌ *Ошибка:* Дата истечения не может быть раньше даты покупки.\n"
            "Пожалуйста, начните заново.",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

    context.user_data['expiration_days'] = expiration_days

    # Сохраняем в SQLite
    user_id = update.message.from_user.id
    product_name = context.user_data['product_name']
    purchase_date_str = context.user_data['purchase_date']
    expires_at_str = context.user_data['expires_at']

    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO products (user_id, name, purchase_date, expiration_days, added_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, product_name, purchase_date_str, expiration_days, datetime.now().isoformat(), expires_at_str))
        product_id = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()

        # Планируем уведомление
        schedule_notification(product_id, user_id, product_name, expiration_days)

        success_text = (
            f"🎉 *Ура! Продукт добавлен!*\n\n"
            f"🔹 *Название:* {product_name}\n"
            f"📅 *Куплено:* {purchase_date_str}\n"
            f"📆 *Истекает:* {expires_at_str}\n"
            f"⏳ *Срок годности:* {expiration_days} дней\n\n"
            "🔔 Я напомню вам за день до истечения срока!"
        )

        await update.message.reply_text(
            success_text,
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )

    except Exception as e:
        logger.error(f"Ошибка сохранения продукта: {e}")
        await update.message.reply_text("❌ Произошла ошибка при сохранении продукта.", reply_markup=get_main_menu_keyboard())

    return ConversationHandler.END

# --- Диалог добавления продукта ПО ФОТО ---
async def start_add_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📸 *Добавление по фото*\n\n"
        "Отправьте фото продукта, и я попробую его распознать!\n\n"
        "_Или нажмите '❌ Отмена' / '🏠 Главное меню'_",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return PHOTO_RECOGNITION

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.message.from_user.id
        os.makedirs("photos", exist_ok=True)
        
        photo_file = await update.message.photo[-1].get_file()
        file_id = update.message.photo[-1].file_id
        photo_hash = file_id[-10:]
        photo_path = f"photos/photo_{user_id}_{photo_hash}.jpg"
        await photo_file.download_to_drive(photo_path)

        product_name = await recognize_product(photo_path)
        
        if os.path.exists(photo_path):
            os.remove(photo_path)

        if not product_name:
            await update.message.reply_text("❌ Не удалось распознать продукт. Попробуйте снова!", reply_markup=get_main_menu_keyboard())
            return ConversationHandler.END

        context.user_data['product_name'] = product_name
        await update.message.reply_text(
            f"🤖 *Распознан продукт:* {product_name}\n\n"
            "📅 *Шаг 2/3: Дата покупки*\n"
            "Введите дату покупки в одном из форматов:\n"
            "• ГГГГ-ММ-ДД (2025-09-23)\n"
            "• ГГГГ.ММ.ДД (2025.09.23)\n"
            "• ДД.ММ.ГГГГ (23.09.2025)",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )

        return CHOOSING_PURCHASE_DATE

    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке фото", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

async def handle_text_in_photo_recognition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_input = update.message.text.strip()
    
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        await cancel(update, context)
        return ConversationHandler.END
        
    await update.message.reply_text(
        "📸 Пожалуйста, отправьте фото продукта для распознавания.\n"
        "Или нажмите '❌ Отмена' для выхода.",
        reply_markup=get_cancel_keyboard()
    )
    return PHOTO_RECOGNITION

# --- Основные команды ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🌟 *Добро пожаловать в Freshly Bot!*\n\n"
        "Я — ваш личный помощник в борьбе с пищевыми отходами 🍎🥦\n\n"
        "✨ *Что я умею:*\n"
        "📸 *Распознавать продукты* по фото\n"
        "📅 *Отслеживать сроки годности*\n"
        "🔔 *Напоминать* о скором истечении срока\n"
        "👩‍🍳 *Предлагать рецепты* для использования продуктов\n\n"
        "Выберите действие ниже и начните спасать еду и деньги! 💰"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await start(update, context)

async def list_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.message.from_user.id
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, purchase_date, expiration_days, expires_at, notified 
            FROM products WHERE user_id = ? ORDER BY expires_at
        ''', (user_id,))
        products = cursor.fetchall()
        conn.close()

        if not products:
            await update.message.reply_text(
                "📦 *Пока нет продуктов.*\n\n"
                "Добавьте первый продукт с помощью кнопок '📸 Добавить по фото' или '✍️ Добавить вручную'!",
                parse_mode='Markdown',
                reply_markup=get_main_menu_keyboard()
            )
            return ConversationHandler.END

        text = "📋 *Ваши продукты:*\n\n"
        today = datetime.now().date()
        
        for i, (prod_id, name, purchase_date, exp_days, expires_at, notified) in enumerate(products, 1):
            expires_date = datetime.strptime(expires_at, '%Y-%m-%d').date()
            days_left = (expires_date - today).days
            
            if days_left < 0:
                status = "🔴 ПРОСРОЧЕНО"
            elif days_left == 0:
                status = "🔴 Истекает сегодня!"
            elif days_left == 1:
                status = "🟠 Истекает завтра"
            elif days_left <= 3:
                status = f"🟡 Истекает через {days_left} дня"
            else:
                status = f"🟢 Ещё {days_left} дней"
                
            text += f"{i}. *{name}* — {status}\n"

        text += "\n🏠 Вернитесь в главное меню, чтобы добавить ещё продуктов."
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в list_products_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке списка продуктов.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

async def show_expired_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.message.from_user.id
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT name, expires_at FROM products 
            WHERE user_id = ? AND expires_at <= ? AND notified = FALSE
            ORDER BY expires_at
        ''', (user_id, today))
        
        expired_products = cursor.fetchall()
        conn.close()

        if not expired_products:
            text = "✅ *Просроченных продуктов нет!*"
        else:
            text = "🚨 *Просроченные продукты:*\n\n"
            for name, expires_at in expired_products:
                text += f"• *{name}* - истек {expires_at}\n"
            text += "\n❌ Рекомендуем выбросить эти продукты!"

        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в show_expired_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке просроченных продуктов.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

async def clear_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.message.from_user.id
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
        conn.commit()
        cursor.close()
        conn.close()

        for job in scheduler.get_jobs():
            if job.id.startswith(f"notify_{user_id}_"):
                try:
                    scheduler.remove_job(job.id)
                except JobLookupError:
                    pass

        await update.message.reply_text("🗑️ *Все продукты удалены!*", parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в clear_products_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка при удалении продуктов.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    help_text = (
        "ℹ️ *Как пользоваться ботом:*\n\n"
        "1️⃣ *Добавить продукт:*\n"
        "   • Нажмите '📸 Добавить по фото' и отправьте снимок.\n"
        "   • Или '✍️ Добавить вручную' и введите название и даты.\n\n"
        "2️⃣ *Мои продукты:*\n"
        "   • Просмотрите список и отследите сроки годности.\n\n"
        "3️⃣ *Просроченные:*\n"
        "   • Узнайте, что нужно выбросить.\n\n"
        "4️⃣ *Очистить всё:*\n"
        "   • Удалите все записи.\n\n"
        "🔔 *Бот автоматически напомнит вам за день до истечения срока годности!*"
    )
    await update.message.reply_text(
        help_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        '✅ Операция отменена.',
        reply_markup=get_main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

# --- Планировщик ---
def schedule_notification(product_id: int, user_id: int, product_name: str, expiration_days: int):
    try:
        notify_time = datetime.now() + timedelta(days=expiration_days - 1)
        job_id = f"notify_{user_id}_{product_id}"
        
        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            pass
            
        scheduler.add_job(
            send_notification,
            'date',
            run_date=notify_time,
            args=[user_id, product_name, product_id],
            id=job_id
        )
        logger.info(f"Запланировано уведомление для продукта {product_id} пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка планирования уведомления: {e}")

async def send_notification(user_id: int, product_name: str, product_id: int):
    try:
        from telegram import Bot
        bot = Bot(token=TOKEN)
        
        await bot.send_message(
            chat_id=user_id,
            text=f"⚠️ *{product_name}* испортится завтра!\nПопробуй приготовить что-нибудь? 👨‍🍳",
            parse_mode='Markdown'
        )
        logger.info(f"Уведомление отправлено пользователю {user_id} для продукта {product_name}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

async def check_expired_products():
    try:
        from telegram import Bot
        bot = Bot(token=TOKEN)
        
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT DISTINCT user_id FROM products 
            WHERE expires_at <= ? AND notified = FALSE
        ''', (today,))
        
        expired_users = cursor.fetchall()
        
        for (user_id,) in expired_users:
            try:
                cursor.execute('''
                    SELECT name, expires_at FROM products 
                    WHERE user_id = ? AND expires_at <= ? AND notified = FALSE
                ''', (user_id, today))
                
                expired_products = cursor.fetchall()
                if expired_products:
                    product_list = "\n".join([f"• {name} (истек {expires_at})" for name, expires_at in expired_products])
                    
                    cursor.execute('''
                        UPDATE products SET notified = TRUE 
                        WHERE user_id = ? AND expires_at <= ?
                    ''', (user_id, today))
                    conn.commit()

                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=f"🚨 *Просроченные продукты:*\n{product_list}\n\nРекомендуем выбросить!",
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
                    
            except Exception as e:
                logger.error(f"Ошибка обработки пользователя {user_id}: {e}")
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Ошибка в check_expired_products: {e}")

# --- Обработчик меню ---
async def handle_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text

    if text == "🏠 Главное меню":
        context.user_data.clear()
        return await show_main_menu(update, context)
    elif text == "❌ Отмена":
        return await cancel(update, context)
    elif text == "📸 Добавить по фото":
        return await start_add_by_photo(update, context)
    elif text == "✍️ Добавить вручную":
        return await start_add_manually(update, context)
    elif text == "📋 Мои продукты":
        return await list_products_handler(update, context)
    elif text == "🚨 Просроченные":
        return await show_expired_handler(update, context)
    elif text == "🗑️ Очистить всё":
        return await clear_products_handler(update, context)
    elif text == "ℹ️ Помощь":
        return await help_handler(update, context)
    else:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

# --- Flask Health Check Server ---
app = Flask(__name__)

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- Основная функция ---
async def set_webhook(application):
    webhook_path = f"/{TOKEN}"
    full_webhook_url = WEBHOOK_URL + webhook_path
    await application.bot.set_webhook(url=full_webhook_url, secret_token=TOKEN)
    logger.info(f"🌐 Webhook установлен: {full_webhook_url}")

def main():
    try:
        # Запускаем Flask в фоновом потоке
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        application = Application.builder().token(TOKEN).build()

        # Обработчики
        manual_conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^✍️ Добавить вручную$"), start_add_manually)],
            states={
                CHOOSING_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_product_name)],
                CHOOSING_PURCHASE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_purchase_date)],
                CHOOSING_EXPIRATION_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_expiration_date)],
            },
            fallbacks=[
                MessageHandler(filters.Regex("^(❌ Отмена|🏠 Главное меню)$"), cancel),
                CommandHandler("start", show_main_menu)
            ],
            allow_reentry=True
        )

        photo_conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^📸 Добавить по фото$"), start_add_by_photo)],
            states={
                PHOTO_RECOGNITION: [
                    MessageHandler(filters.PHOTO, handle_photo),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_in_photo_recognition)
                ],
                CHOOSING_PURCHASE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_purchase_date)],
                CHOOSING_EXPIRATION_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_expiration_date)],
            },
            fallbacks=[
                MessageHandler(filters.Regex("^(❌ Отмена|🏠 Главное меню)$"), cancel),
                CommandHandler("start", show_main_menu)
            ],
            allow_reentry=True
        )

        application.add_handler(manual_conv_handler)
        application.add_handler(photo_conv_handler)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_choice))
        application.add_handler(CommandHandler("start", start))

        # Планировщик
        scheduler.add_job(
            check_expired_products,
            'cron',
            hour=9,
            minute=0,
            id='daily_expired_check'
        )

        # Устанавливаем вебхук
        asyncio.run(set_webhook(application))

        # Запускаем Telegram бота через вебхук
        PORT = int(os.environ.get('PORT', 8080))
        logger.info(f"🚀 Запуск Telegram бота через Webhook на порту {PORT}...")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=WEBHOOK_URL + f"/{TOKEN}",
            secret_token=TOKEN
        )

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        scheduler.shutdown()

if __name__ == '__main__':
    main()
