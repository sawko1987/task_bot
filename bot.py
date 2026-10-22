import logging
import re
from typing import List, Optional, Tuple
from config import config
from db import get_db
from models import IncomingMessage, Task, TaskStatus
from max_api import max_api

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


def parse_bulk_tasks(text: str, available_sections: List[str]) -> List[dict]:
    """
    Разбирает чек-листы от нейросетей (ChatGPT, Claude, Gemini и др.), прайс-листы
    и списки задач любого формата.
    """
    lines = text.strip().split("\n")
    if not lines:
        return []

    sec_map = {s.lower(): s for s in available_sections}
    current_section = None

    first_line = lines[0].strip()
    sec_match = re.match(r"^\[([a-zA-Zа-яА-ЯёЁ0-9_\-\s]+)\]$", first_line)
    if not sec_match:
        sec_match = re.match(r"^(?:раздел|section):\s*([a-zA-Zа-яА-ЯёЁ0-9_\-\s]+)$", first_line, re.IGNORECASE)
    if not sec_match and first_line.lower() in sec_map:
        current_section = sec_map[first_line.lower()]
        lines = lines[1:]
    elif sec_match:
        cand = sec_match.group(1).strip()
        current_section = sec_map.get(cand.lower(), cand)
        lines = lines[1:]

    tasks = []
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        possible_sec = None
        if line_str.startswith("#"):
            possible_sec = line_str.lstrip("#").strip().rstrip(":")
        elif line_str.startswith("[") and line_str.endswith("]"):
            possible_sec = line_str[1:-1].strip()
        elif line_str.lower().startswith("раздел:") or line_str.lower().startswith("section:"):
            possible_sec = line_str.split(":", 1)[1].strip()

        if possible_sec:
            sec_lower = possible_sec.lower()
            if sec_lower in ("без раздела", "общие", "нет", "none", "general", "no section"):
                current_section = None
                continue
            elif sec_lower in sec_map:
                current_section = sec_map[sec_lower]
                continue
            elif len(possible_sec) <= 30 and not re.search(r"^\d+[\.\)]", possible_sec):
                current_section = possible_sec
                continue

        if re.match(r"^[-=_*]{3,}$", line_str):
            continue

        cleaned = re.sub(r"^[-*•+–—]?\s*\[[ xX]?\]\s*", "", line_str)
        cleaned = re.sub(r"^(?:\d+[\.\)\-:]|\#\d+)\s*", "", cleaned)
        cleaned = re.sub(r"^[-*•+–—]\s*", "", cleaned)
        cleaned = re.sub(r"^\[[ xX]?\]\s*", "", cleaned)
        cleaned = cleaned.strip()

        if not cleaned or len(cleaned) < 2:
            continue

        line_sec = current_section
        tag_match = re.match(r"^\[([a-zA-Zа-яА-ЯёЁ0-9_\-\s]+)\]\s*(.*)$", cleaned)
        if tag_match:
            cand_sec = tag_match.group(1).strip()
            if cand_sec.lower() in ("без раздела", "общие", "нет", "none", "general", "no section"):
                line_sec = None
            else:
                line_sec = sec_map.get(cand_sec.lower(), cand_sec)
            cleaned = tag_match.group(2).strip()
        else:
            prefix_match = re.match(r"^([a-zA-Zа-яА-ЯёЁ0-9_\-\s]{2,20}):\s*(.+)$", cleaned)
            if prefix_match:
                prefix_key = prefix_match.group(1).lower()
                if prefix_key in ("без раздела", "общие", "нет", "none", "general", "no section"):
                    line_sec = None
                    cleaned = prefix_match.group(2).strip()
                elif prefix_key in sec_map:
                    line_sec = sec_map[prefix_key]
                    cleaned = prefix_match.group(2).strip()

        if not cleaned:
            continue

        tasks.append({
            "title": cleaned,
            "description": cleaned,
            "section": line_sec
        })

    return tasks


