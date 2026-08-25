from __future__ import annotations

from typing import Any, Dict, List, Optional

from storage.base import StorageBackend
from utils.dates import utc_now, parse_iso
from utils.ids import generate_id


class MilestoneManager:
    def __init__(self, storage: StorageBackend):
        self.storage = storage

    def create(
        self,
        title: str,
        description: str = "",
        due_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now().isoformat()

        milestone: Dict[str, Any] = {
            "id": generate_id("milestone"),
            "title": title.strip(),
            "description": description.strip(),
            "due_at": due_at,
            "status": "pending",
            "archived": False,
            "completed_at": None,
            "created_at": now,
            "updated_at": now,
        }

        self.storage.add("milestones", milestone)
        return milestone

    def list_all(self) -> List[Dict[str, Any]]:
        return self.storage.get_all("milestones")

    def get(self, milestone_id: str) -> Optional[Dict[str, Any]]:
        return self.storage.get_by_id("milestones", milestone_id)

    def update(self, milestone_id: str, **updates: Any) -> Dict[str, Any]:
        existing = self.get(milestone_id)
        if existing is None:
            raise KeyError(f"milestone with id '{milestone_id}' not found")

        normalized = dict(updates)

        if "title" in normalized and normalized["title"] is not None:
            normalized["title"] = str(normalized["title"]).strip()

        if "description" in normalized and normalized["description"] is not None:
            normalized["description"] = str(normalized["description"]).strip()

        normalized["updated_at"] = utc_now().isoformat()

        self.storage.update("milestones", milestone_id, normalized)

        updated = self.get(milestone_id)
        if updated is None:
            raise KeyError(f"milestone with id '{milestone_id}' not found after update")

        return updated

    def mark_reached(self, milestone_id: str) -> Dict[str, Any]:
        now = utc_now().isoformat()

        self.storage.update(
            "milestones",
            milestone_id,
            {
                "status": "done",
                "completed_at": now,
                "updated_at": now,
            },
        )

        milestone = self.get(milestone_id)
        if milestone is None:
            raise KeyError(f"milestone with id '{milestone_id}' not found")

        return milestone

    def archive(self, milestone_id: str) -> Dict[str, Any]:
        self.storage.update(
            "milestones",
            milestone_id,
            {
                "archived": True,
                "updated_at": utc_now().isoformat(),
            },
        )

        milestone = self.get(milestone_id)
        if milestone is None:
            raise KeyError(f"milestone with id '{milestone_id}' not found")

        return milestone

    def delete(self, milestone_id: str) -> None:
        self.storage.delete("milestones", milestone_id)

    def get_upcoming(self) -> List[Dict[str, Any]]:
        now = utc_now()
        upcoming: List[Dict[str, Any]] = []

        for milestone in self.list_all():
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