import json
import logging
import os
import sqlite3
import urllib.parse
import uuid
from datetime import datetime
from typing import List, Optional, Tuple
from config import config
from models import Task, TaskStatus

logger = logging.getLogger(__name__)

class BaseDatabase:
    def create_task(self, chat_id: str, title: str, description: str, 
                    created_by: str, created_by_name: str, screenshots: List[str],
                    section: Optional[str] = None) -> Task:
        raise NotImplementedError

    def create_tasks_bulk(self, chat_id: str, task_items: List[dict],
                          created_by: str, created_by_name: str) -> List[Task]:
        raise NotImplementedError

    def get_open_tasks(self, chat_id: str, section: Optional[str] = None) -> List[Task]:
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

    def delete_task(self, chat_id: str, task_number: int) -> bool:
        raise NotImplementedError

    def update_task_section(self, chat_id: str, task_number: int, section: Optional[str]) -> Optional[Task]:
        raise NotImplementedError

    def get_sections(self, chat_id: str) -> List[str]:
        raise NotImplementedError

    def add_section(self, chat_id: str, section_name: str) -> bool:
        raise NotImplementedError

    def delete_section(self, chat_id: str, section_name: str) -> bool:
        raise NotImplementedError

    def set_user_state(self, chat_id: str, user_id: str, state: str, data: Optional[dict] = None):
        raise NotImplementedError

    def get_user_state(self, chat_id: str, user_id: str) -> Optional[Tuple[str, dict]]:
        raise NotImplementedError

    def clear_user_state(self, chat_id: str, user_id: str):
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
                    section TEXT,
                    PRIMARY KEY (chat_id, task_number)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_counters (
                    chat_id TEXT PRIMARY KEY,
                    last_task_number INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_sections (
                    chat_id TEXT,
                    section_name TEXT,
                    created_at TEXT,
                    PRIMARY KEY (chat_id, section_name)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_states (
                    chat_id TEXT,
                    user_id TEXT,
                    state TEXT,
                    state_data TEXT,
                    updated_at TEXT,
                    PRIMARY KEY (chat_id, user_id)
                )
            """)
            # Миграция: если колонка section еще не добавлена
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN section TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def set_user_state(self, chat_id: str, user_id: str, state: str, data: Optional[dict] = None):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            now_iso = datetime.utcnow().isoformat()
            data_json = json.dumps(data or {}, ensure_ascii=False)
            cursor.execute("SELECT chat_id FROM user_states WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            if cursor.fetchone():
                cursor.execute("""
                    UPDATE user_states SET state = ?, state_data = ?, updated_at = ?
                    WHERE chat_id = ? AND user_id = ?
                """, (state, data_json, now_iso, chat_id, user_id))
            else:
                cursor.execute("""
                    INSERT INTO user_states (chat_id, user_id, state, state_data, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (chat_id, user_id, state, data_json, now_iso))
            conn.commit()

    def get_user_state(self, chat_id: str, user_id: str) -> Optional[Tuple[str, dict]]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT state, state_data FROM user_states WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            row = cursor.fetchone()
            if not row or not row[0]:
                return None
            try:
                data = json.loads(row[1]) if row[1] else {}
            except Exception:
                data = {}
            return row[0], data

    def clear_user_state(self, chat_id: str, user_id: str):
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_states WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
            conn.commit()

    def delete_task(self, chat_id: str, task_number: int) -> bool:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks WHERE chat_id = ? AND task_number = ?", (chat_id, task_number))
            conn.commit()
            return cursor.rowcount > 0

    def update_task_section(self, chat_id: str, task_number: int, section: Optional[str]) -> Optional[Task]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE tasks SET section = ? WHERE chat_id = ? AND task_number = ?", (section, chat_id, task_number))
            conn.commit()
            return self.get_task_by_number(chat_id, task_number)

    def get_sections(self, chat_id: str) -> List[str]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT section_name FROM chat_sections WHERE chat_id = ? ORDER BY created_at ASC", (chat_id,))
            return [r[0] for r in cursor.fetchall()]

    def add_section(self, chat_id: str, section_name: str) -> bool:
        name = section_name.strip()
        if not name:
            return False
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT section_name FROM chat_sections WHERE chat_id = ?", (chat_id,))
            existing = [r[0].lower() for r in cursor.fetchall()]
            if len(existing) >= 3 or name.lower() in existing:
                return False
            now_iso = datetime.utcnow().isoformat()
            cursor.execute("INSERT INTO chat_sections (chat_id, section_name, created_at) VALUES (?, ?, ?)",
                           (chat_id, name, now_iso))
            conn.commit()
            return True

    def delete_section(self, chat_id: str, section_name: str) -> bool:
        name = section_name.strip()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT section_name FROM chat_sections WHERE chat_id = ?", (chat_id,))
            rows = cursor.fetchall()
            target_name = None
            for r in rows:
                if r[0].lower() == name.lower():
                    target_name = r[0]
                    break
            if not target_name:
                return False
            cursor.execute("DELETE FROM chat_sections WHERE chat_id = ? AND section_name = ?", (chat_id, target_name))
            cursor.execute("UPDATE tasks SET section = NULL WHERE chat_id = ? AND section = ?", (chat_id, target_name))
            conn.commit()
            return True

    def create_task(self, chat_id: str, title: str, description: str,
                    created_by: str, created_by_name: str, screenshots: List[str],
                    section: Optional[str] = None) -> Task:
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
                screenshots=screenshots,
                section=section
            )
            cursor.execute("""
                INSERT INTO tasks (
                    chat_id, task_number, task_id, title, description, status,
                    created_by, created_by_name, created_at, completed_by, completed_by_name,
                    completed_at, screenshots_json, section
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.chat_id, task.task_number, task.task_id, task.title, task.description,
                task.status, task.created_by, task.created_by_name, task.created_at,
                None, None, None, task.screenshots_to_json(), task.section
            ))
            conn.commit()
            return task

    def create_tasks_bulk(self, chat_id: str, task_items: List[dict],
                          created_by: str, created_by_name: str) -> List[Task]:
        if not task_items:
            return []
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_task_number FROM chat_counters WHERE chat_id = ?", (chat_id,))
            row = cursor.fetchone()
            current_num = row[0] if row else 0

            created_tasks = []
            now_iso = datetime.utcnow().isoformat()
            insert_rows = []

            for item in task_items:
                current_num += 1
                task_id = str(uuid.uuid4())
                screenshots = item.get("screenshots") or []
                sec = item.get("section")
                title = item["title"]
                description = item.get("description", title)
                task = Task(
                    chat_id=chat_id,
                    task_number=current_num,
                    task_id=task_id,
                    title=title,
                    description=description,
                    status=TaskStatus.OPEN,
                    created_by=created_by,
                    created_by_name=created_by_name,
                    created_at=now_iso,
                    screenshots=screenshots,
                    section=sec
                )
                created_tasks.append(task)
                insert_rows.append((
                    task.chat_id, task.task_number, task.task_id, task.title, task.description,
                    task.status, task.created_by, task.created_by_name, task.created_at,
                    None, None, None, task.screenshots_to_json(), task.section
                ))

            cursor.execute("SELECT chat_id FROM chat_counters WHERE chat_id = ?", (chat_id,))
            if cursor.fetchone():
                cursor.execute("UPDATE chat_counters SET last_task_number = ? WHERE chat_id = ?", (current_num, chat_id))
            else:
                cursor.execute("INSERT INTO chat_counters (chat_id, last_task_number) VALUES (?, ?)", (chat_id, current_num))

            cursor.executemany("""
                INSERT INTO tasks (
                    chat_id, task_number, task_id, title, description, status,
                    created_by, created_by_name, created_at, completed_by, completed_by_name,
                    completed_at, screenshots_json, section
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, insert_rows)
            conn.commit()
            return created_tasks

    def get_open_tasks(self, chat_id: str, section: Optional[str] = None) -> List[Task]:
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if section:
                cursor.execute("""
                    SELECT chat_id, task_number, task_id, title, description, status,
                           created_by, created_by_name, created_at, completed_by, completed_by_name,
                           completed_at, screenshots_json, section
                    FROM tasks
                    WHERE chat_id = ? AND status = ? AND LOWER(section) = LOWER(?)
                    ORDER BY task_number ASC
                """, (chat_id, TaskStatus.OPEN, section))
            else:
                cursor.execute("""
                    SELECT chat_id, task_number, task_id, title, description, status,
                           created_by, created_by_name, created_at, completed_by, completed_by_name,
                           completed_at, screenshots_json, section
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
                       completed_at, screenshots_json, section
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
            screenshots=Task.screenshots_from_json(r[12]),
            section=r[13] if len(r) > 13 and r[13] else None
        )


class YdbDatabase(BaseDatabase):
    """
    Клиент для Yandex Database (YDB) Serverless.
    Использует автоматическую аутентификацию сервисного аккаунта в Yandex Cloud Functions
    через ydb.iam.MetadataUrlCredentials().
    """
    def __init__(self, endpoint: str, database: str):
        if endpoint and ("?" in endpoint or "database=" in endpoint):
            parsed = urllib.parse.urlparse(endpoint)
            query_params = urllib.parse.parse_qs(parsed.query)
            clean_endpoint = f"{parsed.scheme}://{parsed.netloc}"
            if not database and "database" in query_params:
                database = query_params["database"][0]
            endpoint = clean_endpoint

        self.endpoint = (endpoint or "").rstrip("/")
        self.database = ("/" + database.strip("/")) if database else ""
        self._driver = None
        self._pool = None

    def _init_tables_ydb(self):
        """Автоматическая проверка и создание таблиц, если они ещё не созданы."""
        def _callee(session):
            for table_sql in [
                """
                CREATE TABLE IF NOT EXISTS `tasks` (
                    chat_id Utf8,
                    task_number Uint64,
                    task_id Utf8,
                    title Utf8,
                    description Utf8,
                    status Utf8,
                    created_by Utf8,
                    created_by_name Utf8,
                    created_at Utf8,
                    completed_by Utf8,
                    completed_by_name Utf8,
                    completed_at Utf8,
                    screenshots_json Utf8,
                    section Utf8,
                    PRIMARY KEY (chat_id, task_number)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS `chat_counters` (
                    chat_id Utf8,
                    last_task_number Uint64,
                    PRIMARY KEY (chat_id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS `chat_sections` (
                    chat_id Utf8,
                    section_name Utf8,
                    created_at Utf8,
                    PRIMARY KEY (chat_id, section_name)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS `user_states` (
                    chat_id Utf8,
                    user_id Utf8,
                    state Utf8,
                    state_data Utf8,
                    updated_at Utf8,
                    PRIMARY KEY (chat_id, user_id)
                );
                """
            ]:
                try:
                    session.execute_scheme(table_sql)
                except Exception as e:
                    logger.info(f"Таблица уже создана или: {e}")

            try:
                session.execute_scheme("ALTER TABLE `tasks` ADD COLUMN section Utf8;")
            except Exception:
                pass

        try:
            self._pool.retry_operation_sync(_callee)
        except Exception as e:
            logger.warning(f"Не удалось выполнить execute_scheme: {e}")

    def _get_pool(self):
        if self._pool is None:
            try:
                import ydb
                credentials = ydb.iam.MetadataUrlCredentials()
                driver_config = ydb.DriverConfig(
                    endpoint=self.endpoint,
                    database=self.database,
                    credentials=credentials,
                )
                self._driver = ydb.Driver(driver_config)
                self._driver.wait(fail_fast=True, timeout=10)
                self._pool = ydb.SessionPool(self._driver)
                self._init_tables_ydb()
            except Exception as e:
                err_text = str(e).strip() or repr(e) or e.__class__.__name__
                logger.error(f"Ошибка подключения к YDB ({self.endpoint}, {self.database}): {err_text}")
                raise RuntimeError(f"YDB connect error ({self.endpoint}): {err_text}") from e
        return self._pool

    def set_user_state(self, chat_id: str, user_id: str, state: str, data: Optional[dict] = None):
        pool = self._get_pool()
        now_iso = datetime.utcnow().isoformat()
        data_json = json.dumps(data or {}, ensure_ascii=False)
        def _callee(session):
            query = """
                DECLARE $chat_id AS Utf8;
                DECLARE $user_id AS Utf8;
                DECLARE $state AS Utf8;
                DECLARE $state_data AS Utf8;
                DECLARE $updated_at AS Utf8;
                UPSERT INTO `user_states` (chat_id, user_id, state, state_data, updated_at)
                VALUES ($chat_id, $user_id, $state, $state_data, $updated_at);
            """
            prep = session.prepare(query)
            session.transaction().execute(prep, {
                "$chat_id": chat_id,
                "$user_id": user_id,
                "$state": state,
                "$state_data": data_json,
                "$updated_at": now_iso
            }, commit_tx=True)
        pool.retry_operation_sync(_callee)

    def get_user_state(self, chat_id: str, user_id: str) -> Optional[Tuple[str, dict]]:
        pool = self._get_pool()
        def _callee(session):
            query = """
                DECLARE $chat_id AS Utf8;
                DECLARE $user_id AS Utf8;
                SELECT state, state_data FROM `user_states`
                WHERE chat_id = $chat_id AND user_id = $user_id;
            """
            prep = session.prepare(query)
            res = session.transaction().execute(prep, {"$chat_id": chat_id, "$user_id": user_id}, commit_tx=True)
            rows = res[0].rows
            if not rows:
                return None
            r = rows[0]
            st = r["state"].decode("utf-8") if isinstance(r["state"], bytes) else str(r["state"])
            sd_raw = r["state_data"].decode("utf-8") if isinstance(r["state_data"], bytes) else str(r["state_data"])
            try:
                sd = json.loads(sd_raw) if sd_raw else {}
            except Exception:
                sd = {}
            return st, sd
        return pool.retry_operation_sync(_callee)

    def clear_user_state(self, chat_id: str, user_id: str):
        pool = self._get_pool()
        def _callee(session):
            query = """
                DECLARE $chat_id AS Utf8;
                DECLARE $user_id AS Utf8;
                DELETE FROM `user_states`
                WHERE chat_id = $chat_id AND user_id = $user_id;
            """
            prep = session.prepare(query)
            session.transaction().execute(prep, {"$chat_id": chat_id, "$user_id": user_id}, commit_tx=True)
        pool.retry_operation_sync(_callee)

    def delete_task(self, chat_id: str, task_number: int) -> bool:
        pool = self._get_pool()
        def _callee(session):
            query = """
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                DELETE FROM `tasks`
                WHERE chat_id = $chat_id AND task_number = $task_number;
            """
            prep = session.prepare(query)
            session.transaction().execute(prep, {"$chat_id": chat_id, "$task_number": task_number}, commit_tx=True)
            return True
        return pool.retry_operation_sync(_callee)

    def update_task_section(self, chat_id: str, task_number: int, section: Optional[str]) -> Optional[Task]:
        pool = self._get_pool()
        def _callee(session):
            query = """
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                DECLARE $section AS Utf8;
                UPDATE `tasks`
                SET section = $section
                WHERE chat_id = $chat_id AND task_number = $task_number;
            """
            prep = session.prepare(query)
            session.transaction().execute(prep, {
                "$chat_id": chat_id,
                "$task_number": task_number,
                "$section": section or ""
            }, commit_tx=True)
            return True
        pool.retry_operation_sync(_callee)
        return self.get_task_by_number(chat_id, task_number)

    def get_sections(self, chat_id: str) -> List[str]:
        pool = self._get_pool()
        def _callee(session):
            query = """
                DECLARE $chat_id AS Utf8;
                SELECT section_name FROM `chat_sections`
                WHERE chat_id = $chat_id;
            """
            prep = session.prepare(query)
            res = session.transaction().execute(prep, {"$chat_id": chat_id}, commit_tx=True)
            sections = []
            for r in res[0].rows:
                name = r["section_name"].decode("utf-8") if isinstance(r["section_name"], bytes) else str(r["section_name"])
                sections.append(name)
            return sections
        return pool.retry_operation_sync(_callee)

    def add_section(self, chat_id: str, section_name: str) -> bool:
        name = section_name.strip()
        if not name:
            return False
        pool = self._get_pool()
        now_iso = datetime.utcnow().isoformat()

        def _callee(session):
            check_q = """
                DECLARE $chat_id AS Utf8;
                SELECT section_name FROM `chat_sections`
                WHERE chat_id = $chat_id;
            """
            prep_chk = session.prepare(check_q)
            res = session.transaction().execute(prep_chk, {"$chat_id": chat_id}, commit_tx=True)
            existing = [
                (r["section_name"].decode("utf-8") if isinstance(r["section_name"], bytes) else str(r["section_name"])).strip().lower()
                for r in res[0].rows
            ]
            if len(existing) >= 3 or name.lower() in existing:
                return False

            ins_q = """
                DECLARE $chat_id AS Utf8;
                DECLARE $section_name AS Utf8;
                DECLARE $created_at AS Utf8;
                UPSERT INTO `chat_sections` (chat_id, section_name, created_at)
                VALUES ($chat_id, $section_name, $created_at);
            """
            prep_ins = session.prepare(ins_q)
            session.transaction().execute(prep_ins, {
                "$chat_id": chat_id,
                "$section_name": name,
                "$created_at": now_iso
            }, commit_tx=True)
            return True

        return pool.retry_operation_sync(_callee)

    def delete_section(self, chat_id: str, section_name: str) -> bool:
        name = section_name.strip()
        pool = self._get_pool()

        def _callee(session):
            find_q = """
                DECLARE $chat_id AS Utf8;
                SELECT section_name FROM `chat_sections`
                WHERE chat_id = $chat_id;
            """
            prep_find = session.prepare(find_q)
            res = session.transaction().execute(prep_find, {"$chat_id": chat_id}, commit_tx=True)
            target_name = None
            for r in res[0].rows:
                sec_text = r["section_name"].decode("utf-8") if isinstance(r["section_name"], bytes) else str(r["section_name"])
                if sec_text.strip().lower() == name.lower():
                    target_name = sec_text
                    break
            if not target_name:
                return False

            del_q = """
                DECLARE $chat_id AS Utf8;
                DECLARE $section_name AS Utf8;
                DELETE FROM `chat_sections`
                WHERE chat_id = $chat_id AND section_name = $section_name;
            """
            prep_del = session.prepare(del_q)
            session.transaction().execute(prep_del, {
                "$chat_id": chat_id,
                "$section_name": target_name
            }, commit_tx=True)
            return True

        return pool.retry_operation_sync(_callee)

    def create_task(self, chat_id: str, title: str, description: str,
                    created_by: str, created_by_name: str, screenshots: List[str],
                    section: Optional[str] = None) -> Task:
        pool = self._get_pool()

        def _callee(session):
            query_counter = """
                DECLARE $chat_id AS Utf8;
                SELECT last_task_number FROM `chat_counters`
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

            query_update_counter = """
                DECLARE $chat_id AS Utf8;
                DECLARE $next_number AS Uint64;
                UPSERT INTO `chat_counters` (chat_id, last_task_number)
                VALUES ($chat_id, $next_number);
            """
            prep_up = session.prepare(query_update_counter)
            session.transaction().execute(
                prep_up, {"$chat_id": chat_id, "$next_number": next_number}, commit_tx=True
            )

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
                screenshots=screenshots,
                section=section
            )

            query_insert = """
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
                DECLARE $section AS Utf8;

                UPSERT INTO `tasks` (
                    chat_id, task_number, task_id, title, description, status,
                    created_by, created_by_name, created_at, screenshots_json, section
                ) VALUES (
                    $chat_id, $task_number, $task_id, $title, $description, $status,
                    $created_by, $created_by_name, $created_at, $screenshots_json, $section
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
                "$section": task.section or ""
            }, commit_tx=True)

            return task

        return pool.retry_operation_sync(_callee)

    def create_tasks_bulk(self, chat_id: str, task_items: List[dict],
                          created_by: str, created_by_name: str) -> List[Task]:
        if not task_items:
            return []
        pool = self._get_pool()

        def _callee(session):
            query_counter = """
                DECLARE $chat_id AS Utf8;
                SELECT last_task_number FROM `chat_counters`
                WHERE chat_id = $chat_id;
            """
            prep_q = session.prepare(query_counter)
            res = session.transaction().execute(prep_q, {"$chat_id": chat_id}, commit_tx=True)
            rows = res[0].rows
            current_num = rows[0]["last_task_number"] if rows else 0

            now_iso = datetime.utcnow().isoformat()
            created_tasks = []

            for item in task_items:
                current_num += 1
                task_id = str(uuid.uuid4())
                screenshots = item.get("screenshots") or []
                sec = item.get("section")
                title = item["title"]
                description = item.get("description", title)
                task = Task(
                    chat_id=chat_id,
                    task_number=current_num,
                    task_id=task_id,
                    title=title,
                    description=description,
                    status=TaskStatus.OPEN,
                    created_by=created_by,
                    created_by_name=created_by_name,
                    created_at=now_iso,
                    screenshots=screenshots,
                    section=sec
                )
                created_tasks.append(task)

            query_update_counter = """
                DECLARE $chat_id AS Utf8;
                DECLARE $next_number AS Uint64;
                UPSERT INTO `chat_counters` (chat_id, last_task_number)
                VALUES ($chat_id, $next_number);
            """
            prep_up = session.prepare(query_update_counter)
            session.transaction().execute(prep_up, {
                "$chat_id": chat_id,
                "$next_number": current_num
            }, commit_tx=True)

            query_insert = """
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
                DECLARE $section AS Utf8;

                UPSERT INTO `tasks` (
                    chat_id, task_number, task_id, title, description, status,
                    created_by, created_by_name, created_at, screenshots_json, section
                ) VALUES (
                    $chat_id, $task_number, $task_id, $title, $description, $status,
                    $created_by, $created_by_name, $created_at, $screenshots_json, $section
                );
            """
            prep_ins = session.prepare(query_insert)
            for t in created_tasks:
                session.transaction().execute(prep_ins, {
                    "$chat_id": t.chat_id,
                    "$task_number": t.task_number,
                    "$task_id": t.task_id,
                    "$title": t.title,
                    "$description": t.description,
                    "$status": t.status,
                    "$created_by": t.created_by,
                    "$created_by_name": t.created_by_name,
                    "$created_at": t.created_at,
                    "$screenshots_json": t.screenshots_to_json(),
                    "$section": t.section or ""
                }, commit_tx=True)

            return created_tasks

        return pool.retry_operation_sync(_callee)

    def get_open_tasks(self, chat_id: str, section: Optional[str] = None) -> List[Task]:
        pool = self._get_pool()

        def _callee(session):
            if section:
                query = """
                    DECLARE $chat_id AS Utf8;
                    DECLARE $status AS Utf8;
                    DECLARE $section AS Utf8;
                    SELECT chat_id, task_number, task_id, title, description, status,
                           created_by, created_by_name, created_at, completed_by, completed_by_name,
                           completed_at, screenshots_json, section
                    FROM `tasks`
                    WHERE chat_id = $chat_id AND status = $status AND section = $section
                    ORDER BY task_number ASC;
                """
                prep = session.prepare(query)
                res = session.transaction().execute(prep, {
                    "$chat_id": chat_id,
                    "$status": TaskStatus.OPEN,
                    "$section": section
                }, commit_tx=True)
            else:
                query = """
                    DECLARE $chat_id AS Utf8;
                    DECLARE $status AS Utf8;
                    SELECT chat_id, task_number, task_id, title, description, status,
                           created_by, created_by_name, created_at, completed_by, completed_by_name,
                           completed_at, screenshots_json, section
                    FROM `tasks`
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
                sec_val = r.get("section")
                if sec_val:
                    sec_str = sec_val.decode("utf-8") if isinstance(sec_val, bytes) else str(sec_val)
                    sec_str = sec_str.strip() or None
                else:
                    sec_str = None

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
                    screenshots=Task.screenshots_from_json(r.get("screenshots_json", "")),
                    section=sec_str
                ))
            return tasks

        return pool.retry_operation_sync(_callee)

    def get_task_by_number(self, chat_id: str, task_number: int) -> Optional[Task]:
        pool = self._get_pool()

        def _callee(session):
            query = """
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                SELECT chat_id, task_number, task_id, title, description, status,
                       created_by, created_by_name, created_at, completed_by, completed_by_name,
                       completed_at, screenshots_json, section
                FROM `tasks`
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
            sec_val = r.get("section")
            if sec_val:
                sec_str = sec_val.decode("utf-8") if isinstance(sec_val, bytes) else str(sec_val)
                sec_str = sec_str.strip() or None
            else:
                sec_str = None

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
                screenshots=Task.screenshots_from_json(r.get("screenshots_json", "")),
                section=sec_str
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
            query = """
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                DECLARE $status AS Utf8;
                DECLARE $completed_by AS Utf8;
                DECLARE $completed_by_name AS Utf8;
                DECLARE $completed_at AS Utf8;

                UPDATE `tasks`
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
            query = """
                DECLARE $chat_id AS Utf8;
                DECLARE $task_number AS Uint64;
                DECLARE $screenshots_json AS Utf8;

                UPDATE `tasks`
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
            count_q = """
                DECLARE $chat_id AS Utf8;
                DECLARE $status AS Utf8;
                SELECT COUNT(*) AS cnt FROM `tasks`
                WHERE chat_id = $chat_id AND status = $status;
            """
            prep_cnt = session.prepare(count_q)
            res = session.transaction().execute(prep_cnt, {
                "$chat_id": chat_id,
                "$status": TaskStatus.DONE
            }, commit_tx=True)
            cnt = res[0].rows[0]["cnt"] if res[0].rows else 0

            if cnt > 0:
                delete_q = """
                    DECLARE $chat_id AS Utf8;
                    DECLARE $status AS Utf8;
                    DELETE FROM `tasks`
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
        sqlite_path = config.SQLITE_PATH
        if os.path.exists("/tmp") and sqlite_path == "tasks_local.db":
            sqlite_path = "/tmp/tasks_local.db"
        return SqliteDatabase(sqlite_path)
    return YdbDatabase(config.YDB_ENDPOINT, config.YDB_DATABASE)
