import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import StorageBackend


class SqliteStore(StorageBackend):
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        cur = self.conn.cursor()

        for table in ["tasks", "notes", "milestones"]:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
                """
            )

        self.conn.commit()

    def load(self) -> None:
        pass

    def save(self) -> None:
        pass

    def get_all(self, collection: str) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        rows = cur.execute(f"SELECT data FROM {collection}").fetchall()

        return [json.loads(r["data"]) for r in rows]

    def get_by_id(self, collection: str, item_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()

        row = cur.execute(
            f"SELECT data FROM {collection} WHERE id=?",
            (item_id,),
        ).fetchone()

        if row:
            return json.loads(row["data"])

        return None

    def add(self, collection: str, item: Dict[str, Any]) -> None:
        cur = self.conn.cursor()

        cur.execute(
            f"INSERT INTO {collection} (id, data) VALUES (?, ?)",
            (item["id"], json.dumps(item)),
        )

        self.conn.commit()

    def update(self, collection: str, item_id: str, updates: Dict[str, Any]) -> None:
        existing = self.get_by_id(collection, item_id)

        if not existing:
            raise KeyError(f"{collection} item '{item_id}' not found")

        existing.update(updates)

        cur = self.conn.cursor()

        cur.execute(
            f"UPDATE {collection} SET data=? WHERE id=?",
            (json.dumps(existing), item_id),
        )

        self.conn.commit()

    def delete(self, collection: str, item_id: str) -> None:
        cur = self.conn.cursor()

        cur.execute(
            f"DELETE FROM {collection} WHERE id=?",
            (item_id,),
        )

        self.conn.commit()