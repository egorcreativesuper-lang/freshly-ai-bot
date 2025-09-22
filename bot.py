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

# База продуктов
PRODUCTS_DATA = {
    "молоко": {"shelf_life": 7, "category": "молочные"},
    "кефир": {"shelf_life": 5, "category": "молочные"},
    "сыр": {"shelf_life": 14, "category": "молочные"},
    "творог": {"shelf_life": 5, "category": "молочные"},
    "сметана": {"shelf_life": 7, "category": "молочные"},
    "йогурт": {"shelf_life": 10, "category": "молочные"},
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
                    expiration_date DATE
                )
            ''')
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
        
        welcome_text = f"""
👋 Привет, {user.first_name}! Я Freshly Bot — твой помощник по отслеживанию сроков годности продуктов.

📸 **Как пользоваться:**
1. Нажми кнопку "📸 Добавить продукт"
2. Укажи дату покупки
3. Следи за сроками

📋 **Команды:**
/list - список ваших продуктов
/clear - очистить все продукты

🎯 Начни с добавления первого продукта!
        """
        
        keyboard = [
            [KeyboardButton("📸 Добавить продукт")],
            [KeyboardButton("📋 Мои продукты")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def add_product_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Начало добавления продукта"""
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
            [KeyboardButton("Отмена")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"📦 Продукт: **{product_name}**\n"
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
        
        if user_input == "Отмена":
            await update.message.reply_text("❌ Операция отменена.")
            return ConversationHandler.END
        
        try:
            if user_input == "Сегодня":
                purchase_date = datetime.now()
            elif user_input == "Вчера":
                purchase_date = datetime.now() - timedelta(days=1)
            else:
                await update.message.reply_text("❌ Пожалуйста, выберите дату из кнопок")
                return WAITING_DATE
            
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
        
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await update.message.reply_text("❌ Ошибка, попробуйте снова")
            return ConversationHandler.END
        
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
        
        if text == "📸 Добавить продукт":
            await self.add_product_start(update, context)
        elif text == "📋 Мои продукты":
            await self.list_products(update, context)
    
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        # ConversationHandler для добавления продукта
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex("^📸 Добавить продукт$"), self.add_product_start)
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
        logger.info(f"Запуск бота с токеном: {self.token[:10]}...")
        
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        
        logger.info("Бот запущен в polling режиме")
        self.application.run_polling()

def main():
    """Основная функция"""
    # Получаем токен из переменных окружения
    BOT_TOKEN = os.environ.get('BOT_TOKEN')
    
    logger.info(f"Получен токен: {BOT_TOKEN[:10] if BOT_TOKEN else 'НЕТ ТОКЕНА'}...")
    
    if not BOT_TOKEN:
        logger.error("Токен бота не найден! Установите переменную BOT_TOKEN")
        return
    
    bot = FreshlyBot(BOT_TOKEN)
    bot.run()

if __name__ == '__main__':
    main()
