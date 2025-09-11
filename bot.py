import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import os

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

# 📊 Google Таблица
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = {
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

    # Проверяем обязательные поля
    if not all([creds_dict["project_id"], creds_dict["private_key"], creds_dict["client_email"]]):
        logger.error("❌ Не хватает данных для Google Credentials!")
        exit(1)

    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    SHEET_URL = os.getenv("SHEET_URL")
    sheet = client.open_by_url
