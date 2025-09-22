import os
import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WAITING_DATE = 1

# База продуктов (в памяти для простоты)
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
    "свинина": {"shelf_life": 4, "category": "мясо"},
    "рыба": {"shelf_life": 2, "category": "рыба"},
    "хлеб": {"shelf_life": 5, "category": "хлеб"},
}

# База рецептов
RECIPES_DATA = {
    "молочные": [
        {
            "name": "Сырники",
            "time": "30 мин",
            "portions": 4
        }
    ],
    "мясо": [
        {
            "name": "Курица с овощами", 
            "time": "40 мин",
            "portions": 3
        }
    ]
}

class Database:
    def __init__(self):
        self.init_db()
    
    def init_db(self):
        """Инициализация базы данных SQLite"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
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
                    notified INTEGER DEFAULT 0
                )
            ''')
            conn.commit()
    
    def add_user(self, user_id, username):
        """Добавление пользователя"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, username) 
                VALUES (?, ?)
            ''', (user_id, username or ''))
            conn.commit()
    
    def add_product(self, user_id, product_name, purchase_date):
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
    
    def get_user_products(self, user_id):
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
    
    def get_products_count(self, user_id):
        """Получение количества продуктов пользователя"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM products WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 0
    
    def clear_user_products(self, user_id):
        """Очистка продуктов пользователя"""
        with sqlite3.connect('products.db', check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
            conn.commit()

class FreshlyBot:
    def __init__(self, token):
        self.token = token
        self.db = Database()
        self.application = None
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        user = update.effective_user
        self.db.add_user(user.id, user.username)
        
        welcome_text = f"""
👋 Привет, {user.first_name}! Я Freshly Bot — твой помощник по отслеживанию сроков годности продуктов.

📸 **Как пользоваться:**
1. Отправь фото продукта или нажми кнопку ниже
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
        
        # Проверка лимита
        if self.db.get_products_count(user.id) >= 5:
            await update.message.reply_text(
                "❌ Вы достигли лимита (5 продуктов). Используйте /clear чтобы очистить список."
            )
            return ConversationHandler.END
        
        # Заглушка для распознавания - всегда "молоко"
        product_name = "молоко"
        context.user_data['current_product'] = product_name
        
        # Кнопки для выбора даты
        keyboard = [
            [KeyboardButton("Сегодня"), KeyboardButton("Вчера")],
            [KeyboardButton("Ввести дату (ДД.ММ.ГГГГ)")]
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
            else:
                # Пытаемся распарсить дату
                if '.' in user_input:
                    purchase_date = datetime.strptime(user_input, '%d.%m.%Y')
                else:
                    raise ValueError("Неверный формат даты")
            
            # Добавляем продукт
            success = self.db.add_product(user.id, product_name, purchase_date)
            
            if success:
                shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                days_left = (expiration_date.date() - datetime.now().date()).days
                
                await update.message.reply_text(
                    f"✅ **{product_name}** добавлен!\n"
                    f"📅 Срок годности: {expiration_date.strftime('%d.%m.%Y')}\n"
                    f"⏳ Осталось дней: {days_left}",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ Ошибка при добавлении продукта")
        
        except ValueError as e:
            await update.message.reply_text(
                "❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ или выберите из кнопок."
            )
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
        
        message += f"📊 Всего продуктов: {len(products)}/5"
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
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок"""
        text = update.message.text
        
        if text == "📸 Сфотографировать продукт":
            await self.handle_photo(update, context)
        elif text == "📋 Мои продукты":
            await self.list_products(update, context)
    
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
        
        # Обработчик кнопок
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.button_handler))
    
    def run(self):
        """Запуск бота"""
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        
        logger.info("Бот запущен")
        self.application.run_polling()

def main():
    """Основная функция"""
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("Токен бота не найден! Установите переменную BOT_TOKEN")
        return
    
    bot = FreshlyBot(BOT_TOKEN)
    bot.run()

if __name__ == '__main__':
    main()
