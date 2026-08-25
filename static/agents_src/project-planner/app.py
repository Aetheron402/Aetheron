from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from core.organizer import ProjectOrganizer
from services.cleanup import CleanupService
from services.reminders import RemindersService
from services.summaries import SummariesService
from storage.json_store import JsonStore
from storage.sqlite_store import SqliteStore


class ProjectOrganizerApp:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config: Dict[str, Any] = self._load_config()

        self.storage = self._init_storage()
        self.organizer = ProjectOrganizer(self.storage)

        self.cleanup_service = CleanupService(
            self.storage,
            self.config.get("services", {}).get("cleanup", {}),
        )
        self.reminders_service = RemindersService(
            self.storage,
            self.config.get("services", {}).get("reminders", {}),
        )
        self.summaries_service = SummariesService(
            self.storage,
            self.config.get("services", {}).get("summaries", {}),
        )

        self._running = False

    def _load_config(self) -> Dict[str, Any]:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _init_storage(self):
        storage_config = self.config.get("storage", {})
        backend = storage_config.get("backend", "json")

        if backend == "json":
            return JsonStore(Path(storage_config.get("path", "data/db.json")))
        if backend == "sqlite":
            return SqliteStore(Path(storage_config.get("path", "data/db.sqlite")))

        raise ValueError(f"Unsupported storage backend: {backend}")

    def start(self) -> None:
        self._running = True

        print("Project Organizer starting...")
        self._run_startup_tasks()
        print("Organizer ready. Press Ctrl+C to stop.")

        while self._running:
            self._run_periodic_tasks()
            time.sleep(5)

    def stop(self) -> None:
        if not self._running:
            return

        print("Shutting down organizer...")
        self._running = False

    def _run_startup_tasks(self) -> None:
        cleanup_result = self.cleanup_service.run()
        if cleanup_result.details:
            print("Cleanup completed:")
            for line in cleanup_result.details:
                print(" -", line)

        summary = self.summaries_service.generate()
        print("Project summary:")
        for line in summary.lines:
            print(" ", line)

    def _run_periodic_tasks(self) -> None:
        reminders = self.reminders_service.get_due()
        for reminder in reminders:
            print(f"[REMINDER] {reminder.message}")
            self.reminders_service.mark_sent(reminder)