from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from storage.base import StorageBackend


def _parse_iso(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, str) or not dt.strip():
        return None
    try:
        s = dt.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Reminder:
    kind: str                # "task" | "milestone"
    item_id: str
    title: str
    due_at: datetime
    message: str


class RemindersService:
    """
    Finds reminders that are due.

    Config keys (suggested under services.reminders):
      - enabled: bool
      - lookahead_minutes: int (default 60)
      - remind_done_tasks: bool (default False)
      - dedupe_key_field: str (default "last_reminded_at")
      - min_repeat_minutes: int (default 60)
    """

    def __init__(self, storage: StorageBackend, config: Optional[Dict[str, Any]] = None):
        self.storage = storage
        self.config = config or {}

    def get_due(self) -> List[Reminder]:
        if not self.config.get("enabled", True):
            return []

        lookahead = int(self.config.get("lookahead_minutes", 60))
        if lookahead < 0:
            lookahead = 0

        now = _utc_now()
        horizon = now + timedelta(minutes=lookahead)

        dedupe_field = str(self.config.get("dedupe_key_field", "last_reminded_at"))
        min_repeat = int(self.config.get("min_repeat_minutes", 60))
        if min_repeat < 0:
            min_repeat = 0

        reminders: List[Reminder] = []
        reminders.extend(self._due_tasks(now, horizon, dedupe_field, min_repeat))
        reminders.extend(self._due_milestones(now, horizon, dedupe_field, min_repeat))

        # Sort by due time
        reminders.sort(key=lambda r: r.due_at)
        return reminders

    def mark_sent(self, reminder: Reminder) -> None:
        """Mark a reminder as sent to support deduping."""
        field = str(self.config.get("dedupe_key_field", "last_reminded_at"))
        now = _utc_now().isoformat()
        collection = "tasks" if reminder.kind == "task" else "milestones"
        self.storage.update(collection, reminder.item_id, {field: now})

    def _due_tasks(self, now: datetime, horizon: datetime, dedupe_field: str, min_repeat: int) -> List[Reminder]:
        remind_done = bool(self.config.get("remind_done_tasks", False))
        out: List[Reminder] = []

        for t in self.storage.get_all("tasks"):
            tid = t.get("id")
            if not tid:
                continue

            status = (t.get("status") or "").lower()
            if not remind_done and status in ("done", "completed", "closed"):
                continue

            due = _parse_iso(t.get("due_at") or t.get("due"))
            if due is None:
                continue

            if not (now <= due <= horizon):
                continue

            if self._dedupe_hit(t, now, dedupe_field, min_repeat):
                continue

            title = (t.get("title") or t.get("name") or "Untitled task").strip()
            msg = f"Task due soon: {title}"
            out.append(Reminder(kind="task", item_id=tid, title=title, due_at=due, message=msg))

        return out

    def _due_milestones(self, now: datetime, horizon: datetime, dedupe_field: str, min_repeat: int) -> List[Reminder]:
        out: List[Reminder] = []

        for m in self.storage.get_all("milestones"):
            mid = m.get("id")
            if not mid:
                continue

            due = _parse_iso(m.get("due_at") or m.get("date") or m.get("target_at"))
            if due is None:
                continue

            if not (now <= due <= horizon):
                continue

            if self._dedupe_hit(m, now, dedupe_field, min_repeat):
                continue

            title = (m.get("title") or m.get("name") or "Untitled milestone").strip()
            msg = f"Milestone due soon: {title}"
            out.append(Reminder(kind="milestone", item_id=mid, title=title, due_at=due, message=msg))

        return out

    def _dedupe_hit(self, item: Dict[str, Any], now: datetime, field: str, min_repeat: int) -> bool:
        last = _parse_iso(item.get(field))
        if last is None:
            return False
        return (now - last) < timedelta(minutes=min_repeat)