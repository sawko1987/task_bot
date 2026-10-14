"""
Скрипт для комплексного локального тестирования кнопочного управления ботом.
Тестирует полный цикл взаимодействия через инлайн-кнопки (callback) и состояния (FSM).
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ["USE_SQLITE"] = "true"
os.environ["SQLITE_PATH"] = "test_tasks.db"

from models import IncomingMessage
from bot import bot, PAGE_SIZE
from db import get_db

def run_tests():
    print("=== Запуск комплексных тестов кнопочного управления ботом ===")
    db = get_db()
    with db._get_conn() as conn:
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM chat_counters")
        conn.execute("DELETE FROM chat_sections")
        conn.execute("DELETE FROM user_states")
        conn.commit()

    chat_id = "test_chat_buttons"
    admin_id = "admin_user"
    admin_name = "Александр"

    # ========================================================
    # 1. ТЕСТ: Создание раздела через кнопки
    # ========================================================
    print("\n1. Тест создания разделов исключительно через кнопки...")

    # Нажатие кнопки [➕ Создать раздел]
    bot.handle_message(IncomingMessage(
        message_id="cb1", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call1", callback_payload="cmd:add_sec_prompt",
        is_admin=True
    ))
    state = db.get_user_state(chat_id, admin_id)
    assert state is not None and state[0] == "WAIT_SECTION_NAME", "Ожидалось состояние WAIT_SECTION_NAME"

    # Пользователь отправляет название раздела текстом (без всяких слеш-команд!)
    bot.handle_message(IncomingMessage(
        message_id="msg_sec1", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="Backend", is_admin=True
    ))
    assert db.get_user_state(chat_id, admin_id) is None, "Состояние должно очиститься после ввода"
    assert db.get_sections(chat_id) == ["Backend"]
    print("✅ Раздел «Backend» успешно создан через кнопку!")

    # Создаем 2-й и 3-й раздел
    bot.handle_message(IncomingMessage(
        message_id="cb2", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call2", callback_payload="cmd:add_sec_prompt", is_admin=True
    ))
    bot.handle_message(IncomingMessage(
        message_id="msg_sec2", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="Frontend", is_admin=True
    ))

    bot.handle_message(IncomingMessage(
        message_id="cb3", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call3", callback_payload="cmd:add_sec_prompt", is_admin=True
    ))
    bot.handle_message(IncomingMessage(
        message_id="msg_sec3", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="DevOps", is_admin=True
    ))
    assert len(db.get_sections(chat_id)) == 3
    print(f"✅ Созданы 3 раздела: {db.get_sections(chat_id)}")

    # ========================================================
    # 2. ТЕСТ: Удаление раздела через кнопки
    # ========================================================
    print("\n2. Тест удаления раздела через кнопки...")
    # Нажатие кнопки [🗑️ Удалить «DevOps»]
    bot.handle_message(IncomingMessage(
        message_id="cb_del", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call4", callback_payload="do_del_sec:DevOps", is_admin=True
    ))
    assert db.get_sections(chat_id) == ["Backend", "Frontend"]
    print("✅ Раздел «DevOps» успешно удалён в один клик по кнопке!")

    # ========================================================
    # 3. ТЕСТ: Создание одиночной задачи через кнопки
    # ========================================================
    print("\n3. Тест создания задачи через кнопки (выбор раздела + ввод текста)...")
    # 1. Нажатие кнопки [➕ Новая задача]
    bot.handle_message(IncomingMessage(
        message_id="cb_nt", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call5", callback_payload="cmd:new_task", is_admin=True
    ))

    # 2. Нажатие кнопки раздела [📁 Backend]
    bot.handle_message(IncomingMessage(
        message_id="cb_sec_pick", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call6", callback_payload="pick_task_sec:Backend", is_admin=True
    ))
    st = db.get_user_state(chat_id, admin_id)
    assert st is not None and st[0] == "WAIT_TASK_TEXT"
    assert st[1].get("section") == "Backend"

    # 3. Отправка текста задачи со скриншотом обычным сообщением
    bot.handle_message(IncomingMessage(
        message_id="msg_t1", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="Спроектировать схему базы данных PostgreSQL",
        screenshots=["https://example.com/db_schema.png"],
        is_admin=True
    ))
    t1 = db.get_task_by_number(chat_id, 1)
    assert t1 is not None
    assert t1.title == "Спроектировать схему базы данных PostgreSQL"
    assert t1.section == "Backend"
    assert len(t1.screenshots) == 1
    print(f"✅ Задача #1 успешно создана через кнопки: [{t1.section}] '{t1.title}'")

    # ========================================================
    # 4. ТЕСТ: Массовое добавление 50 задач через кнопки
    # ========================================================
    print("\n4. Тест массового добавления 50 задач через кнопки...")
    # 1. Нажатие кнопки [📥 Чек-лист]
    bot.handle_message(IncomingMessage(
        message_id="cb_bulk", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call7", callback_payload="cmd:bulk_task", is_admin=True
    ))

    # 2. Выбор режима [✨ Авто-определение]
    bot.handle_message(IncomingMessage(
        message_id="cb_bulk_auto", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call8", callback_payload="pick_bulk_sec:__auto__", is_admin=True
    ))
    assert db.get_user_state(chat_id, admin_id)[0] == "WAIT_BULK_TEXT"

    # 3. Отправка 50 задач от нейросети
    bulk_tasks = [
        "## Backend",
        "- [ ] 1. Авторизация JWT",
        "- [ ] 2. Хеширование паролей",
        "- [ ] 3. Валидация входных данных",
        "- [ ] 4. Настройка CORS",
        "- [ ] 5. Rate limiting эндпоинтов",
        "- [ ] 6. Логирование запросов",
        "- [ ] 7. Интеграция с YDB",
        "- [ ] 8. Тестирование контроллеров",
        "- [ ] 9. Оптимизация индексов",
        "- [ ] 10. Swagger документация",
        "- [ ] 11. Подготовка Dockerfile",
        "- [ ] 12. Healthcheck эндпоинт",
        "- [ ] 13. Экспорт метрик Prometheus",
        "- [ ] 14. Настройка Sentry",
        "- [ ] 15. Рефакторинг моделей",
        "- [ ] 16. Скрипт сидинга данных",
        "- [ ] 17. Обработка 404 и 500 ошибок",
        "- [ ] 18. Кэширование ответов",
        "- [ ] 19. Асинхронные воркеры",
        "- [ ] 20. Финальная сборка API",
        "## Frontend",
        "1. Инициализация Vite проекта",
        "2. Настройка Tailwind",
        "3. Верстка формы входа",
        "4. Верстка формы регистрации",
        "5. Компонент модальных окон",
        "6. Компонент кнопок",
        "7. Интеграция Axios",
        "8. Сохранение токенов",
        "9. Защищенные роуты",
        "10. Таблица задач",
        "11. Пагинация по 10 задач",
        "12. Фильтры разделов",
        "13. Индикаторы загрузки",
        "14. Скелетоны карточек",
        "15. Toast уведомления",
        "16. Адаптивность для мобильных",
        "17. Обработка ошибок сети",
        "18. Тесты компонентов",
        "19. Оптимизация бандла",
        "20. Развертывание SPA",
        "## Без раздела",
        "- Задача развертывания 1",
        "- Задача развертывания 2",
        "- Задача развертывания 3",
        "- Задача развертывания 4",
        "- Задача развертывания 5",
        "- Задача развертывания 6",
        "- Задача развертывания 7",
        "- Задача развертывания 8",
        "- Задача развертывания 9",
        "- Задача развертывания 10"
    ]
    bot.handle_message(IncomingMessage(
        message_id="msg_bulk", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="\n".join(bulk_tasks), is_admin=True
    ))

    all_open = db.get_open_tasks(chat_id)
    # 1 одиночная + 50 массовых = 51 задача
    assert len(all_open) == 51
    assert all_open[0].task_number == 1
    assert all_open[-1].task_number == 51
    print(f"✅ Успешно создано {len(all_open)} задач (#1 – #51) полностью через кнопки!")

    # ========================================================
    # 5. ТЕСТ: Карточка задачи и смена раздела по кнопке
    # ========================================================
    print("\n5. Тест смены раздела задачи через инлайн-кнопки...")
    # Нажатие [📁 Сменить раздел] для задачи #1
    bot.handle_message(IncomingMessage(
        message_id="cb_ch_sec", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call9", callback_payload="set_task_sec:1:Frontend", is_admin=True
    ))
    t1_updated = db.get_task_by_number(chat_id, 1)
    assert t1_updated.section == "Frontend"
    print("✅ Раздел задачи #1 успешно изменён на Frontend нажатием кнопки!")

    # ========================================================
    # 6. ТЕСТ: Удаление задачи по кнопке
    # ========================================================
    print("\n6. Тест удаления задачи через кнопку...")
    bot.handle_message(IncomingMessage(
        message_id="cb_del_t", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call10", callback_payload="do_del_task:1", is_admin=True
    ))
    assert db.get_task_by_number(chat_id, 1) is None
    assert len(db.get_open_tasks(chat_id)) == 50
    print("✅ Задача #1 успешно удалена нажатием кнопки!")

    # ========================================================
    # 7. ТЕСТ: Выполнение задачи и очистка базы через кнопки
    # ========================================================
    print("\n7. Тест отметки выполнения и очистки через кнопки...")
    # Выполнение задачи #2
    bot.handle_message(IncomingMessage(
        message_id="cb_done", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call11", callback_payload="done:2", is_admin=True
    ))
    t2 = db.get_task_by_number(chat_id, 2)
    assert t2.status == "DONE"

    # Очистка базы по кнопке [Да, очистить]
    bot.handle_message(IncomingMessage(
        message_id="cb_clear", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call12", callback_payload="do_clear_done", is_admin=True
    ))
    assert db.get_task_by_number(chat_id, 2) is None
    print("✅ Выполнение и очистка через кнопки сработали корректно!")

    # ========================================================
    # 8. ТЕСТ: Отмена действия по кнопке [❌ Отмена]
    # ========================================================
    print("\n8. Тест кнопки [❌ Отмена]...")
    bot.handle_message(IncomingMessage(
        message_id="cb_cancel1", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call13", callback_payload="cmd:new_task", is_admin=True
    ))
    bot.handle_message(IncomingMessage(
        message_id="cb_cancel2", chat_id=chat_id, chat_type="group",
        user_id=admin_id, user_name=admin_name,
        text="", callback_id="call14", callback_payload="cancel_state", is_admin=True
    ))
    assert db.get_user_state(chat_id, admin_id) is None
    print("✅ Кнопка отмены моментально сбрасывает режим ввода!")

    print("\n🎉 Все тесты 100% кнопочного управления успешно пройдены!")

if __name__ == "__main__":
    run_tests()
