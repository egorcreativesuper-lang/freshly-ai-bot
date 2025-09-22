import os
import logging
import sqlite3
import asyncio
import re
import signal
import sys
from datetime import datetime, timedelta
from contextlib import contextmanager

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

    async def add_user(self, user_id: int, username: str, first_name: str):
        pass  # Упрощенная версия

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
            return cursor.fetchone()[0]

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

    async def show_main_menu_with_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главное меню с фото холодильника"""
        image_url = "https://i.imgur.com/OjC80T8.jpeg"  # Ваша картинка

        text = "🎯 Выберите действие:"
        keyboard = [
            [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
            [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
            [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            if hasattr(update, 'callback_query') and update.callback_query:
                # Удаляем предыдущее сообщение
                await update.callback_query.message.delete()
                # Отправляем фото + текст
                await update.callback_query.message.reply_photo(
                    photo=image_url,
                    caption=text,
                    reply_markup=reply_markup
                )
            else:
                await update.message.reply_photo(
                    photo=image_url,
                    caption=text,
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.warning(f"Не удалось отправить фото: {e}")
            # Если фото не отправилось — отправляем текст
            await self._send_text(update, text, reply_markup)

    async def _send_text(self, update: Update, text: str, reply_markup=None):
        """Универсальная отправка текста"""
        try:
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
            else:
                await update.message.reply_text(text, reply_markup=reply_markup)
        except Exception as e:
            logger.warning(f"Не удалось отредактировать: {e}")
            try:
                if hasattr(update, 'callback_query') and update.callback_query:
                    await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
                else:
                    await update.message.reply_text(text, reply_markup=reply_markup)
            except Exception as e2:
                logger.error(f"Не удалось отправить сообщение: {e2}")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await self.show_main_menu_with_photo(update, context)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "📖 **Помощь по командам:**\n\n"
            "• /start — Главное меню\n"
            "• /list — Показать список продуктов\n"
            "• /add — Добавить продукт\n"
            "• /clear — Очистить список\n\n"
            "💡 Бот поможет отслеживать сроки годности и не выбрасывать еду зря!"
        )
        keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self._send_text(update, help_text, reply_markup)

    async def list_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        products = await self.db.get_user_products(user.id)

        if not products:
            text = "📭 У вас нет добавленных продуктов."
        else:
            text = "📋 **Ваши продукты:**\n\n"
            today = datetime.now().date()
            
            for product_name, purchase_date, expiration_date in products:
                if isinstance(purchase_date, str):
                    purchase_date = datetime.strptime(purchase_date, "%Y-%m-%d").date()
                if isinstance(expiration_date, str):
                    expiration_date = datetime.strptime(expiration_date, "%Y-%m-%d").date()
                
                days_left = (expiration_date - today).days
                if days_left < 0:
                    status = "🔴 ПРОСРОЧЕНО"
                elif days_left == 0:
                    status = "🟠 Сегодня"
                elif days_left == 1:
                    status = "🟡 Завтра"
                else:
                    status = f"🟢 {days_left} дней"
                
                text += f"**{product_name}**\n"
                text += f"   📅 Срок: {expiration_date.strftime('%d.%m.%Y')}\n"
                text += f"   ⏳ {status}\n\n"

        keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            if hasattr(update, 'callback_query') and update.callback_query:
                await update.callback_query.message.delete()
                await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка при отображении списка: {e}")
            await self._send_text(update, text, reply_markup)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if query.data == "back_to_menu":
            await self.show_main_menu_with_photo(update, context)
        elif query.data == "list_products":
            await self.list_products(update, context)
        elif query.data == "help":
            await self.help_command(update, context)
        elif query.data == "clear_products":
            user = update.effective_user
            await self.db.clear_user_products(user.id)
            await self._send_text(update, "✅ Все продукты удалены!")
            await asyncio.sleep(1)
            await self.show_main_menu_with_photo(update, context)
        elif query.data == "add_product":
            await self.add_product_start(update, context)
        elif query.data.startswith("product_"):
            product_name = query.data[8:]
            context.user_data['current_product'] = product_name

            keyboard = [
                [InlineKeyboardButton("📅 Сегодня", callback_data="today")],
                [InlineKeyboardButton("⏪ Вчера", callback_data="yesterday")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"📦 Вы выбрали: **{product_name}**\n📆 Когда купили?",
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            return WAITING_DATE
        elif query.data in ["today", "yesterday"]:
            product_name = context.user_data.get('current_product')
            if not product_name:
                await query.edit_message_text("❌ Ошибка: продукт не выбран.")
                return
            
            if query.data == "today":
                purchase_date = datetime.now()
            else:
                purchase_date = datetime.now() - timedelta(days=1)

            success = await self.db.add_product(query.from_user.id, product_name, purchase_date)
            if success:
                shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                days_left = (expiration_date.date() - datetime.now().date()).days
                
                msg = f"✅ **{product_name}** добавлен!\n📅 Срок: {expiration_date.strftime('%d.%m.%Y')}\n⏳ Осталось: {days_left} дней"
            else:
                msg = "❌ Ошибка при добавлении."

            await query.edit_message_text(msg, parse_mode="Markdown")
            await asyncio.sleep(2)
            await self.show_main_menu_with_photo(update, context)
            return ConversationHandler.END
        elif query.data == "cancel":
            await query.edit_message_text("❌ Операция отменена.")
            await asyncio.sleep(1)
            await self.show_main_menu_with_photo(update, context)
            return ConversationHandler.END

    async def add_product_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = []
        products = list(PRODUCTS_DATA.keys())
        
        for i in range(0, len(products), 2):
            row = []
            for j in range(2):
                if i + j < len(products):
                    product = products[i + j]
                    row.append(InlineKeyboardButton(product.capitalize(), callback_data=f"product_{product}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "📦 Выберите продукт:"
        await self._send_text(update, text, reply_markup)
        
        return WAITING_PRODUCT

    def setup_handlers(self):
        # Основные команды
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("list", self.list_products))
        
        # Обработчик кнопок
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Conversation handler для добавления продуктов
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.button_handler, pattern="^add_product$")],
            states={
                WAITING_PRODUCT: [CallbackQueryHandler(self.button_handler, pattern=r"^product_.+$")],
                WAITING_DATE: [CallbackQueryHandler(self.button_handler, pattern=r"^(today|yesterday|cancel)$")]
            },
            fallbacks=[CallbackQueryHandler(self.button_handler, pattern="^cancel$")],
            per_message=False,
            allow_reentry=True
        )
        self.application.add_handler(conv_handler)

    def run(self):
        """Упрощенный запуск бота"""
        if os.name == 'posix':
            os.system('pkill -f "python.*bot.py" 2>/dev/null')
        
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        
        logger.info("🚀 Бот запускается...")
        
        try:
            self.application.run_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}")


def main():
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("❌ Установите TELEGRAM_BOT_TOKEN")
        return
    
    bot = FreshlyBot(BOT_TOKEN)
    bot.run()


if __name__ == '__main__':
    main()
