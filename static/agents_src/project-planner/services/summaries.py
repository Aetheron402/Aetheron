from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from storage.base import StorageBackend


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ProjectSummary:
    generated_at: datetime
    open_tasks: int
    done_tasks: int
    milestones_total: int
    notes_total: int
    lines: List[str]


class SummariesService:
    """
    Builds structured summaries from stored items.

    Config keys (suggested under services.summaries):
      - enabled: bool
      - store_as_note: bool (default False)
      - note_title_prefix: str (default "Project Summary")
      - max_recent_notes: int (default 5)  # only used to show "recent notes" snippet
    """

    def __init__(self, storage: StorageBackend, config: Optional[Dict[str, Any]] = None):
        self.storage = storage
        self.config = config or {}

    def generate(self) -> ProjectSummary:
        if not self.config.get("enabled", True):
            return ProjectSummary(
                generated_at=_utc_now(),
                open_tasks=0,
                done_tasks=0,
                milestones_total=0,
                notes_total=0,
                lines=["summaries disabled"],
            )

        tasks = self.storage.get_all("tasks")
        notes = self.storage.get_all("notes")
        milestones = self.storage.get_all("milestones")

        done = 0
        open_ = 0
        for t in tasks:
            status = (t.get("status") or "").lower()
            if status in ("done", "completed", "closed"):
                done += 1
            else:
                open_ += 1

        lines: List[str] = []
        lines.append(f"Tasks: {open_} open / {done} done (total {len(tasks)})")
        lines.append(f"Milestones: {len(milestones)} total")
        lines.append(f"Notes: {len(notes)} total")

        # Add a small “top” preview (non-destructive, no assumptions)
        top_open = [t for t in tasks if (t.get("status") or "").lower() not in ("done", "completed", "closed")]
        top_open = top_open[:5]
        if top_open:
            lines.append("")
            lines.append("Top open tasks:")
            for t in top_open:
                title = (t.get("title") or t.get("name") or "Untitled").strip()
                tid = t.get("id", "?")
                lines.append(f"- [{tid}] {title}")

        summary = ProjectSummary(
            generated_at=_utc_now(),
            open_tasks=open_,
            done_tasks=done,
            milestones_total=len(milestones),
            notes_total=len(notes),
            lines=lines,
        )

        if bool(self.config.get("store_as_note", False)):
            self._store_summary_note(summary)

        return summary

    def _store_summary_note(self, summary: ProjectSummary) -> None:
        prefix = str(self.config.get("note_title_prefix", "Project Summary")).strip() or "Project Summary"
        title = f"{prefix} — {summary.generated_at.date().isoformat()}"
        body = "\n".join(summary.lines)

        note = {
            "id": f"summary-{int(summary.generated_at.timestamp())}",
            "title": title,
            "body": body,
            "created_at": summary.generated_at.isoformat(),
            "type": "summary",
        }
        self.storage.add("notes", note)