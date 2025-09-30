import logging
import sqlite3
import os
import random
from datetime import datetime, timedelta, time
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
)
import json

# 🔑 ВСТРОЕННЫЙ ТОКЕН (ТОЛЬКО ДЛЯ ТЕСТА!)
TOKEN = "8123646923:AAERiVrcFss2IubX3SMUJI12c9qHbX2KRgA"

# Состояния
(
    PHOTO_RECOGNITION,
    CHOOSING_PRODUCT_NAME,
    CHOOSING_PURCHASE_DATE,
    CHOOSING_EXPIRATION_DATE,
    ENTERING_PROMO_CODE
) = range(5)

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
                    is_premium BOOLEAN DEFAULT FALSE,
                    premium_until TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    days INTEGER NOT NULL,
                    max_uses INTEGER,
                    uses_count INTEGER DEFAULT 0
                )
            ''')
            conn.commit()
        logger.info("✅ База данных инициализирована")
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        exit(1)

def ensure_promo_codes_exist():
    """Создаёт промокоды при первом запуске"""
    promo_list = []
    for i in range(1, 6):
        promo_list.append((f"FRESHW{i}", 7, 1))   # Неделя
        promo_list.append((f"FRESHM{i}", 30, 1))  # Месяц
        promo_list.append((f"FRESHY{i}", 365, 1)) # Год

    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        for code, days, max_uses in promo_list:
            cursor.execute('''
                INSERT OR IGNORE INTO promo_codes (code, days, max_uses)
                VALUES (?, ?, ?)
            ''', (code, days, max_uses))
        conn.commit()
    logger.info("✅ Промокоды созданы")

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

def get_user_premium_status(user_id: int) -> bool:
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_premium, premium_until FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            return False
        is_premium, premium_until_str = row
        if not is_premium:
            return False
        if premium_until_str:
            premium_until = datetime.fromisoformat(premium_until_str)
            if datetime.now() < premium_until:
                return True
            else:
                cursor.execute("UPDATE users SET is_premium = FALSE, premium_until = NULL WHERE user_id = ?", (user_id,))
                conn.commit()
                return False
        return False

def activate_premium(user_id: int, days: int):
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        premium_until = datetime.now() + timedelta(days=days)
        cursor.execute("""
            INSERT INTO users (user_id, is_premium, premium_until)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                is_premium = TRUE,
                premium_until = ?
        """, (user_id, True, premium_until.isoformat(), premium_until.isoformat()))
        conn.commit()

def get_main_menu_keyboard(is_premium: bool = False):
    keyboard = [
        ["📸 Добавить по фото", "✍️ Добавить вручную"],
        ["📋 Мои продукты", "🚨 Просроченные"],
        ["🗑️ Очистить всё", "ℹ️ Помощь"]
    ]
    if is_premium:
        keyboard.append(["👨‍🍳 Рецепты", "📊 Статистика"])
        keyboard.append(["📤 Экспорт", "💎 Премиум"])
    else:
        keyboard.append(["💎 Премиум", "🎟️ Промокод"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup([["❌ Отмена", "🏠 Главное меню"]], resize_keyboard=True, one_time_keyboard=True)

# ======================
# УВЕДОМЛЕНИЯ (ИСПРАВЛЕННЫЕ!)
# ======================

async def send_notification_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.data["user_id"]
    product_name = job.data["product_name"]
    days_left = job.data.get("days_left", 1)

    try:
        # Проверяем, существует ли продукт
        with sqlite3.connect('products.db') as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM products WHERE user_id = ? AND name = ? AND notified = FALSE", (user_id, product_name))
            if not cursor.fetchone():
                return

        # Базовое сообщение
        if days_left == 1:
            text = f"⚠️ *{product_name}* испортится завтра!\n"
        else:
            text = f"⏳ *{product_name}* испортится через {days_left} дней!\n"

        # Для премиум-пользователей — добавляем рецепт
        if get_user_premium_status(user_id):
            suitable_recipes = [
                r for r in RECIPES 
                if any(ing.lower() in product_name.lower() for ing in r.get("ingredients", []))
            ]
            if suitable_recipes:
                recipe = suitable_recipes[0]
                text += f"\n👨‍🍳 *Рецепт:* {recipe['name']}\n{recipe.get('steps', '')}"
            else:
                text += "\nМожет, пора готовить?"

        await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
        logger.info(f"✅ Уведомление отправлено пользователю {user_id}")
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
                        text=f"🚨 *ПРОСРОЧЕНО:* {name} (срок истёк {expires_at})\nРекомендуем выбросить!",
                        parse_mode='Markdown'
                    )
                    cursor.execute("UPDATE products SET notified = TRUE WHERE user_id = ? AND name = ?", (user_id, name))
                    conn.commit()
                except Exception as e:
                    logger.error(f"Ошибка отправки просрочки {user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка в check_expired_daily: {e}")

def schedule_notifications(context: ContextTypes.DEFAULT_TYPE, user_id: int, product_name: str, expiration_days: int):
    is_premium = get_user_premium_status(user_id)

    # Уведомление за 1 день (всем)
    if expiration_days >= 1:
        notify_time = datetime.now() + timedelta(days=expiration_days - 1)
        if notify_time > datetime.now():
            context.job_queue.run_once(
                send_notification_job,
                when=notify_time,
                data={"user_id": user_id, "product_name": product_name, "days_left": 1},
                name=f"notify_{user_id}_{product_name}_1d"
            )

    # Уведомление за 3 дня (только премиум)
    if is_premium and expiration_days > 3:
        notify_time = datetime.now() + timedelta(days=expiration_days - 3)
        if notify_time > datetime.now():
            context.job_queue.run_once(
                send_notification_job,
                when=notify_time,
                data={"user_id": user_id, "product_name": product_name, "days_left": 3},
                name=f"notify_{user_id}_{product_name}_3d"
            )

# ======================
# ОБРАБОТЧИКИ ПРЕМИУМ-ФУНКЦИЙ
# ======================

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not get_user_premium_status(user_id):
        await update.message.reply_text("📊 Эта функция доступна только в Премиуме!", reply_markup=get_main_menu_keyboard())
        return

    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = ?", (user_id,))
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = ? AND notified = TRUE AND expires_at > ?", (user_id, datetime.now().date().isoformat()))
        saved = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM products WHERE user_id = ? AND expires_at >= ?", (user_id, datetime.now().date().isoformat()))
        active = cursor.fetchone()[0]

    text = (
        "📊 *Ваша статистика:*\n\n"
        f"📦 Всего добавлено: {total}\n"
        f"✅ Активных продуктов: {active}\n"
        f"🛡️ Спасено от просрочки: {saved}\n\n"
        "Продолжайте в том же духе! 🌱"
    )
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard(True))

async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not get_user_premium_status(user_id):
        await update.message.reply_text("📤 Эта функция доступна только в Премиуме!", reply_markup=get_main_menu_keyboard())
        return

    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, purchase_date, expires_at FROM products 
            WHERE user_id = ? ORDER BY expires_at
        ''', (user_id,))
        products = cursor.fetchall()

    if not products:
        await update.message.reply_text("Нет продуктов для экспорта.", reply_markup=get_main_menu_keyboard(True))
        return

    text = "📋 *Ваши продукты (экспорт):*\n\n"
    for name, purchase, expires in products:
        text += f"• {name} | Куплено: {purchase} | Истекает: {expires}\n"

    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard(True))

