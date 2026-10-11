import logging
from typing import Any, Dict, List, Optional
import requests
from config import config
from models import IncomingMessage

logger = logging.getLogger(__name__)

class MaxApiClient:
    """
    Официальный клиент для платформы API мессенджера MAX (https://dev.max.ru).
    Базовый URL: https://platform-api2.max.ru
    Авторизация: Header 'Authorization: <access_token>'
    """
    def __init__(self, token: str = None, base_url: str = None):
        self.token = token or config.MAX_BOT_TOKEN
        self.base_url = (base_url or config.MAX_API_BASE_URL).rstrip("/")
        self.session = requests.Session()
        # По документации dev.max.ru токен передаётся прямо в Authorization: <access_token>
        self.session.headers.update({
            "Authorization": self.token,
            "Content-Type": "application/json"
        })

    def parse_webhook_event(self, data: Dict[str, Any]) -> Optional[IncomingMessage]:
        """
        Парсер входящего объекта Update от платформы MAX.
        Объект Update содержит:
        - update_type: 'message_created', 'bot_started'
        - message: { id, chat: { id, type }, sender: { id, name, username }, text, attachments, link }
        """
        if not data:
            return None

        # Проверяем тип события Update
        update_type = data.get("update_type") or data.get("type", "")
        if update_type and update_type not in ("message_created", "bot_started", "message"):
            logger.info(f"Пропущено событие типа {update_type}")
            return None

        # Извлекаем message
        msg_data = data.get("message") or data.get("payload") or data

        message_id = str(msg_data.get("id") or msg_data.get("message_id") or "")

        # Чат
        chat_obj = msg_data.get("chat") or {}
        if isinstance(chat_obj, dict):
            chat_id = str(chat_obj.get("id") or chat_obj.get("chat_id") or "")
            chat_type = chat_obj.get("type", "group")
        else:
            chat_id = str(chat_obj)
            chat_type = "group"

        if not chat_id:
            return None

        # Отправитель (sender или from)
        sender_obj = msg_data.get("sender") or msg_data.get("from") or {}
        if isinstance(sender_obj, dict):
            user_id = str(sender_obj.get("id") or sender_obj.get("user_id", ""))
            user_name = sender_obj.get("name") or sender_obj.get("username") or user_id
            is_admin_flag = sender_obj.get("is_admin")
        else:
            user_id = str(sender_obj)
            user_name = user_id
            is_admin_flag = None

        text = (msg_data.get("text") or "").strip()

        # Вложения (attachments)
        screenshots = []
        raw_attachments = msg_data.get("attachments") or []
        for att in raw_attachments:
            if isinstance(att, dict):
                att_type = att.get("type", "")
                payload = att.get("payload") or {}
                url = payload.get("url") or payload.get("file_url") or att.get("url")
                if att_type in ("image", "photo", "file") and url:
                    screenshots.append(str(url))
            elif isinstance(att, str):
                screenshots.append(att)

        # Ссылка на сообщение (ответ/reply)
        reply_to_id = None
        reply_to_text = None
        reply_to_screenshots = []
        link_obj = msg_data.get("link") or msg_data.get("reply_to_message")
        if link_obj and isinstance(link_obj, dict):
            reply_msg = link_obj.get("message") or link_obj
            reply_to_id = str(reply_msg.get("id") or "")
            reply_to_text = (reply_msg.get("text") or "").strip()
            reply_atts = reply_msg.get("attachments") or []
            for att in reply_atts:
                if isinstance(att, dict):
                    payload = att.get("payload") or {}
                    url = payload.get("url") or payload.get("file_url") or att.get("url")
                    if url:
                        reply_to_screenshots.append(str(url))

        return IncomingMessage(
            message_id=message_id,
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
            text=text,
            screenshots=screenshots,
            reply_to_message_id=reply_to_id,
            reply_to_text=reply_to_text,
            reply_to_screenshots=reply_to_screenshots,
            is_admin=is_admin_flag
        )

    def send_message(self, chat_id: str, text: str, reply_to_id: Optional[str] = None) -> bool:
        """
        Отправка текстового сообщения в чат MAX:
        POST /messages?chat_id={chat_id}
        """
        url = f"{self.base_url}/messages"
        params = {"chat_id": chat_id}
        payload = {
            "text": text,
            "format": "markdown",
        }
        if reply_to_id:
            payload["link"] = {
                "type": "reply",
                "message_id": int(reply_to_id) if reply_to_id.isdigit() else reply_to_id
            }

        try:
            resp = self.session.post(url, params=params, json=payload, timeout=8)
            if resp.status_code >= 400:
                logger.warning(f"Ошибка отправки сообщения в MAX API: {resp.status_code} {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Исключение при вызове MAX send_message: {e}")
            return False

    def send_photos(self, chat_id: str, photos: List[str], caption: Optional[str] = None) -> bool:
        """
        Отправка сообщения с вложенными изображениями в чат MAX:
        POST /messages?chat_id={chat_id}
        с массивом attachments: [{"type": "image", "payload": {"url": ...}}]
        """
        url = f"{self.base_url}/messages"
        params = {"chat_id": chat_id}
        attachments = []
        for p in photos:
            attachments.append({
                "type": "image",
                "payload": {"url": p}
            })

        payload = {
            "text": caption or "",
            "format": "markdown",
            "attachments": attachments
        }
        try:
            resp = self.session.post(url, params=params, json=payload, timeout=12)
            if resp.status_code >= 400:
                logger.warning(f"Ошибка отправки фото в MAX API: {resp.status_code} {resp.text}")
                # Если платформа не приняла вложения ссылками, отправляем текстовыми ссылками
                fallback = f"{caption or ''}\n\n" + "\n".join([f"📷 Скриншот: {p}" for p in photos])
                return self.send_message(chat_id, fallback.strip())
            return True
        except Exception as e:
            logger.error(f"Исключение при вызове MAX send_photos: {e}")
            fallback = f"{caption or ''}\n\n" + "\n".join([f"📷 Скриншот: {p}" for p in photos])
            return self.send_message(chat_id, fallback.strip())

    def is_user_admin(self, chat_id: str, user_id: str, incoming_flag: Optional[bool] = None) -> bool:
        """
        Проверка прав администратора пользователя в чате:
        1. Если в самом объекте события передан is_admin.
        2. Если user_id указан в глобальном списке DEFAULT_ADMIN_IDS (config).
        3. Запрос GET /chats/{chat_id}/members/admins в API MAX.
        """
        if incoming_flag is True:
            return True

        if user_id in config.DEFAULT_ADMIN_IDS:
            return True

        # Проверка через API списка администраторов чата MAX
        try:
            url = f"{self.base_url}/chats/{chat_id}/members/admins"
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                admins = data.get("admins") or data.get("members") or (data if isinstance(data, list) else [])
                for admin in admins:
                    admin_id = str(admin.get("id") or admin.get("user_id") or admin.get("userId") or "")
                    if admin_id == str(user_id):
                        return True
                return False
        except Exception as e:
            logger.warning(f"Не удалось получить список админов через MAX API ({e})")

        # Если список админов не настроен в конфигурации, разрешаем,
        # чтобы бот работал до добавления ID
        if not config.DEFAULT_ADMIN_IDS:
            return True

        return False

    def set_webhook(self, webhook_url: str, secret: Optional[str] = None) -> bool:
        """
        Подписка на события через Webhook по спецификации MAX:
        POST https://platform-api2.max.ru/subscriptions
        """
        url = f"{self.base_url}/subscriptions"
        payload = {
            "url": webhook_url,
            "update_types": ["message_created", "bot_started"]
        }
        if secret:
            payload["secret"] = secret

        try:
            resp = self.session.post(url, json=payload, timeout=8)
            logger.info(f"Регистрация вебхука в MAX: {resp.status_code} {resp.text}")
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"Ошибка вызова POST /subscriptions: {e}")
            return False

max_api = MaxApiClient()
