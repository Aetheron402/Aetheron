from __future__ import annotations

from typing import Any, Dict, List, Optional

from storage.base import StorageBackend
from utils.dates import utc_now, parse_iso
from utils.formatting import format_task_title
from utils.ids import generate_id


class TaskManager:
    def __init__(self, storage: StorageBackend):
        self.storage = storage

    def create(
        self,
        title: str,
        description: str = "",
        due_at: Optional[str] = None,
        priority: Optional[int] = None,
        status: str = "open",
    ) -> Dict[str, Any]:
        now = utc_now().isoformat()

        task: Dict[str, Any] = {
            "id": generate_id("task"),
            "title": format_task_title(title),
            "description": description.strip(),
            "status": status,
            "priority": priority,
            "due_at": due_at,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }

        self.storage.add("tasks", task)
        return task

    def list_all(self) -> List[Dict[str, Any]]:
        return self.storage.get_all("tasks")

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self.storage.get_by_id("tasks", task_id)

    def update(self, task_id: str, **updates: Any) -> Dict[str, Any]:
        existing = self.get(task_id)
        if existing is None:
            raise KeyError(f"task with id '{task_id}' not found")

        normalized = dict(updates)

        if "title" in normalized and normalized["title"] is not None:
            normalized["title"] = format_task_title(str(normalized["title"]))

        if "description" in normalized and normalized["description"] is not None:
            normalized["description"] = str(normalized["description"]).strip()

        normalized["updated_at"] = utc_now().isoformat()

        self.storage.update("tasks", task_id, normalized)

        updated = self.get(task_id)
        if updated is None:
            raise KeyError(f"task with id '{task_id}' not found after update")

        return updated

    def mark_done(self, task_id: str) -> Dict[str, Any]:
        now = utc_now().isoformat()
        self.storage.update(
            "tasks",
            task_id,
            {
                "status": "done",
                "completed_at": now,
                "updated_at": now,
            },
        )

        task = self.get(task_id)
        if task is None:
            raise KeyError(f"task with id '{task_id}' not found")

        return task

    def reopen(self, task_id: str) -> Dict[str, Any]:
        now = utc_now().isoformat()
        self.storage.update(
            "tasks",
            task_id,
            {
                "status": "open",
                "completed_at": None,
                "updated_at": now,
            },
        )

        task = self.get(task_id)
        if task is None:
            raise KeyError(f"task with id '{task_id}' not found")

        return task

    def delete(self, task_id: str) -> None:
        self.storage.delete("tasks", task_id)

    def get_overdue(self) -> List[Dict[str, Any]]:
        now = utc_now()
        overdue: List[Dict[str, Any]] = []

        for task in self.list_all():
            status = str(task.get("status") or "").lower()
            if status in ("done", "completed", "closed"):
                continue

            due_at = parse_iso(task.get("due_at"))
            if due_at is None:
                continue

            if due_at < now:
                overdue.append(task)

        return overdue