# ======================
# ОСТАЛЬНЫЕ ОБРАБОТЧИКИ
# ======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_premium = get_user_premium_status(user_id)
    welcome_text = (
        "🌟 *Добро пожаловать в Freshly Bot!*\n\n"
        "Я помогу вам не выбрасывать еду и экономить деньги!\n\n"
        "✨ *Премиум-функции:* рецепты, статистика, экспорт и ранние уведомления."
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard(is_premium)
    )

async def premium_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_premium = get_user_premium_status(user_id)
    if is_premium:
        await update.message.reply_text(
            "💎 *Вы уже в Премиуме!*\n\n"
            "✨ Доступны:\n"
            "• 👨‍🍳 Рецепты по вашим продуктам\n"
            "• ⏳ Уведомления за 3 дня\n"
            "• 📊 Статистика использования\n"
            "• 📤 Экспорт списка\n\n"
            "Спасибо, что поддерживаете бота!",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard(True)
        )
    else:
        await update.message.reply_text(
            "💎 *Попробуйте Премиум бесплатно на 1 день!*\n\n"
            "Или активируйте полную версию с помощью промокода!",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard(False)
        )

async def recipes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not get_user_premium_status(user_id):
        await update.message.reply_text("👨‍🍳 Эта функция доступна только в Премиуме!", reply_markup=get_main_menu_keyboard())
        return

    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM products WHERE user_id = ? AND notified = FALSE", (user_id,))
        products = [row[0] for row in cursor.fetchall()]

    if not products:
        await update.message.reply_text("У вас нет активных продуктов для рецептов.", reply_markup=get_main_menu_keyboard(True))
        return

    suitable_recipes = []
    for recipe in RECIPES:
        if any(ing.lower() in p.lower() for p in products for ing in recipe.get("ingredients", [])):
            suitable_recipes.append(recipe)

    if not suitable_recipes:
        suitable_recipes = RECIPES[:2]

    text = "👨‍🍳 *Рецепты для вас:*\n\n"
    for r in suitable_recipes[:2]:
        text += f"🔹 *{r['name']}*\nИнгредиенты: {', '.join(r.get('ingredients', []))}\n{r.get('steps', '')}\n\n"

    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard(True))

