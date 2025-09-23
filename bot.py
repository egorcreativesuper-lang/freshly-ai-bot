import logging
import sqlite3
import os
import re
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.base import JobLookupError
import json

# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# ДИАГНОСТИЧЕСКОЕ ДОБАВЛЕНИЕ: ПРОВЕРКА ВЕРСИИ БИБЛИОТЕКИ
import telegram
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

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

# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# ВЫВОД ВЕРСИИ БИБЛИОТЕКИ ДЛЯ ДИАГНОСТИКИ
logger.info(f"✅ Запуск бота. Версия python-telegram-bot: {telegram.__version__}")
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

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
        [InlineKeyboardButton("❌ Пропустить", callback_data="ignore")]
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
            reply_markup=create_recipe_keyboard(product_name, product_id)
        )
        logger.info(f"Уведомление отправлено пользователю {user_id} для продукта {product_name}")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я — Freshly Bot 🤖\n"
        "Я помогу тебе не выбрасывать еду — и никто не узнает, что у тебя в холодильнике.\n\n"
        "📸 Отправь мне фото продукта — и я скажу, когда он испортится.\n"
        "📋 Команды:\n"
        "/list — показать все продукты\n"
        "/expired — показать просроченные продукты\n"
        "/clear — удалить все продукты"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            return

        # Сохраняем продукт в БД
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        purchase_date = datetime.now().strftime('%Y-%m-%d')
        expiration_days = random.randint(3, 14)  # Случайный срок годности
        added_at = datetime.now().isoformat()
        expires_at = (datetime.now() + timedelta(days=expiration_days)).strftime('%Y-%m-%d')

        cursor.execute('''
            INSERT INTO products (user_id, name, purchase_date, expiration_days, added_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, product_name, purchase_date, expiration_days, added_at, expires_at))
        product_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Планируем уведомление
        schedule_notification(product_id, user_id, product_name, expiration_days)

        await update.message.reply_text(
            f"✅ Распознал: *{product_name}*\n"
            f"📅 Куплено: {purchase_date}\n"
            f"⏳ Истекает: {expires_at} (через {expiration_days} дней)\n"
            "🔔 Напомню за 1 день до истечения!",
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке фото")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        if query.data == "ignore":
            await query.edit_message_text("Хорошо, напомню в следующий раз 😉")
            
        elif query.data.startswith("remind_"):
            # Переносим напоминание на завтра
            product_id = query.data.split('_')[1]
            await query.edit_message_text("🔔 Напомню через день! ⏰")
            
        elif query.data.startswith("recipe_"):
            product_name, product_id = parse_callback_data(query.data)
            
            if not product_name:
                await query.edit_message_text("❌ Ошибка обработки запроса")
                return

            # Ищем рецепт (простой поиск по имени)
            recipe = None
            for r in RECIPES:
                if r.get('name', '').lower() == product_name.lower():
                    recipe = r
                    break

            if recipe:
                # Форматируем рецепт
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
                        InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")
                    ]])
                )
            else:
                await query.edit_message_text(
                    f"📚 Рецепт для *{product_name}* не найден 😔\n\n"
                    "Попробуйте поискать в интернете или придумать свой рецепт!",
                    parse_mode='Markdown'
                )
                
        elif query.data == "back_to_main":
            await query.edit_message_text(
                "🔔 Я напомню о других продуктах вовремя! 😊",
                reply_markup=None
            )

    except Exception as e:
        logger.error(f"Ошибка в button_handler: {e}")
        try:
            await query.edit_message_text("❌ Произошла ошибка при обработке запроса")
        except:
            pass

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, purchase_date, expiration_days, expires_at, notified 
            FROM products WHERE user_id = ? ORDER BY expires_at
        ''', (user_id,))
        products = cursor.fetchall()
        conn.close()

        if not products:
            await update.message.reply_text("📦 Пока нет продуктов. Отправь фото — и я добавлю!")
            return

        text = "📋 *Твои продукты:*\n\n"
        today = datetime.now().date()
        
        for name, purchase_date, exp_days, expires_at, notified in products:
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

        await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка в list_products: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке списка продуктов")

async def show_expired(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает просроченные продукты"""
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
            await update.message.reply_text("✅ Просроченных продуктов нет!")
            return
        
        text = "🚨 *Просроченные продукты:*\n\n"
        for name, expires_at in expired_products:
            text += f"• *{name}* - истек {expires_at}\n"
        
        text += "\n❌ Рекомендуем выбросить эти продукты!"
        await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка в show_expired: {e}")
        await update.message.reply_text("❌ Произошла ошибка при загрузке просроченных продуктов")

async def clear_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        await update.message.reply_text("🗑️ Все продукты удалены!")
        
    except Exception as e:
        logger.error(f"Ошибка в clear_products: {e}")
        await update.message.reply_text("❌ Произошла ошибка при удалении продуктов")

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
                        parse_mode='Markdown'
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

def main():
    try:
        application = Application.builder().token(TOKEN).build()

        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("list", list_products))
        application.add_handler(CommandHandler("clear", clear_products))
        application.add_handler(CommandHandler("expired", show_expired))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
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
        logger.error(f"Критическая ошибка: {e}")
    finally:
        scheduler.shutdown()

if __name__ == '__main__':
    main()
