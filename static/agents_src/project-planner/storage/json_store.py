import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import StorageBackend


class JsonStore(StorageBackend):
    def __init__(self, path: Path):
        self.path = path
        self._data: Dict[str, List[Dict[str, Any]]] = {}

        self.path.parent.mkdir(parents=True, exist_ok=True)

        if self.path.exists():
            self.load()
        else:
            self._initialize()
            self.save()

    def _initialize(self) -> None:
        self._data = {
            "tasks": [],
            "notes": [],
            "milestones": [],
        }

    def load(self) -> None:
        with open(self.path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

        for key in ("tasks", "notes", "milestones"):
            self._data.setdefault(key, [])

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get_all(self, collection: str) -> List[Dict[str, Any]]:
        return list(self._data.get(collection, []))

    def get_by_id(self, collection: str, item_id: str) -> Optional[Dict[str, Any]]:
        for item in self._data.get(collection, []):
            if item.get("id") == item_id:
                return dict(item)
        return None

    def add(self, collection: str, item: Dict[str, Any]) -> None:
        self._data.setdefault(collection, [])
        self._data[collection].append(dict(item))
        self.save()

    def update(self, collection: str, item_id: str, updates: Dict[str, Any]) -> None:
        items = self._data.get(collection, [])

        for i, item in enumerate(items):
            if item.get("id") == item_id:
                updated = dict(item)
                updated.update(updates)
                items[i] = updated
                self.save()
                return

        raise KeyError(f"{collection} item with id '{item_id}' not found")

    def delete(self, collection: str, item_id: str) -> None:
        items = self._data.get(collection, [])

        for i, item in enumerate(items):
            if item.get("id") == item_id:
                del items[i]
                self.save()
                return

        raise KeyError(f"{collection} item with id '{item_id}' not found")