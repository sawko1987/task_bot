import logging
import re
from typing import Optional
from config import config
from db import get_db
from models import IncomingMessage, TaskStatus
from max_api import max_api

logger = logging.getLogger(__name__)

class TaskBot:
    def __init__(self):
        self.db = get_db()
        self.api = max_api

    def handle_message(self, msg: IncomingMessage):
        """Главный диспетчер входящих сообщений и нажатий кнопок."""
        try:
            # 1. Обработка нажатий на инлайн-кнопки (callback)
            if msg.callback_payload:
                payload = msg.callback_payload
                if payload == "cmd:list":
                    self.api.answer_callback(msg.callback_id, "Загрузка списка...")
                    self._handle_list_tasks(msg)
                elif payload == "cmd:help":
                    self.api.answer_callback(msg.callback_id, "Справка")
                    self._handle_help(msg)
                elif payload == "cmd:clear_done":
                    self.api.answer_callback(msg.callback_id, "Очистка...")
                    self._handle_clear_done(msg)
                elif payload.startswith("done:"):
                    task_num = payload.split(":", 1)[1]
                    self.api.answer_callback(msg.callback_id, f"Выполняем #{task_num}...")
                    self._handle_mark_done(msg, task_num)
                elif payload.startswith("info:"):
                    task_num = payload.split(":", 1)[1]
                    self.api.answer_callback(msg.callback_id, f"Инфо #{task_num}")
                    self._handle_task_info(msg, task_num)
                return

            text = msg.text.strip()
            if not text and not msg.screenshots:
                return

            # Удаляем упоминание бота, если бот вызывается через @bot_name /command
            clean_text = re.sub(r"^@\w+\s*", "", text).strip()
            command, *args = clean_text.split(maxsplit=1)
            command = command.lower()
            arg_text = args[0].strip() if args else ""

            # Проверяем вызов команд
            if command in ("/start", "/help", "/помощь", "помощь"):
                self._handle_help(msg)
            elif command in ("/task", "/add", "/задача", "/новое"):
                self._handle_add_task(msg, arg_text)
            elif command in ("/tasks", "/list", "/список", "/задачи", "задачи"):
                self._handle_list_tasks(msg)
            elif command in ("/done", "/выполнено", "/готово", "готово"):
                self._handle_mark_done(msg, arg_text)
            elif command in ("/info", "/инфо", "/подробнее"):
                self._handle_task_info(msg, arg_text)
            elif command in ("/attach", "/скриншот", "/фото"):
                self._handle_attach(msg, arg_text)
            elif command in ("/clear_done", "/очистить", "/удалить_выполненные"):
                self._handle_clear_done(msg)
            else:
                m = re.match(r"^/(\d+)$", command)
                if m:
                    self._handle_task_info(msg, m.group(1))

        except Exception as e:
            logger.exception(f"Ошибка при обработке команды бота: {e}")
            err_msg = (
                f"⚠️ **Ошибка при выполнении:**\n`{e}`\n\n"
                f"💡 Если это ошибка базы данных — убедитесь, что в консоли Yandex Cloud в вашей базе YDB выполнен скрипт создания таблиц `schema.sql`."
            )
            self.api.send_message(msg.chat_id, err_msg, reply_to_id=msg.message_id)

    def _handle_help(self, msg: IncomingMessage):
        help_text = (
            "🤖 **Менеджер задач чата**\n\n"
            "Каждый чат имеет собственный изолированный список задач.\n"
            "Вы можете создавать задачи, отмечать их выполнение и просматривать списки кнопками или командами.\n\n"
            "📌 **Команды бота:**\n"
            "• `/task <текст>` — создать задачу (можно прикрепить скриншот)\n"
            "• `/tasks` или `/список` — показать открытые задачи\n"
            "• `/done <номер>` — отметить выполненной\n"
            "• `/info <номер>` — подробная информация и скриншоты\n"
            "• `/attach <номер>` — прикрепить скриншот к задаче\n"
            "• `/clear_done` — удалить завершённые задачи\n"
            "• `/help` — эта справка"
        )
        buttons = [
            [
                {"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"},
                {"type": "callback", "text": "🧹 Очистить закрытые", "payload": "cmd:clear_done"}
            ],
            [
                {"type": "message", "text": "/task Новая задача"}
            ]
        ]
        self.api.send_message(msg.chat_id, help_text, reply_to_id=msg.message_id, buttons=buttons)

    def _handle_add_task(self, msg: IncomingMessage, task_content: str):
        # Проверяем права админа
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.send_message(
                msg.chat_id,
                "⛔ Добавлять задачи могут только администраторы чата.",
                reply_to_id=msg.message_id
            )
            return

        # Собираем скриншоты
        screenshots = list(msg.screenshots)
        if msg.reply_to_screenshots:
            screenshots.extend(msg.reply_to_screenshots)

        if not task_content and msg.reply_to_text:
            task_content = msg.reply_to_text

        if not task_content and not screenshots:
            buttons = [
                [{"type": "message", "text": "/task Задача: пример текста"}]
            ]
            self.api.send_message(
                msg.chat_id,
                "⚠️ Укажите текст задачи после команды.\nПример: `/task Исправить ошибку авторизации` (и прикрепите скриншот).",
                reply_to_id=msg.message_id,
                buttons=buttons
            )
            return

        title = task_content if task_content else "Задача со скриншотами"
        description = task_content

        task = self.db.create_task(
            chat_id=msg.chat_id,
            title=title,
            description=description,
            created_by=msg.user_id,
            created_by_name=msg.user_name,
            screenshots=screenshots
        )

        screens_info = f"🖼️ Скриншотов: {len(screenshots)}" if screenshots else "🖼️ Без скриншотов"
        reply = (
            f"✅ **Задача #{task.task_number} создана!**\n\n"
            f"📌 **{task.title}**\n"
            f"👤 Автор: {task.created_by_name}\n"
            f"{screens_info}"
        )
        buttons = [
            [
                {"type": "callback", "text": f"✅ Выполнить #{task.task_number}", "payload": f"done:{task.task_number}"},
                {"type": "callback", "text": "📋 Все задачи", "payload": "cmd:list"}
            ]
        ]
        self.api.send_message(msg.chat_id, reply, reply_to_id=msg.message_id, buttons=buttons)

    def _handle_list_tasks(self, msg: IncomingMessage):
        tasks = self.db.get_open_tasks(msg.chat_id)
        if not tasks:
            buttons = [
                [
                    {"type": "message", "text": "/task "},
                    {"type": "callback", "text": "🔄 Обновить", "payload": "cmd:list"}
                ]
            ]
            self.api.send_message(
                msg.chat_id,
                "🎉 **В этом чате нет открытых задач!**\nВсе задачи выполнены или ещё не добавлены.\n\nНажмите кнопку ниже, чтобы создать задачу:",
                reply_to_id=msg.message_id,
                buttons=buttons
            )
            return

        lines = [f"📋 **Открытые задачи этого чата ({len(tasks)}):**\n"]
        buttons = []

        # Формируем интерактивные кнопки для открытых задач
        for t in tasks[:8]:
            screen_badge = f" [📷 {len(t.screenshots)}]" if t.screenshots else ""
            lines.append(f"• **#{t.task_number}** {t.title}{screen_badge} — *{t.created_by_name}*")
            buttons.append([
                {"type": "callback", "text": f"✅ Выполнить #{t.task_number}", "payload": f"done:{t.task_number}"},
                {"type": "callback", "text": f"🔍 Инфо #{t.task_number}", "payload": f"info:{t.task_number}"}
            ])

        if len(tasks) > 8:
            lines.append(f"\n*...и ещё {len(tasks) - 8} задач*")

        # Нижняя панель действий
        buttons.append([
            {"type": "message", "text": "/task "},
            {"type": "callback", "text": "🔄 Обновить", "payload": "cmd:list"},
            {"type": "callback", "text": "🧹 Очистить", "payload": "cmd:clear_done"}
        ])

        self.api.send_message(msg.chat_id, "\n".join(lines), reply_to_id=msg.message_id, buttons=buttons)

    def _handle_task_info(self, msg: IncomingMessage, arg_text: str):
        if not arg_text.isdigit():
            self.api.send_message(
                msg.chat_id,
                "⚠️ Укажите номер задачи. Пример: `/info 1`",
                reply_to_id=msg.message_id
            )
            return

        task_num = int(arg_text)
        task = self.db.get_task_by_number(msg.chat_id, task_num)
        if not task:
            self.api.send_message(
                msg.chat_id,
                f"❌ Задача #{task_num} не найдена в этом чате.",
                reply_to_id=msg.message_id
            )
            return

        status_text = "🟢 Открыта" if task.status == TaskStatus.OPEN else "✅ Выполнена"
        info_lines = [
            f"📌 **Задача #{task.task_number}**",
            f"Статус: {status_text}",
            f"Описание: {task.description or task.title}",
            f"Создал: {task.created_by_name} ({task.created_at[:16] if task.created_at else ''})",
        ]
        if task.completed_by_name:
            info_lines.append(f"Выполнил: {task.completed_by_name} ({task.completed_at[:16] if task.completed_at else ''})")

        text_out = "\n".join(info_lines)

        buttons = []
        if task.status == TaskStatus.OPEN:
            buttons.append([
                {"type": "callback", "text": f"✅ Выполнить #{task.task_number}", "payload": f"done:{task.task_number}"},
                {"type": "message", "text": f"/attach {task.task_number}"}
            ])
        buttons.append([
            {"type": "callback", "text": "📋 Все задачи", "payload": "cmd:list"}
        ])

        if task.screenshots:
            text_out += f"\n\n📷 Прикреплено скриншотов: {len(task.screenshots)}"
            self.api.send_photos(msg.chat_id, task.screenshots, caption=text_out, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text_out, reply_to_id=msg.message_id, buttons=buttons)

    def _handle_attach(self, msg: IncomingMessage, arg_text: str):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.send_message(
                msg.chat_id,
                "⛔ Прикреплять материалы могут только администраторы чата.",
                reply_to_id=msg.message_id
            )
            return

        if not arg_text.isdigit():
            self.api.send_message(
                msg.chat_id,
                "⚠️ Укажите номер задачи. Пример: `/attach 1` (прикрепив скриншот)",
                reply_to_id=msg.message_id
            )
            return

        task_num = int(arg_text)
        screenshots = list(msg.screenshots)
        if msg.reply_to_screenshots:
            screenshots.extend(msg.reply_to_screenshots)

        if not screenshots:
            self.api.send_message(
                msg.chat_id,
                "⚠️ Не найдено скриншотов в сообщении. Прикрепите фото или ответьте командой на скриншот.",
                reply_to_id=msg.message_id
            )
            return

        task = self.db.attach_screenshots(msg.chat_id, task_num, screenshots)
        if not task:
            self.api.send_message(
                msg.chat_id,
                f"❌ Задача #{task_num} не найдена в этом чате.",
                reply_to_id=msg.message_id
            )
            return

        buttons = [
            [{"type": "callback", "text": f"🔍 Инфо #{task_num}", "payload": f"info:{task_num}"}]
        ]
        self.api.send_message(
            msg.chat_id,
            f"🖼️ К задаче #{task_num} успешно добавлено {len(screenshots)} скриншотов! Всего: {len(task.screenshots)}.",
            reply_to_id=msg.message_id,
            buttons=buttons
        )

    def _handle_mark_done(self, msg: IncomingMessage, arg_text: str):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.send_message(
                msg.chat_id,
                "⛔ Отмечать задачи выполненными могут только администраторы чата.",
                reply_to_id=msg.message_id
            )
            return

        if not arg_text.isdigit():
            self.api.send_message(
                msg.chat_id,
                "⚠️ Укажите номер задачи для отметки. Пример: `/done 1`",
                reply_to_id=msg.message_id
            )
            return

        task_num = int(arg_text)
        task = self.db.mark_task_done(
            chat_id=msg.chat_id,
            task_number=task_num,
            completed_by=msg.user_id,
            completed_by_name=msg.user_name
        )

        if not task:
            self.api.send_message(
                msg.chat_id,
                f"❌ Задача #{task_num} не найдена в этом чате.",
                reply_to_id=msg.message_id
            )
            return

        buttons = [
            [
                {"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"},
                {"type": "callback", "text": "🧹 Очистить закрытые", "payload": "cmd:clear_done"}
            ]
        ]
        self.api.send_message(
            msg.chat_id,
            f"✔️ **Задача #{task.task_number} выполнена!**\n"
            f"📌 {task.title}\n"
            f"👤 Отметил: {task.completed_by_name}",
            reply_to_id=msg.message_id,
            buttons=buttons
        )

    def _handle_clear_done(self, msg: IncomingMessage):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.send_message(
                msg.chat_id,
                "⛔ Очищать базу выполненных задач могут только администраторы чата.",
                reply_to_id=msg.message_id
            )
            return

        deleted_count = self.db.delete_completed_tasks(msg.chat_id)
        buttons = [
            [{"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}]
        ]
        if deleted_count == 0:
            self.api.send_message(
                msg.chat_id,
                "ℹ️ В этом чате нет выполненных задач для очистки. База в порядке!",
                reply_to_id=msg.message_id,
                buttons=buttons
            )
        else:
            self.api.send_message(
                msg.chat_id,
                f"🗑️ **Очистка завершена!**\n"
                f"Удалено {deleted_count} выполненных задач из базы данных. Память освобождена.",
                reply_to_id=msg.message_id,
                buttons=buttons
            )

bot = TaskBot()
