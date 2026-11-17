import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")  # ключ Fernet, см. README
DB_PATH = os.environ.get("DB_PATH", "/data/bot.db")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "/data/backups")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)  # Telegram id админа для алертов
ELJUR_BASE = os.environ.get("ELJUR_BASE", "https://kmpo.eljur.ru")
HEARTBEAT = os.environ.get("HEARTBEAT", "/tmp/heartbeat")
REMIND_HOUR = int(os.environ.get("REMIND_HOUR", "9"))  # по Москве
# Элжур блокирует клиентов с «программными» User-Agent ("Crawler detected"), поэтому представляемся браузером.
ELJUR_UA = os.environ.get("ELJUR_UA", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
