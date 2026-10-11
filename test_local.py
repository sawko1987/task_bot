"""
Скрипт для локального тестирования логики бота без Yandex Cloud.
Использует встроенную SQLite базу и эмулирует входящие сообщения от мессенджера MAX.
"""
import os
import sys

# Настройка UTF-8 для консоли Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Включаем локальный режим SQLite
os.environ["USE_SQLITE"] = "true"
os.environ["SQLITE_PATH"] = "test_tasks.db"

from models import IncomingMessage
from bot import bot
from db import get_db

def run_tests():
    print("=== Запуск локальных тестов бота ===")
    db = get_db()
    with db._get_conn() as conn:
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM chat_counters")
        conn.commit()
    chat_id = "test_chat_123"

    # 1. Добавление первой задачи со скриншотами
    print("\n1. Тест создания задачи #1 со скриншотами...")
    msg1 = IncomingMessage(
        message_id="m1",
        chat_id=chat_id,
        chat_type="group",
        user_id="user_admin",
        user_name="Админ Александр",
        text="/task Исправить ошибку при загрузке документа",
        screenshots=["https://example.com/screen1.png", "https://example.com/screen2.png"],
        is_admin=True
    )
    bot.handle_message(msg1)

    tasks = db.get_open_tasks(chat_id)
    assert len(tasks) == 1, f"Ожидалась 1 задача, получено {len(tasks)}"
    assert tasks[0].task_number == 1
    assert len(tasks[0].screenshots) == 2
    print(f"✅ Задача #1 успешно создана: '{tasks[0].title}', скриншотов: {len(tasks[0].screenshots)}")

    # 2. Добавление второй задачи
    print("\n2. Тест создания задачи #2...")
    msg2 = IncomingMessage(
        message_id="m2",
        chat_id=chat_id,
        chat_type="group",
        user_id="user_admin",
        user_name="Админ Александр",
        text="/task Обновить базу данных YDB",
        screenshots=[],
        is_admin=True
    )
    bot.handle_message(msg2)
    tasks = db.get_open_tasks(chat_id)
    assert len(tasks) == 2
    print(f"✅ Задача #2 успешно создана: '{tasks[1].title}'")

    # 3. Изоляция по чатам: проверка в другом чате
    print("\n3. Тест изоляции задач по разным чатам...")
    tasks_other = db.get_open_tasks("other_chat_999")
    assert len(tasks_other) == 0, "В другом чате не должно быть задач первого чата!"
    print("✅ Изоляция чатов подтверждена: в другом чате 0 задач.")

    # 4. Отметка задачи #1 как выполненной
    print("\n4. Тест завершения задачи (/done 1)...")
    msg_done = IncomingMessage(
        message_id="m3",
        chat_id=chat_id,
        chat_type="group",
        user_id="user_admin_2",
        user_name="Админ Мария",
        text="/done 1",
        is_admin=True
    )
    bot.handle_message(msg_done)
    open_tasks = db.get_open_tasks(chat_id)
    assert len(open_tasks) == 1, f"Ожидалась 1 открытая задача, получено {len(open_tasks)}"
    assert open_tasks[0].task_number == 2
    task1 = db.get_task_by_number(chat_id, 1)
    assert task1.status == "DONE"
    assert task1.completed_by_name == "Админ Мария"
    print(f"✅ Задача #1 закрыта Марией. Открытых задач осталось: {len(open_tasks)} (Задача #{open_tasks[0].task_number})")

    # 5. Очистка выполненных задач из базы данных (/clear_done)
    print("\n5. Тест очистки выполненных задач (/clear_done)...")
    msg_clear = IncomingMessage(
        message_id="m4",
        chat_id=chat_id,
        chat_type="group",
        user_id="user_admin",
        user_name="Админ Александр",
        text="/clear_done",
        is_admin=True
    )
    bot.handle_message(msg_clear)
    # Проверяем, что задача 1 физически удалена из базы
    deleted_task = db.get_task_by_number(chat_id, 1)
    assert deleted_task is None, "Выполненная задача должна быть удалена из БД!"
    # А задача 2 осталась
    task2 = db.get_task_by_number(chat_id, 2)
    assert task2 is not None and task2.status == "OPEN"
    print(f"✅ База очищена: выполненная задача #1 удалена, активная задача #2 сохранена.")

    print("\n🎉 Все локальные тесты логики успешно пройдены!")

if __name__ == "__main__":
    run_tests()
