import os
import logging
import sqlite3
import asyncio
import re
import signal
import sys
from datetime import datetime, timedelta
from contextlib import contextmanager
from functools import lru_cache

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния
WAITING_PRODUCT, WAITING_DATE, WAITING_CUSTOM_DATE = range(3)

# Конфигурация
class Config:
    MAX_PRODUCTS = 5
    NOTIFICATION_HOUR = 10
    NOTIFICATION_MINUTE = 0

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
    "паста": {"shelf_life": 60, "category": "макароны"},
    "рис": {"shelf_life": 90, "category": "злаки"},
}

class Database:
    def __init__(self):
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect('products.db', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
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
            conn.commit()

    async def add_product(self, user_id: int, product_name: str, purchase_date: datetime) -> bool:
        if product_name not in PRODUCTS_DATA:
            return False

        shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
        expiration_date = purchase_date + timedelta(days=shelf_life)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO products (user_id, product_name, purchase_date, expiration_date)
                VALUES (?, ?, ?, ?)
            ''', (user_id, product_name, purchase_date.date(), expiration_date.date()))
            conn.commit()
        return True

    async def get_user_products(self, user_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT product_name, purchase_date, expiration_date
                FROM products WHERE user_id = ?
                ORDER BY expiration_date
            ''', (user_id,))
            return cursor.fetchall()

    async def get_products_count(self, user_id: int) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM products WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 0

    async def clear_user_products(self, user_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
            conn.commit()

class FreshlyBot:
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.application = None

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        welcome_text = (
            f"👋 Привет, {user.first_name}! Я Freshly Bot\n\n"
            "📌 Что я умею:\n"
            "• Добавлять продукты с датой покупки\n"
            "• Отслеживать сроки годности\n"
            "• Уведомлять об истечении срока\n\n"
            "Выберите действие:"
        )
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
            [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
            [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            await update.message.reply_text(welcome_text, reply_markup=reply_markup)
        else:
            await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📖 Помощь по командам:\n\n"
            "/start - Запустить бота\n"
            "/list - Показать список продуктов\n"
            "/help - Эта справка\n\n"
            "Просто нажимайте на кнопки для управления продуктами!"
        )
        
        keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            await update.message.reply_text(help_text, reply_markup=reply_markup)
        else:
            await update.callback_query.edit_message_text(help_text, reply_markup=reply_markup)

    async def list_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        products = await self.db.get_user_products(user.id)

        if not products:
            text = "📭 У вас нет добавленных продуктов."
        else:
            text = "📋 Ваши продукты:\n\n"
            today = datetime.now().date()
            
            for product_name, purchase_date, expiration_date in products:
                # Преобразуем строки в даты если нужно
                if isinstance(purchase_date, str):
                    purchase_date = datetime.strptime(purchase_date, "%Y-%m-%d").date()
                if isinstance(expiration_date, str):
                    expiration_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
                
                days_left = (expiration_date - today).days
                
                if days_left < 0:
                    status = "🔴 ПРОСРОЧЕНО"
                elif days_left == 0:
                    status = "🔴 Истекает сегодня"
                elif days_left <= 3:
                    status = f"🟡 Истекает через {days_left} дня"
                else:
                    status = f"🟢 Осталось {days_left} дней"
                
                text += f"**{product_name}**\n"
                text += f"📅 Куплен: {purchase_date.strftime('%d.%m.%Y')}\n"
                text += f"⏳ {status}\n\n"

        keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    async def clear_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await self.db.clear_user_products(user.id)
        
        if hasattr(update, 'callback_query') and update.callback_query:
            await update.callback_query.edit_message_text("✅ Все продукты удалены!")
        else:
            await update.message.reply_text("✅ Все продукты удалены!")
        
        await asyncio.sleep(1)
        await self.start(update, context)

    async def add_product_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        
        # Проверка лимита
        if await self.db.get_products_count(user.id) >= Config.MAX_PRODUCTS:
            await self._edit_or_reply(update, f"❌ Лимит {Config.MAX_PRODUCTS} продуктов. Очистите список.")
            await asyncio.sleep(2)
            await self.start(update, context)
            return ConversationHandler.END

        # Создаем клавиатуру с продуктами
        keyboard = []
        products = list(PRODUCTS_DATA.keys())
        
        # Группируем по 2 продукта в ряд
        for i in range(0, len(products), 2):
            row = []
            for j in range(2):
                if i + j < len(products):
                    product = products[i + j]
                    row.append(InlineKeyboardButton(product.capitalize(), callback_data=f"product_{product}"))
            if row:
                keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self._edit_or_reply(update, "📦 Выберите продукт:", reply_markup)
        return WAITING_PRODUCT

    async def handle_product_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data.startswith("product_"):
            product_name = query.data[8:]
            context.user_data['current_product'] = product_name
            
            # Клавиатура выбора даты
            keyboard = [
                [InlineKeyboardButton("📅 Сегодня", callback_data="today")],
                [InlineKeyboardButton("⏪ Вчера", callback_data="yesterday")],
                [InlineKeyboardButton("📅 Выбрать дату", callback_data="custom_date")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
            await query.edit_message_text(
                f"📦 Продукт: **{product_name}**\n"
                f"⏳ Срок годности: {shelf_life} дней\n"
                f"📆 Когда вы его купили?",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return WAITING_DATE
        
        elif query.data == "cancel":
            await query.edit_message_text("❌ Операция отменена.")
            await asyncio.sleep(1)
            await self.start(update, context)
            return ConversationHandler.END
        
        return WAITING_PRODUCT

    async def handle_date_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        product_name = context.user_data.get('current_product')
        if not product_name:
            await query.edit_message_text("❌ Ошибка: продукт не выбран.")
            return ConversationHandler.END

        if query.data == "today":
            purchase_date = datetime.now()
        elif query.data == "yesterday":
            purchase_date = datetime.now() - timedelta(days=1)
        elif query.data == "custom_date":
            await query.edit_message_text("📅 Введите дату в формате ДД.ММ.ГГГГ (например: 25.12.2024)")
            return WAITING_CUSTOM_DATE
        elif query.data == "cancel":
            await query.edit_message_text("❌ Операция отменена.")
            await asyncio.sleep(1)
            await self.start(update, context)
            return ConversationHandler.END
        else:
            return WAITING_DATE

        # Добавляем продукт
        success = await self.db.add_product(query.from_user.id, product_name, purchase_date)
        if success:
            shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
            expiration_date = purchase_date + timedelta(days=shelf_life)
            days_left = (expiration_date.date() - datetime.now().date()).days
            
            msg = (f"✅ **{product_name}** добавлен!\n"
                  f"📅 Срок годности: {expiration_date.strftime('%d.%m.%Y')}\n"
                  f"⏳ Осталось дней: {days_left}")
        else:
            msg = "❌ Ошибка при добавлении продукта."

        await query.edit_message_text(msg, parse_mode="Markdown")
        await asyncio.sleep(2)
        await self.start(update, context)
        return ConversationHandler.END

    async def handle_custom_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            date_str = update.message.text.strip()
            
            if not re.match(r'^\d{1,2}\.\d{1,2}\.\d{4}$', date_str):
                await update.message.reply_text("❌ Неверный формат! Используйте ДД.ММ.ГГГГ")
                return WAITING_CUSTOM_DATE
            
            purchase_date = datetime.strptime(date_str, "%d.%m.%Y")
            
            if purchase_date.date() > datetime.now().date():
                await update.message.reply_text("❌ Дата не может быть в будущем!")
                return WAITING_CUSTOM_DATE
            
            product_name = context.user_data.get('current_product')
            if not product_name:
                await update.message.reply_text("❌ Ошибка: продукт не выбран.")
                return ConversationHandler.END
            
            success = await self.db.add_product(update.effective_user.id, product_name, purchase_date)
            if success:
                shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                days_left = (expiration_date.date() - datetime.now().date()).days
                
                msg = (f"✅ **{product_name}** добавлен!\n"
                      f"📅 Срок годности: {expiration_date.strftime('%d.%m.%Y')}\n"
                      f"⏳ Осталось дней: {days_left}")
            else:
                msg = "❌ Ошибка при добавлении продукта."
            
            await update.message.reply_text(msg, parse_mode="Markdown")
            await asyncio.sleep(2)
            await self.start(update, context)
            
        except ValueError:
            await update.message.reply_text("❌ Неверная дата! Проверьте правильность ввода.")
            return WAITING_CUSTOM_DATE
        
        return ConversationHandler.END

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == "back_to_menu":
            await self.start(update, context)
        elif query.data == "list_products":
            await self.list_products(update, context)
        elif query.data == "help":
            await self.help_command(update, context)
        elif query.data == "clear_products":
            await self.clear_products(update, context)
        elif query.data == "add_product":
            return await self.add_product_start(update, context)
        elif query.data == "cancel":
            await query.edit_message_text("❌ Операция отменена.")
            await asyncio.sleep(1)
            await self.start(update, context)

    async def _edit_or_reply(self, update: Update, text: str, reply_markup=None):
        try:
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
            else:
                await update.message.reply_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.warning(f"Не удалось отредактировать: {e}")

    def setup_handlers(self):
        # Основные команды
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("list", self.list_products))
        self.application.add_handler(CommandHandler("clear", self.clear_products))
        
        # Обработчик кнопок главного меню
        self.application.add_handler(CallbackQueryHandler(
            self.button_handler, 
            pattern=r"^(back_to_menu|list_products|help|clear_products|add_product)$"
        ))
        
        # Conversation handler для добавления продуктов
        conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self.button_handler, pattern="^add_product$")
            ],
            states={
                WAITING_PRODUCT: [
                    CallbackQueryHandler(self.handle_product_selection, pattern=r"^(product_.+|cancel)$")
                ],
                WAITING_DATE: [
                    CallbackQueryHandler(self.handle_date_selection, pattern=r"^(today|yesterday|custom_date|cancel)$")
                ],
                WAITING_CUSTOM_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_custom_date)
                ]
            },
            fallbacks=[
                CommandHandler("start", self.start),
                CallbackQueryHandler(self.button_handler, pattern="^cancel$")
            ]
        )
        self.application.add_handler(conv_handler)

    def run(self):
        # Завершаем предыдущие процессы
        if os.name == 'posix':
            os.system('pkill -f "python.*bot" 2>/dev/null')
        
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        
        logger.info("🚀 Бот запускается...")
        
        try:
            self.application.run_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )
        except Exception as e:
            logger.error(f"Ошибка запуска: {e}")

def main():
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("❌ Установите переменную TELEGRAM_BOT_TOKEN")
        return
    
    # Завершаем возможные предыдущие процессы
    if os.name == 'posix':
        os.system('pkill -f python 2>/dev/null')
        os.system('pkill -f bot.py 2>/dev/null')
    
    bot = FreshlyBot(BOT_TOKEN)
    bot.run()

if __name__ == '__main__':
    main()
