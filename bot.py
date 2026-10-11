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
        """Главный диспетчер входящих сообщений."""
        text = msg.text.strip()
        if not text and not msg.screenshots:
            return

        # Удаляем упоминание бота, если бот вызывается через @bot_name /command
        clean_text = re.sub(r"^@\w+\s*", "", text).strip()
        command, *args = clean_text.split(maxsplit=1)
        command = command.lower()
        arg_text = args[0].strip() if args else ""

        # Проверяем, не является ли это вызовом команды
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
            # Если пользователь написал просто номер задачи (например, "/1" или "инфо 1")
            m = re.match(r"^/(\d+)$", command)
            if m:
                self._handle_task_info(msg, m.group(1))

    def _handle_help(self, msg: IncomingMessage):
        help_text = (
            "🤖 **Менеджер задач чата**\n\n"
            "Каждый чат имеет собственный изолированный список задач.\n"
            "Любой администратор чата может создавать задачи и закрывать их.\n\n"
            "📌 **Основные команды:**\n"
            "• `/task <текст задачи>` — создать новую задачу (можно прикрепить скриншоты сразу к сообщению или в ответе на фото)\n"
            "• `/tasks` или `/список` — показать все открытые задачи этого чата\n"
            "• `/info <номер>` — подробная информация о задаче и её скриншоты\n"
            "• `/done <номер>` — отметить задачу выполненной (только для админов)\n"
            "• `/attach <номер>` — прикрепить скриншот к существующей задаче\n"
            "• `/clear_done` — удалить все выполненные задачи из базы, чтобы не захламлять чат\n"
            "• `/help` — показать эту справку"
        )
        self.api.send_message(msg.chat_id, help_text, reply_to_id=msg.message_id)

    def _handle_add_task(self, msg: IncomingMessage, task_content: str):
        # Проверяем права админа
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.send_message(
                msg.chat_id,
                "⛔ Добавлять задачи могут только администраторы чата.",
                reply_to_id=msg.message_id
            )
            return

        # Собираем скриншоты:
        # 1. Из текущего сообщения
        # 2. Если команда отправлена как ответ (reply) на сообщение со скриншотами
        screenshots = list(msg.screenshots)
        if msg.reply_to_screenshots:
            screenshots.extend(msg.reply_to_screenshots)

        # Если текст команды пустой, но ответили на текстовое сообщение
        if not task_content and msg.reply_to_text:
            task_content = msg.reply_to_text

        if not task_content and not screenshots:
            self.api.send_message(
                msg.chat_id,
                "⚠️ Укажите текст задачи после команды.\nПример: `/task Исправить ошибку авторизации` (и прикрепите скриншот).",
                reply_to_id=msg.message_id
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
            f"{screens_info}\n\n"
            f"Чтобы отметить выполненной: `/done {task.task_number}`"
        )
        self.api.send_message(msg.chat_id, reply, reply_to_id=msg.message_id)

    def _handle_list_tasks(self, msg: IncomingMessage):
        tasks = self.db.get_open_tasks(msg.chat_id)
        if not tasks:
            self.api.send_message(
                msg.chat_id,
                "🎉 В этом чате нет открытых задач! Все выполнены или ещё не добавлены.",
                reply_to_id=msg.message_id
            )
            return

        lines = [f"📋 **Открытые задачи этого чата ({len(tasks)}):**\n"]
        for t in tasks:
            screen_badge = f" [📷 {len(t.screenshots)}]" if t.screenshots else ""
            lines.append(f"• **#{t.task_number}** {t.title}{screen_badge} — *{t.created_by_name}*")

        lines.append("\n💡 `/info <номер>` — посмотреть задачу и скриншоты")
        lines.append("💡 `/done <номер>` — отметить выполненной")
        self.api.send_message(msg.chat_id, "\n".join(lines), reply_to_id=msg.message_id)

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

        # Если есть скриншоты, отправляем их вместе с описанием
        if task.screenshots:
            text_out += f"\n\n📷 Прикреплено скриншотов: {len(task.screenshots)}"
            self.api.send_photos(msg.chat_id, task.screenshots, caption=text_out)
        else:
            self.api.send_message(msg.chat_id, text_out, reply_to_id=msg.message_id)

    def _handle_attach(self, msg: IncomingMessage, arg_text: str):
        """Прикрепление скриншотов к существующей задаче."""
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

        self.api.send_message(
            msg.chat_id,
            f"🖼️ К задаче #{task_num} успешно добавлено {len(screenshots)} скриншотов! Всего: {len(task.screenshots)}.",
            reply_to_id=msg.message_id
        )

    def _handle_mark_done(self, msg: IncomingMessage, arg_text: str):
        # Проверяем права админа
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

        self.api.send_message(
            msg.chat_id,
            f"✔️ **Задача #{task.task_number} выполнена!**\n"
            f"📌 {task.title}\n"
            f"👤 Отметил: {task.completed_by_name}",
            reply_to_id=msg.message_id
        )

    def _handle_clear_done(self, msg: IncomingMessage):
        # Проверяем права админа
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.send_message(
                msg.chat_id,
                "⛔ Очищать базу выполненных задач могут только администраторы чата.",
                reply_to_id=msg.message_id
            )
            return

        deleted_count = self.db.delete_completed_tasks(msg.chat_id)
        if deleted_count == 0:
            self.api.send_message(
                msg.chat_id,
                "ℹ️ В этом чате нет выполненных задач для очистки. База уже в порядке!",
                reply_to_id=msg.message_id
            )
        else:
            self.api.send_message(
                msg.chat_id,
                f"🗑️ **Очистка завершена!**\n"
                f"Удалено {deleted_count} выполненных задач из базы данных этого чата. Память освобождена.",
                reply_to_id=msg.message_id
            )

bot = TaskBot()
