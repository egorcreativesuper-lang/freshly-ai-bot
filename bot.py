import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
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
BOT_TOKEN = "8123646923:AAGUnlS9WMD65B4USzmHyGm3AGcgxDZ5U28"

# 📊 Google Таблица — используем google-auth (современная библиотека)
try:
    # JSON-данные сервисного аккаунта
    creds_data = {
        "type": "service_account",
        "project_id": "freshly-471815",
        "private_key_id": "cf74c5d008258cb35b907e322079eb5f2f2e429f",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCtkWviQ0ANLhCM\n84gfQLeR+ckrK/Yq6K3vmAh7hqHw72XQF+LaBcbrtPvovTOVfwV0QTqP1W08VGxP\nI+lofvpDZxMEkDGIyvm3U1N+7EBiWw4tIL3KDMWtsqVVDDBnaXHOXjbbepfMX6F8\nmsTZehRpD3owGRyZr0y5adaDfGHLUqhjARTKfMmy3IGtminP9721iW8TYf6v2i+o\n66xXkdeb1q6Q2pgOpmZ+JDuGibLsF8cYHPVuDNqSV87/xwlQ+FNFKzCo6WtW+so2\nI0vTww2EEXjxZqrByNT0dtPxjexN7IRqgUA/7Pk4EOSHFj+a6uHNYm8QA1Mn7Fkr\nKgXvv0ydAgMBAAECggEAH+PYa9fZ2KIWUeN75uwb3lMD4m3/GoaqUJuBMXr83ZkK\nvpdo65CEqjGUWECNDgJq1N+YPC2lVqCXTtolDDlKT6CMVroMk9rhU0zYyjjronai\n7ek2Xb2Hg4DPjkcBTLrLuXRHhX9qjRckA1InaWLcBaqdk2FFxzn1cZqv2nQ7vqC5\nZp7oxIhcs38FErVP/Xs1EsDBxYU5j+Gng2F5y/Nnrgk3FpEci5t1cBm8HIas0NKq\nU5AMNw38EF0FYMKVMeGQpvg/sU+F0Ey21VH1tDZCXoOODtsePrCP2gokI/zO+biE\nkCJjOVXCRpXjWajXuinRRSVjWFnwZDnx1pW5H6OvwQKBgQDg6ctfWJp66a1n4hnB\n1A4UFLWOaCQg6udYmAvehBdPJvbKYdmzW6SNvWW4gBlUPLDRlLQDHrUweA7nM9U9\ngzVZbMOugGYqg86Mk6umQiGuVyh2GNXnM9Y/sJURaOSdEEnd4o0ncUCWGt/hnx8X\nGpwiQT4MizLyKBUhn3f+fatZQQKBgQDFjttVdMBbAL4yXJLGsTSRDrsWb31j4c4G\nX+IEQP8dT7rWX9g8biqbYO0GVWNbTKzgIZwy9yqna/sWemrn9VpzAF7R9nCzdWjB\nV1aJ6TO9123+EdInKFPj7mZd7BoZ7LwkDb1wl2sZkqPl1kc0mSDE37fXP5m6xchN\nMXyj4afgXQKBgEq7ZGf5+Np+aq/p4MUWwNbLSshWsip94wD9BHSbT2NtfvMgMEX4\nXWT7WaFEbyYeRGJfFrEysuG4Aruv7VrTDhb4nMyOvWPDCA6NwqsrriVPsJINDoYU\nI0xmUCHIyK2ni+O+M0i3yM4Xf+xoAtyaaua25vckCXmM9/iEFErrVtQBAoGAQ97P\nRW2Fw/3eWcjp9+7bI1aPOab1ygHCWPhJ2rJFstk4U/u7ew9R/e1voLRnHO+bmKiT\nVAMMGVaEfXVzEtt8xnODH9jtYQneAkYyCdEfIIJJXHbc3u0A3RaC/pNlaDCndi9u\nPKcYeUGiowxZjB1rX5eIPh+wfbUDGln8+wREO1UCgYBmN6sfcDUjTwMtAcyaikju\nqNCTXnA1pa2UHY7+r3gWTTeBQY9ncZYzkrS32hKwsCB+EW4QNRSsuUaK+TuXPl/0\n+UWYGWaoTw7TJC0N8B8GtfhNqe40Up1qGhXLGtKHCWqF4DSNntbnNfN90QH3igbE\nASi34mUKhWfeorb0azi9xg==\n-----END PRIVATE KEY-----\n",
        "client_email": "freshly@freshly-471815.iam.gserviceaccount.com",
        "client_id": "108328236847731558238",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/freshly%40freshly-471815.iam.gserviceaccount.com",
        "universe_domain": "googleapis.com"
    }

    # Создаём временный файл credentials.json
    with open("credentials.json", "w") as f:
        json.dump(creds_data, f)

    # Авторизация через google-auth
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
    client = gspread.authorize(creds)

    # Открываем таблицу
    SHEET_URL = "https://docs.google.com/spreadsheets/d/10kN4te505m4ALsv5Ixo5WsSH4Uot2AQ3Kni05Mwn_WE"
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
