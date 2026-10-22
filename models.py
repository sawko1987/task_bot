from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import json

class TaskStatus:
    OPEN = "OPEN"
    DONE = "DONE"

@dataclass
class Task:
    chat_id: str
    task_number: int
    task_id: str
    title: str
    description: str
    status: str = TaskStatus.OPEN
    created_by: str = ""
    created_by_name: str = ""
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    completed_by: Optional[str] = None
    completed_by_name: Optional[str] = None
    completed_at: Optional[str] = None
    screenshots: List[str] = field(default_factory=list)
    section: Optional[str] = None

    def screenshots_to_json(self) -> str:
        return json.dumps(self.screenshots, ensure_ascii=False)

    @classmethod
    def screenshots_from_json(cls, raw: str) -> List[str]:
        if not raw:
            return []
        try:
            return json.loads(raw)
        except Exception:
            return []

@dataclass
class ChatSection:
    chat_id: str
    section_name: str
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class IncomingMessage:
    message_id: str
    chat_id: str
    chat_type: str
    user_id: str
    user_name: str
    text: str
    screenshots: List[str] = field(default_factory=list)
    reply_to_message_id: Optional[str] = None
    reply_to_text: Optional[str] = None
    reply_to_screenshots: List[str] = field(default_factory=list)
    reply_to_sender_name: Optional[str] = None
    is_admin: Optional[bool] = None
    callback_id: Optional[str] = None
    callback_payload: Optional[str] = None
