import logging
import sqlite3
import os
import re
import random
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError
import json

# Состояния для ConversationHandler
CHOOSING_PRODUCT_NAME, CHOOSING_PURCHASE_DATE, CHOOSING_EXPIRATION_DATE, BROWSE_PRODUCTS, BROWSE_PRODUCT_DETAIL = range(5)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка токена
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    logger.error("❌ Токен не найден! Добавьте переменную TELEGRAM_BOT_TOKEN в Render → Environment")
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
            added_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            notified BOOLEAN DEFAULT FALSE
        )
    ''')
    # Добавляем индексы для быстрого поиска
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON products(user_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_expires_at ON products(expires_at)')
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

# Вспомогательные функции
def create_safe_callback_data(product_name, product_id):
    """Создает безопасный callback_data без спецсимволов (для внутреннего использования)"""
    safe_name = re.sub(r'[^a-zA-Z0-9а-яА-Я]', '_', product_name)
    return f"recipe_{safe_name}_{product_id}"

def parse_callback_data(callback_data):
    """Парсит callback_data и возвращает product_name и product_id (для внутреннего использования)"""
    try:
        parts = callback_data.split('_')
        if len(parts) >= 3:
            product_name = parts[1].replace('_', ' ')  # Восстанавливаем пробелы
            product_id = parts[2]
            return product_name, product_id
        return None, None
    except Exception as e:
        logger.error(f"Ошибка парсинга callback_ {e}")
        return None, None

async def recognize_product(photo_path: str) -> str:
    """Заглушка для распознавания продукта"""
    products = ["Молоко", "Хлеб", "Яйца", "Сыр", "Йогурт", "Мясо", "Рыба", "Овощи", "Фрукты"]
    return random.choice(products)

def get_main_menu_keyboard():
    """Возвращает обычную клавиатуру главного меню."""
    keyboard = [
        ["📸 Добавить по фото", "✍️ Добавить вручную"],
        ["📋 Мои продукты", "🚨 Просроченные"],
        ["🗑️ Очистить всё", "ℹ️ Помощь"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_cancel_keyboard():
    """Возвращает клавиатуру с кнопкой отмены и главного меню."""
    keyboard = [["❌ Отмена", "🏠 Главное меню"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

def get_back_to_menu_keyboard():
    """Возвращает клавиатуру с кнопкой возврата в меню."""
    keyboard = [["🏠 Главное меню"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

def schedule_notification(product_id: int, user_id: int, product_name: str, expiration_days: int):
    """Планирует уведомление за 1 день до истечения срока"""
    try:
        notify_time = datetime.now() + timedelta(days=expiration_days - 1)
        job_id = f"notify_{user_id}_{product_id}"
        
        # Удаляем старую job если существует
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
    """Отправляет уведомление о скором истечении срока"""
    try:
        from telegram import Bot
        bot = Bot(token=TOKEN)
        
        # Готовим инлайн-клавиатуру для уведомления (это ограничение Telegram — в "толкнутых" сообщениях можно использовать только инлайн)
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Показать рецепт", callback_data=create_safe_callback_data(product_name, product_id))],
            [InlineKeyboardButton("🔕 Больше не напоминать", callback_data=f"disable_notify_{product_id}")],
        ])
        
        await bot.send_message(
            chat_id=user_id,
            text=f"⚠️ *{product_name}* испортится завтра!\nПопробуй приготовить что-нибудь? 👨‍🍳",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        logger.info(f"Уведомление отправлено пользователю {user_id} для продукта {product_name}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

# Обработчики команд и состояний
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и показ главного меню."""
    await update.message.reply_text(
        "🌟 *Добро пожаловать в Freshly Bot!*\n\n"
        "Я — ваш личный помощник в борьбе с пищевыми отходами 🍎🥦\n\n"
        "✨ *Что я умею:*\n"
        "📸 *Распознавать продукты* по фото\n"
        "📅 *Отслеживать сроки годности*\n"
        "🔔 *Напоминать* о скором истечении срока\n"
        "👩‍🍳 *Предлагать рецепты* для использования продуктов\n\n"
        "Выберите действие ниже и начните спасать еду и деньги! 💰",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает главное меню с красивым описанием."""
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

# --- Диалог добавления продукта ВРУЧНУЮ ---
async def start_add_manually(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления продукта вручную. Запрашивает название."""
    await update.message.reply_text(
        "✏️ *Шаг 1/3: Название продукта*\n\n"
        "Введите название продукта (например, 'Молоко', 'Сыр Моцарелла'):\n\n"
        "_Или нажмите '❌ Отмена' / '🏠 Главное меню'_",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_PRODUCT_NAME

async def choose_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет название продукта и запрашивает дату покупки."""
    user_input = update.message.text.strip()
    if user_input == "❌ Отмена" or user_input == "🏠 Главное меню":
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
    """Сохраняет дату покупки и запрашивает дату истечения срока."""
    user_input = update.message.text.strip()
    if user_input == "❌ Отмена" or user_input == "🏠 Главное меню":
        await cancel(update, context)
        return ConversationHandler.END

    # Пробуем разные форматы даты
    date_formats = ['%Y-%m-%d', '%Y.%m.%d', '%d.%m.%Y', '%d-%m-%Y']
    parsed_date = None

    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(user_input, fmt).date()
            break
        except ValueError:
            continue

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
    return CHOOSING_EXPIRATION_DATE  # <-- Ключевая строка! Возвращаем следующее состояние

async def choose_expiration_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет дату истечения срока, вычисляет срок годности в днях и сохраняет продукт в БД."""
    user_input = update.message.text.strip()
    if user_input == "❌ Отмена" or user_input == "🏠 Главное меню":
        await cancel(update, context)
        return ConversationHandler.END

    # Пробуем разные форматы даты
    date_formats = ['%Y-%m-%d', '%Y.%m.%d', '%d.%m.%Y', '%d-%m-%Y']
    parsed_date = None

    for fmt in date_formats:
        try:
            parsed_date = datetime.strptime(user_input, fmt).date()
            break
        except ValueError:
            continue

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

    context.user_data['expires_at'] = parsed_date.isoformat()

    # Вычисляем срок годности в днях
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

    # Сохраняем в БД
    user_id = update.message.from_user.id
    product_name = context.user_data['product_name']
    purchase_date_str = context.user_data['purchase_date']
    expires_at_str = context.user_data['expires_at']

    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    added_at = datetime.now().isoformat()

    cursor.execute('''
        INSERT INTO products (user_id, name, purchase_date, expiration_days, added_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, product_name, purchase_date_str, expiration_days, added_at, expires_at_str))
    product_id = cursor.lastrowid
    conn.commit()
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

    return ConversationHandler.END

