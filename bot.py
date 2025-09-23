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

# --- Диалог добавления продукта ВРУЧНУЮ ---
async def start_add_manually(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления продукта вручную. Запрашивает название."""
    await update.message.reply_text(
        "Введите название продукта (или нажмите '❌ Отмена' / '🏠 Главное меню'):",
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
        "Введите дату покупки в формате ГГГГ-ММ-ДД или ГГГГ.ММ.ДД (например, 2025-09-23):",
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_PURCHASE_DATE

async def choose_purchase_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет дату покупки и запрашивает дату истечения срока."""
    user_input = update.message.text.strip()
    if user_input == "❌ Отмена" or user_input == "🏠 Главное меню":
        await cancel(update, context)
        return ConversationHandler.END

    # Пробуем распарсить дату с точками или дефисами
    try:
        # Сначала пробуем стандартный формат
        purchase_date = datetime.strptime(user_input, '%Y-%m-%d').date()
    except ValueError:
        try:
            # Если не получилось, заменяем точки на дефисы
            formatted_date = user_input.replace('.', '-')
            purchase_date = datetime.strptime(formatted_date, '%Y-%m-%d').date()
        except ValueError:
            await update.message.reply_text("Неверный формат даты. Пожалуйста, введите дату в формате ГГГГ-ММ-ДД или ГГГГ.ММ.ДД.")
            return CHOOSING_PURCHASE_DATE

    context.user_data['purchase_date'] = purchase_date.isoformat()
    await update.message.reply_text(
        "Отлично! Теперь введите дату *истечения срока* в том же формате:",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_EXPIRATION_DATE

async def choose_expiration_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет дату истечения срока, вычисляет срок годности в днях и сохраняет продукт в БД."""
    user_input = update.message.text.strip()
    if user_input == "❌ Отмена" or user_input == "🏠 Главное меню":
        await cancel(update, context)
        return ConversationHandler.END

    # Пробуем распарсить дату с точками или дефисами
    try:
        expires_at = datetime.strptime(user_input, '%Y-%m-%d').date()
    except ValueError:
        try:
            formatted_date = user_input.replace('.', '-')
            expires_at = datetime.strptime(formatted_date, '%Y-%m-%d').date()
        except ValueError:
            await update.message.reply_text("Неверный формат даты. Пожалуйста, введите дату в формате ГГГГ-ММ-ДД или ГГГГ.ММ.ДД.")
            return CHOOSING_EXPIRATION_DATE

    # Вычисляем срок годности в днях
    purchase_date = datetime.strptime(context.user_data['purchase_date'], '%Y-%m-%d').date()
    expiration_days = (expires_at - purchase_date).days

    if expiration_days < 0:
        await update.message.reply_text("Дата истечения не может быть раньше даты покупки. Начните заново.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    # Сохраняем в БД
    user_id = update.message.from_user.id
    product_name = context.user_data['product_name']
    purchase_date_str = context.user_data['purchase_date']
    expires_at_str = expires_at.isoformat()

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
        f"✅ *Готово!* Продукт *{product_name}* успешно добавлен в ваш список!\n"
        f"📅 Куплено: {purchase_date_str}\n"
        f"📆 Истекает: {expires_at_str}\n"
        f"⏳ Срок: {expiration_days} дней\n"
        "🔔 Я напомню вам за день до истечения срока!",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

    # Очищаем временные данные
    context.user_data.clear()
    return ConversationHandler.END

# --- Диалог добавления продукта ПО ФОТО ---
async def start_add_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс добавления продукта через фото."""
    await update.message.reply_text(
        "Отправьте фото продукта (или нажмите '❌ Отмена' / '🏠 Главное меню'):",
        reply_markup=get_cancel_keyboard()
    )
    context.user_data['adding_by_photo'] = True
    return ConversationHandler.END

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает фото и начинает запрос даты покупки."""
    if not context.user_data.get('adding_by_photo'):
        await update.message.reply_text("Пожалуйста, используйте кнопки меню для добавления продукта.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    try:
        user_id = update.message.from_user.id
        os.makedirs("photos", exist_ok=True)
        
        # Скачиваем фото
        photo_file = await update.message.photo[-1].get_file()
        file_id = update.message.photo[-1].file_id
        photo_hash = file_id[-10:]
        photo_path = f"photos/photo_{user_id}_{photo_hash}.jpg"
        await photo_file.download_to_drive(photo_path)

        # Распознаем продукт
        product_name = await recognize_product(photo_path)
        
        if not product_name:
            await update.message.reply_text("❌ Не удалось распознать продукт. Попробуйте снова!", reply_markup=get_main_menu_keyboard())
            context.user_data.pop('adding_by_photo', None)
            return ConversationHandler.END

        context.user_data['product_name'] = product_name
        context.user_data.pop('adding_by_photo', None)
        await update.message.reply_text(
            f"📷 Распознан продукт: *{product_name}*\n"
            "Теперь введите дату *покупки* в формате ГГГГ-ММ-ДД или ГГГГ.ММ.ДД:",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return CHOOSING_PURCHASE_DATE

    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке фото", reply_markup=get_main_menu_keyboard())
        context.user_data.pop('adding_by_photo', None)
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
                "📦 Пока нет продуктов. Добавьте первый с помощью кнопки '📸 Добавить по фото'!",
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

        text += "\nЧтобы удалить продукт или посмотреть рецепт — добавьте эту функцию позже 😉"
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в list_products_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке списка.", reply_markup=get_main_menu_keyboard())
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
            WHERE user_id = ? AND expires_at < ? AND notified = FALSE
            ORDER BY expires_at
        ''', (user_id, today))
        
        expired_products = cursor.fetchall()
        conn.close()

        if not expired_products:
            text = "✅ Поздравляю! У вас нет просроченных продуктов!"
        else:
            text = "🚨 *Внимание! Просроченные продукты:*\n\n"
            for name, expires_at in expired_products:
                text += f"• *{name}* — истекло {expires_at}\n"
            text += "\n🗑️ Настоятельно рекомендуем выбросить!"

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

        await update.message.reply_text("🗑️ Все продукты успешно удалены!", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в clear_products_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка при удалении продуктов.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

# --- Отмена любого действия ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущий диалог."""
    await update.message.reply_text(
        'Операция отменена. Вы в главном меню.',
        reply_markup=get_main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

# --- Обработчик текстовых сообщений (для навигации по меню) ---
async def handle_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор пользователя из главного меню."""
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
        await update.message.reply_text(
            "ℹ️ *Как пользоваться ботом:*\n\n"
            "1️⃣ *Добавить продукт:* Нажмите '📸 Добавить по фото' или '✍️ Добавить вручную'.\n"
            "2️⃣ *Введите даты:* Сначала дату покупки, потом дату истечения срока.\n"
            "3️⃣ *Мои продукты:* Показывает список всех добавленных продуктов.\n"
            "4️⃣ *Просроченные:* Показывает, что нужно срочно выбросить.\n"
            "5️⃣ *Очистить всё:* Удаляет все записи.\n\n"
            "Бот автоматически напомнит вам за день до истечения срока годности! 🍅",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню 👇", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает главное меню."""
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard()
    )
    return ConversationHandler.END

# --- Планировщик уведомлений ---
def schedule_notification(product_id: int, user_id: int, product_name: str, expiration_days: int):
    """Планирует уведомление за 1 день до истечения срока"""
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
    """Отправляет уведомление о скором истечении срока"""
    try:
        from telegram import Bot
        bot = Bot(token=TOKEN)
        
        await bot.send_message(
            chat_id=user_id,
            text=f"⚠️ *Напоминание!* Продукт *{product_name}* испортится завтра!\nПопробуйте использовать его сегодня 👨‍🍳",
            parse_mode='Markdown'
        )
        logger.info(f"Уведомление отправлено пользователю {user_id} для продукта {product_name}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

# --- Основная функция ---
def main():
    try:
        application = Application.builder().token(TOKEN).build()

        # Создаем ConversationHandler
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("start", show_main_menu),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_choice),
            ],
            states={
                CHOOSING_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_product_name)],
                CHOOSING_PURCHASE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_purchase_date)],
                CHOOSING_EXPIRATION_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_expiration_date)],
            },
            fallbacks=[
                MessageHandler(filters.Regex("^🏠 Главное меню$"), show_main_menu),
                MessageHandler(filters.Regex("^❌ Отмена$"), cancel)
            ],
            allow_reentry=True
        )

        # Добавляем обработчики
        application.add_handler(conv_handler)
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

        logger.info("🚀 Бот успешно запущен и готов к работе!")
        application.run_polling()

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        scheduler.shutdown()

if __name__ == '__main__':
    main()
