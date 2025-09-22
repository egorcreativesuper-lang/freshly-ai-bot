import os
import logging
import sqlite3
import asyncio
import shutil
import re
from datetime import datetime, timedelta
from contextlib import contextmanager
from functools import lru_cache
from typing import Dict, List, Optional

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from telegram.error import Forbidden, BadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'products.db')
    BACKUP_ENABLED = os.getenv('BACKUP_ENABLED', 'True').lower() == 'true'
    BACKUP_DIR = os.getenv('BACKUP_DIR', 'backups')
    MAX_PRODUCTS = int(os.getenv('MAX_PRODUCTS', '5'))
    NOTIFICATION_HOUR = int(os.getenv('NOTIFICATION_HOUR', '10'))
    NOTIFICATION_MINUTE = int(os.getenv('NOTIFICATION_MINUTE', '0'))

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

# Советы по использованию продуктов
PRODUCT_TIPS = {
    "молочные": "🥛 Сделайте сырники, коктейль или используйте для выпечки!",
    "мясо": "🍖 Приготовьте жаркое, гуляш или фарш для котлет!",
    "рыба": "🐟 Запеките с овощами или приготовьте уху!",
    "хлеб": "🍞 Сделайте гренки, панировку или хлебный пудинг!",
    "фрукты": "🍎 Приготовьте фруктовый салат, смузи или компот!",
    "овощи": "🥦 Сварите суп, рагу или запеките с сыром!",
    "яйца": "🥚 Приготовьте омлет, яичницу или используйте для выпечки!",
    "макароны": "🍝 Сделайте пасту с соусом или запеканку!",
    "злаки": "🍚 Сварите кашу или используйте как гарнир!",
    "напитки": "☕ Используйте для приготовления напитков или коктейлей!",
    "сладости": "🍫 Используйте для десертов или выпечки!",
    "жиры": "🧈 Используйте для готовки или заправки салатов!",
    "консервы": "🥫 Используйте как готовый продукт или для салатов!"
}

class ProductManager:
    """Менеджер для работы с продуктами"""
    
    @staticmethod
    @lru_cache(maxsize=1)
    def get_products_data() -> Dict:
        """Кэшированные данные о продуктах"""
        return PRODUCTS_DATA.copy()
    
    @staticmethod
    @lru_cache(maxsize=1)
    def get_product_tips() -> Dict:
        """Кэшированные советы по продуктам"""
        return PRODUCT_TIPS.copy()
    
    @classmethod
    def get_product_categories(cls) -> List[str]:
        """Получить список категорий"""
        return list(set(product["category"] for product in cls.get_products_data().values()))
    
    @classmethod
    def get_products_by_category(cls, category: str) -> List[str]:
        """Получить продукты по категории"""
        return [name for name, data in cls.get_products_data().items() if data["category"] == category]

