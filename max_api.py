import logging
from typing import Any, Dict, List, Optional
import requests
import urllib3
from config import config
from models import IncomingMessage

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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
        self.session.verify = False
        # Токен передаётся прямо в заголовке Authorization: <access_token>
        self.session.headers.update({
            "Authorization": self.token,
            "Content-Type": "application/json"
        })

    def parse_webhook_event(self, data: Dict[str, Any]) -> Optional[IncomingMessage]:
        """
        Парсер входящего объекта Update от платформы MAX.
        Поддерживает официальную спецификацию OpenAPI dev.max.ru:
        - update_type: 'message_created', 'bot_started', 'message_callback'
        """
        if not data:
            return None

        update_type = data.get("update_type") or data.get("type", "")

        # 1. Событие нажатия пользователем кнопки 'Старт'
        if update_type == "bot_started":
            user_obj = data.get("user") or {}
            user_id = str(user_obj.get("user_id") or "")
            user_name = str(user_obj.get("first_name") or user_obj.get("name") or user_id)
            raw_chat_id = data.get("chat_id") or user_id
            return IncomingMessage(
                message_id="",
                chat_id=str(raw_chat_id),
                chat_type="dialog",
                user_id=user_id,
                user_name=user_name,
                text="/help",
                screenshots=[],
                is_admin=True
            )

        # 2. Событие нажатия на callback-кнопку в чате
        if update_type == "message_callback":
            callback_obj = data.get("callback") or {}
            callback_id = str(callback_obj.get("callback_id") or "")
            payload_str = str(callback_obj.get("payload") or "")
            user_obj = callback_obj.get("user") or {}
            user_id = str(user_obj.get("user_id") or "")
            user_name = str(user_obj.get("first_name") or user_obj.get("name") or user_id)
            msg_obj = data.get("message") or {}
            body_obj = msg_obj.get("body") or {}
            message_id = str(body_obj.get("mid") or msg_obj.get("id") or "")
            recipient_obj = msg_obj.get("recipient") or {}
            chat_type = str(recipient_obj.get("chat_type") or "chat")
            raw_chat_id = recipient_obj.get("chat_id")
            if raw_chat_id is None:
                raw_chat_id = recipient_obj.get("user_id") or user_id
            return IncomingMessage(
                message_id=message_id,
                chat_id=str(raw_chat_id),
                chat_type=chat_type,
                user_id=user_id,
                user_name=user_name,
                text="",
                screenshots=[],
                is_admin=True if chat_type == "dialog" else None,
                callback_id=callback_id,
                callback_payload=payload_str
            )

        if update_type and update_type not in ("message_created", "message"):
            logger.info(f"Пропущено событие типа {update_type}")
            return None

        # 3. Событие отправки сообщения (message_created)
        msg_data = data.get("message") or data.get("payload") or data
        body_obj = msg_data.get("body") or {}

        # Идентификатор сообщения (в MAX OpenAPI это body.mid)
        message_id = str(body_obj.get("mid") or msg_data.get("id") or msg_data.get("message_id") or "")

        # Получатель / чат (в MAX OpenAPI это msg.recipient { chat_id, chat_type, user_id })
        recipient_obj = msg_data.get("recipient") or {}
        chat_obj = msg_data.get("chat") or {}

        chat_type = recipient_obj.get("chat_type") or chat_obj.get("type") or "chat"
        raw_chat_id = recipient_obj.get("chat_id") or chat_obj.get("id") or chat_obj.get("chat_id")

        # Если это личный диалог, chat_id может быть передан в user_id
        if raw_chat_id is None:
            raw_chat_id = recipient_obj.get("user_id")

        chat_id = str(raw_chat_id or "")
        if not chat_id:
            logger.warning(f"Не удалось определить chat_id из сообщения: {data}")
            return None

        # Отправитель (msg.sender)
        sender_obj = msg_data.get("sender") or msg_data.get("from") or {}
        user_id = str(sender_obj.get("user_id") or sender_obj.get("id") or "")
        user_name = str(sender_obj.get("first_name") or sender_obj.get("name") or sender_obj.get("username") or user_id)
        is_admin_flag = sender_obj.get("is_admin")

        # В личном диалоге пользователь всегда админ своих задач
        if chat_type == "dialog":
            is_admin_flag = True

        # Текст (в MAX OpenAPI это body.text)
        text = (body_obj.get("text") or msg_data.get("text") or "").strip()

        # Вложения (в MAX OpenAPI это body.attachments)
        screenshots = []
        raw_attachments = body_obj.get("attachments") or msg_data.get("attachments") or []
        for att in raw_attachments:
            if isinstance(att, dict):
                att_type = att.get("type", "")
                payload = att.get("payload") or {}
                url = payload.get("url") or payload.get("file_url") or att.get("url")
                if att_type in ("image", "photo", "file") and url:
                    screenshots.append(str(url))
            elif isinstance(att, str):
                screenshots.append(att)

        # Ссылка на ответ (reply / link)
        reply_to_id = None
        reply_to_text = None
        reply_to_screenshots = []
        link_obj = msg_data.get("link") or msg_data.get("reply_to_message")
        if link_obj and isinstance(link_obj, dict):
            reply_msg = link_obj.get("message") or link_obj
            reply_body = reply_msg.get("body") or {}
            reply_to_id = str(reply_body.get("mid") or link_obj.get("mid") or reply_msg.get("id") or "")
            reply_to_text = (reply_body.get("text") or reply_msg.get("text") or "").strip()
            reply_atts = reply_body.get("attachments") or reply_msg.get("attachments") or []
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

    def _parse_id(self, val: str) -> Any:
        try:
            return int(val)
        except Exception:
            return val

    def send_message(self, chat_id: str, text: str, reply_to_id: Optional[str] = None,
                     buttons: Optional[List[List[Dict[str, Any]]]] = None) -> bool:
        """
        Отправка текстового сообщения в чат или пользователю MAX.
        Поддерживает прикрепление инлайн-кнопок (buttons).
        """
        url = f"{self.base_url}/messages"
        target_id = self._parse_id(chat_id)
        params = {"chat_id": target_id}
        payload = {
            "text": text,
            "format": "markdown",
        }
        if reply_to_id:
            payload["link"] = {
                "type": "reply",
                "mid": str(reply_to_id)
            }
        if buttons:
            payload["attachments"] = [
                {
                    "type": "inline_keyboard",
                    "payload": {
                        "buttons": buttons
                    }
                }
            ]

        try:
            resp = self.session.post(url, params=params, json=payload, timeout=8)
            if resp.status_code >= 400:
                # Если через chat_id не найдено (диалог), пробуем user_id
                logger.info(f"Пробуем user_id={target_id} для отправки...")
                resp2 = self.session.post(url, params={"user_id": target_id}, json=payload, timeout=8)
                if resp2.status_code >= 400:
                    logger.warning(f"Ошибка отправки в MAX API: {resp.status_code} {resp.text} / {resp2.status_code} {resp2.text}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Исключение при вызове MAX send_message: {e}")
            return False

    def answer_callback(self, callback_id: str, notification: Optional[str] = None) -> bool:
        """
        Ответ на нажатие callback-кнопки (показывает уведомление или закрывает индикатор).
        """
        if not callback_id:
            return False
        url = f"{self.base_url}/answers"
        params = {"callback_id": callback_id}
        payload = {}
        if notification:
            payload["notification"] = notification
        try:
            resp = self.session.post(url, params=params, json=payload, timeout=5)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Ошибка при вызове answer_callback: {e}")
            return False

    def send_photos(self, chat_id: str, photos: List[str], caption: Optional[str] = None,
                    buttons: Optional[List[List[Dict[str, Any]]]] = None) -> bool:
        """
        Отправка сообщения с изображениями в чат MAX.
        """
        url = f"{self.base_url}/messages"
        target_id = self._parse_id(chat_id)
        params = {"chat_id": target_id}
        attachments = []
        for p in photos:
            attachments.append({
                "type": "image",
                "payload": {"url": p}
            })

        if buttons:
            attachments.append({
                "type": "inline_keyboard",
                "payload": {"buttons": buttons}
            })

        payload = {
            "text": caption or "",
            "format": "markdown",
            "attachments": attachments
        }
        try:
            resp = self.session.post(url, params=params, json=payload, timeout=12)
            if resp.status_code >= 400:
                resp2 = self.session.post(url, params={"user_id": target_id}, json=payload, timeout=12)
                if resp2.status_code >= 400:
                    logger.warning(f"Ошибка отправки фото в MAX API: {resp.status_code} {resp.text}")
                    fallback = f"{caption or ''}\n\n" + "\n".join([f"📷 Скриншот: {p}" for p in photos])
                    return self.send_message(chat_id, fallback.strip(), buttons=buttons)
            return True
        except Exception as e:
            logger.error(f"Исключение при вызове MAX send_photos: {e}")
            fallback = f"{caption or ''}\n\n" + "\n".join([f"📷 Скриншот: {p}" for p in photos])
            return self.send_message(chat_id, fallback.strip(), buttons=buttons)

    def is_user_admin(self, chat_id: str, user_id: str, incoming_flag: Optional[bool] = None) -> bool:
        """
        Проверка прав администратора пользователя в чате.
        """
        if incoming_flag is True:
            return True

        if user_id in config.DEFAULT_ADMIN_IDS:
            return True

        target_id = self._parse_id(chat_id)
        try:
            url = f"{self.base_url}/chats/{target_id}/members/admins"
            resp = self.session.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                admins = data.get("members") or data.get("admins") or (data if isinstance(data, list) else [])
                for admin in admins:
                    admin_id = str(admin.get("user_id") or admin.get("id") or "")
                    if admin_id == str(user_id):
                        return True
                return False
        except Exception as e:
            logger.warning(f"Не удалось получить список админов через MAX API ({e})")

        # Если не удалось проверить или в личных диалогах — разрешаем
        return True

    def set_webhook(self, webhook_url: str, secret: Optional[str] = None) -> bool:
        """
        Подписка на события через Webhook по спецификации MAX:
        POST https://platform-api2.max.ru/subscriptions
        """
        url = f"{self.base_url}/subscriptions"
        payload = {
            "url": webhook_url,
            "update_types": ["message_created", "bot_started", "message_callback"]
        }
        if secret:
            payload["secret"] = secret

        try:
            resp = self.session.post(url, json=payload, timeout=8)
            logger.info(f"Регистрация вебхука в MAX: {resp.status_code} {resp.text}")
            return resp.status_code in (200, 201)
        except Exception as e:
            logger.error(f"Ошибка вызова POST /subscriptions: {e}")
            return False

max_api = MaxApiClient()
