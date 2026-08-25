from typing import Dict, List
from datetime import datetime

from schemas.signal import Signal
from schemas.narrative import Narrative


class NarrativeEngine:
    def __init__(self):
        # active narratives keyed by narrative id
        self._narratives: Dict[str, Narrative] = {}

    def update(
        self,
        signals: List[Signal],
        timestamp: datetime
    ) -> List[Narrative]:
        """
        Ingest new signals and update narrative state.
        Returns unique updated narratives only.
        """
        touched_ids = set()

        for signal in signals:
            narrative_id = self._resolve_narrative_id(signal)

            narrative = self._narratives.get(narrative_id)
            if narrative is None:
                narrative = self._create_narrative(signal, timestamp)

            self._update_narrative(narrative, signal, timestamp)

            self._narratives[narrative.id] = narrative
            touched_ids.add(narrative.id)

        return [self._narratives[nid] for nid in touched_ids]

    def get_active(self) -> List[Narrative]:
        return list(self._narratives.values())

    def _resolve_narrative_id(self, signal: Signal) -> str:
        """
        Decide which narrative a signal belongs to.
        V1: simple mapping based on signal key.
        """
        return signal.key

    def _create_narrative(
        self,
        signal: Signal,
        timestamp: datetime
    ) -> Narrative:
        """
        Create a new narrative from a first signal.
        """
        return Narrative(
            id=signal.key,
            name=signal.key,
            keywords=[signal.key],
            signals=[],
            strength=0.0,
            momentum=0.0,
            freshness=1.0,
            first_seen=timestamp,
            last_updated=timestamp,
        )

    def _update_narrative(
        self,
        narrative: Narrative,
        signal: Signal,
        timestamp: datetime
    ) -> None:
        """
        Update narrative metrics with a new signal.
        """
        narrative.signals.append(signal)
        narrative.last_updated = timestamp

        # simple aggregate strength update (v1)
        narrative.strength += signal.value

        # momentum and freshness are placeholders for now
        narrative.momentum = self._compute_momentum(narrative)
        narrative.freshness = self._compute_freshness(narrative, timestamp)

    def _compute_momentum(self, narrative: Narrative) -> float:
        # placeholder: refine later
        return narrative.strength / max(len(narrative.signals), 1)

    def _compute_freshness(
        self,
        narrative: Narrative,
        timestamp: datetime
    ) -> float:
        # placeholder decay logic
        age_seconds = (timestamp - narrative.last_updated).total_seconds()
        return max(0.0, 1.0 - age_seconds / 3600)