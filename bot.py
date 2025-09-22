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

🎯 Нажми на кнопку ниже, чтобы начать!
        """

        # Инлайн-кнопки
        keyboard = [
            [InlineKeyboardButton("➕ Добавить", callback_data="add_product")],
            [InlineKeyboardButton("📋 Список", callback_data="list_products")],
            [InlineKeyboardButton("🗑️ Очистить", callback_data="clear_products")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

    async def handle_menu_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка нажатий инлайн-кнопок главного меню"""
        query = update.callback_query
        await query.answer()

        if query.data == "add_product":
            user = query.from_user
            products_count = await self.db.get_products_count(user.id)
            if products_count >= 5:
                await query.edit_message_text(
                    "❌ Вы достигли лимита (5 продуктов). Используйте 🗑️ Очистить чтобы освободить место."
                )
                return

            products_list = "\n".join([f"• {product}" for product in PRODUCTS_DATA.keys()])
            await query.edit_message_text(
                f"📦 **Доступные продукты:**\n{products_list}\n\n"
                "📝 Введите название продукта:"
            )
            return WAITING_PRODUCT

        elif query.data == "list_products":
            products = await self.db.get_user_products(query.from_user.id)
            if not products:
                await query.edit_message_text("📭 У вас нет добавленных продуктов.")
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
                message += f"{status} **{product_name}**\n📅 До {expiration_date}\n⏰ {status_text}\n\n"
            products_count = await self.db.get_products_count(query.from_user.id)
            message += f"📊 Всего продуктов: {products_count}/5"
            await query.edit_message_text(message)

        elif query.data == "clear_products":
            await self.db.clear_user_products(query.from_user.id)
            await query.edit_message_text("✅ Все продукты удалены!")

    async def list_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать список продуктов (через команду /list)"""
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
        """Очистка всех продуктов (через команду /clear)"""
        user = update.effective_user
        await self.db.clear_user_products(user.id)
        await update.message.reply_text("✅ Все продукты удалены!")

    async def add_product_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начало добавления продукта (через команду /add)"""
        user = update.effective_user
        products_count = await self.db.get_products_count(user.id)
        if products_count >= 5:
            await update.message.reply_text(
                "❌ Вы достигли лимита (5 продуктов). Используйте 🗑️ Очистить чтобы освободить место."
            )
            return ConversationHandler.END

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

        # Кнопки для выбора даты — инлайн
        keyboard = [
            [InlineKeyboardButton("📅 Сегодня", callback_data="today")],
            [InlineKeyboardButton("⏪ Вчера", callback_data="yesterday")],
            [InlineKeyboardButton("⏪ 2 дня назад", callback_data="two_days_ago")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_product"),
             InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"📦 Продукт: **{product_name}**\n"
            "📆 Когда вы купили этот продукт?",
            reply_markup=reply_markup
        )

        return WAITING_DATE

    async def handle_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка даты покупки"""
        query = update.callback_query
        await query.answer()

        callback_data = query.data

        if