class TaskBot:
    def __init__(self):
        self.db = get_db()
        self.api = max_api

    def handle_message(self, msg: IncomingMessage):
        """Главный диспетчер входящих сообщений и нажатий кнопок."""
        try:
            # 1. ОБРАБОТКА НАЖАТИЙ НА ИНЛАЙН-КНОПКИ (CALLBACK)
            if msg.callback_payload:
                self._handle_callback(msg)
                return

            text = msg.text.strip()
            screenshots = list(msg.screenshots)
            if msg.reply_to_screenshots:
                screenshots.extend(msg.reply_to_screenshots)

            # 2. ПРОВЕРКА СОСТОЯНИЙ ДИАЛОГА (FSM ДЛЯ УПРАВЛЕНИЯ ЧЕРЕЗ КНОПКИ)
            user_state = self.db.get_user_state(msg.chat_id, msg.user_id)
            if user_state:
                state, state_data = user_state
                # Команда отмены
                if text.lower() in ("/cancel", "/отмена", "отмена"):
                    self.db.clear_user_state(msg.chat_id, msg.user_id)
                    buttons = [
                        [{"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}],
                        [{"type": "callback", "text": "🏠 Главное меню", "payload": "cmd:menu"}]
                    ]
                    self.api.send_message(msg.chat_id, "❌ Действие отменено.", reply_to_id=msg.message_id, buttons=buttons)
                    return

                # Состояние ожидания имени нового раздела
                if state == "WAIT_SECTION_NAME":
                    self._process_state_section_name(msg, text)
                    return

                # Состояние ожидания текста одиночной задачи
                if state == "WAIT_TASK_TEXT":
                    self._process_state_task_text(msg, text, screenshots, state_data)
                    return

                # Состояние ожидания чек-листа для массового добавления
                if state == "WAIT_BULK_TEXT":
                    self._process_state_bulk_text(msg, text, state_data)
                    return

                # Состояние ожидания фото для прикрепления к задаче
                if state == "WAIT_ATTACH_SCREENSHOT":
                    self._process_state_attach(msg, screenshots, state_data)
                    return

            # Если нет текста и нет скриншотов — ничего не делаем
            if not text and not screenshots:
                return

            is_group = msg.chat_type != "dialog"
            is_mention = bool(re.match(r"^@\w+", text))
            clean_text = re.sub(r"^@\w+\s*", "", text).strip()
            is_reply = bool(msg.reply_to_message_id or msg.reply_to_text or msg.reply_to_screenshots)

            # В групповом чате без состояния FSM реагируем на:
            # 1. Команды с "/" (например, /task, /done, /attach, /1)
            # 2. Обращения к боту через @ (например, @bot задача, @bot /tasks)
            # 3. Сообщения-ответы (reply) на сообщения с командами управления задачами
            if is_group and not clean_text.startswith("/") and not is_mention:
                if not is_reply:
                    return
                # Проверяем, содержит ли ответ ключевые слова задач
                low = clean_text.lower()
                is_recognized_reply = (
                    low in ("+", "задача", "в задачу", "к задаче", "готово", "выполнено", "инфо")
                    or low.startswith(("+ ", "задача ", "в задачу ", "к задаче ", "добавить в задачу ", "добавить ", "прикрепить "))
                    or bool(re.match(r"^(?:в\s*задачу|к\s*задаче|добавить|\+)\s+\d+", low))
                )
                if not is_recognized_reply:
                    return

            # Нормализация естественных текстовых команд при ответе на сообщение
            if is_reply:
                low = clean_text.lower()
                m_to_task = re.match(r"^(?:в\s*задачу|к\s*задаче|добавить\s*в\s*задачу|добавить|\+)\s+(\d+)(.*)$", clean_text, re.IGNORECASE)
                if m_to_task:
                    num = m_to_task.group(1)
                    rest = m_to_task.group(2).strip()
                    clean_text = f"/add_to {num} {rest}".strip()
                elif low in ("+", "задача", "в задачу", "сделать задачей", "в задачи"):
                    clean_text = "/task"
                elif low.startswith("задача "):
                    clean_text = "/task " + clean_text[7:].strip()
                elif low.startswith("в задачу "):
                    clean_text = "/task " + clean_text[9:].strip()
                elif low in ("готово", "выполнено"):
                    clean_text = "/done"
                elif low in ("инфо", "подробнее"):
                    clean_text = "/info"

            parts = clean_text.split(maxsplit=1)
            command = parts[0].lower()
            arg_text = parts[1].strip() if len(parts) > 1 else ""

            # 3. ТЕКСТОВЫЕ КОМАНДЫ (ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ И БЫСТРОГО ВВОДА)
            if command in ("/start", "/help", "/помощь", "помощь", "/menu", "/меню"):
                self._handle_main_menu(msg)
            elif command in ("/task", "/add", "/задача", "/новое", "/todo"):
                self._handle_add_task_cmd(msg, arg_text)
            elif command in ("/add_to", "/в_задачу", "/к_задаче", "/append", "/добавить_в"):
                self._handle_add_to_cmd(msg, arg_text)
            elif command in ("/bulk", "/mass_task", "/массово", "/чеклист", "/checklist", "/tasks_bulk"):
                self._handle_bulk_tasks_cmd(msg, arg_text)
            elif command in ("/tasks", "/list", "/список", "/задачи", "задачи"):
                self._handle_list_tasks_cmd(msg, arg_text)
            elif command in ("/done", "/выполнено", "/готово", "готово"):
                self._handle_mark_done_cmd(msg, arg_text)
            elif command in ("/info", "/инфо", "/подробнее"):
                self._handle_task_info_cmd(msg, arg_text)
            elif command in ("/attach", "/скриншот", "/фото", "/прикрепить"):
                self._handle_attach_cmd(msg, arg_text)
            elif command in ("/clear_done", "/очистить", "/удалить_выполненные"):
                self._handle_clear_done_cmd(msg)
            elif command in ("/add_section", "/создать_раздел", "/раздел_плюс", "/новый_раздел", "/добавить_раздел"):
                self._handle_add_section_cmd(msg, arg_text)
            elif command in ("/del_section", "/delete_section", "/удалить_раздел", "/раздел_минус"):
                self._handle_del_section_cmd(msg, arg_text)
            elif command in ("/sections", "/разделы", "/папки"):
                self._handle_sections_menu(msg)
            else:
                m = re.match(r"^/(\d+)$", command)
                if m:
                    self._render_task_info(msg, int(m.group(1)))
                elif not is_group:
                    self._handle_main_menu(msg)

        except Exception as e:
            err_details = str(e).strip() or repr(e) or e.__class__.__name__
            logger.exception(f"Ошибка при обработке сообщения: {err_details}")
            err_msg = f"⚠️ **Ошибка:**\n`{err_details}`"
            self.api.send_message(msg.chat_id, err_msg, reply_to_id=msg.message_id)

    # ========================================================
    # ОБРАБОТЧИК CALLBACK-КНОПОК
    # ========================================================

    def _handle_callback(self, msg: IncomingMessage):
        payload = msg.callback_payload

        # Главное меню
        if payload == "cmd:menu":
            self.api.answer_callback(msg.callback_id, "Главное меню")
            self._handle_main_menu(msg, edit_message_id=msg.message_id)

        # Список задач
        elif payload == "cmd:list":
            self.api.answer_callback(msg.callback_id, "Загрузка списка...")
            self._render_task_list(msg, page=1, section=None, edit_message_id=msg.message_id)

        # Меню управления разделами
        elif payload == "cmd:sections":
            self.api.answer_callback(msg.callback_id, "Разделы чата")
            self._handle_sections_menu(msg, edit_message_id=msg.message_id)

        # Кнопка создания новой задачи (через кнопки)
        elif payload == "cmd:new_task":
            self._btn_start_new_task(msg)

        # Выбор раздела для новой задачи
        elif payload.startswith("pick_task_sec:"):
            sec_arg = payload.split(":", 1)[1]
            chosen_sec = None if sec_arg == "__none__" else sec_arg
            self._btn_prompt_task_text(msg, chosen_sec)

        # Кнопка массового добавления чек-листа
        elif payload == "cmd:bulk_task":
            self._btn_start_bulk_task(msg)

        # Выбор раздела для массового добавления
        elif payload.startswith("pick_bulk_sec:"):
            sec_arg = payload.split(":", 1)[1]
            self._btn_prompt_bulk_text(msg, sec_arg)

        # Кнопка создания нового раздела
        elif payload == "cmd:add_sec_prompt":
            self._btn_prompt_new_section(msg)

        # Меню выбора раздела для удаления
        elif payload == "cmd:del_sec_select":
            self._btn_show_delete_sections(msg)

        # Удаление конкретного раздела по кнопке
        elif payload.startswith("do_del_sec:"):
            sec_name = payload.split(":", 1)[1]
            self._btn_perform_delete_section(msg, sec_name)

        # Пагинация списка задач
        elif payload.startswith("page:"):
            parts = payload.split(":", 2)
            page_num = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            sec_name = parts[2] if len(parts) > 2 and parts[2] else None
            self.api.answer_callback(msg.callback_id, f"Стр. {page_num}")
            self._render_task_list(msg, page=page_num, section=sec_name, edit_message_id=msg.message_id)

        # Фильтрация списка задач по разделу
        elif payload.startswith("sec_filter:"):
            sec_arg = payload.split(":", 1)[1]
            sec_name = None if sec_arg == "__all__" else sec_arg
            self.api.answer_callback(msg.callback_id, f"Раздел: {sec_name or 'Все'}")
            self._render_task_list(msg, page=1, section=sec_name, edit_message_id=msg.message_id)

        # Подробная информация о задаче
        elif payload.startswith("info:"):
            task_num = int(payload.split(":", 1)[1])
            self.api.answer_callback(msg.callback_id, f"Инфо #{task_num}")
            self._render_task_info(msg, task_num, edit_message_id=msg.message_id)

        # Отметка задачи выполненной
        elif payload.startswith("done:"):
            task_num = int(payload.split(":", 1)[1])
            self._btn_mark_task_done(msg, task_num)

        # Запрос смены раздела для задачи
        elif payload.startswith("change_sec_prompt:"):
            task_num = int(payload.split(":", 1)[1])
            self._btn_show_change_section(msg, task_num)

        # Установка нового раздела задачи
        elif payload.startswith("set_task_sec:"):
            parts = payload.split(":", 2)
            task_num = int(parts[1])
            sec_arg = parts[2]
            chosen_sec = None if sec_arg == "__none__" else sec_arg
            self._btn_perform_change_section(msg, task_num, chosen_sec)

        # Подтверждение удаления задачи
        elif payload.startswith("del_task_confirm:"):
            task_num = int(payload.split(":", 1)[1])
            self._btn_confirm_delete_task(msg, task_num)

        # Выполнение удаления задачи
        elif payload.startswith("do_del_task:"):
            task_num = int(payload.split(":", 1)[1])
            self._btn_perform_delete_task(msg, task_num)

        # Запрос фото для прикрепления
        elif payload.startswith("attach_prompt:"):
            task_num = int(payload.split(":", 1)[1])
            self._btn_prompt_attach(msg, task_num)

        # Запрос подтверждения очистки закрытых задач
        elif payload == "cmd:clear_done":
            self._btn_prompt_clear_done(msg)

        # Выполнение очистки закрытых задач
        elif payload == "do_clear_done":
            self._btn_perform_clear_done(msg)

        # Отмена любого активного ввода
        elif payload == "cancel_state":
            self.db.clear_user_state(msg.chat_id, msg.user_id)
            self.api.answer_callback(msg.callback_id, "Отменено")
            self._handle_main_menu(msg, edit_message_id=msg.message_id)

    # ========================================================
    # ГЛАВНОЕ МЕНЮ (ПАНЕЛЬ УПРАВЛЕНИЯ)
    # ========================================================

    def _handle_main_menu(self, msg: IncomingMessage, edit_message_id: Optional[str] = None):
        all_tasks = self.db.get_open_tasks(msg.chat_id)
        sections = self.db.get_sections(msg.chat_id)

        sec_list_str = ", ".join([f"«{s}»" for s in sections]) if sections else "нет"

        menu_text = (
            "🤖 **Панель управления задачами**\n\n"
            f"📋 **Открытых задач:** {len(all_tasks)}\n"
            f"📁 **Разделы ({len(sections)}/3):** {sec_list_str}\n\n"
            "Используйте кнопки ниже для быстрого управления:"
        )

        buttons = [
            [
                {"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"},
                {"type": "callback", "text": "➕ Новая задача", "payload": "cmd:new_task"}
            ],
            [
                {"type": "callback", "text": "📥 Чек-лист нейросети", "payload": "cmd:bulk_task"},
                {"type": "callback", "text": "📁 Разделы чата", "payload": "cmd:sections"}
            ],
            [
                {"type": "callback", "text": "🧹 Очистить закрытые", "payload": "cmd:clear_done"},
                {"type": "callback", "text": "🔄 Обновить", "payload": "cmd:menu"}
            ]
        ]

        if edit_message_id:
            if not self.api.edit_message(edit_message_id, menu_text, buttons=buttons):
                self.api.send_message(msg.chat_id, menu_text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, menu_text, reply_to_id=msg.message_id, buttons=buttons)

    # ========================================================
    # СОЗДАНИЕ ОДИНОЧНОЙ ЗАДАЧИ ЧЕРЕЗ КНОПКИ
    # ========================================================

    def _btn_start_new_task(self, msg: IncomingMessage):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        sections = self.db.get_sections(msg.chat_id)
        if not sections:
            self._btn_prompt_task_text(msg, None)
            return

        self.api.answer_callback(msg.callback_id, "Выберите раздел")
        text = "➕ **Создание задачи**\n\nВыберите раздел для новой задачи:"
        buttons = []
        sec_row = []
        for s in sections:
            sec_row.append({"type": "callback", "text": f"📁 {s}", "payload": f"pick_task_sec:{s}"})
        buttons.append(sec_row)
        buttons.append([
            {"type": "callback", "text": "📁 Без раздела", "payload": "pick_task_sec:__none__"},
            {"type": "callback", "text": "❌ Отмена", "payload": "cancel_state"}
        ])

        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _btn_prompt_task_text(self, msg: IncomingMessage, section: Optional[str]):
        self.db.set_user_state(msg.chat_id, msg.user_id, "WAIT_TASK_TEXT", {"section": section})
        self.api.answer_callback(msg.callback_id, "Ожидание текста задачи")

        sec_title = f" в раздел «{section}»" if section else ""
        text = (
            f"✏️ **Отправьте текст новой задачи{sec_title} в сообщении.**\n\n"
            f"💡 Можно сразу прикрепить скриншот или фото прямо к сообщению."
        )
        buttons = [
            [{"type": "callback", "text": "❌ Отмена", "payload": "cancel_state"}]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _process_state_task_text(self, msg: IncomingMessage, text: str, screenshots: List[str], state_data: dict):
        self.db.clear_user_state(msg.chat_id, msg.user_id)
        section = state_data.get("section")

        title = text if text else "Задача со скриншотом"
        task = self.db.create_task(
            chat_id=msg.chat_id,
            title=title,
            description=title,
            created_by=msg.user_id,
            created_by_name=msg.user_name,
            screenshots=screenshots,
            section=section
        )

        sec_str = f"📁 Раздел: **{task.section}**\n" if task.section else ""
        screens_str = f"🖼️ Прикреплено скриншотов: {len(screenshots)}\n" if screenshots else ""

        reply = (
            f"✅ **Задача #{task.task_number} успешно создана!**\n\n"
            f"📌 **{task.title}**\n"
            f"{sec_str}"
            f"{screens_str}"
            f"👤 Автор: {task.created_by_name}"
        )

        buttons = [
            [
                {"type": "callback", "text": f"✅ Выполнить #{task.task_number}", "payload": f"done:{task.task_number}"},
                {"type": "callback", "text": f"🔍 Инфо #{task.task_number}", "payload": f"info:{task.task_number}"}
            ],
            [
                {"type": "callback", "text": "➕ Ещё задача", "payload": "cmd:new_task"},
                {"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}
            ]
        ]
        self.api.send_message(msg.chat_id, reply, reply_to_id=msg.message_id, buttons=buttons)

    # ========================================================
    # МАССОВОЕ ДОБАВЛЕНИЕ ЧЕК-ЛИСТОВ ЧЕРЕЗ КНОПКИ
    # ========================================================

    def _btn_start_bulk_task(self, msg: IncomingMessage):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        sections = self.db.get_sections(msg.chat_id)
        if not sections:
            self._btn_prompt_bulk_text(msg, "__auto__")
            return

        self.api.answer_callback(msg.callback_id, "Настройка раздела")
        text = "📥 **Массовое добавление задач**\n\nВыберите раздел для задач или включите авто-определение из текста:"
        buttons = [
            [{"type": "callback", "text": "✨ Авто-определение из чек-листа", "payload": "pick_bulk_sec:__auto__"}]
        ]
        sec_row = []
        for s in sections:
            sec_row.append({"type": "callback", "text": f"📁 {s}", "payload": f"pick_bulk_sec:{s}"})
        buttons.append(sec_row)
        buttons.append([
            {"type": "callback", "text": "📁 Без раздела", "payload": "pick_bulk_sec:__none__"},
            {"type": "callback", "text": "❌ Отмена", "payload": "cancel_state"}
        ])

        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _btn_prompt_bulk_text(self, msg: IncomingMessage, sec_arg: str):
        chosen_sec = None if sec_arg in ("__auto__", "__none__") else sec_arg
        self.db.set_user_state(msg.chat_id, msg.user_id, "WAIT_BULK_TEXT", {"section": chosen_sec, "mode": sec_arg})
        self.api.answer_callback(msg.callback_id, "Ожидание списка задач")

        target_str = "Авто-определение" if sec_arg == "__auto__" else (f"Раздел «{chosen_sec}»" if chosen_sec else "Без раздела")
        text = (
            f"📋 **Вставьте чек-лист от нейросети или список задач.**\n"
            f"🎯 Назначение: **{target_str}**\n\n"
            f"Поддерживаются любые форматы:\n"
            f"• Чекбоксы: `- [ ] Задача`\n"
            f"• Нумерация: `1. Задача`, `1) Задача`\n"
            f"• Маркеры: `• Задача`, `- Задача`\n"
            f"• Разделы: `## Backend`, `[Frontend]`\n\n"
            f"Просто вставьте весь текст от нейросети и отправьте:"
        )
        buttons = [
            [{"type": "callback", "text": "❌ Отмена", "payload": "cancel_state"}]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _process_state_bulk_text(self, msg: IncomingMessage, text: str, state_data: dict):
        self.db.clear_user_state(msg.chat_id, msg.user_id)
        default_section = state_data.get("section")

        sections = self.db.get_sections(msg.chat_id)
        task_items = parse_bulk_tasks(text, sections)

        if not task_items:
            buttons = [
                [{"type": "callback", "text": "📥 Попробовать снова", "payload": "cmd:bulk_task"}],
                [{"type": "callback", "text": "🏠 Главное меню", "payload": "cmd:menu"}]
            ]
            self.api.send_message(
                msg.chat_id,
                "⚠️ Не удалось распознать задачи. Убедитесь, что задачи идут с новой строки.",
                reply_to_id=msg.message_id,
                buttons=buttons
            )
            return

        # Если был выбран фиксированный раздел и у задачи нет своего раздела — назначаем выбранный
        if default_section:
            for item in task_items:
                if not item.get("section"):
                    item["section"] = default_section

        # Авто-создание разделов если лимит позволяет
        unique_sections = {item["section"] for item in task_items if item.get("section")}
        for sec in unique_sections:
            if sec and sec.lower() not in [s.lower() for s in sections] and len(sections) < 3:
                self.db.add_section(msg.chat_id, sec)
                sections.append(sec)

        created_tasks = self.db.create_tasks_bulk(
            chat_id=msg.chat_id,
            task_items=task_items,
            created_by=msg.user_id,
            created_by_name=msg.user_name
        )

        total_cnt = len(created_tasks)
        first_num = created_tasks[0].task_number
        last_num = created_tasks[-1].task_number

        sec_counts = {}
        for t in created_tasks:
            s_name = t.section or "Без раздела"
            sec_counts[s_name] = sec_counts.get(s_name, 0) + 1
        sec_summary = ", ".join([f"{k} ({v})" for k, v in sec_counts.items()])

        preview_lines = [
            f"🎉 **Успешно создано {total_cnt} задач!** (№{first_num} – №{last_num})\n",
            f"📁 **Разделы:** {sec_summary}\n",
            "📌 **Превью первых задач:**"
        ]
        for t in created_tasks[:5]:
            sec_badge = f" `[{t.section}]`" if t.section else ""
            preview_lines.append(f"• **#{t.task_number}**{sec_badge} {t.title}")

        if total_cnt > 5:
            preview_lines.append(f"• *...и ещё {total_cnt - 5} задач*")

        buttons = [
            [
                {"type": "callback", "text": "📋 Открыть список задач", "payload": "cmd:list"},
                {"type": "callback", "text": "📁 Разделы", "payload": "cmd:sections"}
            ]
        ]
        self.api.send_message(msg.chat_id, "\n".join(preview_lines), reply_to_id=msg.message_id, buttons=buttons)

    # ========================================================
    # УПРАВЛЕНИЕ РАЗДЕЛАМИ ЧЕРЕЗ КНОПКИ
    # ========================================================

    def _handle_sections_menu(self, msg: IncomingMessage, edit_message_id: Optional[str] = None):
        sections = self.db.get_sections(msg.chat_id)
        all_open = self.db.get_open_tasks(msg.chat_id)

        lines = [f"📁 **Разделы задач этого чата ({len(sections)}/3):**\n"]
        sec_buttons_row = []

        if sections:
            for idx, sec in enumerate(sections, 1):
                cnt = len([t for t in all_open if t.section and t.section.lower() == sec.lower()])
                lines.append(f"{idx}. **{sec}** — открытых задач: {cnt}")
                sec_buttons_row.append({
                    "type": "callback",
                    "text": f"📁 {sec} ({cnt})",
                    "payload": f"sec_filter:{sec}"
                })
        else:
            lines.append("Разделов пока нет. Можно создать до 3 разделов.")

        no_sec_cnt = len([t for t in all_open if not t.section])
        if no_sec_cnt > 0:
            lines.append(f"• *Без раздела* — открытых задач: {no_sec_cnt}")

        buttons = []
        if sec_buttons_row:
            buttons.append(sec_buttons_row)

        action_row = []
        if len(sections) < 3:
            action_row.append({"type": "callback", "text": "➕ Создать раздел", "payload": "cmd:add_sec_prompt"})
        if len(sections) > 0:
            action_row.append({"type": "callback", "text": "🗑️ Удалить раздел", "payload": "cmd:del_sec_select"})

        if action_row:
            buttons.append(action_row)

        buttons.append([
            {"type": "callback", "text": "📋 Все задачи", "payload": "cmd:list"},
            {"type": "callback", "text": "🏠 Главное меню", "payload": "cmd:menu"}
        ])

        text_out = "\n".join(lines)
        if edit_message_id:
            if not self.api.edit_message(edit_message_id, text_out, buttons=buttons):
                self.api.send_message(msg.chat_id, text_out, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text_out, reply_to_id=msg.message_id, buttons=buttons)

    def _btn_prompt_new_section(self, msg: IncomingMessage):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        sections = self.db.get_sections(msg.chat_id)
        if len(sections) >= 3:
            self.api.answer_callback(msg.callback_id, "Достигнут лимит 3 разделов")
            return

        self.db.set_user_state(msg.chat_id, msg.user_id, "WAIT_SECTION_NAME")
        self.api.answer_callback(msg.callback_id, "Введите название раздела")

        text = (
            "✏️ **Введите название нового раздела в сообщении**:\n"
            "*(например: Backend, Frontend, Дизайн)*"
        )
        buttons = [
            [{"type": "callback", "text": "❌ Отмена", "payload": "cancel_state"}]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _process_state_section_name(self, msg: IncomingMessage, name: str):
        self.db.clear_user_state(msg.chat_id, msg.user_id)
        sec_name = name.strip().strip("[]")

        if not sec_name:
            self.api.send_message(msg.chat_id, "⚠️ Название раздела не может быть пустым.", reply_to_id=msg.message_id)
            return

        if len(sec_name) > 30:
            self.api.send_message(msg.chat_id, "⚠️ Название раздела не должно превышать 30 символов.", reply_to_id=msg.message_id)
            return

        existing = self.db.get_sections(msg.chat_id)
        if len(existing) >= 3:
            self.api.send_message(msg.chat_id, "⛔ В этом чате уже создано максимальное количество разделов (3).", reply_to_id=msg.message_id)
            return

        if sec_name.lower() in [s.lower() for s in existing]:
            self.api.send_message(msg.chat_id, f"⚠️ Раздел «{sec_name}» уже существует.", reply_to_id=msg.message_id)
            return

        created = self.db.add_section(msg.chat_id, sec_name)
        if created:
            all_sec = self.db.get_sections(msg.chat_id)
            buttons = [
                [{"type": "callback", "text": f"➕ Добавить задачу в «{sec_name}»", "payload": f"pick_task_sec:{sec_name}"}],
                [
                    {"type": "callback", "text": "📁 Все разделы", "payload": "cmd:sections"},
                    {"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}
                ]
            ]
            self.api.send_message(
                msg.chat_id,
                f"📁 **Раздел «{sec_name}» успешно создан!** ({len(all_sec)}/3)",
                reply_to_id=msg.message_id,
                buttons=buttons
            )

    def _btn_show_delete_sections(self, msg: IncomingMessage):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        sections = self.db.get_sections(msg.chat_id)
        if not sections:
            self.api.answer_callback(msg.callback_id, "В чате нет разделов")
            return

        self.api.answer_callback(msg.callback_id, "Удаление раздела")
        text = "🗑️ **Выберите раздел для удаления:**\n*(Задачи этого раздела не удалятся, а перейдут в список «Без раздела»)*"
        buttons = []
        for s in sections:
            buttons.append([{"type": "callback", "text": f"🗑️ Удалить «{s}»", "payload": f"do_del_sec:{s}"}])

        buttons.append([{"type": "callback", "text": "⬅️ Назад к разделам", "payload": "cmd:sections"}])

        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _btn_perform_delete_section(self, msg: IncomingMessage, section_name: str):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        deleted = self.db.delete_section(msg.chat_id, section_name)
        if deleted:
            self.api.answer_callback(msg.callback_id, f"Раздел «{section_name}» удалён")
            text = f"🗑️ **Раздел «{section_name}» удалён.**\nВсе задачи этого раздела сохранены в общем списке."
        else:
            self.api.answer_callback(msg.callback_id, "Раздел не найден")
            text = f"❌ Раздел «{section_name}» не найден."

        buttons = [
            [{"type": "callback", "text": "📁 К разделам", "payload": "cmd:sections"}],
            [{"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    # ========================================================
    # СПИСОК ЗАДАЧ И ПАГИНАЦИЯ (ПО 10 НА СТРАНИЦУ)
    # ========================================================

    def _render_task_list(self, msg: IncomingMessage, page: int = 1,
                          section: Optional[str] = None, edit_message_id: Optional[str] = None):
        all_tasks = self.db.get_open_tasks(msg.chat_id, section=section)
        total_tasks = len(all_tasks)

        if total_tasks == 0:
            if section:
                empty_text = f"🎉 **В разделе «{section}» нет открытых задач!**"
                buttons = [
                    [{"type": "callback", "text": f"➕ Добавить задачу в «{section}»", "payload": f"pick_task_sec:{section}"}],
                    [
                        {"type": "callback", "text": "📁 Все задачи", "payload": "sec_filter:__all__"},
                        {"type": "callback", "text": "📁 Разделы", "payload": "cmd:sections"}
                    ]
                ]
            else:
                empty_text = "🎉 **В этом чате нет открытых задач!**\nВсе задачи выполнены или ещё не добавлены."
                buttons = [
                    [
                        {"type": "callback", "text": "➕ Новая задача", "payload": "cmd:new_task"},
                        {"type": "callback", "text": "📥 Чек-лист", "payload": "cmd:bulk_task"}
                    ],
                    [{"type": "callback", "text": "📁 Разделы", "payload": "cmd:sections"}]
                ]

            if edit_message_id:
                if not self.api.edit_message(edit_message_id, empty_text, buttons=buttons):
                    self.api.send_message(msg.chat_id, empty_text, buttons=buttons)
            else:
                self.api.send_message(msg.chat_id, empty_text, reply_to_id=msg.message_id, buttons=buttons)
            return

        total_pages = max(1, (total_tasks + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * PAGE_SIZE
        end_idx = min(start_idx + PAGE_SIZE, total_tasks)
        page_tasks = all_tasks[start_idx:end_idx]

        sec_header = f" в разделе «{section}»" if section else ""
        lines = [f"📋 **Открытые задачи{sec_header} (всего: {total_tasks}, стр. {page}/{total_pages}):**\n"]
        buttons = []

        for t in page_tasks:
            screen_badge = f" [📷 {len(t.screenshots)}]" if t.screenshots else ""
            sec_badge = f" `[{t.section}]`" if t.section and not section else ""
            lines.append(f"• **#{t.task_number}**{sec_badge} {t.title}{screen_badge}")
            buttons.append([
                {"type": "callback", "text": f"✅ #{t.task_number}", "payload": f"done:{t.task_number}"},
                {"type": "callback", "text": f"🔍 Инфо #{t.task_number}", "payload": f"info:{t.task_number}"}
            ])

        # Навигация пагинации (если страниц > 1)
        if total_pages > 1:
            nav_row = []
            sec_param = section or ""
            if page > 1:
                nav_row.append({"type": "callback", "text": "⬅️ Назад", "payload": f"page:{page-1}:{sec_param}"})
            nav_row.append({"type": "callback", "text": f"📄 {page}/{total_pages}", "payload": f"page:{page}:{sec_param}"})
            if page < total_pages:
                nav_row.append({"type": "callback", "text": "Вперёд ➡️", "payload": f"page:{page+1}:{sec_param}"})
            buttons.append(nav_row)

        # Фильтры разделов
        sections = self.db.get_sections(msg.chat_id)
        if sections:
            sec_row = [
                {
                    "type": "callback",
                    "text": "• Все •" if not section else "📁 Все",
                    "payload": "sec_filter:__all__"
                }
            ]
            for s in sections:
                is_active = section and section.lower() == s.lower()
                sec_row.append({
                    "type": "callback",
                    "text": f"• {s} •" if is_active else f"📁 {s}",
                    "payload": f"sec_filter:{s}"
                })
            buttons.append(sec_row)

        # Строка основных действий через кнопки
        buttons.append([
            {"type": "callback", "text": "➕ Задача", "payload": "cmd:new_task"},
            {"type": "callback", "text": "📥 Чек-лист", "payload": "cmd:bulk_task"},
            {"type": "callback", "text": "📁 Разделы", "payload": "cmd:sections"}
        ])

        # Нижняя вспомогательная строка
        buttons.append([
            {"type": "callback", "text": "🔄 Обновить", "payload": f"page:{page}:{section or ''}"},
            {"type": "callback", "text": "🧹 Очистить", "payload": "cmd:clear_done"},
            {"type": "callback", "text": "🏠 Меню", "payload": "cmd:menu"}
        ])

        full_text = "\n".join(lines)
        if edit_message_id:
            if not self.api.edit_message(edit_message_id, full_text, buttons=buttons):
                self.api.send_message(msg.chat_id, full_text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, full_text, reply_to_id=msg.message_id, buttons=buttons)

    # ========================================================
    # КАРТОЧКА ЗАДАЧИ И ДЕЙСТВИЯ (ИНФО, ВЫПОЛНЕНИЕ, СМЕНА РАЗДЕЛА, УДАЛЕНИЕ)
    # ========================================================

    def _render_task_info(self, msg: IncomingMessage, task_num: int, edit_message_id: Optional[str] = None):
        task = self.db.get_task_by_number(msg.chat_id, task_num)
        if not task:
            text = f"❌ Задача #{task_num} не найдена в этом чате."
            buttons = [[{"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}]]
            if edit_message_id:
                if not self.api.edit_message(edit_message_id, text, buttons=buttons):
                    self.api.send_message(msg.chat_id, text, buttons=buttons)
            else:
                self.api.send_message(msg.chat_id, text, reply_to_id=msg.message_id, buttons=buttons)
            return

        status_text = "🟢 Открыта" if task.status == TaskStatus.OPEN else "✅ Выполнена"
        info_lines = [
            f"📌 **Задача #{task.task_number}**",
            f"Статус: {status_text}",
        ]
        if task.section:
            info_lines.append(f"Раздел: 📁 **{task.section}**")
        else:
            info_lines.append("Раздел: *Без раздела*")

        info_lines.extend([
            f"Описание: {task.description or task.title}",
            f"Создал: {task.created_by_name} ({task.created_at[:16] if task.created_at else ''})",
        ])
        if task.completed_by_name:
            info_lines.append(f"Выполнил: {task.completed_by_name} ({task.completed_at[:16] if task.completed_at else ''})")

        buttons = []
        action_row = []
        if task.status == TaskStatus.OPEN:
            action_row.append({"type": "callback", "text": f"✅ Выполнить #{task.task_number}", "payload": f"done:{task.task_number}"})
        action_row.append({"type": "callback", "text": "📷 Фото", "payload": f"attach_prompt:{task.task_number}"})
        buttons.append(action_row)

        sections = self.db.get_sections(msg.chat_id)
        manage_row = []
        if sections:
            manage_row.append({"type": "callback", "text": "📁 Сменить раздел", "payload": f"change_sec_prompt:{task.task_number}"})
        manage_row.append({"type": "callback", "text": "🗑️ Удалить задачу", "payload": f"del_task_confirm:{task.task_number}"})
        buttons.append(manage_row)

        buttons.append([
            {"type": "callback", "text": "📋 К списку задач", "payload": "cmd:list"},
            {"type": "callback", "text": "🏠 Главное меню", "payload": "cmd:menu"}
        ])

        text_out = "\n".join(info_lines)
        if task.screenshots:
            text_out += f"\n\n📷 Прикреплено скриншотов: {len(task.screenshots)}"
            self.api.send_photos(msg.chat_id, task.screenshots, caption=text_out, buttons=buttons)
        else:
            if edit_message_id:
                if not self.api.edit_message(edit_message_id, text_out, buttons=buttons):
                    self.api.send_message(msg.chat_id, text_out, buttons=buttons)
            else:
                self.api.send_message(msg.chat_id, text_out, reply_to_id=msg.message_id, buttons=buttons)

    def _btn_mark_task_done(self, msg: IncomingMessage, task_num: int):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        task = self.db.mark_task_done(msg.chat_id, task_num, msg.user_id, msg.user_name)
        if not task:
            self.api.answer_callback(msg.callback_id, "Задача не найдена")
            return

        self.api.answer_callback(msg.callback_id, f"Задача #{task_num} выполнена!")
        reply = (
            f"✔️ **Задача #{task.task_number} выполнена!**\n"
            f"📌 {task.title}\n"
            f"👤 Отметил: {task.completed_by_name}"
        )
        buttons = [
            [
                {"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"},
                {"type": "callback", "text": "🧹 Очистить закрытые", "payload": "cmd:clear_done"}
            ]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, reply, buttons=buttons):
                self.api.send_message(msg.chat_id, reply, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, reply, buttons=buttons)

    def _btn_show_change_section(self, msg: IncomingMessage, task_num: int):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        sections = self.db.get_sections(msg.chat_id)
        self.api.answer_callback(msg.callback_id, "Смена раздела")
        text = f"📁 **Выберите новый раздел для задачи #{task_num}:**"

        buttons = []
        sec_row = []
        for s in sections:
            sec_row.append({"type": "callback", "text": f"📁 {s}", "payload": f"set_task_sec:{task_num}:{s}"})
        buttons.append(sec_row)
        buttons.append([
            {"type": "callback", "text": "📁 Без раздела", "payload": f"set_task_sec:{task_num}:__none__"},
            {"type": "callback", "text": "⬅️ Назад", "payload": f"info:{task_num}"}
        ])

        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _btn_perform_change_section(self, msg: IncomingMessage, task_num: int, section: Optional[str]):
        task = self.db.update_task_section(msg.chat_id, task_num, section)
        sec_name = f"«{section}»" if section else "«Без раздела»"
        self.api.answer_callback(msg.callback_id, f"Раздел изменён на {sec_name}")
        self._render_task_info(msg, task_num, edit_message_id=msg.message_id)

    def _btn_confirm_delete_task(self, msg: IncomingMessage, task_num: int):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        self.api.answer_callback(msg.callback_id, "Подтверждение удаления")
        text = f"⚠️ **Вы уверены, что хотите удалить задачу #{task_num} безвозвратно?**"
        buttons = [
            [
                {"type": "callback", "text": "🗑️ Да, удалить", "payload": f"do_del_task:{task_num}"},
                {"type": "callback", "text": "❌ Отмена", "payload": f"info:{task_num}"}
            ]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _btn_perform_delete_task(self, msg: IncomingMessage, task_num: int):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        self.db.delete_task(msg.chat_id, task_num)
        self.api.answer_callback(msg.callback_id, f"Задача #{task_num} удалена")
        text = f"🗑️ **Задача #{task_num} успешно удалена.**"
        buttons = [
            [{"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}],
            [{"type": "callback", "text": "🏠 Главное меню", "payload": "cmd:menu"}]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _btn_prompt_attach(self, msg: IncomingMessage, task_num: int):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        self.db.set_user_state(msg.chat_id, msg.user_id, "WAIT_ATTACH_SCREENSHOT", {"task_number": task_num})
        self.api.answer_callback(msg.callback_id, "Отправьте фото")
        text = f"📷 **Отправьте одно или несколько фото/скриншотов для прикрепления к задаче #{task_num}:**"
        buttons = [
            [{"type": "callback", "text": "❌ Отмена", "payload": f"info:{task_num}"}]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _process_state_attach(self, msg: IncomingMessage, screenshots: List[str], state_data: dict):
        self.db.clear_user_state(msg.chat_id, msg.user_id)
        task_num = state_data.get("task_number")

        if not screenshots:
            buttons = [
                [{"type": "callback", "text": f"🔍 Вернуться к задаче #{task_num}", "payload": f"info:{task_num}"}]
            ]
            self.api.send_message(msg.chat_id, "⚠️ В сообщении не найдено изображений.", reply_to_id=msg.message_id, buttons=buttons)
            return

        task = self.db.attach_screenshots(msg.chat_id, task_num, screenshots)
        buttons = [
            [{"type": "callback", "text": f"🔍 Инфо #{task_num}", "payload": f"info:{task_num}"}],
            [{"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}]
        ]
        self.api.send_message(
            msg.chat_id,
            f"🖼️ К задаче #{task_num} успешно добавлено {len(screenshots)} скриншотов!",
            reply_to_id=msg.message_id,
            buttons=buttons
        )

    def _btn_prompt_clear_done(self, msg: IncomingMessage):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        self.api.answer_callback(msg.callback_id, "Очистка закрытых")
        text = (
            "🧹 **Очистка выполненных задач**\n\n"
            "Вы действительно хотите удалить все выполненные задачи из базы данных?\n"
            "Это освободит память и скроет закрытые задачи."
        )
        buttons = [
            [
                {"type": "callback", "text": "🗑️ Да, очистить базу", "payload": "do_clear_done"},
                {"type": "callback", "text": "❌ Отмена", "payload": "cmd:list"}
            ]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    def _btn_perform_clear_done(self, msg: IncomingMessage):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.answer_callback(msg.callback_id, "Только для администраторов чата")
            return

        deleted_cnt = self.db.delete_completed_tasks(msg.chat_id)
        self.api.answer_callback(msg.callback_id, f"Удалено: {deleted_cnt}")
        if deleted_cnt == 0:
            text = "ℹ️ В этом чате нет выполненных задач для очистки."
        else:
            text = f"🗑️ **Очистка завершена!**\nУдалено {deleted_cnt} выполненных задач из базы данных."

        buttons = [
            [{"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"}],
            [{"type": "callback", "text": "🏠 Главное меню", "payload": "cmd:menu"}]
        ]
        if msg.message_id:
            if not self.api.edit_message(msg.message_id, text, buttons=buttons):
                self.api.send_message(msg.chat_id, text, buttons=buttons)
        else:
            self.api.send_message(msg.chat_id, text, buttons=buttons)

    # ========================================================
    # СОВМЕСТИМОСТЬ С ТЕКСТОВЫМИ КОМАНДАМИ
    # ========================================================

    def _handle_list_tasks_cmd(self, msg: IncomingMessage, arg_text: str = ""):
        sections = self.db.get_sections(msg.chat_id)
        sec_map = {s.lower(): s for s in sections}
        page = 1
        section = None
        for tok in arg_text.strip().split():
            if tok.isdigit():
                page = int(tok)
            else:
                clean_tok = tok.strip("[]")
                if clean_tok.lower() in sec_map:
                    section = sec_map[clean_tok.lower()]
                elif not section:
                    section = clean_tok
        self._render_task_list(msg, page=page, section=section)

    def _handle_add_task_cmd(self, msg: IncomingMessage, task_content: str):
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.send_message(msg.chat_id, "⛔ Добавлять задачи могут только администраторы чата.", reply_to_id=msg.message_id)
            return

        is_reply = bool(msg.reply_to_message_id or msg.reply_to_text or msg.reply_to_screenshots)

        # Если в ответе на сообщение передан номер задачи (например, "/task 5" или "/task 5 примечание"),
        # перенаправляем в добавление к существующей задаче!
        if is_reply and task_content:
            m_num = re.match(r"^(\d+)(?:\s+(.*))?$", task_content)
            if m_num:
                self._handle_add_to_cmd(msg, task_content)
                return

        screenshots = list(msg.screenshots)
        if msg.reply_to_screenshots:
            for s in msg.reply_to_screenshots:
                if s not in screenshots:
                    screenshots.append(s)

        section = None
        clean_content = task_content
        sections = self.db.get_sections(msg.chat_id)
        sec_map = {s.lower(): s for s in sections}

        if task_content:
            m_sec = re.match(r"^\[([a-zA-Zа-яА-ЯёЁ0-9_\-\s]+)\]\s*(.*)$", task_content)
            if m_sec:
                cand = m_sec.group(1).strip()
                if cand.lower() in sec_map:
                    section = sec_map[cand.lower()]
                    clean_content = m_sec.group(2).strip()
                elif len(sections) < 3 and len(cand) <= 30:
                    self.db.add_section(msg.chat_id, cand)
                    section = cand
                    clean_content = m_sec.group(2).strip()
            else:
                m_colon = re.match(r"^([a-zA-Zа-яА-ЯёЁ0-9_\-\s]{2,20}):\s*(.+)$", task_content)
                if m_colon and m_colon.group(1).lower() in sec_map:
                    section = sec_map[m_colon.group(1).lower()]
                    clean_content = m_colon.group(2).strip()

        # Формирование заголовка и описания задачи
        title = ""
        description = ""

        if clean_content:
            # Пользователь ввел свой заголовок в команде
            title = clean_content
            if msg.reply_to_text:
                sender_info = f" (от {msg.reply_to_sender_name})" if msg.reply_to_sender_name else ""
                description = f"{clean_content}\n\n💬 Пересланное сообщение{sender_info}:\n{msg.reply_to_text}"
            else:
                description = clean_content
        elif msg.reply_to_text:
            # Текст берется полностью из цитируемого сообщения
            full_text = msg.reply_to_text
            first_line = full_text.split("\n", 1)[0].strip()
            m_sec_in_msg = re.match(r"^\[([a-zA-Zа-яА-ЯёЁ0-9_\-\s]+)\]\s*(.*)$", first_line)
            if not section and m_sec_in_msg:
                cand = m_sec_in_msg.group(1).strip()
                if cand.lower() in sec_map:
                    section = sec_map[cand.lower()]
                    first_line = m_sec_in_msg.group(2).strip()

            if len(first_line) > 100:
                title = first_line[:97] + "..."
            else:
                title = first_line or "Задача из сообщения"

            sender_info = f" (от {msg.reply_to_sender_name})" if msg.reply_to_sender_name else ""
            description = f"💬 Сообщение{sender_info}:\n{full_text}"
        elif screenshots:
            title = "Задача со скриншотом"
            description = "Задача создана со скриншотом"

        if not title and not screenshots:
            self._btn_start_new_task(msg)
            return

        task = self.db.create_task(
            chat_id=msg.chat_id,
            title=title,
            description=description or title,
            created_by=msg.user_id,
            created_by_name=msg.user_name,
            screenshots=screenshots,
            section=section
        )
        sec_str = f"📁 Раздел: **{task.section}**\n" if task.section else ""
        screens_str = f"🖼️ Скриншотов: {len(screenshots)}\n" if screenshots else ""
        from_msg_str = " из сообщения" if (msg.reply_to_text or msg.reply_to_screenshots) else ""
        reply = (
            f"✅ **Задача #{task.task_number} создана{from_msg_str}!**\n\n"
            f"📌 **{task.title}**\n"
            f"{sec_str}"
            f"👤 Автор: {task.created_by_name}\n"
            f"{screens_str}"
        )
        buttons = [
            [
                {"type": "callback", "text": f"✅ Выполнить #{task.task_number}", "payload": f"done:{task.task_number}"},
                {"type": "callback", "text": f"🔍 Инфо #{task.task_number}", "payload": f"info:{task.task_number}"}
            ],
            [
                {"type": "callback", "text": "📋 Все задачи", "payload": "cmd:list"},
                {"type": "callback", "text": "🏠 Главное меню", "payload": "cmd:menu"}
            ]
        ]
        self.api.send_message(msg.chat_id, reply, reply_to_id=msg.message_id, buttons=buttons)

    def _handle_add_to_cmd(self, msg: IncomingMessage, arg_text: str):
        """Добавление пересланного сообщения, текста или фото к существующей задаче."""
        if not self.api.is_user_admin(msg.chat_id, msg.user_id, msg.is_admin):
            self.api.send_message(msg.chat_id, "⛔ Добавлять информацию в задачи могут только администраторы чата.", reply_to_id=msg.message_id)
            return

        # Ищем номер задачи
        task_num = None
        extra_note = ""
        m_num = re.match(r"^(\d+)(?:\s+(.*))?$", arg_text)
        if m_num:
            task_num = int(m_num.group(1))
            extra_note = (m_num.group(2) or "").strip()
        elif msg.reply_to_text:
            m_card = re.search(r"#(\d+)", msg.reply_to_text)
            if m_card:
                task_num = int(m_card.group(1))
                extra_note = arg_text.strip()

        if not task_num:
            self.api.send_message(
                msg.chat_id,
                "⚠️ Укажите номер задачи, в которую нужно добавить сообщение.\n"
                "Пример: ответьте на сообщение командой `/attach 1` или `/add_to 1`",
                reply_to_id=msg.message_id
            )
            return

        task = self.db.get_task_by_number(msg.chat_id, task_num)
        if not task:
            self.api.send_message(msg.chat_id, f"❌ Задача #{task_num} не найдена в этом чате.", reply_to_id=msg.message_id)
            return

        screenshots = list(msg.screenshots)
        if msg.reply_to_screenshots:
            for s in msg.reply_to_screenshots:
                if s not in screenshots:
                    screenshots.append(s)

        add_parts = []
        if extra_note:
            add_parts.append(extra_note)
        if msg.reply_to_text:
            sender_info = f" (от {msg.reply_to_sender_name})" if msg.reply_to_sender_name else ""
            add_parts.append(f"💬 Сообщение{sender_info}:\n{msg.reply_to_text}")

        additional_text = None
        if add_parts:
            additional_text = f"📎 Добавлено ({msg.user_name}):\n" + "\n\n".join(add_parts)

        if not additional_text and not screenshots:
            # Если нет ни текста, ни скриншотов, и команда была просто /attach 1 без reply — переводим в режим ожидания скриншота
            self.db.set_user_state(msg.chat_id, msg.user_id, "WAIT_ATTACH_SCREENSHOT", {"task_number": task_num})
            buttons = [
                [{"type": "callback", "text": "❌ Отмена", "payload": f"info:{task_num}"}]
            ]
            self.api.send_message(
                msg.chat_id,
                f"📷 Отправьте скриншот или фото для прикрепления к задаче #{task_num}:",
                reply_to_id=msg.message_id,
                buttons=buttons
            )
            return

        updated_task = self.db.append_to_task(
            chat_id=msg.chat_id,
            task_number=task_num,
            additional_text=additional_text,
            additional_screenshots=screenshots
        )

        screens_str = f"🖼️ Прикреплено скриншотов: {len(screenshots)}\n" if screenshots else ""
        text_str = "📝 Текст добавлен в описание задачи.\n" if additional_text else ""
        reply = (
            f"📎 **К задаче #{task_num} успешно добавлена информация!**\n\n"
            f"📌 **{updated_task.title if updated_task else task.title}**\n"
            f"{text_str}"
            f"{screens_str}"
        )
        buttons = [
            [
                {"type": "callback", "text": f"🔍 Инфо #{task_num}", "payload": f"info:{task_num}"},
                {"type": "callback", "text": f"✅ Выполнить #{task_num}", "payload": f"done:{task_num}"}
            ],
            [
                {"type": "callback", "text": "📋 Список задач", "payload": "cmd:list"},
                {"type": "callback", "text": "🏠 Главное меню", "payload": "cmd:menu"}
            ]
        ]
        self.api.send_message(msg.chat_id, reply, reply_to_id=msg.message_id, buttons=buttons)

    def _handle_bulk_tasks_cmd(self, msg: IncomingMessage, raw_text: str):
        section = None
        text_to_parse = raw_text
        if raw_text:
            m_sec = re.match(r"^\[([a-zA-Zа-яА-ЯёЁ0-9_\-\s]+)\]\s*(.*)$", raw_text)
            if m_sec:
                section = m_sec.group(1).strip()
                text_to_parse = m_sec.group(2).strip()

        if not text_to_parse and msg.reply_to_text:
            text_to_parse = msg.reply_to_text

        if not text_to_parse:
            self._btn_start_bulk_task(msg)
            return
        self._process_state_bulk_text(msg, text_to_parse, {"section": section, "mode": "__auto__"})

    def _handle_add_section_cmd(self, msg: IncomingMessage, arg_text: str):
        if not arg_text:
            self._btn_prompt_new_section(msg)
            return
        self._process_state_section_name(msg, arg_text)

    def _handle_del_section_cmd(self, msg: IncomingMessage, arg_text: str):
        if not arg_text:
            self._btn_show_delete_sections(msg)
            return
        self._btn_perform_delete_section(msg, arg_text.strip().strip("[]"))

    def _handle_mark_done_cmd(self, msg: IncomingMessage, arg_text: str):
        task_num = None
        if arg_text.isdigit():
            task_num = int(arg_text)
        elif msg.reply_to_text:
            m_card = re.search(r"#(\d+)", msg.reply_to_text)
            if m_card:
                task_num = int(m_card.group(1))

        if task_num:
            self._btn_mark_task_done(msg, task_num)
        else:
            self.api.send_message(msg.chat_id, "⚠️ Укажите номер задачи. Пример: `/done 1`", reply_to_id=msg.message_id)

    def _handle_task_info_cmd(self, msg: IncomingMessage, arg_text: str):
        task_num = None
        if arg_text.isdigit():
            task_num = int(arg_text)
        elif msg.reply_to_text:
            m_card = re.search(r"#(\d+)", msg.reply_to_text)
            if m_card:
                task_num = int(m_card.group(1))

        if task_num:
            self._render_task_info(msg, task_num)
        else:
            self.api.send_message(msg.chat_id, "⚠️ Укажите номер задачи. Пример: `/info 1`", reply_to_id=msg.message_id)

    def _handle_attach_cmd(self, msg: IncomingMessage, arg_text: str):
        self._handle_add_to_cmd(msg, arg_text)

    def _handle_clear_done_cmd(self, msg: IncomingMessage):
        self._btn_prompt_clear_done(msg)

bot = TaskBot()
