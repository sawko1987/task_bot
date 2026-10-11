import os

class Config:
    # Токен бота в мессенджере MAX
    MAX_BOT_TOKEN: str = os.getenv("MAX_BOT_TOKEN", "")

    # Базовый URL API мессенджера MAX (официальный эндпоинт платформы)
    MAX_API_BASE_URL: str = os.getenv("MAX_API_BASE_URL", "https://platform-api2.max.ru").rstrip("/")

    # Настройки Yandex Database (YDB) Serverless
    YDB_ENDPOINT: str = os.getenv("YDB_ENDPOINT", "")
    YDB_DATABASE: str = os.getenv("YDB_DATABASE", "")

    # Использовать ли локальный SQLite (для локальной отладки без YDB)
    USE_SQLITE: bool = os.getenv("USE_SQLITE", "false").lower() in ("true", "1", "yes")
    SQLITE_PATH: str = os.getenv("SQLITE_PATH", "tasks_local.db")

    # Секретный токен для проверки вебхука (опционально для защиты эндпоинта)
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")

    # Список ID пользователей с правами админа по умолчанию (через запятую)
    # Используется, если API мессенджера не возвращает статус администратора чата
    DEFAULT_ADMIN_IDS: set = {
        uid.strip() for uid in os.getenv("DEFAULT_ADMIN_IDS", "").split(",") if uid.strip()
    }

config = Config()
