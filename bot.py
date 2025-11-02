import logging
import sqlite3
import os
import random
import csv
from io import StringIO
from datetime import datetime, timedelta, time
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
)
import json

# 🔑 ВСТРОЕННЫЙ ТОКЕН
TOKEN = "8123646923:AAERiVrcFss2IubX3SMUJI12c9qHbX2KRgA"

# 👤 ID АДМИНА (твой Telegram ID)
ADMIN_USER_ID = 7334272040

# Состояния
(
    PHOTO_RECOGNITION,
    CHOOSING_PRODUCT_NAME,
    CHOOSING_PURCHASE_DATE,
    CHOOSING_EXPIRATION_DATE
) = range(4)

# Логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================
# БАЗА ДАННЫХ
# ======================

def init_db():
    try:
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    purchase_date TEXT NOT NULL,
                    expiration_days INTEGER NOT NULL,
                    added_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    notified BOOLEAN DEFAULT FALSE
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    premium_until TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    days INTEGER NOT NULL,
                    max_uses INTEGER NOT NULL,
                    used_count INTEGER DEFAULT 0
                )
            ''')
            conn.commit()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        exit(1)

# ======================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ======================

def parse_date(date_str: str):
    formats = ['%Y-%m-%d', '%Y.%m.%d', '%d.%m.%Y', '%d-%m-%Y']
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None

def get_main_menu_keyboard():
    keyboard = [
        ["📸 Добавить по фото", "✍️ Добавить вручную"],
        ["📋 Мои продукты", "🚨 Просроченные"],
        ["📊 Статистика", "👨‍🍳 Рецепты"],
        ["💎 Получить Premium", "🗑️ Очистить всё"],
        ["ℹ️ Помощь"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([["❌ Отмена", "🏠 Главное меню"]], resize_keyboard=True, one_time_keyboard=True)

def get_premium_days_left(user_id: int) -> int:
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return 0
        try:
            premium_until = datetime.strptime(row[0], '%Y-%m-%d').date()
            days_left = (premium_until - datetime.now().date()).days
            return max(0, days_left)
        except Exception:
            return 0

def is_premium(user_id: int) -> bool:
    return get_premium_days_left(user_id) > 0

def grant_premium(user_id: int, days: int):
    new_until = datetime.now().date() + timedelta(days=days)
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO users (user_id, premium_until) VALUES (?, ?)", (user_id, new_until.isoformat()))
        conn.commit()

# ======================
# УВЕДОМЛЕНИЯ
# ======================

EXPIRATION_MESSAGES = [
    "⚠️ Эй! Твой {product} испортится завтра! Не забудь использовать его!",
    "🍅 Срочно! {product} ждёт своего часа на кухне — завтра последний день!",
    "⏰ Внимание! Завтра {product} станет непригодным. Самое время приготовить что-то вкусное!",
    "🥬 Доброе утро! Напоминаю: завтра {product} испортится. Давай спасём еду вместе!",
    "🔔 Добрый вечер! У тебя есть ещё один день, чтобы использовать {product}. Не упусти шанс!"
]

PREMIUM_MESSAGES = [
    "⏳ Привет! Твой {product} испортится через {days} дней. Может, начнёшь планировать ужин?",
    "📅 Напоминаю: у тебя есть {days} дней до окончания срока у {product}. Время проявить кулинарные таланты!",
    "🛒 Совет: через {days} дней закончится срок годности у {product}. Подумай о рецепте заранее!",
    "🌿 Забота о планете начинается с тебя! Через {days} дней {product} испортится. Давай используем его с умом!",
    "💡 Идея дня: через {days} дней {product} нужно будет выбросить. А что если приготовить из него что-то вкусное уже сегодня?"
]

async def send_notification_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.data["user_id"]
    product_name = job.data["product_name"]
    days_left = job.data.get("days_left", 1)

    try:
        if days_left == 1:
            message_template = random.choice(EXPIRATION_MESSAGES)
            text = message_template.format(product=product_name)
        else:
            message_template = random.choice(PREMIUM_MESSAGES)
            text = message_template.format(product=product_name, days=days_left)

        await context.bot.send_message(chat_id=user_id, text=text)
        logger.info(f"✅ Уведомление отправлено пользователю {user_id}: {text}")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления: {e}")

async def check_expired_daily(context: ContextTypes.DEFAULT_TYPE):
    try:
        today = datetime.now().date().isoformat()
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT user_id, name, expires_at FROM products 
                WHERE expires_at <= ? AND notified = FALSE
            ''', (today,))
            expired = cursor.fetchall()

            for user_id, name, expires_at in expired:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🚨 *ПРОСРОЧЕНО:* {name} (срок истёк {expires_at})\nПожалуйста, выбросьте его, чтобы избежать проблем со здоровьем!",
                        parse_mode='Markdown'
                    )
                    cursor.execute("UPDATE products SET notified = TRUE WHERE user_id = ? AND name = ?", (user_id, name))
                    conn.commit()
                except Exception as e:
                    logger.error(f"Ошибка отправки просрочки {user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка в check_expired_daily: {e}")

