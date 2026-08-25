from typing import Dict
from datetime import datetime

from schemas.narrative import Narrative


class AgentState:
    def __init__(self):
        self.last_run: datetime | None = None
        self.narratives: Dict[str, Narrative] = {}

    def load_narratives(self) -> Dict[str, Narrative]:
        return self.narratives

    def save_narratives(self, narratives: Dict[str, Narrative]) -> None:
        self.narratives = narratives

    def update_last_run(self, timestamp: datetime) -> None:
        self.last_run = timestamp
