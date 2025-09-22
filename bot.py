import os
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
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

🎯 Выбери действие ниже:
        """

        keyboard = [
            [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
            [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
            [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

    async def list_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать список продуктов"""
        user = update.effective_user
        products = await self.db.get_user_products(user.id)

        if not products:
            text = "📭 У вас нет добавленных продуктов."
            if update.callback_query:
                await update.callback_query.edit_message_text(text)
            else:
                await update.message.reply_text(text)
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
            message += f"   📅 Куплен: {purchase_date}\n"
            message += f"   ⏳ Срок до: {expiration_date} ({status_text})\n\n"

        products_count = await self.db.get_products_count(user.id)
        message += f"📊 Всего продуктов: {products_count}/5"

        if update.callback_query:
            await update.callback_query.edit_message_text(message)
        else:
            await update.message.reply_text(message)

    async def clear_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Очистка всех продуктов"""
        user = update.effective_user
        await self.db.clear_user_products(user.id)

        text = "✅ Все продукты удалены!"
        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)

        # Возвращаем главное меню
        await self.show_main_menu(update, context)

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать главное меню с кнопками"""
        keyboard = [
            [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
            [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
            [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            await update.callback_query.message.reply_text("🎯 Выберите действие:", reply_markup=reply_markup)
        else:
            await update.message.reply_text("🎯 Выберите действие:", reply_markup=reply_markup)

    async def add_product_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начало добавления продукта"""
        user = update.effective_user
        products_count = await self.db.get_products_count(user.id)

        if products_count >= 5:
            text = "❌ Вы достигли лимита (5 продуктов). Используйте 🗑️ Очистить чтобы освободить место."
            if update.callback_query:
                await update.callback_query.edit_message_text(text)
            else:
                await update.message.reply_text(text)
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        products_list = "\n".join([f"• {product}" for product in PRODUCTS_DATA.keys()])
        text = f"📦 **Доступные продукты:**\n{products_list}\n\n📝 Введите название продукта:"

        if update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)

        return WAITING_PRODUCT

    async def handle_product_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка ввода продукта"""
        product_name = update.message.text.lower().strip()

        if product_name not in PRODUCTS_DATA:
            await update.message.reply_text("❌ Продукт не найден. Попробуйте ещё раз:")
            return WAITING_PRODUCT

        context.user_data['current_product'] = product_name

        keyboard = [
            [InlineKeyboardButton("📅 Сегодня", callback_data="today")],
            [InlineKeyboardButton("⏪ Вчера", callback_data="yesterday")],
            [InlineKeyboardButton("⏪ 2 дня назад", callback_data="two_days_ago")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_product"),
             InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"📦 Продукт: **{product_name}**\n📆 Когда вы его купили?",
            reply_markup=reply_markup
        )

        return WAITING_DATE

    async def handle_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка даты покупки через кнопки"""
        query = update.callback_query
        await query.answer()

        callback_data = query.data

        if callback_data == "cancel":
            await query.edit_message_text("❌ Операция отменена.")
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        if callback_data == "back_to_product":
            # Возвращаемся к вводу продукта
            products_list = "\n".join([f"• {product}" for product in PRODUCTS_DATA.keys()])
            await query.edit_message_text(
                f"📦 **Доступные продукты:**\n{products_list}\n\n📝 Введите название продукта:"
            )
            return WAITING_PRODUCT

        try:
            if callback_data == "today":
                purchase_date = datetime.now()
            elif callback_data == "yesterday":
                purchase_date = datetime.now() - timedelta(days=1)
            elif callback_data == "two_days_ago":
                purchase_date = datetime.now() - timedelta(days=2)
            else:
                await query.edit_message_text("❌ Пожалуйста, выберите дату из кнопок.")
                return WAITING_DATE

            product_name = context.user_data['current_product']
            success = await self.db.add_product(query.from_user.id, product_name, purchase_date)

            if success:
                shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                days_left = (expiration_date.date() - datetime.now().date()).days

                message = f"✅ **{product_name}** добавлен!\n"
                message += f"📅 Срок годности: {expiration_date.strftime('%d.%m.%Y')}\n"
                message += f"⏳ Осталось дней: {days_left}"

                await query.edit_message_text(message)

                # Возвращаемся в главное меню
                await self.show_main_menu(update, context)

                return ConversationHandler.END
            else:
                await query.edit_message_text("❌ Ошибка при добавлении продукта.")

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await query.edit_message_text("❌ Произошла ошибка. Попробуйте снова.")
            return WAITING_DATE

        return ConversationHandler.END

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработчик кнопок из меню /start и других мест"""
        query = update.callback_query
        await query.answer()

        if query.data == "add_product":
            return await self.add_product_start(update, context)
        elif query.data == "list_products":
            await self.list_products(update, context)
            return ConversationHandler.END
        elif query.data == "clear_products":
            await self.clear_products(update, context)
            return ConversationHandler.END
        else:
            await query.edit_message_text("Неизвестная команда.")
            return ConversationHandler.END

    async def stay_in_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Остаёмся в состоянии WAITING_DATE, если пользователь ввёл текст вместо нажатия кнопки"""
        await update.message.reply_text("❗ Пожалуйста, используйте кнопки для выбора даты.")
        return WAITING_DATE

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Отмена текущей операции — возврат в главное меню"""
        await update.message.reply_text("❌ Операция отменена.")
        await self.show_main_menu(update, context)
        return ConversationHandler.END

    async def check_expiring_products(self):
        """Проверка продуктов с истекающим сроком — отправка уведомлений"""
        try:
            expiring_products = await self.db.get_expiring_products()

            for user_id, username, product_name, expiration_date in expiring_products:
                try:
                    message = f"⚠️ Твой **{product_name}** испортится завтра!\n"

                    category = PRODUCTS_DATA[product_name]['category']
                    if category == "молочные":
                        message += "🍳 Попробуй приготовить сырники или молочный коктейль!"
                    elif category == "мясо":
                        message += "🍖 Попробуй жаркое или гуляш!"
                    elif category == "рыба":
                        message += "🐟 Попробуй запечённую рыбу с овощами!"
                    elif category == "хлеб":
                        message += "🍞 Сделай гренки или сухарики!"
                    else:
                        message += "🥡 Используй его сегодня!"

                    await self.application.bot.send_message(chat_id=user_id, text=message)
                    await self.db.mark_as_notified(user_id, product_name)

                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка проверки продуктов: {e}")

    def setup_handlers(self):
        """Настройка обработчиков команд и кнопок"""

        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('add', self.add_product_start),
                CommandHandler('list', self.list_products),
                CommandHandler('clear', self.clear_products),
                CallbackQueryHandler(self.button_handler, pattern=r"^(add_product|list_products|clear_products)$")
            ],
            states={
                WAITING_PRODUCT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_product_input)
                ],
                WAITING_DATE: [
                    CallbackQueryHandler(self.handle_date, pattern=r"^(today|yesterday|two_days_ago|back_to_product|cancel)$")
                ]
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.stay_in_state)
            ]
        )

        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(conv_handler)

    def setup_scheduler(self):
        """Настройка планировщика уведомлений"""
        self.scheduler.add_job(
            self.check_expiring_products,
            trigger=CronTrigger(hour=10, minute=0),  # Каждый день в 10:00
            id='daily_check'
        )

    def run(self):
        """Запуск бота и планировщика"""
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        self.setup_scheduler()
        self.scheduler.start()
        logger.info("🚀 Бот запускается...")
        self.application.run_polling()


def main():
    """Основная функция"""
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')

    if not BOT_TOKEN:
        logger.error("Токен бота не найден! Установите переменную TELEGRAM_BOT_TOKEN")
        return

    bot = FreshlyBot(BOT_TOKEN)
    bot.run()


if __name__ == '__main__':
    main()
