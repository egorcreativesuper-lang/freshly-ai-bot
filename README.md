import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler
)
from datetime import datetime, timedelta
from PIL import Image
import io

from config import BOT_TOKEN, FREE_PRODUCT_LIMIT, STATUS_COLORS
from database import Database
from scheduler import NotificationScheduler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
WAITING_PHOTO, WAITING_DATE = range(2)

class FreshlyBot:
    def __init__(self):
        self.db = Database()
        self.application = None
    
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

🍳 **Что я умею:**
• Распознавать продукты (пока заглушка)
• Отслеживать сроки годности
• Напоминать за 1 день до истечения
• Предлагать рецепты

📋 **Команды:**
/start - показать это сообщение
/list - список ваших продуктов
/clear - очистить все продукты

🎯 Начни с отправки фото продукта!
        """
        
        keyboard = [
            [KeyboardButton("📸 Сфотографировать продукт")],
            [KeyboardButton("📋 Мои продукты"), KeyboardButton("⚙️ Настройки")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка фотографии продукта"""
        user = update.effective_user
        
        # Проверка лимита для бесплатной версии
        if self.db.get_products_count(user.id) >= FREE_PRODUCT_LIMIT:
            await update.message.reply_text(
                f"❌ Вы достигли лимита бесплатной версии ({FREE_PRODUCT_LIMIT} продуктов). "
                "Удалите старые продукты или приобретите премиум-версию."
            )
            return ConversationHandler.END
        
        # Заглушка для распознавания продукта
        # В будущем можно добавить ML-модель
        product_name = "молоко"  # Заглушка
        
        context.user_data['current_product'] = product_name
        
        # Предлагаем выбрать дату покупки
        today = datetime.now().date()
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
                # Парсим дату из формата ДД.ММ.ГГГГ
                purchase_date = datetime.strptime(user_input, '%d.%m.%Y')
            
            # Добавляем продукт в базу
            success = self.db.add_product(user.id, product_name, purchase_date)
            
            if success:
                shelf_life = self.db.products_data[product_name]['shelf_life']
                expiration_date = purchase_date + timedelta(days=shelf_life)
                
                await update.message.reply_text(
                    f"✅ Продукт добавлен!\n"
                    f"📦 **{product_name}**\n"
                    f"📅 Срок годности до: {expiration_date.strftime('%d.%m.%Y')}\n"
                    f"⏳ Осталось дней: {(expiration_date.date() - datetime.now().date()).days}",
                    parse_mode='Markdown'
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
            
            # Определяем статус
            if days_left < 0:
                status = STATUS_COLORS['expired']
                status_text = "ПРОСРОЧЕНО"
            elif days_left == 0:
                status = STATUS_COLORS['today']
                status_text = "Истекает сегодня"
            elif days_left == 1:
                status = STATUS_COLORS['tomorrow']
                status_text = "Истекает завтра"
            elif days_left <= 3:
                status = STATUS_COLORS['2-3_days']
                status_text = f"Истекает через {days_left} дня"
            else:
                status = STATUS_COLORS['safe']
                status_text = f"Осталось {days_left} дней"
            
            message += f"{status} **{product_name}**\n"
            message += f"   📅 До {expiration_date.strftime('%d.%m.%Y')}\n"
            message += f"   ⏰ {status_text}\n\n"
        
        # Добавляем информацию о лимите
        products_count = self.db.get_products_count(user.id)
        message += f"📊 Использовано: {products_count}/{FREE_PRODUCT_LIMIT}"
        
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
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # Настройка обработчиков
        self.setup_handlers()
        
        # Запуск планировщика уведомлений
        self.scheduler = NotificationScheduler(self.application.bot)
        self.scheduler.start()
        
        # Запуск бота
        logger.info("Бот запущен")
        self.application.run_polling()

if __name__ == '__main__':
    bot = FreshlyBot()
    bot.run()
cd freshly-bot
