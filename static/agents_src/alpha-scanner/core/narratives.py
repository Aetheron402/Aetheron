from typing import Dict, List
from datetime import datetime

from schemas.signal import Signal
from schemas.narrative import Narrative
from utils.decay import time_decay

# How long a narrative keeps half its strength without new signals. Short,
# because these are intraday moves: a token that stopped trading two hours ago
# is not a live opportunity.
HALF_LIFE_SECONDS = 1800


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

        # Age everything first, including narratives no signal arrived for.
        # Without this a narrative kept whatever strength it accumulated and
        # never left the ranking, so the board filled with tokens that had
        # stopped moving hours earlier.
        self._age(timestamp)

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

        # Only the recent signals matter, and an unbounded list grows for the
        # lifetime of the process.
        if len(narrative.signals) > 50:
            narrative.signals = narrative.signals[-50:]

        # Weighted by confidence, so a strong reading from a thin pair counts
        # for less than the same reading from a deep one.
        narrative.strength += signal.value * signal.confidence
        narrative.last_updated = timestamp

        narrative.momentum = self._compute_momentum(narrative)
        narrative.freshness = 1.0        # just updated, by definition

    def _age(self, now: datetime) -> None:
        """
        Decay every narrative toward zero, and drop the ones that got there.

        utils.decay.time_decay already existed and was never called, while
        freshness was computed immediately after last_updated had been set to
        the same timestamp, so it evaluated to exactly 1.0 every time and
        nothing ever aged.
        """
        for narrative_id, narrative in list(self._narratives.items()):
            factor = time_decay(narrative.last_updated, now, HALF_LIFE_SECONDS)
            narrative.strength *= factor
            narrative.freshness = factor

            # Below this it cannot rank, and keeping it only grows the store.
            if narrative.strength < 0.01:
                del self._narratives[narrative_id]

    def _compute_momentum(self, narrative: Narrative) -> float:
        """
        Whether recent signals are stronger than the ones before them.

        The mean of every signal ever seen is a level, not momentum: it moves
        less the longer a narrative has existed, which is backwards.
        """
        values = [s.value for s in narrative.signals]
        if len(values) < 4:
            return 0.0

        half = len(values) // 2
        earlier = sum(values[:half]) / half
        recent = sum(values[half:]) / (len(values) - half)
        return max(-1.0, min(1.0, recent - earlier))