class Database:
    def __init__(self):
        self.init_db()
        self.create_backup_dir()

    def create_backup_dir(self):
        """Создать директорию для бэкапов"""
        if Config.BACKUP_ENABLED and not os.path.exists(Config.BACKUP_DIR):
            os.makedirs(Config.BACKUP_DIR)

    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для соединения с БД"""
        conn = sqlite3.connect(Config.DATABASE_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
        try:
            yield conn
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def init_db(self):
        """Инициализация базы данных"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    product_name TEXT,
                    purchase_date DATE,
                    expiration_date DATE,
                    notified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_products_expiration ON products(expiration_date)
            ''')
            conn.commit()

    async def backup_database(self):
        """Создание резервной копии базы данных"""
        if not Config.BACKUP_ENABLED:
            return
            
        try:
            backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            backup_path = os.path.join(Config.BACKUP_DIR, backup_name)
            shutil.copy2(Config.DATABASE_PATH, backup_path)
            logger.info(f"Создан бэкап базы данных: {backup_path}")
            
            # Удаляем старые бэкапы (оставляем последние 10)
            backups = sorted([f for f in os.listdir(Config.BACKUP_DIR) if f.startswith('backup_')])
            if len(backups) > 10:
                for old_backup in backups[:-10]:
                    os.remove(os.path.join(Config.BACKUP_DIR, old_backup))
                    
        except Exception as e:
            logger.error(f"Ошибка создания бэкапа: {e}")

    async def add_user(self, user_id: int, username: str, first_name: str):
        """Добавление/обновление пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_activity)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (user_id, username or '', first_name or ''))
            conn.commit()

    async def add_product(self, user_id: int, product_name: str, purchase_date: datetime) -> bool:
        """Добавление продукта с логированием"""
        logger.info(f"Добавление продукта: user={user_id}, product={product_name}, date={purchase_date}")
        
        products_data = ProductManager.get_products_data()
        if product_name not in products_data:
            return False

        shelf_life = products_data[product_name]['shelf_life']
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
        """Получение продуктов пользователя"""
        with self.get_connection() as conn:
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
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM products WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 0

    async def clear_user_products(self, user_id: int):
        """Очистка продуктов пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
            conn.commit()
        logger.info(f"Очищены продукты пользователя: {user_id}")

    async def get_expiring_products(self):
        """Получение продуктов с истекающим сроком"""
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT p.user_id, u.username, u.first_name, p.product_name, p.expiration_date
                FROM products p
                JOIN users u ON p.user_id = u.user_id
                WHERE p.expiration_date = ? AND p.notified = FALSE
            ''', (tomorrow,))
            return cursor.fetchall()

    async def mark_as_notified(self, user_id: int, product_name: str):
        """Пометка продукта как уведомленного"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE products
                SET notified = TRUE
                WHERE user_id = ? AND product_name = ?
            ''', (user_id, product_name))
            conn.commit()

    async def remove_expired_products(self):
        """Удаление просроченных продуктов (старше 7 дней)"""
        week_ago = (datetime.now() - timedelta(days=7)).date()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM products WHERE expiration_date < ?', (week_ago,))
            deleted_count = cursor.rowcount
            conn.commit()
            
        if deleted_count > 0:
            logger.info(f"Удалено просроченных продуктов: {deleted_count}")

class FreshlyBot:
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.product_manager = ProductManager()
        self.application: Application = None
        self.scheduler = AsyncIOScheduler()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик команды /start"""
        user = update.effective_user
        await self.db.add_user(user.id, user.username, user.first_name)

        welcome_text = (
            f"👋 Привет, {user.first_name}! Я *Freshly Bot* — твой личный помощник по срокам годности продуктов.\n\n"
            "📌 **Что я умею:**\n"
            "• 📋 Добавить продукт с датой покупки\n"
            "• ⏳ Автоматически отслеживать сроки годности\n"
            "• 🛎️ Уведомлять за день до истечения срока\n"
            "• 🍽️ Подсказывать рецепты для скоропортящихся продуктов\n"
            "• 🗑️ Очищать список при необходимости\n\n"
            f"✅ Максимум {Config.MAX_PRODUCTS} продуктов одновременно\n\n"
            "🎯 Начни с кнопки *➕ Добавить продукт*!"
        )

        await self._send_welcome_message(update, welcome_text)

    async def _send_welcome_message(self, update: Update, text: str):
        """Отправка приветственного сообщения с медиа"""
        animation_url = "https://i.imgur.com/6JQV9Xj.gif"
        fallback_image_url = "https://i.imgur.com/8Y0fKuB.png"

        keyboard = [
            [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
            [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
            [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await update.message.reply_animation(
                animation=animation_url,
                caption=text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить анимацию: {e}")
            try:
                await update.message.reply_photo(
                    photo=fallback_image_url,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            except Exception as e2:
                logger.error(f"Не удалось отправить фото: {e2}")
                await update.message.reply_text(
                    text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Команда помощи"""
        help_text = (
            "📖 **Помощь по командам:**\n\n"
            "• /start - Запустить бота\n"
            "• /list - Показать список продуктов\n"
            "• /add - Добавить продукт\n"
            "• /clear - Очистить все продукты\n"
            "• /help - Эта справка\n\n"
            "💡 **Советы:**\n"
            "• Можно выбрать дату покупки или ввести свою\n"
            "• Бот уведомит за день до истечения срока\n"
            "• Для просроченных продуктов предложит рецепты"
        )
        
        if update.message:
            await update.message.reply_text(help_text, parse_mode="Markdown")
        else:
            await update.callback_query.edit_message_text(help_text, parse_mode="Markdown")

    async def show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Главное меню"""
        image_url = "https://i.imgur.com/8Y0fKuB.png"
        text = "🎯 Выберите действие:"

        keyboard = [
            [InlineKeyboardButton("➕ Добавить продукт", callback_data="add_product")],
            [InlineKeyboardButton("📋 Показать список", callback_data="list_products")],
            [InlineKeyboardButton("🗑️ Очистить всё", callback_data="clear_products")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
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
        """Показать список продуктов"""
        user = update.effective_user
        products = await self.db.get_user_products(user.id)

        if not products:
            text = "📭 У вас нет добавленных продуктов."
        else:
            text = "📋 **Ваши продукты:**\n\n"
            today = datetime.now().date()
            
            for product_name, purchase_date, expiration_date in products:
                days_left = (expiration_date - today).days
                status, status_text = self._get_expiration_status(days_left)
                
                text += f"{status} **{product_name}**\n"
                text += f"   📅 Куплен: {purchase_date}\n"
                text += f"   ⏳ Срок до: {expiration_date} ({status_text})\n\n"
            
            products_count = await self.db.get_products_count(user.id)
            text += f"📊 Всего продуктов: {products_count}/{Config.MAX_PRODUCTS}"

        keyboard = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await self._edit_or_reply(update, text, reply_markup)

    def _get_expiration_status(self, days_left: int) -> tuple:
        """Получить статус и текст для срока годности"""
        if days_left < 0:
            return "🔴", "ПРОСРОЧЕНО"
        elif days_left == 0:
            return "🔴", "Истекает сегодня"
        elif days_left == 1:
            return "🟠", "Истекает завтра"
        elif days_left <= 3:
            return "🟡", f"Истекает через {days_left} дня"
        else:
            return "🟢", f"Осталось {days_left} дней"

    async def clear_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Очистка всех продуктов"""
        user = update.effective_user
        await self.db.clear_user_products(user.id)

        await self._edit_or_reply(update, "✅ Все продукты удалены!")
        await asyncio.sleep(1)
        await self.show_main_menu(update, context)

    async def add_product_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Начало добавления продукта"""
        user = update.effective_user
        
        # Проверка лимита продуктов
        if await self.db.get_products_count(user.id) >= Config.MAX_PRODUCTS:
            await self._edit_or_reply(update, 
                f"❌ Лимит {Config.MAX_PRODUCTS} продуктов. Очистите список.")
            await asyncio.sleep(2)
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        # Создание клавиатуры с продуктами по категориям
        keyboard = []
        categories = self.product_manager.get_product_categories()
        
        for category in sorted(categories):
            category_products = self.product_manager.get_products_by_category(category)
            if category_products:
                # Добавляем заголовок категории
                keyboard.append([InlineKeyboardButton(
                    f"📁 {category.capitalize()}", callback_data=f"category_{category}")])
                
                # Добавляем продукты категории (по 2 в ряд)
                for i in range(0, len(category_products), 2):
                    row = []
                    for j in range(2):
                        if i + j < len(category_products):
                            product = category_products[i + j]
                            row.append(InlineKeyboardButton(
                                product.capitalize(), 
                                callback_data=f"product_{product}"
                            ))
                    if row:
                        keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await self._edit_or_reply(update, "📦 Выберите продукт:", reply_markup)
        return WAITING_PRODUCT

    async def handle_custom_date_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработка ввода пользовательской даты"""
        try:
            date_str = update.message.text.strip()
            
            # Проверка формата даты
            if not re.match(r'^\d{1,2}\.\d{1,2}\.\d{4}$', date_str):
                await update.message.reply_text("❌ Неверный формат! Используйте ДД.ММ.ГГГГ")
                return WAITING_CUSTOM_DATE
            
            # Парсинг даты
            purchase_date = datetime.strptime(date_str, "%d.%m.%Y")
            
            # Проверка что дата не в будущем
            if purchase_date.date() > datetime.now().date():
                await update.message.reply_text("❌ Дата не может быть в будущем!")
                return WAITING_CUSTOM_DATE
            
            # Проверка что дата не слишком старая (больше года назад)
            if purchase_date.date() < (datetime.now() - timedelta(days=365)).date():
                await update.message.reply_text("❌ Дата не может быть больше года назад!")
                return WAITING_CUSTOM_DATE
            
            # Добавление продукта
            product_name = context.user_data.get('current_product')
            if not product_name:
                await update.message.reply_text("❌ Ошибка: продукт не выбран.")
                return ConversationHandler.END
            
            success = await self.db.add_product(update.effective_user.id, product_name, purchase_date)
            if success:
                shelf_life = self.product_manager.get_products_data()[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                days_left = (expiration_date.date() - datetime.now().date()).days
                
                msg = (f"✅ **{product_name}** добавлен!\n"
                      f"📅 Срок годности: {expiration_date.strftime('%d.%m.%Y')}\n"
                      f"⏳ Осталось дней: {days_left}")
            else:
                msg = "❌ Ошибка при добавлении продукта."
            
            await update.message.reply_text(msg, parse_mode="Markdown")
            await asyncio.sleep(2)
            await self.show_main_menu(update, context)
            
        except ValueError:
            await update.message.reply_text("❌ Неверная дата! Проверьте правильность ввода.")
            return WAITING_CUSTOM_DATE
        
        return ConversationHandler.END

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()

        if query.data == "back_to_menu":
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        elif query.data == "clear_products":
            await self.clear_products(update, context)
            return ConversationHandler.END

        elif query.data == "list_products":
            await self.list_products(update, context)
            return ConversationHandler.END

        elif query.data == "help":
            await self.help_command(update, context)
            return ConversationHandler.END

        elif query.data == "cancel":
            await query.edit_message_text("❌ Операция отменена.")
            await asyncio.sleep(1)
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        elif query.data.startswith("category_"):
            # Показываем продукты конкретной категории
            category = query.data[9:]
            products = self.product_manager.get_products_by_category(category)
            
            keyboard = []
            for i in range(0, len(products), 2):
                row = []
                for j in range(2):
                    if i + j < len(products):
                        product = products[i + j]
                        row.append(InlineKeyboardButton(
                            product.capitalize(), 
                            callback_data=f"product_{product}"
                        ))
                if row:
                    keyboard.append(row)
            
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_categories")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"📁 Категория: {category}\nВыберите продукт:",
                reply_markup=reply_markup
            )
            return WAITING_PRODUCT

        elif query.data == "back_to_categories":
            return await self.add_product_start(update, context)

        elif query.data.startswith("product_"):
            product_name = query.data[8:]
            context.user_data['current_product'] = product_name

            # Клавиатура выбора даты
            keyboard = [
                [InlineKeyboardButton("📅 Сегодня", callback_data="today")],
                [InlineKeyboardButton("⏪ Вчера", callback_data="yesterday")],
                [InlineKeyboardButton("⏪ 2 дня назад", callback_data="two_days_ago")],
                [InlineKeyboardButton("📅 Выбрать дату", callback_data="custom_date")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_product"),
                 InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            shelf_life = self.product_manager.get_products_data()[product_name]['shelf_life']
            
            await query.edit_message_text(
                f"📦 Продукт: **{product_name}**\n"
                f"⏳ Срок годности: {shelf_life} дней\n"
                f"📆 Когда вы его купили?",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return WAITING_DATE

        elif query.data == "back_to_product":
            return await self.add_product_start(update, context)

        elif query.data == "custom_date":
            await query.edit_message_text(
                "📅 Введите дату покупки в формате ДД.ММ.ГГГГ\n"
                "Например: 25.12.2024"
            )
            return WAITING_CUSTOM_DATE

        elif query.data in ["today", "yesterday", "two_days_ago"]:
            product_name = context.user_data.get('current_product')
            if not product_name:
                await query.edit_message_text("❌ Ошибка: продукт не выбран.")
                await asyncio.sleep(1)
                await self.show_main_menu(update, context)
                return ConversationHandler.END

            # Определяем дату покупки
            if query.data == "today":
                purchase_date = datetime.now()
            elif query.data == "yesterday":
                purchase_date = datetime.now() - timedelta(days=1)
            elif query.data == "two_days_ago":
                purchase_date = datetime.now() - timedelta(days=2)

            success = await self.db.add_product(query.from_user.id, product_name, purchase_date)
            if success:
                shelf_life = self.product_manager.get_products_data()[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                days_left = (expiration_date.date() - datetime.now().date()).days
                
                msg = (f"✅ **{product_name}** добавлен!\n"
                      f"📅 Срок годности: {expiration_date.strftime('%d.%m.%Y')}\n"
                      f"⏳ Осталось дней: {days_left}")
            else:
                msg = "❌ Ошибка при добавлении продукта."

            await query.edit_message_text(msg, parse_mode="Markdown")
            await asyncio.sleep(2)
            await self.show_main_menu(update, context)
            return ConversationHandler.END

        elif query.data == "add_product":
            return await self.add_product_start(update, context)

        else:
            await query.edit_message_text("❓ Неизвестная команда.")
            await asyncio.sleep(1)
            await self.show_main_menu(update, context)
            return ConversationHandler.END

    async def _edit_or_reply(self, update: Update, text: str, reply_markup=None) -> None:
        """Универсальный метод для редактирования или отправки сообщения"""
        try:
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )
        except Exception as e:
            logger.warning(f"Не удалось отредактировать: {e}")
            if update.callback_query:
                await update.callback_query.message.reply_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(
                    text, reply_markup=reply_markup, parse_mode="Markdown"
                )

    async def check_expiring_products(self):
        """Проверка и уведомление о скоропортящихся продуктах"""
        try:
            expiring = await self.db.get_expiring_products()
            for user_id, _, first_name, product_name, _ in expiring:
                try:
                    # Проверяем, что пользователь не заблокировал бота
                    await self.application.bot.send_chat_action(user_id, "typing")
                    
                    # Формируем сообщение с советом
                    category = self.product_manager.get_products_data()[product_name]['category']
                    tip = self.product_manager.get_product_tips().get(category, "Используйте его сегодня!")
                    
                    msg = (f"⚠️ **{product_name}** испортится завтра!\n\n"
                          f"💡 **Совет:** {tip}\n\n"
                          f"🕐 Рекомендуем использовать продукт сегодня!")
                    
                    await self.application.bot.send_message(user_id, msg, parse_mode="Markdown")
                    await self.db.mark_as_notified(user_id, product_name)
                    
                    logger.info(f"Отправлено уведомление пользователю {user_id} о продукте {product_name}")
                    
                except Forbidden:
                    logger.info(f"Пользователь {user_id} заблокировал бота")
                except BadRequest as e:
                    logger.error(f"Ошибка запроса для {user_id}: {e}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления {user_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Критическая ошибка проверки продуктов: {e}")

    async def cleanup_task(self):
        """Задача очистки устаревших данных"""
        try:
            # Удаляем просроченные продукты
            await self.db.remove_expired_products()
            
            # Создаем бэкап базы данных
            await self.db.backup_database()
            
            logger.info("Задача очистки выполнена успешно")
        except Exception as e:
            logger.error(f"Ошибка в задаче очистки: {e}")

    def setup_handlers(self):
        """Настройка обработчиков команд"""
        # Conversation handler для добавления продуктов
        conv_handler = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(self.button_handler, pattern="^add_product$")
            ],
            states={
                WAITING_PRODUCT: [
                    CallbackQueryHandler(self.button_handler, pattern=r"^(product_.+|category_.+|back_to_categories|cancel)$")
                ],
                WAITING_DATE: [
                    CallbackQueryHandler(self.button_handler, pattern=r"^(today|yesterday|two_days_ago|custom_date|back_to_product|cancel)$")
                ],
                WAITING_CUSTOM_DATE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_custom_date_input)
                ]
            },
            fallbacks=[
                CallbackQueryHandler(self.button_handler, pattern="^cancel$"),
                CommandHandler("start", self.start)
            ],
            per_message=False,
            allow_reentry=True
        )

        # Отдельные обработчики команд
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("list", self.list_products))
        self.application.add_handler(CommandHandler("clear", self.clear_products))
        self.application.add_handler(CommandHandler("add", self.add_product_start))
        self.application.add_handler(conv_handler)
        
        # Обработчик кнопок главного меню
        self.application.add_handler(CallbackQueryHandler(
            self.button_handler, 
            pattern=r"^(back_to_menu|list_products|clear_products|help)$"
        ))

    def setup_scheduler(self):
        """Настройка планировщика задач"""
        # Ежедневная проверка продуктов
        self.scheduler.add_job(
            self.check_expiring_products, 
            CronTrigger(hour=Config.NOTIFICATION_HOUR, minute=Config.NOTIFICATION_MINUTE),
            id='daily_check'
        )
        
        # Еженедельная очистка (каждое воскресенье в 3:00)
        self.scheduler.add_job(
            self.cleanup_task,
            CronTrigger(day_of_week=0, hour=3, minute=0),
            id='weekly_cleanup'
        )

    def run(self):
        """Запуск бота"""
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        self.setup_scheduler()
        
        self.scheduler.start()
        logger.info("🚀 Бот запущен")
        
        try:
            self.application.run_polling()
        except KeyboardInterrupt:
            logger.info("⏹️ Бот остановлен")
        finally:
            self.scheduler.shutdown()


def main():
    """Основная функция"""
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not BOT_TOKEN:
        logger.error("❌ Установите переменную окружения TELEGRAM_BOT_TOKEN")
        return
    
    # Создаем и запускаем бота
    bot = FreshlyBot(BOT_TOKEN)
    bot.run()


if __name__ == '__main__':
    main()
