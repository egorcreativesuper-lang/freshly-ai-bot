from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import logging
from database import Database

logger = logging.getLogger(__name__)

class NotificationScheduler:
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.scheduler = BackgroundScheduler()
        self.setup_scheduler()
    
    def setup_scheduler(self):
        """Настройка планировщика"""
        # Проверка каждый день в 10:00
        self.scheduler.add_job(
            self.check_expiring_products,
            trigger=CronTrigger(hour=10, minute=0),
            id='daily_check'
        )
    
    def start(self):
        """Запуск планировщика"""
        self.scheduler.start()
        logger.info("Планировщик уведомлений запущен")
    
    def check_expiring_products(self):
        """Проверка продуктов с истекающим сроком"""
        try:
            expiring_products = self.db.get_expiring_products()
            
            for user_id, username, product_name, expiration_date in expiring_products:
                try:
                    # Отправка уведомления
                    category = self.db.get_product_category(product_name)
                    recipes = self.db.get_recipes_by_category(category)
                    
                    message = f"⚠️ Твой {product_name} испортится завтра!\n"
                    
                    if recipes:
                        recipe = recipes[0]  # Берем первый рецепт
                        message += f"🍳 Попробуй {recipe['name']}!\n"
                        message += f"⏱ Время: {recipe['time']}\n"
                        message += f"🍽 Порции: {recipe['portions']}"
                    
                    self.bot.send_message(
                        chat_id=user_id,
                        text=message
                    )
                    
                    # Помечаем как уведомленный
                    self.db.mark_as_notified(user_id, product_name)
                    
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
        
        except Exception as e:
            logger.error(f"Ошибка проверки продуктов: {e}")
    
    def shutdown(self):
        """Остановка планировщика"""
        self.scheduler.shutdown()
