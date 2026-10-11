import base64
import json
import logging
from config import config
from max_api import max_api
from bot import bot

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    """
    Точка входа (Handler) для Yandex Cloud Functions.
    Принимает HTTP-запрос вебхука от мессенджера MAX.
    """
    try:
        http_method = event.get("httpMethod", "POST")
        if http_method == "GET":
            # Проверка работоспособности функции
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"status": "healthy", "service": "max-task-bot"})
            }

        # Получаем тело запроса
        body_raw = event.get("body", "{}")
        if event.get("isBase64Encoded", False):
            body_raw = base64.b64decode(body_raw).decode("utf-8")

        # Опциональная проверка секретного токена вебхука
        if config.WEBHOOK_SECRET:
            headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
            received_secret = (
                headers.get("x-max-bot-secret") or 
                headers.get("x-telegram-bot-api-secret-token") or
                headers.get("x-webhook-secret")
            )
            if received_secret != config.WEBHOOK_SECRET:
                logger.warning("Отклонён вебхук с неверным секретным токеном")
                return {
                    "statusCode": 403,
                    "body": json.dumps({"error": "Forbidden"})
                }

        payload = json.loads(body_raw) if isinstance(body_raw, str) else body_raw
        logger.info(f"Получен вебхук: {json.dumps(payload, ensure_ascii=False)[:300]}")

        # Парсим сообщение из формата мессенджера MAX
        incoming_msg = max_api.parse_webhook_event(payload)
        if incoming_msg:
            # Обрабатываем команду бота
            bot.handle_message(incoming_msg)

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "ok"})
        }

    except Exception as e:
        logger.exception(f"Ошибка при обработке вебхука: {e}")
        # Возвращаем 200, чтобы мессенджер не спамил повторами при внутренней ошибке логики
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "error_handled", "message": str(e)})
        }
