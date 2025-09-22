import os
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WAITING_PRODUCT, WAITING_DATE = range(2)

# База продуктов
PRODUCTS_DATA = {
    "молоко": {"shelf_life": 7, "category": "молочные"},
    "кефир": {"shelf_life": 5, "category": "молочные"},
    "сыр": {"shelf_life": 14, "category": "молочные"},
    "творог": {"shelf_life": 5, "category": "молочные"},
    "сметана": {"shelf_life": 7, "category": "молочные"},
    "йогурт": {"shelf_life": 10, "category": "молочные"},
    "яйца": {"shelf_life": 30, "category": "яйца"},
    "курица": {"shelf_life": 3, "category": "мясо"},
    "говядина": {"shelf_life": 4, "category": "мясо"},
    "рыба": {"shelf_life": 2, "category": "рыба"},
    "хлеб": {"shelf_life": 5, "category": "хлеб"},
}

class Database:
    def __init__(self):
        self.init_db()

    def init_db(self):
        """Инициализация базы данных SQLite"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    product_name TEXT,
                    purchase_date DATE,
                    expiration_date DATE,
                    notified BOOLEAN DEFAULT FALSE
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    async def add_user(self, user_id: int, username: str):
        """Добавление пользователя"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
                (user_id, username or '')
            )
            conn.commit()

    async def add_product(self, user_id: int, product_name: str, purchase_date: datetime) -> bool:
        """Добавление продукта"""
        if product_name not in PRODUCTS_DATA:
            return False

        shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
        expiration_date = purchase_date + timedelta(days=shelf_life)

        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO products (user_id, product_name, purchase_date, expiration_date)
                VALUES (?, ?, ?, ?)
            ''', (user_id, product_name, purchase_date.date(), expiration_date.date()))
            conn.commit()

        return True

    async def get_user_products(self, user_id: int):
        """Получение продуктов пользователя"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT product_name, purchase_date, expiration_date
                FROM products
                WHERE user_id = ?
                ORDER BY expiration_date
            ''', (user_id,))
            return cursor.fetchall()

    async def get_products_count(self, user_id: int) -> int:
        """Получение количества продуктов пользователя"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM products WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 0

    async def clear_user_products(self, user_id: int):
        """Очистка продуктов пользователя"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
            conn.commit()

    async def get_expiring_products(self):
        """Получение продуктов, срок которых истекает завтра"""
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT p.user_id, u.username, p.product_name, p.expiration_date
                FROM products p
                JOIN users u ON p.user_id = u.user_id
                WHERE p.expiration_date = ? AND p.notified = FALSE
            ''', (tomorrow,))
            return cursor.fetchall()

    async def mark_as_notified(self, user_id: int, product_name: str):
        """Пометить продукт как уведомленный"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE products
                SET notified = TRUE
                WHERE user_id = ? AND product_name = ?
            ''', (user_id, product_name))
            conn.commit()


class FreshlyBot:
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.application: Application = None
        self.scheduler = AsyncIOScheduler()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /start"""
        user = update.effective_user
        await self.db.add_user(user.id, user.username)

        welcome_text = f"""
👋 Привет, {user.first_name}! Я Freshly Bot — твой помощник по отслеживанию сроков годности продуктов.

📋 **Доступные команды:**
/start - показать это сообщение  
/list - список ваших продуктов
/add - добавить продукт
/clear - очистить все продукты

