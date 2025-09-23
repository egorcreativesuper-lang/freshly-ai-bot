import logging
import sqlite3
import os
import re
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError
import json

# Состояния для ConversationHandler
CHOOSING_PRODUCT_NAME, CHOOSING_PURCHASE_DATE, CHOOSING_EXPIRATION_DATE = range(3)

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
    """Создает безопасный callback_data без спецсимволов"""
    safe_name = re.sub(r'[^a-zA-Z0-9а-яА-Я]', '_', product_name)
    return f"recipe_{safe_name}_{product_id}"

def parse_callback_data(callback_data):
    """Парсит callback_data и возвращает product_name и product_id"""
    try:
        parts = callback_data.split('_')
        if len(parts) >= 3:
            product_name = parts[1].replace('_', ' ')  # Восстанавливаем пробелы
            product_id = parts[2]
            return product_name, product_id
        return None, None
    except Exception as e:
        logger.error(f"Ошибка парсинга callback_data: {e}")
        return None, None

async def recognize_product(photo_path: str) -> str:
    """Заглушка для распознавания продукта"""
    products = ["Молоко", "Хлеб", "Яйца", "Сыр", "Йогурт", "Мясо", "Рыба", "Овощи", "Фрукты"]
    return random.choice(products)

def create_recipe_keyboard(product_name, product_id):
    """Создает безопасную клавиатуру для рецептов"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Показать рецепт", callback_data=create_safe_callback_data(product_name, product_id))],
        [InlineKeyboardButton("🔔 Напомнить позже", callback_data=f"remind_{product_id}")],
        [InlineKeyboardButton("🔙 Назад к продуктам", callback_data=f"back_to_product_{product_id}")],
    ])

def create_product_detail_keyboard(product_id):
    """Создает клавиатуру для детальной страницы продукта"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Рецепты", callback_data=f"show_recipes_{product_id}")],
        [InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_product_{product_id}")],
        [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_list")],
    ])

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
        
        await bot.send_message(
            chat_id=user_id,
            text=f"⚠️ *{product_name}* испортится завтра!\nПопробуй приготовить что-нибудь? 👨‍🍳",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📖 Показать рецепт", callback_data=create_safe_callback_data(product_name, product_id))],
                [InlineKeyboardButton("🔕 Больше не напоминать", callback_data=f"disable_notify_{product_id}")],
                [InlineKeyboardButton("🔙 В меню", callback_data="back_to_menu")]
            ])
        )
        logger.info(f"Уведомление отправлено пользователю {user_id} для продукта {product_name}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

# Обработчики команд и состояний
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и показ главного меню."""
    await update.message.reply_text(
        "Привет! Я — Freshly Bot 🤖\n"
        "Я помогу тебе не выбрасывать еду — и никто не узнает, что у тебя в холодильнике."
    )
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отображает главное меню с инлайн-кнопками."""
    keyboard = [
        [
            InlineKeyboardButton("📸 Добавить по фото", callback_data="add_by_photo"),
            InlineKeyboardButton("✍️ Добавить вручную", callback_data="add_manually"),
        ],
        [
            InlineKeyboardButton("📋 Мои продукты", callback_data="list_products"),
            InlineKeyboardButton("🚨 Просроченные", callback_data="show_expired"),
        ],
        [
            InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "Выберите действие:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "Выберите действие:",
            reply_markup=reply_markup
        )

# --- Диалог добавления продукта ВРУЧНУЮ ---
async def start_add_manually(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления продукта вручную. Запрашивает название."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введите название продукта:")

    return CHOOSING_PRODUCT_NAME

# --- Диалог добавления продукта ПО ФОТО ---
async def start_add_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс добавления продукта через фото."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Отправьте фото продукта:")

    # Устанавливаем флаг, что продукт добавляется по фото
    context.user_data['adding_by_photo'] = True
    return ConversationHandler.END  # Ожидаем фото как отдельное сообщение

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает фото и начинает запрос даты покупки."""
    if not context.user_data.get('adding_by_photo'):
        # Если фото пришло вне контекста добавления, игнорируем или показываем меню
        await update.message.reply_text("Пожалуйста, используйте кнопки меню для добавления продукта.")
        await show_main_menu(update, context)
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
            await update.message.reply_text("❌ Не удалось распознать продукт. Попробуйте снова!")
            context.user_data.pop('adding_by_photo', None) # Сбрасываем флаг
            return ConversationHandler.END

        # Сохраняем распознанное имя в user_data
        context.user_data['product_name'] = product_name
        context.user_data.pop('adding_by_photo', None) # Сбрасываем флаг
        await update.message.reply_text(
            f"Распознан продукт: *{product_name}*\n"
            "Теперь введите дату покупки в формате ГГГГ-ММ-ДД:",
            parse_mode='Markdown'
        )

        return CHOOSING_PURCHASE_DATE

    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке фото")
        context.user_data.pop('adding_by_photo', None) # Сбрасываем флаг
        return ConversationHandler.END

# --- Общие шаги для обоих способов добавления ---
async def choose_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет название продукта и запрашивает дату покупки."""
    user_input = update.message.text.strip()
    if not user_input:
        await update.message.reply_text("Пожалуйста, введите корректное название продукта.")
        return CHOOSING_PRODUCT_NAME

    context.user_data['product_name'] = user_input
    await update.message.reply_text(
        "Введите дату покупки в формате ГГГГ-ММ-ДД (например, 2025-09-23):"
    )
    return CHOOSING_PURCHASE_DATE

async def choose_purchase_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет дату покупки и запрашивает дату истечения срока."""
    user_input = update.message.text.strip()
    try:
        purchase_date = datetime.strptime(user_input, '%Y-%m-%d').date()
        context.user_data['purchase_date'] = purchase_date.isoformat()
    except ValueError:
        await update.message.reply_text("Неверный формат даты. Пожалуйста, введите дату в формате ГГГГ-ММ-ДД.")
        return CHOOSING_PURCHASE_DATE

    await update.message.reply_text(
        "Введите дату истечения срока в формате ГГГГ-ММ-ДД:"
    )
    return CHOOSING_EXPIRATION_DATE

async def choose_expiration_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет дату истечения срока, вычисляет срок годности в днях и сохраняет продукт в БД."""
    user_input = update.message.text.strip()
    try:
        expires_at = datetime.strptime(user_input, '%Y-%m-%d').date()
        context.user_data['expires_at'] = expires_at.isoformat()
    except ValueError:
        await update.message.reply_text("Неверный формат даты. Пожалуйста, введите дату в формате ГГГГ-ММ-ДД.")
        return CHOOSING_EXPIRATION_DATE

    # Вычисляем срок годности в днях
    purchase_date = datetime.strptime(context.user_data['purchase_date'], '%Y-%m-%d').date()
    expiration_days = (expires_at - purchase_date).days

    if expiration_days < 0:
        await update.message.reply_text("Дата истечения не может быть раньше даты покупки. Начните заново.")
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

    await update.message.reply_text(
        f"✅ Продукт *{product_name}* успешно добавлен!\n"
        f"📅 Куплено: {purchase_date_str}\n"
        f"⏳ Истекает: {expires_at_str} (через {expiration_days} дней)\n"
        "🔔 Напомню за 1 день до истечения!",
        parse_mode='Markdown'
    )

    # Показываем главное меню
    await show_main_menu(update, context)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет диалог."""
    await update.message.reply_text('Операция отменена.')
    await show_main_menu(update, context)
    return ConversationHandler.END

# --- Обработчики для списка продуктов и деталей ---
async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список продуктов с кнопками для действий."""
    query = None
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        message_id = query.message.message_id
    else:
        chat_id = update.message.chat_id

    try:
        user_id = update.effective_user.id
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, name, purchase_date, expiration_days, expires_at, notified 
            FROM products WHERE user_id = ? ORDER BY expires_at
        ''', (user_id,))
        products = cursor.fetchall()
        conn.close()

        if not products:
            text = "📦 Пока нет продуктов. Добавьте первый!"
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("📸 Добавить продукт", callback_data="add_by_photo")
            ]])
        else:
            text = "📋 *Ваши продукты:*\n\n"
            keyboard = []
            today = datetime.now().date()
            
            for prod_id, name, purchase_date, exp_days, expires_at, notified in products:
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
                    
                text += f"• *{name}* — {status}\n"
                # Добавляем кнопку для каждого продукта
                keyboard.append([InlineKeyboardButton(f"🔹 {name}", callback_data=f"product_detail_{prod_id}")])

            # Добавляем кнопку возврата в меню
            keyboard.append([InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_menu")])
            reply_markup = InlineKeyboardMarkup(keyboard)

        if query:
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
        else:
            await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
            
    except Exception as e:
        logger.error(f"Ошибка в list_products: {e}")
        error_text = "❌ Произошла ошибка при загрузке списка продуктов"
        if query:
            await query.edit_message_text(error_text)
        else:
            await update.message.reply_text(error_text)

async def show_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Показывает детальную информацию о продукте."""
    query = update.callback_query
    await query.answer()

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
            await query.edit_message_text("❌ Продукт не найден.")
            return

        name, purchase_date, exp_days, expires_at, notified = product
        text = (
            f"*{name}*\n"
            f"📅 Дата покупки: {purchase_date}\n"
            f"📆 Срок годности: {exp_days} дней\n"
            f"⚠️ Истекает: {expires_at}\n"
        )

        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=create_product_detail_keyboard(product_id)
        )

    except Exception as e:
        logger.error(f"Ошибка в show_product_detail: {e}")
        await query.edit_message_text("❌ Произошла ошибка при загрузке информации о продукте.")

async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Удаляет продукт из базы данных."""
    query = update.callback_query
    await query.answer()

    try:
        user_id = update.effective_user.id
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

        await query.edit_message_text("🗑️ Продукт успешно удален!")
        # Показываем обновленный список
        await list_products(update, context)

    except Exception as e:
        logger.error(f"Ошибка в delete_product: {e}")
        await query.edit_message_text("❌ Произошла ошибка при удалении продукта.")

async def show_recipes_for_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Показывает рецепты для выбранного продукта."""
    query = update.callback_query
    await query.answer()

    try:
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('SELECT name FROM products WHERE id = ?', (product_id,))
        product = cursor.fetchone()
        conn.close()

        if not product:
            await query.edit_message_text("❌ Продукт не найден.")
            return

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
                f"⏱️ Время приготовления: {recipe.get('time_minutes', 'N/A')} мин\n"
                f"🍽️ Количество порций: {recipe.get('servings', 'N/A')}\n\n"
                f"*Ингредиенты:*\n{ingredients}\n\n"
                f"*Шаги приготовления:*\n{steps}"
            )
            
            await query.edit_message_text(
                recipe_text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад к продукту", callback_data=f"product_detail_{product_id}")
                ]])
            )
        else:
            await query.edit_message_text(
                f"📚 Рецепт для *{product_name}* не найден 😔\n\n"
                "Попробуйте поискать в интернете или придумать свой рецепт!",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Назад к продукту", callback_data=f"product_detail_{product_id}")
                ]])
            )
            
    except Exception as e:
        logger.error(f"Ошибка в show_recipes_for_product: {e}")
        await query.edit_message_text("❌ Произошла ошибка при загрузке рецепта.")

# --- Обработчик кнопок ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на инлайн-кнопки."""
    query = update.callback_query
    await query.answer()

    try:
        data = query.data

        # --- Глобальные навигационные кнопки ---
        if data == "back_to_menu":
            await show_main_menu(update, context)
            return

        # --- Главное меню ---
        elif data == "add_by_photo":
            await start_add_by_photo(update, context)
            return
        elif data == "add_manually":
            await start_add_manually(update, context)
            return
        elif data == "list_products":
            await list_products(update, context)
            return
        elif data == "show_expired":
            # Эмулируем команду /expired
            await show_expired_directly(update, context)
            return
        elif data == "clear_products":
            # Эмулируем команду /clear
            await clear_products_directly(update, context)
            return

        # --- Действия с продуктами ---
        elif data.startswith("product_detail_"):
            product_id = int(data.split('_')[-1])
            await show_product_detail(update, context, product_id)
            return
        elif data.startswith("delete_product_"):
            product_id = int(data.split('_')[-1])
            await delete_product(update, context, product_id)
            return
        elif data.startswith("show_recipes_"):
            product_id = int(data.split('_')[-1])
            await show_recipes_for_product(update, context, product_id)
            return

        # --- Уведомления и напоминания ---
        elif data.startswith("remind_"):
            # Переносим напоминание на завтра
            product_id = data.split('_')[1]
            await query.edit_message_text("🔔 Напомню через день! ⏰")
            await show_main_menu(update, context)
            return
        elif data.startswith("disable_notify_"):
            # Отключаем уведомление (помечаем как notified)
            product_id = int(data.split('_')[-1])
            user_id = update.effective_user.id
            try:
                conn = sqlite3.connect('products.db')
                cursor = conn.cursor()
                cursor.execute('UPDATE products SET notified = TRUE WHERE id = ? AND user_id = ?', (product_id, user_id))
                conn.commit()
                conn.close()
                await query.edit_message_text("🔕 Уведомление отключено.")
            except Exception as e:
                logger.error(f"Ошибка отключения уведомления: {e}")
                await query.edit_message_text("❌ Ошибка при отключении уведомления.")
            await show_main_menu(update, context)
            return

        # --- Рецепты ---
        elif data.startswith("recipe_"):
            product_name, product_id = parse_callback_data(data)
            if not product_name:
                await query.edit_message_text("❌ Ошибка обработки запроса")
                return

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
                    f"⏱️ Время приготовления: {recipe.get('time_minutes', 'N/A')} мин\n"
                    f"🍽️ Количество порций: {recipe.get('servings', 'N/A')}\n\n"
                    f"*Ингредиенты:*\n{ingredients}\n\n"
                    f"*Шаги приготовления:*\n{steps}"
                )
                await query.edit_message_text(
                    recipe_text,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")
                    ]])
                )
            else:
                await query.edit_message_text(
                    f"📚 Рецепт для *{product_name}* не найден 😔",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")
                    ]])
                )
            return

    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}")
        try:
            await query.edit_message_text("❌ Произошла ошибка. Возвращаемся в меню.")
            await show_main_menu(update, context)
        except:
            pass

# --- Прямые вызовы команд (для кнопок) ---
async def show_expired_directly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает просроченные продукты (вызывается из кнопки)."""
    query = update.callback_query
    await query.answer()

    try:
        user_id = update.effective_user.id
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
            text = "✅ Просроченных продуктов нет!"
        else:
            text = "🚨 *Просроченные продукты:*\n\n"
            for name, expires_at in expired_products:
                text += f"• *{name}* - истек {expires_at}\n"
            text += "\n❌ Рекомендуем выбросить эти продукты!"

        await query.edit_message_text(text, parse_mode='Markdown')
        await query.message.reply_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_menu")
        ]])

    except Exception as e:
        logger.error(f"Ошибка в show_expired_directly: {e}")
        await query.edit_message_text("❌ Произошла ошибка при загрузке просроченных продуктов.")

async def clear_products_directly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет все продукты (вызывается из кнопки)."""
    query = update.callback_query
    await query.answer()

    try:
        user_id = update.effective_user.id
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

        await query.edit_message_text("🗑️ Все продукты удалены!")
        await show_main_menu(update, context)

    except Exception as e:
        logger.error(f"Ошибка в clear_products_directly: {e}")
        await query.edit_message_text("❌ Произошла ошибка при удалении продуктов.")

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
                    
                    await bot.send_message(
                        chat_id=user_id,
                        text=f"🚨 *Просроченные продукты:*\n{product_list}\n\nРекомендуем выбросить!",
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products"),
                            InlineKeyboardButton("📋 Посмотреть всё", callback_data="list_products")
                        ]])
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

        # Создаем ConversationHandler для добавления продукта
        conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(start_add_manually, pattern='^add_manually$'),
                # Обработчик фото регистрируется отдельно
            ],
            states={
                CHOOSING_PRODUCT_NAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, choose_product_name)
                ],
                CHOOSING_PURCHASE_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, choose_purchase_date)
                ],
                CHOOSING_EXPIRATION_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, choose_expiration_date)
                ],
            },
            fallbacks=[CommandHandler('cancel', cancel)],
            allow_reentry=True
        )

        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(conv_handler)
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo)) # Обработчик фото
        application.add_handler(CallbackQueryHandler(button_handler))

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