def schedule_notifications(context: ContextTypes.DEFAULT_TYPE, user_id: int, product_name: str, expiration_days: int):
    today = datetime.now().date()
    expires_at = today + timedelta(days=expiration_days)

    if expiration_days >= 1:
        notify_date_1d = expires_at - timedelta(days=1)
        notify_time_1d = datetime.combine(notify_date_1d, time(hour=9, minute=0))
        if notify_time_1d > datetime.now():
            context.job_queue.run_once(
                send_notification_job,
                when=notify_time_1d,
                data={"user_id": user_id, "product_name": product_name, "days_left": 1},
                name=f"notify_{user_id}_{product_name}_1d"
            )

    if expiration_days > 3:
        notify_date_3d = expires_at - timedelta(days=3)
        notify_time_3d = datetime.combine(notify_date_3d, time(hour=9, minute=0))
        if notify_time_3d > datetime.now():
            context.job_queue.run_once(
                send_notification_job,
                when=notify_time_3d,
                data={"user_id": user_id, "product_name": product_name, "days_left": 3},
                name=f"notify_{user_id}_{product_name}_3d"
            )

    if is_premium(user_id) and expiration_days > 7:
        notify_date_7d = expires_at - timedelta(days=7)
        notify_time_7d = datetime.combine(notify_date_7d, time(hour=9, minute=0))
        if notify_time_7d > datetime.now():
            context.job_queue.run_once(
                send_notification_job,
                when=notify_time_7d,
                data={"user_id": user_id, "product_name": product_name, "days_left": 7},
                name=f"notify_{user_id}_{product_name}_7d"
            )