🎯 Нажми на кнопку ниже, чтобы начать!
        """

        # Кнопки с эмодзи
        keyboard = [
            [KeyboardButton("➕ Добавить"), KeyboardButton("📋 Список")],
            [KeyboardButton("🗑️ Очистить")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

    async def list_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать список продуктов"""
        user = update.effective_user
        products = await self.db.get_user_products(user.id)

        if not products:
            await update.message.reply_text("📭 У вас нет добавленных продуктов.")
            return

        message = "📋 **Ваши продукты:**\n\n"
        today = datetime.now().date()

        for product_name, purchase_date, expiration_date in products:
            days_left = (expiration_date - today).days

            if days_left < 0:
                status = "🔴"
                status_text = "ПРОСРОЧЕНО"
            elif days_left == 0:
                status = "🔴"
                status_text = "Истекает сегодня"
            elif days_left == 1:
                status = "🟠"
                status_text = "Истекает завтра"
            elif days_left <= 3:
                status = "🟡"
                status_text = f"Истекает через {days_left} дня"
            else:
                status = "🟢"
                status_text = f"Осталось {days_left} дней"

            message += f"{status} **{product_name}**\n"
            message += f"   📅 До {expiration_date}\n"
            message += f"   ⏰ {status_text}\n\n"

        products_count = await self.db.get_products_count(user.id)
        message += f"📊 Всего продуктов: {products_count}/5"
        await update.message.reply_text(message)

    async def clear_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Очистка всех продуктов"""
        user = update.effective_user
        await self.db.clear_user_products(user.id)
        await update.message.reply_text("✅ Все продукты удалены!")

    async def add_product_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начало добавления продукта"""
        user = update.effective_user

        # Проверка лимита
        products_count = await self.db.get_products_count(user.id)
        if products_count >= 5:
            await update.message.reply_text(
                "❌ Вы достигли лимита (5 продуктов). Используйте 🗑️ Очистить чтобы освободить место."
            )
            return ConversationHandler.END

        # Список доступных продуктов
        products_list = "\n".join([f"• {product}" for product in PRODUCTS_DATA.keys()])

        await update.message.reply_text(
            f"📦 **Доступные продукты:**\n{products_list}\n\n"
            "📝 Введите название продукта:"
        )

        return WAITING_PRODUCT

    async def handle_product_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка ввода продукта"""
        product_name = update.message.text.lower().strip()

        if product_name not in PRODUCTS_DATA:
            await update.message.reply_text("❌ Продукт не найден. Попробуйте еще раз:")
            return WAITING_PRODUCT

        context.user_data['current_product'] = product_name

        # Кнопки для выбора даты — с эмодзи!
        keyboard = [
            [KeyboardButton("📅 Сегодня"), KeyboardButton("⏪ Вчера")],
            [KeyboardButton("⏪ 2 дня назад"), KeyboardButton("❌ Отмена")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            f"📦 Продукт: **{product_name}**\n"
            "📆 Когда вы купили этот продукт?",
            reply_markup=reply_markup
        )

        return WAITING_DATE

    async def handle_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка даты покупки"""
        user_input = update.message.text
        product_name = context.user_data.get('current_product')
        user = update.effective_user

        if user_input == "❌ Отмена":
            await update.message.reply_text("❌ Операция отменена.")
            return ConversationHandler.END

        try:
            if user_input == "📅 Сегодня":
                purchase_date = datetime.now()
            elif user_input == "⏪ Вчера":
                purchase_date = datetime.now() - timedelta(days=1)
            elif user_input == "⏪ 2 дня назад":
                purchase_date = datetime.now() - timedelta(days=2)
            else:
                await update.message.reply_text("❌ Пожалуйста, выберите дату из кнопок")
                return WAITING_DATE

            # Добавляем продукт
            success = await self.db.add_product(user.id, product_name, purchase_date)

            if success:
                shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                days_left = (expiration_date.date() - datetime.now().date()).days

                await update.message.reply_text(
                    f"✅ **{product_name}** добавлен!\n"
                    f"📅 Срок годности: {expiration_date.strftime('%d.%m.%Y')}\n"
                    f"⏳ Осталось дней: {days_left}"
                )
            else:
                await update.message.reply_text("❌ Ошибка при добавлении продукта")

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await update.message.reply_text("❌ Ошибка, попробуйте снова")
            return ConversationHandler.END

        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отмена текущей операции"""
        await update.message.reply_text("❌ Операция отменена.")
        return ConversationHandler.END

    async def check_expiring_products(self):
        """Проверка продуктов с истекающим сроком"""
        try:
            expiring_products = await self.db.get_expiring_products()

            for user_id, username, product_name, expiration_date in expiring_products:
                try:
                    message = f"⚠️ Твой {product_name} испортится завтра!\n"

                    # Предлагаем рецепт
                    category = PRODUCTS_DATA[product_name]['category']
                    if category == "молочные":
                        message += "🍳 Попробуй приготовить сырники или молочный коктейль!"
                    elif category == "мясо":
                        message += "🍳 Попробуй жаркое или гуляш!"
                    elif category == "рыба":
                        message += "🍳 Попробуй запеченную рыбу с овощами!"

                    await self.application.bot.send_message(
                        chat_id=user_id,
                        text=message
                    )

                    await self.db.mark_as_notified(user_id, product_name)

                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка проверки продуктов: {e}")

    def setup_handlers(self):
        """Настройка обработчиков команд и текстовых кнопок"""

        # Обработчики для текстовых кнопок
        add_button_handler = MessageHandler(filters.Text(["➕ Добавить"]), self.add_product_start)
        list_button_handler = MessageHandler(filters.Text(["📋 Список"]), self.list_products)
        clear_button_handler = MessageHandler(filters.Text(["🗑️ Очистить"]), self.clear_products)

        # ConversationHandler для добавления продукта
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('add', self.add_product_start),
                add_button_handler  # ← Кнопка тоже запускает добавление
            ],
            states={
                WAITING_PRODUCT: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_product_input)],
                WAITING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_date)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel)]
        )

        # Регистрируем обработчики
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("list", self.list_products))
        self.application.add_handler(CommandHandler("clear", self.clear_products))

        # Добавляем обработчики кнопок
        self.application.add_handler(list_button_handler)
        self.application.add_handler(clear_button_handler)

        self.application.add_handler(conv_handler)

    def setup_scheduler(self):
        """Настройка планировщика уведомлений"""
        # Проверка каждый день в 10:00
        self.scheduler.add_job(
            self.check_expiring_products,
            trigger=CronTrigger(hour=10, minute=0),
            id='daily_check'
        )

    def run(self):
        """Запуск бота и планировщика"""
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()    # Настраиваем хендлеры
        self.setup_scheduler()   # Настраиваем планировщик
        self.scheduler.start()   # Запускаем планировщик
        logger.info("🚀 Бот запускается...")
        self.application.run_polling()  # ← Главный цикл бота


def main():
    """Основная функция"""
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

    if not BOT_TOKEN:
        logger.error("Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")
        return

    bot = FreshlyBot(BOT_TOKEN)
    bot.run()  # ← Запуск без asyncio.run()


if __name__ == '__main__':
    main()
