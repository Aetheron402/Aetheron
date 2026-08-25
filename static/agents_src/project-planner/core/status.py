from __future__ import annotations

from typing import Any, Dict, List

from storage.base import StorageBackend
from utils.dates import utc_now, parse_iso


class StatusManager:
    def __init__(self, storage: StorageBackend):
        self.storage = storage

    def get_counts(self) -> Dict[str, int]:
        tasks = self.storage.get_all("tasks")
        notes = self.storage.get_all("notes")
        milestones = self.storage.get_all("milestones")

        done_tasks = 0
        open_tasks = 0

        for task in tasks:
            status = str(task.get("status") or "").lower()
            if status in ("done", "completed", "closed"):
                done_tasks += 1
            else:
                open_tasks += 1

        completed_milestones = 0
        pending_milestones = 0

        for milestone in milestones:
            status = str(milestone.get("status") or "").lower()
            if status in ("done", "completed", "closed"):
                completed_milestones += 1
            else:
                pending_milestones += 1

        return {
            "tasks_total": len(tasks),
            "tasks_open": open_tasks,
            "tasks_done": done_tasks,
            "notes_total": len(notes),
            "milestones_total": len(milestones),
            "milestones_pending": pending_milestones,
            "milestones_completed": completed_milestones,
        }

    def get_overdue_tasks(self) -> List[Dict[str, Any]]:
        now = utc_now()
        overdue: List[Dict[str, Any]] = []

        for task in self.storage.get_all("tasks"):
            status = str(task.get("status") or "").lower()
            if status in ("done", "completed", "closed"):
                continue

            due_at = parse_iso(task.get("due_at"))
            if due_at is None:
                continue

            if due_at < now:
                overdue.append(task)

        overdue.sort(key=lambda item: item.get("due_at") or "")
        return overdue

    def get_upcoming_milestones(self) -> List[Dict[str, Any]]:
        now = utc_now()
        upcoming: List[Dict[str, Any]] = []

        for milestone in self.storage.get_all("milestones"):
            if bool(milestone.get("archived", False)):
                continue

            status = str(milestone.get("status") or "").lower()
            if status in ("done", "completed", "closed"):
                continue

            due_at = parse_iso(milestone.get("due_at"))
            if due_at is None:
                continue

            if due_at >= now:
                upcoming.append(milestone)

        upcoming.sort(key=lambda item: item.get("due_at") or "")
        return upcoming

    def get_project_status(self) -> Dict[str, Any]:
        counts = self.get_counts()
        overdue_tasks = self.get_overdue_tasks()
        upcoming_milestones = self.get_upcoming_milestones()

        if counts["tasks_open"] == 0 and counts["milestones_pending"] == 0:
            overall = "complete"
        elif overdue_tasks:
            overall = "at_risk"
        elif counts["tasks_open"] > 0 or counts["milestones_pending"] > 0:
            overall = "active"
        else:
            overall = "idle"

        return {
            "overall": overall,
            "counts": counts,
            "overdue_tasks": overdue_tasks,
            "upcoming_milestones": upcoming_milestones[:5],
        }