# health_check.py
from flask import Flask
import threading

app = Flask(__name__)

@app.route('/health')
def health_check():
    """Эндпоинт для проверки здоровья приложения"""
    return {'status': 'ok', 'message': 'Freshly Bot is running'}, 200

def run_health_check():
    """Запуск health check сервера в отдельном потоке"""
    app.run(host='0.0.0.0', port=8081, debug=False, use_reloader=False)

# Запуск health check в основном файле бота
def start_health_check():
    """Запуск health check в фоновом режиме"""
    health_thread = threading.Thread(target=run_health_check, daemon=True)
    health_thread.start()
