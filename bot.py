import os
import logging
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (Updater, CommandHandler, MessageHandler, 
                         Filters, CallbackContext, ConversationHandler)

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

def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    
    welcome_text = f"""
👋 Привет, {user.first_name}! Я Freshly Bot — твой помощник по отслеживанию сроков годности продуктов.

📋 **Доступные команды:**
/start - показать это сообщение  
/list - список ваших продуктов
/add - добавить продукт
/clear - очистить все продукты

🎯 Начни с добавления первого продукта командой /add!
    """
    
    update.message.reply_text(welcome_text)

def list_products(update: Update, context: CallbackContext) -> None:
    """Показать список продуктов"""
    db = Database()
    user = update.effective_user
    products = db.get_user_products(user.id)
    
    if not products:
        update.message.reply_text("📭 У вас нет добавленных продуктов.")
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
    update.message.reply_text(message)

def clear_products(update: Update, context: CallbackContext) -> None:
    """Очистка всех продуктов"""
    db = Database()
    user = update.effective_user
    db.clear_user_products(user.id)
    update.message.reply_text("✅ Все продукты удалены!")

def add_product_start(update: Update, context: CallbackContext) -> int:
    """Начало добавления продукта"""
    db = Database()
    user = update.effective_user
    
    # Проверка лимита
    if db.get_products_count(user.id) >= 5:
        update.message.reply_text(
            "❌ Вы достигли лимита (5 продуктов). Используйте /clear чтобы очистить список."
        )
        return ConversationHandler.END
    
    # Список доступных продуктов
    products_list = "\n".join([f"• {product}" for product in PRODUCTS_DATA.keys()])
    
    update.message.reply_text(
        f"📦 **Доступные продукты:**\n{products_list}\n\n"
        "📝 Введите название продукта:"
    )
    
    return WAITING_PRODUCT

def handle_product_input(update: Update, context: CallbackContext) -> int:
    """Обработка ввода продукта"""
    product_name = update.message.text.lower().strip()
    
    if product_name not in PRODUCTS_DATA:
        update.message.reply_text("❌ Продукт не найден. Попробуйте еще раз:")
        return WAITING_PRODUCT
    
    context.user_data['current_product'] = product_name
    
    # Кнопки для выбора даты
    keyboard = [
        [KeyboardButton("Сегодня"), KeyboardButton("Вчера")],
        [KeyboardButton("2 дня назад"), KeyboardButton("Отмена")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        f"📦 Продукт: **{product_name}**\n"
        "📅 Когда вы купили этот продукт?",
        reply_markup=reply_markup
    )
    
    return WAITING_DATE

def handle_date(update: Update, context: CallbackContext) -> int:
    """Обработка даты покупки"""
    db = Database()
    user_input = update.message.text
    product_name = context.user_data.get('current_product')
    user = update.effective_user
    
    if user_input == "Отмена":
        update.message.reply_text("❌ Операция отменена.")
        return ConversationHandler.END
    
    try:
        if user_input == "Сегодня":
            purchase_date = datetime.now()
        elif user_input == "Вчера":
            purchase_date = datetime.now() - timedelta(days=1)
        elif user_input == "2 дня назад":
            purchase_date = datetime.now() - timedelta(days=2)
        else:
            update.message.reply_text("❌ Пожалуйста, выберите дату из кнопок")
            return WAITING_DATE
        
        # Добавляем продукт
        success = db.add_product(user.id, product_name, purchase_date)
        
        if success:
            shelf_life = PRODUCTS_DATA[product_name]['shelf_life']
            expiration_date = purchase_date + timedelta(days=shelf_life)
            days_left = (expiration_date.date() - datetime.now().date()).days
            
            update.message.reply_text(
                f"✅ **{product_name}** добавлен!\n"
                f"📅 Срок годности: {expiration_date.strftime('%d.%m.%Y')}\n"
                f"⏳ Осталось дней: {days_left}"
            )
        else:
            update.message.reply_text("❌ Ошибка при добавлении продукта")
    
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        update.message.reply_text("❌ Ошибка, попробуйте снова")
        return ConversationHandler.END
    
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext) -> int:
    """Отмена текущей операции"""
    update.message.reply_text("❌ Операция отменена.")
    return ConversationHandler.END

def main():
    """Основная функция"""
    # Получаем токен из переменных окружения
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not BOT_TOKEN:
        logger.error("Токен бота не найден! Установите переменную BOT_TOKEN")
        return
    
    logger.info("Запуск бота...")
    
    # Создаем Updater и передаем ему токен бота
    updater = Updater(BOT_TOKEN)
    
    # Получаем диспетчер для регистрации обработчиков
    dispatcher = updater.dispatcher
    
    # ConversationHandler для добавления продукта
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_product_start)],
        states={
            WAITING_PRODUCT: [MessageHandler(Filters.text & ~Filters.command, handle_product_input)],
            WAITING_DATE: [MessageHandler(Filters.text & ~Filters.command, handle_date)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    # Регистрируем обработчики команд
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("list", list_products))
    dispatcher.add_handler(CommandHandler("clear", clear_products))
    dispatcher.add_handler(conv_handler)
    
    # Запускаем бота
    updater.start_polling()
    logger.info("Бот запущен и готов к работе!")
    
    # Бот работает до прерывания Ctrl-C
    updater.idle()

if __name__ == '__main__':
    main()
