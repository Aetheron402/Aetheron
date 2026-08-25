from __future__ import annotations

from typing import Any, Dict, List, Optional

from storage.base import StorageBackend
from utils.dates import utc_now
from utils.ids import generate_id


class NoteManager:
    def __init__(self, storage: StorageBackend):
        self.storage = storage

    def create(self, title: str, body: str = "") -> Dict[str, Any]:
        now = utc_now().isoformat()

        note: Dict[str, Any] = {
            "id": generate_id("note"),
            "title": title.strip(),
            "body": body.strip(),
            "created_at": now,
            "updated_at": now,
        }

        self.storage.add("notes", note)
        return note

    def list_all(self) -> List[Dict[str, Any]]:
        return self.storage.get_all("notes")

    def get(self, note_id: str) -> Optional[Dict[str, Any]]:
        return self.storage.get_by_id("notes", note_id)

    def update(self, note_id: str, **updates: Any) -> Dict[str, Any]:
        existing = self.get(note_id)
        if existing is None:
            raise KeyError(f"note with id '{note_id}' not found")

        normalized = dict(updates)

        if "title" in normalized and normalized["title"] is not None:
            normalized["title"] = str(normalized["title"]).strip()

        if "body" in normalized and normalized["body"] is not None:
            normalized["body"] = str(normalized["body"]).strip()

        normalized["updated_at"] = utc_now().isoformat()

        self.storage.update("notes", note_id, normalized)

        updated = self.get(note_id)
        if updated is None:
            raise KeyError(f"note with id '{note_id}' not found after update")

        return updated

    def delete(self, note_id: str) -> None:
        self.storage.delete("notes", note_id)