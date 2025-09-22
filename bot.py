import logging
import sqlite3
import json
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WAITING_PHOTO, WAITING_DATE = range(2)

class Database:
    def __init__(self):
        self.init_db()
        self.load_products_data()
        self.load_recipes_data()
    
    def init_db(self):
        """Инициализация базы данных"""
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    premium INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    product_name TEXT,
                    purchase_date DATE,
                    expiration_date DATE,
                    notified INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            conn.commit()
    
    def load_products_data(self):
        """Загрузка данных о продуктах"""
        products = {
            "молоко": {"shelf_life": 7, "category": "молочные"},
            "кефир": {"shelf_life": 5, "category": "молочные"},
            "сыр": {"shelf_life": 14, "category": "молочные"},
            "творог": {"shelf_life": 5, "category": "молочные"},
            "сметана": {"shelf_life": 7, "category": "молочные"},
            "йогурт": {"shelf_life": 10, "category": "молочные"},
            "яйца": {"shelf_life": 30, "category": "яйца"},
            "курица": {"shelf_life": 3, "category": "мясо"},
            "говядина": {"shelf_life": 4, "category": "мясо"},
            "свинина": {"shelf_life": 4, "category": "мясо"},
            "рыба": {"shelf_life": 2, "category": "рыба"},
            "хлеб": {"shelf_life": 5, "category": "хлеб"}
        }
        self.products_data = products
    
    def load_recipes_data(self):
        """Загрузка данных о рецептах"""
        recipes = {
            "молочные": [
                {
                    "name": "Сырники",
                    "ingredients": ["творог 500г", "яйцо 2шт", "мука 4ст.л", "сахар 2ст.л"],
                    "steps": ["Смешать творог с яйцами", "Добавить муку и сахар", "Жарить на сковороде"],
                    "time": "30 мин",
                    "portions": 4
                }
            ],
            "мясо": [
                {
                    "name": "Курица с овощами",
                    "ingredients": ["курица 500г", "овощи 300г", "специи"],
                    "steps": ["Обжарить курицу", "Добавить овощи", "Тушить 20 мин"],
                    "time": "40 мин",
                    "portions": 3
                }
            ]
        }
        self.recipes_data = recipes
    
    def add_user(self, user_id, username):
        """Добавление пользователя"""
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, username) 
                VALUES (?, ?)
            ''', (user_id, username))
            conn.commit()
    
    def add_product(self, user_id, product_name, purchase_date):
        """Добавление продукта"""
        if product_name not in self.products_data:
            return False
        
        shelf_life = self.products_data[product_name]['shelf_life']
        expiration_date = purchase_date + timedelta(days=shelf_life)
        
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO products (user_id, product_name, purchase_date, expiration_date)
                VALUES (?, ?, ?, ?)
            ''', (user_id, product_name, purchase_date.date(), expiration_date.date()))
            conn.commit()
        
        return True
    
    def get_user_products(self, user_id):
        """Получение продуктов пользователя"""
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT product_name, purchase_date, expiration_date 
                FROM products 
                WHERE user_id = ? 
                ORDER BY expiration_date
            ''', (user_id,))
            return cursor.fetchall()
    
    def get_products_count(self, user_id):
        """Получение количества продуктов пользователя"""
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM products WHERE user_id = ?', (user_id,))
            return cursor.fetchone()[0]
    
    def clear_user_products(self, user_id):
        """Очистка продуктов пользователя"""
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
            conn.commit()
    
    def get_expiring_products(self):
        """Получение продуктов, срок которых истекает завтра"""
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT p.user_id, u.username, p.product_name, p.expiration_date
                FROM products p
                JOIN users u ON p.user_id = u.user_id
                WHERE p.expiration_date = ? AND p.notified = 0
            ''', (tomorrow,))
            return cursor.fetchall()
    
    def mark_as_notified(self, user_id, product_name):
        """Пометить продукт как уведомленный"""
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE products 
                SET notified = 1 
                WHERE user_id = ? AND product_name = ?
            ''', (user_id, product_name))
            conn.commit()
    
    def get_recipes_by_category(self, category):
        """Получение рецептов по категории"""
        return self.recipes_data.get(category, [])
    
    def get_product_category(self, product_name):
        """Получение категории продукта"""
        if product_name in self.products_data:
            return self.products_data[product_name]['category']
        return None

class FreshlyBot:
    def __init__(self):
        self.db = Database()
        self.application = None
        self.setup_scheduler()
    
    def setup_scheduler(self):
        """Настройка планировщика уведомлений"""
        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(
            self.check_expiring_products,
            trigger=CronTrigger(hour=10, minute=0),
            id='daily_check'
        )
    
    def check_expiring_products(self):
        """Проверка продуктов с истекающим сроком"""
        try:
            expiring_products = self.db.get_expiring_products()
            
            for user_id, username, product_name, expiration_date in expiring_products:
                try:
                    category = self.db.get_product_category(product_name)
                    recipes = self.db.get_recipes_by_category(category)
                    
                    message = f"⚠️ Твой {product_name} испортится завтра!\n"
                    
                    if recipes:
                        recipe = recipes[0]
                        message += f"🍳 Попробуй {recipe['name']}!\n"
                        message += f"⏱ Время: {recipe['time']}\n"
                        message += f"🍽 Порции: {recipe['portions']}"
                    
                    # Отправка уведомления через бота
                    if self.application:
                        self.application.bot.send_message(
                            chat_id=user_id,
                            text=message
                        )
                    
                    self.db.mark_as_notified(user_id, product_name)
                    
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
        
        except Exception as e:
            logger.error(f"Ошибка проверки продуктов: {e}")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        self.db.add_user(user.id, user.username)
        
        welcome_text = f"""
👋 Привет, {user.first_name}! Я Freshly Bot — твой помощник по отслеживанию сроков годности продуктов.

📸 **Как пользоваться:**
1. Сфотографируй продукт
2. Укажи дату покупки
3. Получай уведомления перед истечением срока

📋 **Команды:**
/start - показать это сообщение
/list - список ваших продуктов
/clear - очистить все продукты

🎯 Начни с отправки фото продукта!
        """
        
        keyboard = [
            [KeyboardButton("📸 Сфотографировать продукт")],
            [KeyboardButton("📋 Мои продукты")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фотографии продукта"""
        user = update.effective_user
        
        # Проверка лимита для бесплатной версии
        if self.db.get_products_count(user.id) >= 5:
            await update.message.reply_text(
                "❌ Вы достигли лимита бесплатной версии (5 продуктов). "
                "Удалите старые продукты командой /clear"
            )
            return ConversationHandler.END
        
        # Заглушка для распознавания продукта
        product_name = "молоко"
        context.user_data['current_product'] = product_name
        
        # Предлагаем выбрать дату покупки
        keyboard = [
            [
                KeyboardButton("Сегодня"),
                KeyboardButton("Вчера"),
                KeyboardButton("Позавчера")
            ],
            [KeyboardButton("Ввести вручную (ДД.ММ.ГГГГ)")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"📦 Распознан продукт: **{product_name}**\n"
            "📅 Когда вы купили этот продукт?",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        return WAITING_DATE
    
    async def handle_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка даты покупки"""
        user = update.effective_user
        user_input = update.message.text
        product_name = context.user_data.get('current_product')
        
        try:
            if user_input == "Сегодня":
                purchase_date = datetime.now()
            elif user_input == "Вчера":
                purchase_date = datetime.now() - timedelta(days=1)
            elif user_input == "Позавчера":
                purchase_date = datetime.now() - timedelta(days=2)
            else:
                purchase_date = datetime.strptime(user_input, '%d.%m.%Y')
            
            success = self.db.add_product(user.id, product_name, purchase_date)
            
            if success:
                shelf_life = self.db.products_data[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                
                await update.message.reply_text(
                    f"✅ Продукт добавлен!\n"
                    f"📦 **{product_name}**\n"
                    f"📅 Срок годности до: {expiration_date.strftime('%d.%m.%Y')}\n"
                    f"⏳ Осталось дней: {(expiration_date.date() - datetime.now().date()).days}"
                )
            else:
                await update.message.reply_text("❌ Ошибка добавления продукта")
        
        except ValueError:
            await update.message.reply_text("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
            return WAITING_DATE
        
        return ConversationHandler.END
    
    async def list_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать список продуктов"""
        user = update.effective_user
        products = self.db.get_user_products(user.id)
        
        if not products:
            await update.message.reply_text("📭 У вас нет добавленных продуктов.")
            return
        
        message = "📋 **Ваши продукты:**\n\n"
        
        for product_name, purchase_date, expiration_date in products:
            days_left = (expiration_date - datetime.now().date()).days
            
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
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def clear_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Очистка всех продуктов"""
        user = update.effective_user
        self.db.clear_user_products(user.id)
        await update.message.reply_text("✅ Все продукты удалены!")
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена текущей операции"""
        await update.message.reply_text("❌ Операция отменена.")
        return ConversationHandler.END
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        # ConversationHandler для добавления продукта
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.PHOTO, self.handle_photo),
                MessageHandler(filters.Regex("^📸 Сфотографировать продукт$"), self.handle_photo)
            ],
            states={
                WAITING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_date)]
            },
            fallbacks=[CommandHandler("cancel", self.cancel)]
        )
        
        # Команды
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("list", self.list_products))
        self.application.add_handler(CommandHandler("clear", self.clear_products))
        self.application.add_handler(conv_handler)
        
        # Обработчики кнопок
        self.application.add_handler(
            MessageHandler(filters.Regex("^📋 Мои продукты$"), self.list_products)
        )
    
    def run(self):
        """Запуск бота"""
        # Получаем токен из переменных окружения
        BOT_TOKEN = os.getenv('BOT_TOKEN')
        if not BOT_TOKEN:
            logger.error("Токен бота не найден! Установите переменную BOT_TOKEN")
            return
        
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Настройка обработчиков
        self.setup_handlers()
        
        # Запуск планировщика уведомлений
        self.scheduler.start()
        
        # Запуск бота
        logger.info("Бот запущен")
        self.application.run_polling()

if __name__ == '__main__':
    bot = FreshlyBot()
    bot.run()