# --- Промокоды ---
async def promo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎟️ *Введите промокод:*\n\n"
        "Доступные промокоды:\n"
        "• Неделя: `FRESHW1`–`FRESHW5`\n"
        "• Месяц: `FRESHM1`–`FRESHM5`\n"
        "• Год: `FRESHY1`–`FRESHY5`",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([["🏠 Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return ENTERING_PROMO_CODE

async def handle_promo_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    code = update.message.text.strip()

    if code == "🏠 Главное меню":
        is_premium = get_user_premium_status(user_id)
        await start(update, context)
        return ConversationHandler.END

    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT days, max_uses, uses_count FROM promo_codes WHERE code = ?", (code,))
        row = cursor.fetchone()

        if not row:
            await update.message.reply_text("❌ Промокод не найден.", reply_markup=get_main_menu_keyboard(get_user_premium_status(user_id)))
            return ConversationHandler.END

        days, max_uses, uses_count = row

        if max_uses is not None and uses_count >= max_uses:
            await update.message.reply_text("❌ Промокод уже использован.", reply_markup=get_main_menu_keyboard(get_user_premium_status(user_id)))
            return ConversationHandler.END

        activate_premium(user_id, days)
        cursor.execute("UPDATE promo_codes SET uses_count = uses_count + 1 WHERE code = ?", (code,))
        conn.commit()

        await update.message.reply_text(
            f"🎉 *Премиум активирован на {days} дней!*\n\n"
            "Доступны: рецепты, статистика, экспорт и ранние уведомления!",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard(True)
        )
        return ConversationHandler.END

# --- Добавление продукта ---
async def start_add_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✏️ Введите название продукта:", reply_markup=get_cancel_keyboard())
    return CHOOSING_PRODUCT_NAME

async def choose_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        return await cancel(update, context)
    if not user_input:
        await update.message.reply_text("Введите корректное название.")
        return CHOOSING_PRODUCT_NAME
    context.user_data['product_name'] = user_input
    await update.message.reply_text("📅 Введите дату покупки (ДД.ММ.ГГГГ):", reply_markup=get_cancel_keyboard())
    return CHOOSING_PURCHASE_DATE

async def choose_purchase_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        return await cancel(update, context)
    parsed_date = parse_date(user_input)
    if parsed_date is None:
        await update.message.reply_text("Неверный формат даты.", reply_markup=get_cancel_keyboard())
        return CHOOSING_PURCHASE_DATE
    context.user_data['purchase_date'] = parsed_date.isoformat()
    await update.message.reply_text("📆 Введите дату истечения срока:", reply_markup=get_cancel_keyboard())
    return CHOOSING_EXPIRATION_DATE

async def choose_expiration_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    if user_input in ["❌ Отмена", "🏠 Главное меню"]:
        return await cancel(update, context)
    parsed_date = parse_date(user_input)
    if parsed_date is None:
        await update.message.reply_text("Неверный формат даты.", reply_markup=get_cancel_keyboard())
        return CHOOSING_EXPIRATION_DATE

    today = datetime.now().date()
    if parsed_date < today:
        await update.message.reply_text("Дата истечения не может быть в прошлом.", reply_markup=get_cancel_keyboard())
        return CHOOSING_EXPIRATION_DATE

    purchase_date = datetime.strptime(context.user_data['purchase_date'], '%Y-%m-%d').date()
    expiration_days = (parsed_date - purchase_date).days
    if expiration_days < 0:
        await update.message.reply_text("Дата истечения раньше покупки.", reply_markup=get_main_menu_keyboard(get_user_premium_status(update.effective_user.id)))
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

        await update.message.reply_text(
            f"🎉 Продукт добавлен!\n\n🔹 {product_name}\n📆 Истекает: {expires_at_str}\n⏳ Срок: {expiration_days} дней",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard(get_user_premium_status(user_id))
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        await update.message.reply_text("❌ Ошибка сохранения.", reply_markup=get_main_menu_keyboard(get_user_premium_status(user_id)))

    return ConversationHandler.END

# --- Фото ---
async def recognize_product(photo_path: str) -> str:
    products = ["Молоко", "Хлеб", "Яйца", "Сыр", "Йогурт", "Мясо", "Рыба", "Овощи", "Фрукты"]
    return random.choice(products)

async def start_add_by_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Отправьте фото продукта!", reply_markup=get_cancel_keyboard())
    return PHOTO_RECOGNITION

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    photo_file = await update.message.photo[-1].get_file()
    file_id = update.message.photo[-1].file_id
    photo_path = f"photos/photo_{user_id}_{file_id[-10:]}.jpg"
    os.makedirs("photos", exist_ok=True)
    await photo_file.download_to_drive(photo_path)
    product_name = await recognize_product(photo_path)
    os.remove(photo_path)

    context.user_data['product_name'] = product_name
    await update.message.reply_text(f"🤖 Распознан: {product_name}\n📅 Введите дату покупки:", reply_markup=get_cancel_keyboard())
    return CHOOSING_PURCHASE_DATE

# --- Прочие обработчики ---
async def list_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT name, expires_at FROM products WHERE user_id = ? ORDER BY expires_at
        ''', (user_id,))
        products = cursor.fetchall()

    if not products:
        await update.message.reply_text("📦 Пока нет продуктов.", reply_markup=get_main_menu_keyboard(get_user_premium_status(user_id)))
        return

    text = "📋 *Ваши продукты:*\n\n"
    today = datetime.now().date()
    for name, expires_at in products:
        exp_date = datetime.strptime(expires_at, '%Y-%m-%d').date()
        days_left = (exp_date - today).days
        if days_left < 0:
            status = "🔴 ПРОСРОЧЕНО"
        elif days_left == 0:
            status = "🔴 Сегодня!"
        elif days_left == 1:
            status = "🟠 Завтра"
        elif days_left <= 3:
            status = f"🟡 Через {days_left} дня"
        else:
            status = f"🟢 Ещё {days_left} дней"
        text += f"• *{name}* — {status}\n"

    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard(get_user_premium_status(user_id)))

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
        text = "✅ Просроченных продуктов нет!"
    else:
        text = "🚨 *Просроченные продукты:*\n\n"
        for name, expires_at in expired:
            text += f"• *{name}* — истек {expires_at}\n"
        text += "\n❌ Рекомендуем выбросить!"

    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=get_main_menu_keyboard(get_user_premium_status(user_id)))

async def clear_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with sqlite3.connect('products.db') as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
        conn.commit()
    await update.message.reply_text("🗑️ Все продукты удалены!", reply_markup=get_main_menu_keyboard(get_user_premium_status(user_id)))

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Как пользоваться:*\n\n"
        "1. Добавьте продукт по фото или вручную\n"
        "2. Бот напомнит за день до истечения\n"
        "3. В Премиуме — рецепты, статистика, экспорт и напоминания за 3 дня\n"
        "4. Используйте промокоды для активации Премиума!",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard(get_user_premium_status(update.effective_user.id))
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_premium = get_user_premium_status(update.effective_user.id)
    await update.message.reply_text('✅ Отменено.', reply_markup=get_main_menu_keyboard(is_premium))
    context.user_data.clear()
    return ConversationHandler.END

async def handle_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    is_premium = get_user_premium_status(user_id)

    handlers = {
        "🏠 Главное меню": start,
        "❌ Отмена": cancel,
        "📸 Добавить по фото": start_add_by_photo,
        "✍️ Добавить вручную": start_add_manually,
        "📋 Мои продукты": list_products_handler,
        "🚨 Просроченные": show_expired_handler,
        "🗑️ Очистить всё": clear_products_handler,
        "ℹ️ Помощь": help_handler,
        "💎 Премиум": premium_handler,
        "🎟️ Промокод": promo_handler,
        "👨‍🍳 Рецепты": recipes_handler,
        "📊 Статистика": stats_handler,
        "📤 Экспорт": export_handler,
    }

    if text in handlers:
        if text in ["🏠 Главное меню", "❌ Отмена"]:
            return await handlers[text](update, context)
        else:
            return await handlers[text](update, context)
    else:
        await update.message.reply_text("Используйте кнопки меню.", reply_markup=get_main_menu_keyboard(is_premium))
        return ConversationHandler.END

# --- Загрузка рецептов ---
def load_recipes():
    try:
        with open('recipes.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return [{"name": "Омлет", "ingredients": ["Яйца"], "steps": "Взбейте яйца и пожарьте."}]

RECIPES = load_recipes()

# --- Основная функция ---
def main():
    init_db()
    ensure_promo_codes_exist()

    application = Application.builder().token(TOKEN).build()

    # Обработчики диалогов
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

    promo_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎟️ Промокод$"), promo_handler)],
        states={
            ENTERING_PROMO_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_promo_code)],
        },
        fallbacks=[MessageHandler(filters.Regex("^🏠 Главное меню$"), start)],
        allow_reentry=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(manual_conv)
    application.add_handler(photo_conv)
    application.add_handler(promo_conv)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_choice))

    # Ежедневная проверка просрочки
    application.job_queue.run_daily(check_expired_daily, time(hour=9, minute=0))

    logger.info("🚀 Бот запущен")
    application.run_polling()

if __name__ == '__main__':
    main()
