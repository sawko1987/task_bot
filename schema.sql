-- Схема таблиц для Yandex Database (YDB) Serverless
-- Выполните этот скрипт в веб-консоли YDB (вкладка "Навигация" -> "SQL-запрос")

-- 1. Таблица задач
CREATE TABLE tasks (
    chat_id Utf8,
    task_number Uint64,
    task_id Utf8,
    title Utf8,
    description Utf8,
    status Utf8,           -- 'OPEN', 'DONE'
    created_by Utf8,
    created_by_name Utf8,
    created_at Utf8,       -- ISO 8601
    completed_by Utf8,
    completed_by_name Utf8,
    completed_at Utf8,     -- ISO 8601
    screenshots_json Utf8, -- JSON массив со ссылками или file_id скриншотов
    section Utf8,          -- Название раздела задачи (опционально)
    PRIMARY KEY (chat_id, task_number)
);

-- 2. Таблица счётчиков номеров задач по чатам
-- Гарантирует, что в каждом чате задачи нумеруются с 1, 2, 3...
CREATE TABLE chat_counters (
    chat_id Utf8,
    last_task_number Uint64,
    PRIMARY KEY (chat_id)
);

-- 3. Таблица разделов для каждого чата (не более 3 на чат)
CREATE TABLE chat_sections (
    chat_id Utf8,
    section_name Utf8,
    created_at Utf8,       -- ISO 8601
    PRIMARY KEY (chat_id, section_name)
);

-- 4. Таблица состояний диалога для управления через кнопки
CREATE TABLE user_states (
    chat_id Utf8,
    user_id Utf8,
    state Utf8,
    state_data Utf8,
    updated_at Utf8,
    PRIMARY KEY (chat_id, user_id)
);


