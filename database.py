import sqlite3
import json
from datetime import datetime, timedelta
DB_NAME = 'products.db'
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.init_db()
        self.load_products_data()
        self.load_recipes_data()
    
    def init_db(self):
        """Инициализация базы данных"""
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    premium INTEGER DEFAULT 0,
                    premium_until DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    product_name TEXT,
                    purchase_date DATE,
                    expiration_date DATE,
                    notified INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            conn.commit()
    
    def load_products_data(self):
        """Загрузка данных о продуктах"""
        try:
            with open('products.json', 'r', encoding='utf-8') as f:
                self.products_data = json.load(f)
        except FileNotFoundError:
            logger.warning("products.json не найден, справочник продуктов будет пустым.")
            self.products_data = {}
    
    def load_recipes_data(self):
        """Загрузка данных о рецептах"""
        with open('recipes.json', 'r', encoding='utf-8') as f:
            self.recipes_data = json.load(f)
    
    def add_user(self, user_id, username):
        """Добавление пользователя"""
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO users (user_id, username) 
                VALUES (?, ?)
            ''', (user_id, username))
            conn.commit()
    
    def add_product(self, user_id, product_name, purchase_date):
        """Добавление продукта"""
        if product_name not in self.products_data:
            return False
        
        shelf_life = self.products_data[product_name]['shelf_life']
        expiration_date = purchase_date + timedelta(days=shelf_life)
        
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO products (user_id, product_name, purchase_date, expiration_date)
                VALUES (?, ?, ?, ?)
            ''', (user_id, product_name, purchase_date.date(), expiration_date.date()))
            conn.commit()
        
        return True
    
    def get_user_products(self, user_id):
        """Получение продуктов пользователя"""
        with sqlite3.connect(DB_NAME) as conn:
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
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM products WHERE user_id = ?', (user_id,))
            return cursor.fetchone()[0]
    
    def clear_user_products(self, user_id):
        """Очистка продуктов пользователя"""
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM products WHERE user_id = ?', (user_id,))
            conn.commit()
    
    def get_expiring_products(self):
        """Получение продуктов, срок которых истекает завтра"""
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT p.user_id, u.username, p.product_name, p.expiration_date
                FROM products p
                JOIN users u ON p.user_id = u.user_id
                WHERE p.expiration_date = ? AND p.notified = 0
            ''', (tomorrow,))
            return cursor.fetchall()
    
    def mark_as_notified(self, user_id, product_name):
        """Пометить продукт как уведомленный"""
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE products 
                SET notified = 1 
                WHERE user_id = ? AND product_name = ?
            ''', (user_id, product_name))
            conn.commit()
    
    def get_recipes_by_category(self, category):
        """Получение рецептов по категории"""
        return self.recipes_data.get(category, [])
    
    def get_product_category(self, product_name):
        """Получение категории продукта"""
        if product_name in self.products_data:
            return self.products_data[product_name]['category']
        return None
