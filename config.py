import os

# Настройки бота
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Настройки базы данных
DB_NAME = 'products.db'

# Лимиты для бесплатной версии
FREE_PRODUCT_LIMIT = 5
PREMIUM_PRICE = 99  # рублей в месяц

# Цветовые коды для статусов
STATUS_COLORS = {
    'expired': '🔴',
    'today': '🔴',
    'tomorrow': '🟠',
    '2-3_days': '🟡',
    'safe': '🟢'
}
