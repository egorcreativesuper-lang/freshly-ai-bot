import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import os
import json

# 🔧 Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 🔑 Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не задан!")
    exit(1)

# 📊 Google Таблица — ИСПОЛЬЗУЕМ ФАЙЛ credentials.json
try:
    # Создаём файл credentials.json из переменных окружения
    creds_data = {
        "type": "service_account",
        "project_id": os.getenv("GSPREAD_PROJECT_ID"),
        "private_key_id": os.getenv("GSPREAD_PRIVATE_ID"),
        "private_key": os.getenv("GSPREAD_PRIVATE_KEY").replace('\\n', '\n') if os.getenv("GSPREAD_PRIVATE_KEY") else None,
        "client_email": os.getenv("GSPREAD_CLIENT_EMAIL"),
        "client_id": os.getenv("GSPREAD_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("GSPREAD_CLIENT_CERT_URL")
    }

    # Записываем в файл credentials.json
    with open("credentials.json", "w") as f:
        json.dump(creds_data, f)

    # Авторизуемся через файл
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client = gspread.authorize(creds)
    SHEET_URL = os.getenv("SHEET_URL")
    sheet = client.open_by_url(SHEET_URL).sheet1
    logger.info("✅ Успешно подключились к Google Таблице")

except Exception as e:
    logger.error(f"❌ Ошибка при подключении к Google Таблице: {e}")
    exit(1)

# 🤖 Команды бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍏 Привет! Я — Freshly AI.\n"
        "✍️ Добавь продукт: /add [название] [срок в дней]\n"
        "📋 /list — покажу активные продукты\n"
        "✅ /eaten [номер] — отмечу как съеденное"
    )

async def add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        name = context.args[0]
        days = int(context.args[1])
        today = datetime.date.today().isoformat()
        sheet.append_row([name, days, today, "Активно"])
        await update.message.reply_text(f"✅ Добавлено: {name} — напомню через {days} дней!")
    except Exception as e:
        logger.error(f"❌ Ошибка в /add: {e}")
        await update.message.reply_text("❗ Используй: /add Название Срок")

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        records = sheet.get_all_records()
        active = [r for r in records if r.get("Статус") == "Активно"]
        if not active:
            await update.message.reply_text("📭 Нет активных продуктов.")
            return
        msg = "📋 Твои продукты:\n"
        for i, r in enumerate(active, 1):
            added = datetime.date.fromisoformat(r["Добавлено"])
            days_left = r["Срок (дней)"] - (datetime.date.today() - added).days
            msg += f"{i}. {r['Название']} — осталось {days_left} дней\n"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"❌ Ошибка в /list: {e}")
        await update.message.reply_text("❗ Ошибка при загрузке списка")

async def mark_eaten(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        index = int(context.args[0]) - 1
        records = sheet.get_all_records()
        active = [r for r in records if r.get("Статус") == "Активно"]
        if 0 <= index < len(active):
            for i, r in enumerate(records):
                if r == active[index]:
                    sheet.update_cell(i + 2, 4, "Съедено")
                    await update.message.reply_text(f"😋 {active[index]['Название']} — отмечено как съеденное!")
                    return
        await update.message.reply_text("❗ Неверный номер.")
    except Exception as e:
        logger.error(f"❌ Ошибка в /eaten: {e}")
        await update.message.reply_text("❗ Используй: /eaten [номер]")

# 🚀 Запуск
def main():
    try:
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("add", add_product))
        app.add_handler(CommandHandler("list", list_products))
        app.add_handler(CommandHandler("eaten", mark_eaten))
        logger.info("✅ Бот запущен...")
        app.run_polling()
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    main()
