import os
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния
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
    "паста": {"shelf_life": 60, "category": "макароны"},
    "рис": {"shelf_life": 90, "category": "злаки"},
    "кофе": {"shelf_life": 180, "category": "напитки"},
    "чай": {"shelf_life": 365, "category": "напитки"},
    "бутылка воды": {"shelf_life": 365, "category": "напитки"},
    "шоколад": {"shelf_life": 90, "category": "сладости"},
    "печенье": {"shelf_life": 30, "category": "сладости"},
    "сахар": {"shelf_life": 365, "category": "сладости"},
    "масло": {"shelf_life": 120, "category": "жиры"},
    "масло растительное": {"shelf_life": 180, "category": "жиры"},
    "консервы": {"shelf_life": 365, "category": "консервы"},
    "овощи": {"shelf_life": 7, "category": "овощи"},
    "фрукты": {"shelf_life": 5, "category": "фрукты"},
}


class Database:
    def __init__(self):
        self.init_db()

    def init_db(self):
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
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)',
                (user_id, username or '')
            )
            conn.commit()

    async def add_product(self, user_id: int, product_name: str, purchase_date: datetime) -> bool:
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
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM products WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 0

    async def clear_user_products(self, user_id: int):
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
            conn.commit()

    async def get_expiring_products(self):
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
        """Обработчик команды /start — красивое приветствие с анимацией"""
        user = update.effective_user
        await self.db.add_user(user.id, user.username)

        animation_url = "https://i.imgur.com/6JQV9Xj.gif"
        fallback_image_url = "https://i.imgur.com/8Y0fKuB.png"

        welcome_text = (
            f"👋 Привет, {user.first_name}! Я *Freshly Bot* — твой личный помощник по срокам годности продуктов.\n\n"
            "📌 **Что я умею:**\n"
            "• 📋 Добавить продукт с датой покупки\n"
            "• ⏳ Автоматически отслеживать сроки годности\n"
            "• 🛎️ Уведомлять за день до истечения срока\n"
            "• 🍽️ Подсказывать рецепты для просроченных продуктов\n"
            "• 🗑️ Очищать список при необходимости\n\n"
            "✅ Максимум 5 продуктов одновременно — чтобы не перегружать память!\n\n"
            "🎯 Начни с кнопки *➕ Добавить продукт* — выбери продукт и дату покупки, и я сделаю всё остальное!\n\n"
            "💡 *Полезные советы:* \n"
            "• Используй «Вчера» или «Сегодня» — удобно!\n"
            "• Если продукт просрочился — я подскажу, что можно сделать!"
        )

        try:
            await update.message.reply_animation(
                animation=animation_url,
                caption=welcome_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
                    [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
                    [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")]
                ])
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить анимацию: {e}")
            try:
                await update.message.reply_photo(
                    photo=fallback_image_url,
                    caption=welcome_text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
                        [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
                        [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")]
                    ])
                )
            except Exception as e2:
                logger.error(f"Не удалось отправить фото: {e2}")
                await update.message.reply_text(
                    welcome_text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
                        [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
                        [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")]
                    ])
                )

    async def show_main_menu_with_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Главное меню с фото холодильника"""
        image_url = "https://i.imgur.com/8Y0fKuB.png"
        text = "🎯 Выберите действие:"

        keyboard = [
            [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
            [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
            [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            if update.callback_query:
                await update.callback_query.message.delete()
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
            await self._edit_or_reply(update, text, reply_markup)

    async def list_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Показать список продуктов с кнопкой 'Назад в меню'"""
        user = update.effective_user
        products = await self.db.get_user_products(user.id)

        if not products:
            text = "📭 У вас нет добавленных продуктов."
        else:
            text = "📋 **Ваши продукты:**\n\n"
            today = datetime.now().date()
            for product_name, purchase_date, expiration_date in products:
                days_left = (expiration_date - today).days
                if days_left < 0:
                    status, status_text = "🔴", "ПРОСРОЧЕНО"
                elif days_left == 0:
                    status, status_text = "🔴", "Истекает сегодня"
                elif days_left == 1:
                    status, status_text = "🟠", "Истекает завтра"
                elif days_left <= 3:
                    status, status_text = "🟡", f"Истекает через {days_left} дня"
                else:
                    status, status_text = "🟢", f"Осталось {days_left} дней"
                text += f"{status} **{product_name}**\n   📅 Куплен: {purchase_date}\n   ⏳ Срок до: {expiration_date} ({status_text})\n\n"
            products_count = await self.db.get_products_count(user.id)
            text += f"📊 Всего продуктов: {products_count}/5"

        keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
            except Exception:
                await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    async def clear_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        await self.db.clear_user_products(user.id)

        if update.callback_query:
            await update.callback_query.edit_message_text("✅ Все продукты удалены!")
        else:
            await update.message.reply_text("✅ Все продукты удалены!")

        await asyncio.sleep(1)
        await self.show_main_menu_with_photo(update, context)

    async def add_product_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        user = update.effective_user
        if await self.db.get_products_count(user.id) >= 5:
            await self._edit_or_reply(update, "❌ Лимит 5 продуктов. Очистите список.")
            await asyncio.sleep(1)
            await self.show_main_menu_with_photo(update, context)
            return ConversationHandler.END

        keyboard = []
        products = list(PRODUCTS_DATA.keys())
        for i in range(0, len(products), 2):
            row = [
                InlineKeyboardButton(products[i].capitalize(), callback_data=f"product_{products[i]}")
            ]
            if i + 1 < len(products):
                row.append(InlineKeyboardButton(products[i+1].capitalize(), callback_data=f"product_{products[i+1]}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await self._edit_or_reply(update, "📦 Выберите продукт:", reply_markup)
        return WAITING_PRODUCT

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()

        if query.data == "back_to_menu":
            await self.show_main_menu_with_photo(update, context)
            return ConversationHandler.END

        elif query.data == "clear_products":
            await self.clear_products(update, context)
            return ConversationHandler.END

        elif query.data == "list_products":
            await self.list_products(update, context)
            return ConversationHandler.END

        elif query.data == "cancel":
            await query.edit_message_text("❌ Операция отменена.")
            await asyncio.sleep(1)
            await self.show_main_menu_with_photo(update, context)
            return ConversationHandler.END

        elif query.data.startswith("product_"):
            product_name = query.data[8:]
            context.user_data['current_product'] = product_name

            keyboard = [
                [InlineKeyboardButton("📅 Сегодня", callback_data="today")],
                [InlineKeyboardButton("⏪ Вчера", callback_data="yesterday")],
                [InlineKeyboardButton("⏪ 2 дня назад", callback_data="two_days_ago")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_product"),
                 InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"📦 Продукт: **{product_name}**\n📆 Когда вы его купили?",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return WAITING_DATE

        elif query.data == "back_to_product":
            return await self.add_product_start(update, context)

        elif query.data in ["today", "yesterday", "two_days_ago"]:
            product_name = context.user_data.get('current_product')
            if not product_name:
                await query.edit_message_text("❌ Ошибка: продукт не выбран.")
                await asyncio.sleep(1)
                await self.show_main_menu_with_photo(update, context)
                return ConversationHandler.END

            if query.data == "today":
                purchase_date = datetime.now()
            elif query.data == "yesterday":
                purchase_date = datetime.now() - timedelta(days=1)
            elif query.data == "two_days_ago":
                purchase_date = datetime.now() - timedelta(days=2)

            success = await self.db.add_product(query.from_user.id, product_name, purchase_date)
            if success:
                shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                days_left = (expiration_date.date() - datetime.now().date()).days
                msg = f"✅ **{product_name}** добавлен!\n📅 Срок: {expiration_date.strftime('%d.%m.%Y')}\n⏳ Осталось: {days_left} дней"
            else:
                msg = "❌ Ошибка при добавлении продукта."

            await query.edit_message_text(msg, parse_mode="Markdown")
            await asyncio.sleep(2)
            await self.show_main_menu_with_photo(update, context)
            return ConversationHandler.END

        elif query.data == "add_product":
            return await self.add_product_start(update, context)

        else:
            await query.edit_message_text("❓ Неизвестная команда.")
            await asyncio.sleep(1)
            await self.show_main_menu_with_photo(update, context)
            return ConversationHandler.END

    async def _edit_or_reply(self, update: Update, text: str, reply_markup=None) -> None:
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Не удалось отредактировать: {e}")
            if update.callback_query:
                await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

    async def check_expiring_products(self):
        try:
            expiring = await self.db.get_expiring_products()
            for user_id, _, product_name, _ in expiring:
                try:
                    msg = f"⚠️ **{product_name}** испортится завтра!\n"
                    cat = PRODUCTS_DATA[product_name]['category']
                    tips = {
                        "молочные": "Сделайте сырники или коктейль!",
                        "мясо": "Приготовьте жаркое или гуляш!",
                        "рыба": "Запеките с овощами!",
                        "хлеб": "Сделайте гренки!",
                        "фрукты": "Приготовьте салат!",
                        "овощи": "Сварите суп!",
                    }
                    msg += tips.get(cat, "Используйте его сегодня!")
                    await self.application.bot.send_message(user_id, msg, parse_mode="Markdown")
                    await self.db.mark_as_notified(user_id, product_name)
                except Exception as e:
                    logger.error(f"Ошибка отправки {user_id}: {e}")
        except Exception as e:
            logger.error(f"Ошибка проверки: {e}")

    def setup_handlers(self):
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("start", self.start),
                CallbackQueryHandler(self.button_handler)
            ],
            states={
                WAITING_PRODUCT: [
                    CallbackQueryHandler(self.button_handler, pattern=r"^(product_.+|cancel)$")
                ],
                WAITING_DATE: [
                    CallbackQueryHandler(self.button_handler, pattern=r"^(today|yesterday|two_days_ago|back_to_product|cancel)$")
                ]
            },
            fallbacks=[
                CallbackQueryHandler(self.button_handler, pattern=r"^cancel$")
            ],
            per_message=False,
            allow_reentry=True
        )
        self.application.add_handler(conv_handler)

    def setup_scheduler(self):
        self.scheduler.add_job(self.check_expiring_products, CronTrigger(hour=10, minute=0), id='daily_check')

    def run(self):
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        self.setup_scheduler()
        self.scheduler.start()
        logger.info("🚀 Бот запущен")
        self.application.run_polling()


def main():
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("Установите TELEGRAM_BOT_TOKEN")
        return
    bot = FreshlyBot(BOT_TOKEN)
    bot.run()


if __name__ == '__main__':
    main()
