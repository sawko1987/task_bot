import json
import logging
import sqlite3
import uuid
from datetime import datetime
from typing import List, Optional
from config import config
from models import Task, TaskStatus

logger = logging.getLogger(__name__)

class BaseDatabase:
    def create_task(self, chat_id: str, title: str, description: str, 
                    created_by: str, created_by_name: str, screenshots: List[str]) -> Task:
        raise NotImplementedError

    def get_open_tasks(self, chat_id: str) -> List[Task]:
        raise NotImplementedError

    def get_task_by_number(self, chat_id: str, task_number: int) -> Optional[Task]:
        raise NotImplementedError

    def mark_task_done(self, chat_id: str, task_number: int, 
                       completed_by: str, completed_by_name: str) -> Optional[Task]:
        raise NotImplementedError

    def attach_screenshots(self, chat_id: str, task_number: int, screenshots: List[str]) -> Optional[Task]:
        raise NotImplementedError

    def delete_completed_tasks(self, chat_id: str) -> int:
        raise NotImplementedError


class SqliteDatabase(BaseDatabase):
    """Локальная база данных SQLite для тестирования и отладки."""
    def __init__(self, db_path: str = "tasks_local.db"):
        self.db_path = db_path
        self._init_tables()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_tables(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    chat_id TEXT,
                    task_number INTEGER,
                    task_id TEXT,
                    title TEXT,
                    description TEXT,
                    status TEXT,
                    created_by TEXT,
                    created_by_name TEXT,
                    created_at TEXT,
                    completed_by TEXT,
                    completed_by_name TEXT,
                    completed_at TEXT,
                    screenshots_json TEXT,
                    PRIMARY KEY (chat_id, task_number)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_counters (
                    chat_id TEXT PRIMARY KEY,
                    last_task_number INTEGER
                )
            """)
            conn.commit()

    def create_task(self, chat_id: str, title: str, description: str,
                    created_by: str, created_by_name: str, screenshots: List[str]) -> Task:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_task_number FROM chat_counters WHERE chat_id = ?", (chat_id,))
            row = cursor.fetchone()
            if row:
                next_number = row[0] + 1
                cursor.execute("UPDATE chat_counters SET last_task_number = ? WHERE chat_id = ?", (next_number, chat_id))
            else:
                next_number = 1
                cursor.execute("INSERT INTO chat_counters (chat_id, last_task_number) VALUES (?, ?)", (chat_id, next_number))

            task_id = str(uuid.uuid4())
            now_iso = datetime.utcnow().isoformat()
            task = Task(
                chat_id=chat_id,
                task_number=next_number,
                task_id=task_id,
                title=title,
                description=description,
                status=TaskStatus.OPEN,
                created_by=created_by,
                created_by_name=created_by_name,
                created_at=now_iso,
                screenshots=screenshots
            )
            cursor.execute("""
                INSERT INTO tasks (
                    chat_id, task_number, task_id, title, description, status,
                    created_by, created_by_name, created_at, completed_by, completed_by_name,
                    completed_at, screenshots_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.chat_id, task.task_number, task.task_id, task.title, task.description,
                task.status, task.created_by, task.created_by_name, task.created_at,
                None, None, None, task.screenshots_to_json()
            ))
            conn.commit()
            return task

    def get_open_tasks(self, chat_id: str) -> List[Task]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT chat_id, task_number, task_id, title, description, status,
                       created_by, created_by_name, created_at, completed_by, completed_by_name,
                       completed_at, screenshots_json
                FROM tasks
                WHERE chat_id = ? AND status = ?
                ORDER BY task_number ASC
            """, (chat_id, TaskStatus.OPEN))
            rows = cursor.fetchall()
            return [self._row_to_task(r) for r in rows]

    def get_task_by_number(self, chat_id: str, task_number: int) -> Optional[Task]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT chat_id, task_number, task_id, title, description, status,
                       created_by, created_by_name, created_at, completed_by, completed_by_name,
                       completed_at, screenshots_json
                FROM tasks
                WHERE chat_id = ? AND task_number = ?
            """, (chat_id, task_number))
            row = cursor.fetchone()
            return self._row_to_task(row) if row else None

    def mark_task_done(self, chat_id: str, task_number: int,
                       completed_by: str, completed_by_name: str) -> Optional[Task]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            task = self.get_task_by_number(chat_id, task_number)
            if not task:
                return None
            now_iso = datetime.utcnow().isoformat()
            cursor.execute("""
                UPDATE tasks
                SET status = ?, completed_by = ?, completed_by_name = ?, completed_at = ?
                WHERE chat_id = ? AND task_number = ?
            """, (TaskStatus.DONE, completed_by, completed_by_name, now_iso, chat_id, task_number))
            conn.commit()
            task.status = TaskStatus.DONE
            task.completed_by = completed_by
            task.completed_by_name = completed_by_name
            task.completed_at = now_iso
            return task

    def attach_screenshots(self, chat_id: str, task_number: int, screenshots: List[str]) -> Optional[Task]:
        with self._get_conn() as conn:
            task = self.get_task_by_number(chat_id, task_number)
            if not task:
                return None
            task.screenshots.extend(screenshots)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tasks
                SET screenshots_json = ?
                WHERE chat_id = ? AND task_number = ?
            """, (task.screenshots_to_json(), chat_id, task_number))
            conn.commit()
            return task

    def delete_completed_tasks(self, chat_id: str) -> int:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tasks WHERE chat_id = ? AND status = ?", (chat_id, TaskStatus.DONE))
            count = cursor.fetchone()[0]
            if count > 0:
                cursor.execute("DELETE FROM tasks WHERE chat_id = ? AND status = ?", (chat_id, TaskStatus.DONE))
                conn.commit()
            return count

    def _row_to_task(self, r) -> Task:
        return Task(
            chat_id=r[0],
            task_number=r[1],
            task_id=r[2],
            title=r[3],
            description=r[4],
            status=r[5],
            created_by=r[6],
            created_by_name=r[7],
            created_at=r[8],
            completed_by=r[9],
            completed_by_name=r[10],
            completed_at=r[11],
            screenshots=Task.screenshots_from_json(r[12])
        )


class YdbDatabase(BaseDatabase):
    """
    Клиент для Yandex Database (YDB) Serverless.
    Использует автоматическую аутентификацию сервисного аккаунта в Yandex Cloud Functions
    через ydb.iam.MetadataUrlCredentials().
    """
    def __init__(self, endpoint: str, database: str):
        self.endpoint = endpoint
        self.database = database
        self._driver = None
        self._pool = None

    def _get_pool(self):
        if self._pool is None:
            try:
                import ydb
                # В Cloud Functions метаданные аккаунта доступны по URL
                credentials = ydb.iam.MetadataUrlCredentials()
                driver_config = ydb.DriverConfig(
                    endpoint=self.endpoint,
                    database=self.database,
                    credentials=credentials,
                )
                self._driver = ydb.Driver(driver_config)
                self._driver.wait(fail_fast=True, timeout=5)
                self._pool = ydb.SessionPool(self._driver)
            except Exception as e:
                logger.error(f"Ошибка подключения к YDB: {e}")
                raise
        return self._pool

    def create_task(self, chat_id: str, title: str, description: str,
                    created_by: str, created_by_name: str, screenshots: List[str]) -> Task:
        pool = self._get_pool()

        def _callee(session):
            # Получаем или инкрементируем счётчик задач для чата
            query_counter = f"""
                DECLARE $chat_id AS Utf8;
                SELECT last_task_number FROM `{self.database}/chat_counters`
                WHERE chat_id = $chat_id;
            """
            prep_q = session.prepare(query_counter)
            res = session.transaction().execute(
                prep_q, {"$chat_id": chat_id}, commit_tx=True
            )
            rows = res[0].rows
            if rows:
                next_number = rows[0]["last_task_number"] + 1
            else:
                next_number = 1

            # Обновляем счётчик
            query_update_counter = f"""
                DECLARE $chat_id AS Utf8;
                DECLARE $next_number AS Uint64;
                UPSERT INTO `{self.database}/chat_counters` (chat_id, last_task_number)
                VALUES ($chat_id, $next_number);
            """
            prep_up = session.prepare(query_update_counter)
            session.transaction().execute(
                prep_up, {"$chat_id": chat_id, "$next_number": next_number}, commit_tx=True
            )

            # Создаём задачу
            task_id = str(uuid.uuid4())
            now_iso = datetime.utcnow().isoformat()
            task = Task(
                chat_id=chat_id,
                task_number=next_number,
                task_id=task_id,
                title=title,
                description=description,
                status=TaskStatus.OPEN,
                created_by=created_by,
                created_by_name=created_by_name,
                created_at=now_iso,
                screenshots=screenshots
            )

            query_insert = f"""
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                DECLARE $task_id AS Utf8;
                DECLARE $title AS Utf8;
                DECLARE $description AS Utf8;
                DECLARE $status AS Utf8;
                DECLARE $created_by AS Utf8;
                DECLARE $created_by_name AS Utf8;
                DECLARE $created_at AS Utf8;
                DECLARE $screenshots_json AS Utf8;

                UPSERT INTO `{self.database}/tasks` (
                    chat_id, task_number, task_id, title, description, status,
                    created_by, created_by_name, created_at, screenshots_json
                ) VALUES (
                    $chat_id, $task_number, $task_id, $title, $description, $status,
                    $created_by, $created_by_name, $created_at, $screenshots_json
                );
            """
            prep_ins = session.prepare(query_insert)
            session.transaction().execute(prep_ins, {
                "$chat_id": task.chat_id,
                "$task_number": task.task_number,
                "$task_id": task.task_id,
                "$title": task.title,
                "$description": task.description,
                "$status": task.status,
                "$created_by": task.created_by,
                "$created_by_name": task.created_by_name,
                "$created_at": task.created_at,
                "$screenshots_json": task.screenshots_to_json(),
            }, commit_tx=True)

            return task

        return pool.retry_operation_sync(_callee)

    def get_open_tasks(self, chat_id: str) -> List[Task]:
        pool = self._get_pool()

        def _callee(session):
            query = f"""
                DECLARE $chat_id AS Utf8;
                DECLARE $status AS Utf8;
                SELECT chat_id, task_number, task_id, title, description, status,
                       created_by, created_by_name, created_at, completed_by, completed_by_name,
                       completed_at, screenshots_json
                FROM `{self.database}/tasks`
                WHERE chat_id = $chat_id AND status = $status
                ORDER BY task_number ASC;
            """
            prep = session.prepare(query)
            res = session.transaction().execute(prep, {
                "$chat_id": chat_id,
                "$status": TaskStatus.OPEN
            }, commit_tx=True)
            tasks = []
            for r in res[0].rows:
                tasks.append(Task(
                    chat_id=r["chat_id"].decode("utf-8") if isinstance(r["chat_id"], bytes) else str(r["chat_id"]),
                    task_number=r["task_number"],
                    task_id=r["task_id"].decode("utf-8") if isinstance(r["task_id"], bytes) else str(r["task_id"]),
                    title=r["title"].decode("utf-8") if isinstance(r["title"], bytes) else str(r["title"]),
                    description=r["description"].decode("utf-8") if isinstance(r["description"], bytes) else str(r["description"]),
                    status=r["status"].decode("utf-8") if isinstance(r["status"], bytes) else str(r["status"]),
                    created_by=str(r.get("created_by", "")),
                    created_by_name=str(r.get("created_by_name", "")),
                    created_at=str(r.get("created_at", "")),
                    completed_by=str(r.get("completed_by", "")) if r.get("completed_by") else None,
                    completed_by_name=str(r.get("completed_by_name", "")) if r.get("completed_by_name") else None,
                    completed_at=str(r.get("completed_at", "")) if r.get("completed_at") else None,
                    screenshots=Task.screenshots_from_json(r.get("screenshots_json", ""))
                ))
            return tasks

        return pool.retry_operation_sync(_callee)

    def get_task_by_number(self, chat_id: str, task_number: int) -> Optional[Task]:
        pool = self._get_pool()

        def _callee(session):
            query = f"""
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                SELECT chat_id, task_number, task_id, title, description, status,
                       created_by, created_by_name, created_at, completed_by, completed_by_name,
                       completed_at, screenshots_json
                FROM `{self.database}/tasks`
                WHERE chat_id = $chat_id AND task_number = $task_number;
            """
            prep = session.prepare(query)
            res = session.transaction().execute(prep, {
                "$chat_id": chat_id,
                "$task_number": task_number
            }, commit_tx=True)
            rows = res[0].rows
            if not rows:
                return None
            r = rows[0]
            return Task(
                chat_id=str(r["chat_id"]),
                task_number=r["task_number"],
                task_id=str(r["task_id"]),
                title=str(r["title"]),
                description=str(r["description"]),
                status=str(r["status"]),
                created_by=str(r.get("created_by", "")),
                created_by_name=str(r.get("created_by_name", "")),
                created_at=str(r.get("created_at", "")),
                completed_by=str(r.get("completed_by")) if r.get("completed_by") else None,
                completed_by_name=str(r.get("completed_by_name")) if r.get("completed_by_name") else None,
                completed_at=str(r.get("completed_at")) if r.get("completed_at") else None,
                screenshots=Task.screenshots_from_json(r.get("screenshots_json", ""))
            )

        return pool.retry_operation_sync(_callee)

    def mark_task_done(self, chat_id: str, task_number: int,
                       completed_by: str, completed_by_name: str) -> Optional[Task]:
        task = self.get_task_by_number(chat_id, task_number)
        if not task:
            return None

        pool = self._get_pool()
        now_iso = datetime.utcnow().isoformat()

        def _callee(session):
            query = f"""
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                DECLARE $status AS Utf8;
                DECLARE $completed_by AS Utf8;
                DECLARE $completed_by_name AS Utf8;
                DECLARE $completed_at AS Utf8;

                UPDATE `{self.database}/tasks`
                SET status = $status, completed_by = $completed_by, 
                    completed_by_name = $completed_by_name, completed_at = $completed_at
                WHERE chat_id = $chat_id AND task_number = $task_number;
            """
            prep = session.prepare(query)
            session.transaction().execute(prep, {
                "$chat_id": chat_id,
                "$task_number": task_number,
                "$status": TaskStatus.DONE,
                "$completed_by": completed_by,
                "$completed_by_name": completed_by_name,
                "$completed_at": now_iso
            }, commit_tx=True)

        pool.retry_operation_sync(_callee)
        task.status = TaskStatus.DONE
        task.completed_by = completed_by
        task.completed_by_name = completed_by_name
        task.completed_at = now_iso
        return task

    def attach_screenshots(self, chat_id: str, task_number: int, screenshots: List[str]) -> Optional[Task]:
        task = self.get_task_by_number(chat_id, task_number)
        if not task:
            return None

        task.screenshots.extend(screenshots)
        pool = self._get_pool()

        def _callee(session):
            query = f"""
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                DECLARE $screenshots_json AS Utf8;

                UPDATE `{self.database}/tasks`
                SET screenshots_json = $screenshots_json
                WHERE chat_id = $chat_id AND task_number = $task_number;
            """
            prep = session.prepare(query)
            session.transaction().execute(prep, {
                "$chat_id": chat_id,
                "$task_number": task_number,
                "$screenshots_json": task.screenshots_to_json()
            }, commit_tx=True)

        pool.retry_operation_sync(_callee)
        return task

    def delete_completed_tasks(self, chat_id: str) -> int:
        pool = self._get_pool()

        def _callee(session):
            # Считаем количество выполненных задач
            count_q = f"""
                DECLARE $chat_id AS Utf8;
                DECLARE $status AS Utf8;
                SELECT COUNT(*) AS cnt FROM `{self.database}/tasks`
                WHERE chat_id = $chat_id AND status = $status;
            """
            prep_cnt = session.prepare(count_q)
            res = session.transaction().execute(prep_cnt, {
                "$chat_id": chat_id,
                "$status": TaskStatus.DONE
            }, commit_tx=True)
            cnt = res[0].rows[0]["cnt"] if res[0].rows else 0

            if cnt > 0:
                delete_q = f"""
                    DECLARE $chat_id AS Utf8;
                    DECLARE $status AS Utf8;
                    DELETE FROM `{self.database}/tasks`
                    WHERE chat_id = $chat_id AND status = $status;
                """
                prep_del = session.prepare(delete_q)
                session.transaction().execute(prep_del, {
                    "$chat_id": chat_id,
                    "$status": TaskStatus.DONE
                }, commit_tx=True)

            return cnt

        return pool.retry_operation_sync(_callee)


def get_db() -> BaseDatabase:
    if config.USE_SQLITE or not config.YDB_ENDPOINT:
        return SqliteDatabase(config.SQLITE_PATH)
    return YdbDatabase(config.YDB_ENDPOINT, config.YDB_DATABASE)
