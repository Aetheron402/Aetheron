from __future__ import annotations

from typing import Any, Dict

from storage.base import StorageBackend

from .milestones import MilestoneManager
from .notes import NoteManager
from .status import StatusManager
from .tasks import TaskManager


class ProjectOrganizer:
    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self.tasks = TaskManager(storage)
        self.notes = NoteManager(storage)
        self.milestones = MilestoneManager(storage)
        self.status = StatusManager(storage)

    def overview(self) -> Dict[str, Any]:
        return self.status.get_project_status()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "tasks": self.tasks.list_all(),
            "notes": self.notes.list_all(),
            "milestones": self.milestones.list_all(),
            "status": self.status.get_project_status(),
        }