def restore_scheduled_jobs(application: Application):
    logger.info("🔄 Восстановление запланированных уведомлений...")
    today = datetime.now().date()

    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, name, expiration_days, expires_at
            FROM products
            WHERE notified = FALSE AND expires_at >= ?
        ''', (today.isoformat(),))
        products = cursor.fetchall()

    restored_count = 0
    for user_id, name, expiration_days, expires_at in products:
        exp_date = datetime.strptime(expires_at, '%Y-%m-%d').date()
        days_until_expiry = (exp_date - today).days

        if days_until_expiry < 0:
            continue

        if days_until_expiry >= 1:
            notify_time_1d = datetime.combine(exp_date - timedelta(days=1), time(hour=9, minute=0))
            if notify_time_1d > datetime.now():
                application.job_queue.run_once(
                    send_notification_job,
                    when=notify_time_1d,
                    data={"user_id": user_id, "product_name": name, "days_left": 1},
                    name=f"notify_{user_id}_{name}_1d"
                )
                restored_count += 1

        if days_until_expiry > 3:
            notify_time_3d = datetime.combine(exp_date - timedelta(days=3), time(hour=9, minute=0))
            if notify_time_3d > datetime.now():
                application.job_queue.run_once(
                    send_notification_job,
                    when=notify_time_3d,
                    data={"user_id": user_id, "product_name": name, "days_left": 3},
                    name=f"notify_{user_id}_{name}_3d"
                )
                restored_count += 1

        if is_premium(user_id) and days_until_expiry > 7:
            notify_time_7d = datetime.combine(exp_date - timedelta(days=7), time(hour=9, minute=0))
            if notify_time_7d > datetime.now():
                application.job_queue.run_once(
                    send_notification_job,
                    when=notify_time_7d,
                    data={"user_id": user_id, "product_name": name, "days_left": 7},
                    name=f"notify_{user_id}_{name}_7d"
                )
                restored_count += 1

    logger.info(f"✅ Восстановлено {restored_count} уведомлений.")

# ======================
# АДМИН-КОМАНДЫ
# ======================

async def give_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Доступ запрещён.")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Использование: /give_premium <user_id> <дней>\nПример: /give_premium 987654321 7")
        return

    try:
        user_id = int(context.args[0])
        days = int(context.args[1])
        if days <= 0 or days > 3650:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Неверный формат. Дни — целое число от 1 до 3650.")
        return

    grant_premium(user_id, days)
    await update.message.reply_text(f"✅ Пользователю {user_id} выдан Premium на {days} дней.")

async def list_promo_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Доступ запрещён.")
        return

    try:
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT code, days, max_uses, used_count FROM promo_codes ORDER BY days, code")
            rows = cursor.fetchall()

        if not rows:
            await update.message.reply_text("📭 Нет созданных промокодов.")
            return

        text = "🎟️ *Список промокодов:*\n\n"
        for code, days, max_uses, used_count in rows:
            status = "♾️ безлимитный" if max_uses == 0 else f"{used_count}/{max_uses} использовано"
            text += f"▫️ `{code}` → {days} дней | {status}\n"

        text += "\n💡 Совет: Используй понятные имена: MONTH30, GIFT7, YEAR2025"
        await update.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка при выводе промокодов: {e}")
        await update.message.reply_text("❌ Ошибка при получении списка.")

async def create_promo_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Доступ запрещён.")
        return

    if len(context.args) != 3:
        await update.message.reply_text(
            "🔤 Использование:\n"
            "/create_promo <КОД> <ДНЕЙ> <МАКС_ИСПОЛЬЗОВАНИЙ>\n\n"
            "• КОД — любое слово (латиница, цифры, без пробелов)\n"
            "• ДНЕЙ — 7, 30, 365 и т.д.\n"
            "• МАКС_ИСПОЛЬЗОВАНИЙ — 0 = безлимитный\n\n"
            "Пример: `/create_promo WELCOME7 7 50`"
        )
        return

    code = context.args[0].strip().upper()
    try:
        days = int(context.args[1])
        max_uses = int(context.args[2])
        if days <= 0 or days > 3650:
            raise ValueError("Неверное количество дней")
        if max_uses < 0:
            raise ValueError("Макс. использования не может быть отрицательным")
    except (ValueError, TypeError):
        await update.message.reply_text("❌ Ошибка: дни и лимит должны быть целыми числами. Лимит ≥ 0.")
        return

    if len(code) < 3 or not code.replace("_", "").replace("-", "").isalnum():
        await update.message.reply_text("❌ Код должен быть ≥3 символов и содержать только буквы, цифры, _ или -")
        return

    try:
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO promo_codes (code, days, max_uses) VALUES (?, ?, ?)", (code, days, max_uses))
            conn.commit()
        status = "безлимитный" if max_uses == 0 else f"макс. {max_uses} раз(а)"
        await update.message.reply_text(
            f"✅ Промокод создан!\n\n"
            f"🎟️ Код: `{code}`\n"
            f"⏳ Срок: {days} дней\n"
            f"🔁 Использований: {status}\n\n"
            f"Ссылка для пользователей:\n"
            f"`/promo {code}`"
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"❌ Промокод `{code}` уже существует!")
    except Exception as e:
        logger.error(f"Ошибка создания промокода: {e}")
        await update.message.reply_text("❌ Не удалось создать промокод.")

async def activate_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "🎟️ Используй: /promo ABC123\n\n"
            "Получи Premium с помощью промокода от разработчика!",
            reply_markup=get_main_menu_keyboard()
        )
        return

    code = context.args[0].strip().upper()
    
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT days, max_uses, used_count FROM promo_codes WHERE code = ?", (code,))
        promo = cursor.fetchone()
        
        if not promo:
            await update.message.reply_text("❌ Такой промокод не существует.")
            return

        days, max_uses, used_count = promo

        if max_uses > 0 and used_count >= max_uses:
            await update.message.reply_text("🚫 Этот промокод уже исчерпан.")
            return

        cursor.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        current_until = row[0] if row else None
        if current_until:
            try:
                if datetime.strptime(current_until, '%Y-%m-%d').date() > datetime.now().date():
                    await update.message.reply_text("ℹ️ У тебя уже есть активный Premium. Используй промокод после его окончания.")
                    return
            except:
                pass

        grant_premium(user_id, days)
        cursor.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?", (code,))
        conn.commit()

        await update.message.reply_text(
            f"🎉 Ура! Ты активировал промокод!\n\n"
            f"💎 Premium активен на **{days} дней**.\n"
            f"Наслаждайся расширенными функциями!",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )

# 📢 РАССЫЛКА ВСЕМ ПОЛЬЗОВАТЕЛЯМ
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("🚫 Доступ запрещён.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /broadcast <сообщение>")
        return

    message_text = " ".join(context.args)

    try:
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users")
            user_ids = [row[0] for row in cursor.fetchall()]

        success = 0
        failed = 0
        for user_id in user_ids:
            try:
                await context.bot.send_message(chat_id=user_id, text=message_text, parse_mode='Markdown')
                success += 1
            except Exception as e:
                logger.warning(f"Не удалось отправить {user_id}: {e}")
                failed += 1

        await update.message.reply_text(f"✅ Рассылка отправлена!\nУспешно: {success}, Ошибок: {failed}")
    except Exception as e:
        logger.error(f"Ошибка рассылки: {e}")
        await update.message.reply_text("❌ Ошибка при отправке рассылки.")

# ======================
# ОБРАБОТЧИКИ ПОЛЬЗОВАТЕЛЕЙ
# ======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()

    welcome_text = (
        "🌟 *Привет, друг! Добро пожаловать в Freshly Bot!*\n\n"
        "Я — твой личный помощник в борьбе с пищевыми отходами 🌍\n\n"
        "✨ *Что я умею:*\n"
        "✅ Отслеживать сроки годности продуктов\n"
        "✅ Напоминать за 3 и за 1 день до истечения срока\n"
        "✅ Предлагать рецепты на основе твоих продуктов\n"
        "✅ Показывать статистику твоих успехов\n\n"
        "💎 В Premium-версии: уведомления за 7 дней, неограниченные продукты, экспорт, умные рецепты и многое другое!\n\n"
        "Вместе мы спасём еду, сэкономим деньги и поможем планете! 💚\n\n"
        "Выбери действие ниже и начни свой путь к осознанному потреблению!"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = ?", (user_id,))
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = ? AND notified = TRUE", (user_id,))
        saved = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = ? AND expires_at >= ?", (user_id, datetime.now().date().isoformat()))
        active = cursor.fetchone()[0]

    text = (
        "📊 *Твоя личная статистика:*\n\n"
        f"📦 Всего добавлено продуктов: *{total}*\n"
        f"✅ Сейчас в холодильнике: *{active}*\n"
        f"🛡️ Успешно спасено от просрочки: *{saved}*\n\n"
        "Ты молодец! Каждый продукт, который ты не выбросил — это победа над пищевыми отходами! 🌱"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())

def load_recipes():
    try:
        with open('recipes.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return [
            {"name": "Простой омлет", "ingredients": ["Яйца"], "steps": "Взбей 2 яйца, добавь соль и жарь на сковороде 3-4 минуты."},
            {"name": "Фруктовый микс", "ingredients": ["Фрукты"], "steps": "Нарежь любимые фрукты и наслаждайся свежим десертом!"},
            {"name": "Сырный сэндвич", "ingredients": ["Сыр", "Хлеб"], "steps": "Положи ломтик сыра между двумя кусочками хлеба и поджарь на сковороде до золотистой корочки."}
        ]

RECIPES = load_recipes()

async def recipes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_premium_user = is_premium(user_id)

    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM products WHERE user_id = ? AND notified = FALSE", (user_id,))
        products = [row[0].lower() for row in cursor.fetchall()]

    if not products:
        await update.message.reply_text(
            "📦 У тебя пока нет активных продуктов.\n\n"
            "Добавь хотя бы один продукт через '📸 Добавить по фото' или '✍️ Добавить вручную', и я подскажу, что из него можно приготовить! 👨‍🍳",
            reply_markup=get_main_menu_keyboard()
        )
        return

    suitable_recipes = []

    if is_premium_user:
        for recipe in RECIPES:
            matched = [ing for ing in recipe.get("ingredients", []) if any(ing.lower() in p for p in products)]
            if len(matched) >= 2:
                suitable_recipes.append(recipe)
        if not suitable_recipes:
            suitable_recipes = [r for r in RECIPES if any(ing.lower() in p for p in products for ing in r.get("ingredients", []))][:2]
    else:
        suitable_recipes = [
            r for r in RECIPES
            if any(ing.lower() in p for p in products for ing in r.get("ingredients", []))
        ][:2]

    text = "👨‍🍳 *Рецепты специально для тебя:*\n\n"
    for r in suitable_recipes[:2]:
        text += f"🔹 *{r['name']}*\nИнгредиенты: {', '.join(r.get('ingredients', []))}\n{r.get('steps', '')}\n\n"

    text += "Готовь с удовольствием и не забывай делиться своими кулинарными успехами! 😋"

    if not is_premium_user:
        text += "\n💎 Хочешь **рецепты по 2+ ингредиентам**? Получи Premium!"

    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())

async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_premium(user_id):
        await update.message.reply_text(
            "Эта функция доступна только в 💎 Premium.\n\nНажми «💎 Получить Premium» в меню!",
            reply_markup=get_main_menu_keyboard()
        )
        return

    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, purchase_date, expires_at, expiration_days
            FROM products WHERE user_id = ?
            ORDER BY expires_at
        ''', (user_id,))
        rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("У тебя пока нет продуктов для экспорта.", reply_markup=get_main_menu_keyboard())
        return

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Название", "Дата покупки", "Истекает", "Срок (дней)"])
    writer.writerows(rows)
    output.seek(0)

    await update.message.reply_document(
        document=output.getvalue().encode("utf-8-sig"),
        filename="freshly_products.csv",
        caption="📊 Вот твой экспорт! Можно открыть в Excel или Google Таблицах."
    )

