from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import os

# 🔑 Токен бота (будем передавать через переменную среды в Render)
BOT_TOKEN = os.getenv("BOT_TOKEN")

# 📊 Google Таблица
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = {
    "type": "service_account",
    "project_id": os.getenv("GSPREAD_PROJECT_ID"),
    "private_id": os.getenv("GSPREAD_PRIVATE_ID"),
    "private_key": os.getenv("GSPREAD_PRIVATE_KEY").replace('\\n', '\n') if os.getenv("GSPREAD_PRIVATE_KEY") else None,
    "client_email": os.getenv("GSPREAD_CLIENT_EMAIL"),
    "client_id": os.getenv("GSPREAD_CLIENT_ID"),
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": os.getenv("GSPREAD_CLIENT_CERT_URL")
}
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_url(os.getenv("SHEET_URL")).sheet1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍏 Привет! Я — Freshly AI.\n"
        "✍️ Добавь продукт: /add [название] [срок в днях]\n"
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
    except:
        await update.message.reply_text("❗ Используй: /add Название Срок")

async def list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    except:
        await update.message.reply_text("❗ Используй: /eaten [номер]")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_product))
    app.add_handler(CommandHandler("list", list_products))
    app.add_handler(CommandHandler("eaten", mark_eaten))
    app.run_polling()

if __name__ == "__main__":
    main()