# --- Диалог добавления продукта ПО ФОТО ---
async def start_add_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс добавления продукта через фото."""
    await update.message.reply_text(
        "📸 *Добавление по фото*\n\n"
        "Отправьте фото продукта, и я попробую его распознать!\n\n"
        "_Или нажмите '❌ Отмена' / '🏠 Главное меню'_",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    # Устанавливаем флаг, что продукт добавляется по фото
    context.user_data['adding_by_photo'] = True
    return ConversationHandler.END

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает фото и начинает запрос даты покупки."""
    if not context.user_data.get('adding_by_photo'):
        # Если фото пришло вне контекста добавления, игнорируем или показываем меню
        await update.message.reply_text("Пожалуйста, используйте кнопки меню для добавления продукта.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    try:
        user_id = update.message.from_user.id
        
        # Создаем папку для фото если нет
        os.makedirs("photos", exist_ok=True)
        
        # Скачиваем фото
        photo_file = await update.message.photo[-1].get_file()
        file_id = update.message.photo[-1].file_id
        photo_hash = file_id[-10:]  # Простой хэш
        photo_path = f"photos/photo_{user_id}_{photo_hash}.jpg"
        await photo_file.download_to_drive(photo_path)

        # Распознаем продукт
        product_name = await recognize_product(photo_path)
        
        if not product_name:
            await update.message.reply_text("❌ Не удалось распознать продукт. Попробуйте снова!", reply_markup=get_main_menu_keyboard())
            context.user_data.pop('adding_by_photo', None) # Сбрасываем флаг
            return ConversationHandler.END

        # Сохраняем распознанное имя в user_data
        context.user_data['product_name'] = product_name
        context.user_data.pop('adding_by_photo', None) # Сбрасываем флаг
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
        context.user_data.pop('adding_by_photo', None) # Сбрасываем флаг
        return ConversationHandler.END

# --- Просмотр списка продуктов ---
async def list_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает список продуктов с возможностью выбора."""
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

        text = "📋 *Ваши продукты:*\n\nВыберите продукт, отправив его номер.\n\n"
        keyboard = []
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
            # Сохраняем продукт в user_data для быстрого доступа
            context.user_data[f'product_{i}'] = prod_id

        text += "\nОтправьте номер продукта для просмотра деталей."
        keyboard = [["🏠 Главное меню"]]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
        return BROWSE_PRODUCTS

    except Exception as e:
        logger.error(f"Ошибка в list_products_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке списка продуктов.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

async def browse_product_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор продукта из списка."""
    user_input = update.message.text.strip()
    if user_input == "🏠 Главное меню":
        await show_main_menu(update, context)
        return ConversationHandler.END

    try:
        product_index = int(user_input)
        product_id = context.user_data.get(f'product_{product_index}')
        if not product_id:
            await update.message.reply_text("Неверный номер продукта. Попробуйте снова.")
            return BROWSE_PRODUCTS

        # Сохраняем ID выбранного продукта
        context.user_data['selected_product_id'] = product_id
        return await show_product_detail(update, context, product_id)

    except ValueError:
        await update.message.reply_text("Пожалуйста, введите номер продукта.")
        return BROWSE_PRODUCTS

async def show_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> int:
    """Показывает детальную информацию о продукте и предлагает действия."""
    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, purchase_date, expiration_days, expires_at, notified 
            FROM products WHERE id = ?
        ''', (product_id,))
        product = cursor.fetchone()
        conn.close()

        if not product:
            await update.message.reply_text("❌ Продукт не найден.", reply_markup=get_main_menu_keyboard())
            return ConversationHandler.END

        name, purchase_date, exp_days, expires_at, notified = product
        text = (
            f"🔹 *{name}*\n"
            f"📅 *Дата покупки:* {purchase_date}\n"
            f"📆 *Срок годности:* {exp_days} дней\n"
            f"⚠️ *Истекает:* {expires_at}\n\n"
            "Выберите действие:"
        )

        keyboard = [
            ["📖 Показать рецепт", "🗑️ Удалить продукт"],
            ["🔙 Назад к списку", "🏠 Главное меню"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
        return BROWSE_PRODUCT_DETAIL

    except Exception as e:
        logger.error(f"Ошибка в show_product_detail: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке информации о продукте.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

async def handle_product_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает действия над выбранным продуктом."""
    user_input = update.message.text.strip()
    product_id = context.user_data.get('selected_product_id')

    if user_input == "🏠 Главное меню":
        await show_main_menu(update, context)
        return ConversationHandler.END
    elif user_input == "🔙 Назад к списку":
        return await list_products_handler(update, context)
    elif user_input == "📖 Показать рецепт":
        return await show_recipes_for_product(update, context, product_id)
    elif user_input == "🗑️ Удалить продукт":
        return await delete_product(update, context, product_id)
    else:
        await update.message.reply_text("Пожалуйста, выберите действие с помощью кнопок.")
        return BROWSE_PRODUCT_DETAIL

async def show_recipes_for_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> int:
    """Показывает рецепты для выбранного продукта."""
    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('SELECT name FROM products WHERE id = ?', (product_id,))
        product = cursor.fetchone()
        conn.close()

        if not product:
            await update.message.reply_text("❌ Продукт не найден.", reply_markup=get_main_menu_keyboard())
            return ConversationHandler.END

        product_name = product[0]
        # Ищем рецепт (простой поиск по имени)
        recipe = None
        for r in RECIPES:
            if r.get('name', '').lower() == product_name.lower():
                recipe = r
                break

        if recipe:
            ingredients = ", ".join(recipe.get('ingredients', []))
            steps = "\n".join([f"{i+1}. {step}" for i, step in enumerate(recipe.get('steps', []))])
            
            recipe_text = (
                f"👩‍🍳 *{recipe['name']}*\n\n"
                f"⏱️ *Время приготовления:* {recipe.get('time_minutes', 'N/A')} мин\n"
                f"🍽️ *Количество порций:* {recipe.get('servings', 'N/A')}\n\n"
                f"*Ингредиенты:*\n{ingredients}\n\n"
                f"*Шаги приготовления:*\n{steps}"
            )
            
            await update.message.reply_text(
                recipe_text,
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup([["🔙 Назад к продукту", "🏠 Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
            )
        else:
            await update.message.reply_text(
                f"📚 *Рецепт для '{product_name}' не найден* 😔\n\n"
                "Попробуйте поискать в интернете или придумать свой рецепт!",
                parse_mode='Markdown',
                reply_markup=ReplyKeyboardMarkup([["🔙 Назад к продукту", "🏠 Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
            )
            
        return BROWSE_PRODUCT_DETAIL

    except Exception as e:
        logger.error(f"Ошибка в show_recipes_for_product: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке рецепта.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> int:
    """Удаляет продукт из базы данных."""
    try:
        user_id = update.message.from_user.id
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM products WHERE id = ? AND user_id = ?', (product_id, user_id))
        conn.commit()
        conn.close()

        # Удаляем запланированное уведомление
        job_id = f"notify_{user_id}_{product_id}"
        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            pass

        await update.message.reply_text("🗑️ *Продукт успешно удален!*", parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в delete_product: {e}")
        await update.message.reply_text("❌ Произошла ошибка при удалении продукта.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

# --- Просмотр просроченных продуктов ---
async def show_expired_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает просроченные продукты."""
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

# --- Очистка всех продуктов ---
async def clear_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Удаляет все продукты."""
    try:
        user_id = update.message.from_user.id
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()

        # Удаляем все запланированные уведомления пользователя
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

# --- Отмена любого действия ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущий диалог."""
    await update.message.reply_text(
        '✅ Операция отменена.',
        reply_markup=get_main_menu_keyboard()
    )
    # Очищаем user_data
    context.user_data.clear()
    return ConversationHandler.END

# --- Обработчик текстовых сообщений (для навигации по меню) ---
async def handle_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор пользователя из главного меню."""
    # Сбрасываем любое предыдущее состояние
    context.user_data.clear()

    text = update.message.text

    if text == "🏠 Главное меню":
        return await show_main_menu(update, context)
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
        help_text = (
            "ℹ️ *Как пользоваться ботом:*\n\n"
            "1️⃣ *Добавить продукт:*\n"
            "   • Нажмите '📸 Добавить по фото' и отправьте снимок.\n"
            "   • Или '✍️ Добавить вручную' и введите название и даты.\n\n"
            "2️⃣ *Мои продукты:*\n"
            "   • Просмотрите список и выберите продукт для деталей.\n\n"
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
    else:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

# --- Ежедневная проверка ---
async def check_expired_products():
    """Ежедневная проверка просроченных продуктов"""
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
                    
                    # Готовим инлайн-клавиатуру для уведомления
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    reply_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")],
                        [InlineKeyboardButton("📋 Посмотреть всё", callback_data="list_products")]
                    ])
                    
                    await bot.send_message(
                        chat_id=user_id,
                        text=f"🚨 *Просроченные продукты:*\n{product_list}\n\nРекомендуем выбросить!",
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
                    
                    # Помечаем как уведомленные
                    cursor.execute('''
                        UPDATE products SET notified = TRUE 
                        WHERE user_id = ? AND expires_at <= ?
                    ''', (user_id, today))
                    
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"Ошибка в check_expired_products: {e}")

# --- Основная функция ---
def main():
    try:
        application = Application.builder().token(TOKEN).build()

        # Создаем ConversationHandler для управления состояниями
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("start", start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_choice),
            ],
            states={
                CHOOSING_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_product_name)],
                CHOOSING_PURCHASE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_purchase_date)],
                CHOOSING_EXPIRATION_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_expiration_date)],
                BROWSE_PRODUCTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, browse_product_selection)],
                BROWSE_PRODUCT_DETAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_action)],
            },
            fallbacks=[
                MessageHandler(filters.Regex("^🏠 Главное меню$"), show_main_menu),
                MessageHandler(filters.Regex("^❌ Отмена$"), cancel)
            ],
            allow_reentry=True
        )

        # Добавляем обработчики
        application.add_handler(conv_handler)
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo)) # Обработчик фото

        # Планируем ежедневную проверку в 9:00
        scheduler.add_job(
            check_expired_products,
            'cron',
            hour=9,
            minute=0,
            id='daily_expired_check'
        )

        logger.info("🚀 Бот запущен...")
        application.run_polling()

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        scheduler.shutdown()

if __name__ == '__main__':
    main()
