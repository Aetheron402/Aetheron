from datetime import datetime, timedelta

from schemas.narrative import Narrative


class RecycledNarrativeFilter:
    def __init__(self, cooldown_hours: int = 24):
        self.cooldown = timedelta(hours=cooldown_hours)

    def allow(self, narrative: Narrative, now: datetime) -> bool:
        """
        Prevent resurfacing of recently active narratives.
        """
        age = now - narrative.first_seen
        return age > self.cooldown