# ✅ ИСПРАВЛЕННЫЙ premium_handler — без риска ошибок
async def premium_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    creator_link = "https://t.me/freshlyai_support"
    user_id = update.effective_user.id
    # Экранируем user_id как строку, чтобы избежать ошибок форматирования
    text = (
        "💎 *Freshly Premium — выбери план!*\n\n"
        "🔹 **7 дней** — 99 ₽ (отлично для теста)\n"
        "🔹 **30 дней** — 249 ₽ (лучшее соотношение)\n"
        "🔹 **365 дней** — 799 ₽ (~2.2 ₽ в день!)\n\n"
        "✨ Что входит:\n"
        "• Уведомления за 7 / 5 / 3 / 1 день\n"
        "• Неограниченные продукты\n"
        "• Умные рецепты по 2+ ингредиентам\n"
        "• Экспорт списка в CSV\n"
        "• История просрочек\n\n"
        f"📩 Напиши мне: {creator_link}\n"
        f"Укажи желаемый срок и свой ID: `{user_id}`"
    )
    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

# --- Добавление продукта (остальное без изменений) ---
# ... [все функции добавления, фото, список, просрочка, помощь и т.д. — как в предыдущем коде] ...

async def start_add_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_premium(user_id):
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = ? AND notified = FALSE", (user_id,))
            active_count = cursor.fetchone()[0]
            if active_count >= 10:
                await update.message.reply_text(
                    "🚫 Бесплатная версия позволяет отслеживать до 10 активных продуктов.\n\n"
                    "💎 Хочешь больше? Получи Premium: нажми «💎 Получить Premium» в меню!",
                    reply_markup=get_main_menu_keyboard()
                )
                return ConversationHandler.END

    await update.message.reply_text(
        "✏️ *Шаг 1/3: Название продукта*\n\n"
        "Напиши, что ты купил(а)! Например: *Молоко*, *Сыр Моцарелла*, *Куриная грудка*.\n\n"
        "Не переживай — я помогу отслеживать срок годности! 💪",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_PRODUCT_NAME

async def choose_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        return await cancel(update, context)
    if not user_input:
        await update.message.reply_text("Пожалуйста, введи корректное название продукта. Например: *Йогурт*.", parse_mode='Markdown')
        return CHOOSING_PRODUCT_NAME
    context.user_data['product_name'] = user_input
    await update.message.reply_text(
        "📅 *Шаг 2/3: Дата покупки*\n\n"
        "В каком формате удобно ввести дату?\n"
        "• ДД.ММ.ГГГГ (например, 25.04.2025)\n"
        "• ГГГГ-ММ-ДД (например, 2025-04-25)\n\n"
        "Выбери любой — я пойму! 😉",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_PURCHASE_DATE

async def choose_purchase_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        return await cancel(update, context)
    parsed_date = parse_date(user_input)
    if parsed_date is None:
        await update.message.reply_text(
            "😔 Кажется, я не понял дату.\n\n"
            "Попробуй ещё раз в одном из форматов:\n"
            "• 25.04.2025\n• 2025-04-25",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return CHOOSING_PURCHASE_DATE
    context.user_data['purchase_date'] = parsed_date.isoformat()
    await update.message.reply_text(
        "📆 *Шаг 3/3: Дата истечения срока*\n\n"
        "Когда продукт станет непригодным для употребления?\n"
        "Введи дату в том же формате, что и покупку.",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return CHOOSING_EXPIRATION_DATE

async def choose_expiration_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        return await cancel(update, context)
    parsed_date = parse_date(user_input)
    if parsed_date is None:
        await update.message.reply_text(
            "😔 Не получилось распознать дату.\n\n"
            "Попробуй, например: *30.04.2025* или *2025-04-30*",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return CHOOSING_EXPIRATION_DATE

    today = datetime.now().date()
    if parsed_date < today:
        await update.message.reply_text(
            "❌ Ой! Дата истечения не может быть в прошлом.\n\n"
            "Проверь, пожалуйста, и введи корректную дату.",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return CHOOSING_EXPIRATION_DATE

    purchase_date = datetime.strptime(context.user_data['purchase_date'], '%Y-%m-%d').date()
    expiration_days = (parsed_date - purchase_date).days
    if expiration_days < 0:
        await update.message.reply_text(
            "❌ Дата истечения не может быть раньше даты покупки.\n\n"
            "Давай попробуем заново!",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

    user_id = update.effective_user.id
    product_name = context.user_data['product_name']
    purchase_date_str = context.user_data['purchase_date']
    expires_at_str = parsed_date.isoformat()

    try:
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO products (user_id, name, purchase_date, expiration_days, added_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, product_name, purchase_date_str, expiration_days, datetime.now().isoformat(), expires_at_str))
            conn.commit()

        schedule_notifications(context, user_id, product_name, expiration_days)

        success_text = (
            f"🎉 *Ура! Продукт добавлен!*\n\n"
            f"🔹 *Название:* {product_name}\n"
            f"📅 *Куплено:* {purchase_date_str}\n"
            f"📆 *Истекает:* {expires_at_str}\n"
            f"⏳ *Срок годности:* {expiration_days} дней\n\n"
            "🔔 Я напомню тебе:\n"
            "• За 3 дня до окончания срока\n"
            "• И за 1 день — последнее напоминание!\n"
        )

        if is_premium(user_id):
            success_text += "✨ А ещё — за 7 дней, ведь ты в Premium!\n\n"
        else:
            success_text += "\n💎 Хочешь уведомления **за 7 дней**? Получи Premium!\n"

        success_text += "Ты делаешь мир лучше — спасибо! 🌍"

        await update.message.reply_text(
            success_text,
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения продукта: {e}")
        await update.message.reply_text("❌ Произошла ошибка при сохранении. Попробуй ещё раз.", reply_markup=get_main_menu_keyboard())

    return ConversationHandler.END

async def recognize_product(photo_path: str) -> str:
    products = ["Молоко", "Хлеб", "Яйца", "Сыр", "Йогурт", "Мясо", "Рыба", "Овощи", "Фрукты", "Курица", "Говядина", "Помидоры", "Огурцы"]
    return random.choice(products)

async def start_add_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_premium(user_id):
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = ? AND notified = FALSE", (user_id,))
            active_count = cursor.fetchone()[0]
            if active_count >= 10:
                await update.message.reply_text(
                    "🚫 Бесплатная версия позволяет отслеживать до 10 активных продуктов.\n\n"
                    "💎 Хочешь больше? Получи Premium: нажми «💎 Получить Premium» в меню!",
                    reply_markup=get_main_menu_keyboard()
                )
                return ConversationHandler.END

    await update.message.reply_text(
        "📸 *Добавление по фото*\n\n"
        "Отправь мне фото упаковки продукта — я постараюсь распознать его название!\n\n"
        "💡 Совет: сделай чёткое фото этикетки при хорошем освещении.",
        parse_mode='Markdown',
        reply_markup=get_cancel_keyboard()
    )
    return PHOTO_RECOGNITION

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        os.makedirs("photos", exist_ok=True)
        
        photo_file = await update.message.photo[-1].get_file()
        file_id = update.message.photo[-1].file_id
        photo_hash = file_id[-10:]
        photo_path = f"photos/photo_{user_id}_{photo_hash}.jpg"
        await photo_file.download_to_drive(photo_path)

        product_name = await recognize_product(photo_path)
        os.remove(photo_path)

        context.user_data['product_name'] = product_name
        await update.message.reply_text(
            f"🤖 *Отлично! Я распознал: {product_name}*\n\n"
            "📅 Теперь введи дату покупки (например, 25.04.2025):",
            parse_mode='Markdown',
            reply_markup=get_cancel_keyboard()
        )
        return CHOOSING_PURCHASE_DATE

    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при обработке фото.\n\n"
            "Попробуй отправить фото ещё раз или добавь продукт вручную.",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

async def list_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, expires_at FROM products WHERE user_id = ? ORDER BY expires_at
        ''', (user_id,))
        products = cursor.fetchall()

    if not products:
        await update.message.reply_text(
            "📦 *Твой холодильник пуст!*\n\n"
            "Добавь первый продукт — и я помогу тебе не пропустить срок его годности! 💚",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return

    text = "📋 *Твои продукты:*\n\n"
    today = datetime.now().date()
    for name, expires_at in products:
        exp_date = datetime.strptime(expires_at, '%Y-%m-%d').date()
        days_left = (exp_date - today).days
        if days_left < 0:
            status = "🔴 ПРОСРОЧЕНО"
        elif days_left == 0:
            status = "🔴 Истекает сегодня!"
        elif days_left == 1:
            status = "🟠 Истекает завтра"
        elif days_left <= 3:
            status = f"🟡 Истекает через {days_left} дня"
        else:
            status = f"🟢 Ещё {days_left} дней"
        text += f"• *{name}* — {status}\n"

    text += "\n💡 Совет: регулярно проверяй этот список, чтобы ничего не пропустить!"

    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())

async def show_expired_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = datetime.now().date().isoformat()
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, expires_at FROM products 
            WHERE user_id = ? AND expires_at <= ? AND notified = FALSE
        ''', (user_id, today))
        expired = cursor.fetchall()

    if not expired:
        text = "✅ *Поздравляю! У тебя нет просроченных продуктов!*\n\nТы отлично справляешься! 🌟"
    else:
        text = "🚨 *Просроченные продукты:*\n\n"
        for name, expires_at in expired:
            text += f"• *{name}* — срок истёк {expires_at}\n"
        text += "\n❌ Пожалуйста, выбрось их, чтобы избежать риска для здоровья!"

    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard())

async def clear_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
        conn.commit()
    await update.message.reply_text(
        "🗑️ *Все продукты удалены!*\n\n"
        "Твой список чист. Готов добавить новые продукты? 😊",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "ℹ️ *Как пользоваться Freshly Bot:*\n\n"
        "1️⃣ *Добавить продукт:*\n"
        "   • Нажми '📸 Добавить по фото' и отправь снимок упаковки.\n"
        "   • Или '✍️ Добавить вручную' — введи название и даты.\n\n"
        "2️⃣ *Получать напоминания:*\n"
        "   • Я автоматически напомню за 3 и за 1 день до окончания срока!\n"
        "   • 💎 Premium: ещё за 7 и 5 дней!\n\n"
        "3️⃣ *Использовать функции:*\n"
        "   • '📋 Мои продукты' — текущий список\n"
        "   • '📊 Статистика' — твои достижения\n"
        "   • '👨‍🍳 Рецепты' — идеи для приготовления\n"
        "   • '💎 Получить Premium' — расширенные возможности\n\n"
        "🌱 Вместе мы сокращаем пищевые отходы и заботимся о планете!"
    )
    await update.message.reply_text(
        help_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '✅ Операция отменена.\n\nВозвращайся, когда будешь готов(а) добавить продукт! 💚',
        reply_markup=get_main_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

async def handle_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    handlers = {
        "🏠 Главное меню": start,
        "❌ Отмена": cancel,
        "📸 Добавить по фото": start_add_by_photo,
        "✍️ Добавить вручную": start_add_manually,
        "📋 Мои продукты": list_products_handler,
        "🚨 Просроченные": show_expired_handler,
        "📊 Статистика": stats_handler,
        "👨‍🍳 Рецепты": recipes_handler,
        "💎 Получить Premium": premium_handler,  # ✅ РАБОТАЕТ!
        "🗑️ Очистить всё": clear_products_handler,
        "ℹ️ Помощь": help_handler,
    }

    if text in handlers:
        if text in ["🏠 Главное меню", "❌ Отмена"]:
            return await handlers[text](update, context)
        else:
            return await handlers[text](update, context)
    else:
        await update.message.reply_text("Пожалуйста, используй кнопки меню.", reply_markup=get_main_menu_keyboard())
        return ConversationHandler.END

# --- Основная функция ---
def main():
    init_db()

    application = Application.builder().token(TOKEN).build()

    manual_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✍️ Добавить вручную$"), start_add_manually)],
        states={
            CHOOSING_PRODUCT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_product_name)],
            CHOOSING_PURCHASE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_purchase_date)],
            CHOOSING_EXPIRATION_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_expiration_date)],
        },
        fallbacks=[MessageHandler(filters.Regex("^(❌ Отмена|🏠 Главное меню)$"), cancel)],
        allow_reentry=True
    )

    photo_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📸 Добавить по фото$"), start_add_by_photo)],
        states={
            PHOTO_RECOGNITION: [MessageHandler(filters.PHOTO, handle_photo)],
            CHOOSING_PURCHASE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_purchase_date)],
            CHOOSING_EXPIRATION_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_expiration_date)],
        },
        fallbacks=[MessageHandler(filters.Regex("^(❌ Отмена|🏠 Главное меню)$"), cancel)],
        allow_reentry=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(manual_conv)
    application.add_handler(photo_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_choice))

    # 💎 Премиум и промокоды
    application.add_handler(CommandHandler("promo", activate_promo))
    application.add_handler(CommandHandler("give_premium", give_premium))
    application.add_handler(CommandHandler("list_promos", list_promo_codes))
    application.add_handler(CommandHandler("create_promo", create_promo_code))
    application.add_handler(CommandHandler("export", export_handler))
    application.add_handler(CommandHandler("broadcast", broadcast))  # ✅ РАССЫЛКА

    # Ежедневная проверка просрочки в 9:00
    application.job_queue.run_daily(check_expired_daily, time(hour=9, minute=0))

    # 🔁 Восстановление задач при запуске
    restore_scheduled_jobs(application)

    logger.info("🚀 Freshly Bot запущен!")
    application.run_polling()

if __name__ == '__main__':
    main()
