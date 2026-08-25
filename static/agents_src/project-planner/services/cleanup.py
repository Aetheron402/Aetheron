from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from storage.base import StorageBackend


def _parse_iso(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, str) or not dt.strip():
        return None
    try:
        # Accept "Z" suffix
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
class CleanupResult:
    removed_tasks: int = 0
    removed_notes: int = 0
    removed_milestones: int = 0
    details: List[str] = None

    def __post_init__(self) -> None:
        if self.details is None:
            self.details = []


class CleanupService:
    """
    Performs safe maintenance on stored data.

    Config keys (suggested under services.cleanup):
      - enabled: bool
      - prune_done_tasks_after_days: int (default 30)
      - prune_empty_notes: bool (default True)
      - prune_archived_milestones: bool (default False)
    """

    def __init__(self, storage: StorageBackend, config: Optional[Dict[str, Any]] = None):
        self.storage = storage
        self.config = config or {}

    def run(self) -> CleanupResult:
        if not self.config.get("enabled", True):
            return CleanupResult(details=["cleanup disabled"])

        result = CleanupResult()

        result.removed_tasks += self._prune_done_tasks(result)
        result.removed_notes += self._prune_empty_notes(result)
        result.removed_milestones += self._prune_archived_milestones(result)

        return result

    def _prune_done_tasks(self, result: CleanupResult) -> int:
        days = int(self.config.get("prune_done_tasks_after_days", 30))
        if days < 0:
            return 0

        cutoff = _utc_now() - timedelta(days=days)
        removed = 0

        tasks = self.storage.get_all("tasks")
        for t in tasks:
            status = (t.get("status") or "").lower()
            if status not in ("done", "completed", "closed"):
                continue

            # Prefer completed_at, fallback updated_at, else created_at
            completed_at = _parse_iso(t.get("completed_at")) or _parse_iso(t.get("updated_at")) or _parse_iso(t.get("created_at"))
            if completed_at is None:
                continue

            if completed_at < cutoff:
                tid = t.get("id")
                if tid:
                    self.storage.delete("tasks", tid)
                    removed += 1

        if removed:
            result.details.append(f"pruned {removed} done tasks older than {days} days")

        return removed

    def _prune_empty_notes(self, result: CleanupResult) -> int:
        if not bool(self.config.get("prune_empty_notes", True)):
            return 0

        removed = 0
        notes = self.storage.get_all("notes")

        for n in notes:
            nid = n.get("id")
            if not nid:
                continue

            title = (n.get("title") or "").strip()
            body = (n.get("body") or n.get("content") or "").strip()

            if not title and not body:
                self.storage.delete("notes", nid)
                removed += 1

        if removed:
            result.details.append(f"pruned {removed} empty notes")

        return removed

    def _prune_archived_milestones(self, result: CleanupResult) -> int:
        if not bool(self.config.get("prune_archived_milestones", False)):
            return 0

        removed = 0
        milestones = self.storage.get_all("milestones")

        for m in milestones:
            mid = m.get("id")
            if not mid:
                continue

            archived = bool(m.get("archived", False))
            if archived:
                self.storage.delete("milestones", mid)
                removed += 1

        if removed:
            result.details.append(f"pruned {removed} archived milestones")

        return removed