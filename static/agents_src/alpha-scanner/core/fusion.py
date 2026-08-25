from typing import Dict, List
from collections import defaultdict

from schemas.narrative import Narrative
from schemas.signal import SignalSource


class SignalFusion:
    def __init__(self, min_sources: int = 2):
        # how many distinct signal sources must agree
        self.min_sources = min_sources

    def fuse(self, narratives: List[Narrative]) -> List[Narrative]:
        """
        Validate narratives by checking cross-signal agreement.
        """
        fused: List[Narrative] = []

        for narrative in narratives:
            source_scores = self._aggregate_by_source(narrative)

            if not self._passes_source_threshold(source_scores):
                continue

            fused_strength = self._compute_fused_strength(source_scores)

            narrative.metadata["source_scores"] = source_scores
            narrative.metadata["fused_strength"] = fused_strength

            fused.append(narrative)

        return fused

    def _aggregate_by_source(
        self,
        narrative: Narrative
    ) -> Dict[SignalSource, float]:
        """
        Aggregate signal strength per signal source.
        """
        scores: Dict[SignalSource, float] = defaultdict(float)

        for signal in narrative.signals:
            scores[signal.source] += signal.value * signal.confidence

        return dict(scores)

    def _passes_source_threshold(
        self,
        source_scores: Dict[SignalSource, float]
    ) -> bool:
        """
        Require agreement across multiple signal domains.
        """
        return len(source_scores.keys()) >= self.min_sources

    def _compute_fused_strength(
        self,
        source_scores: Dict[SignalSource, float]
    ) -> float:
        """
        Combine cross-source scores into a single value.
        """
        if not source_scores:
            return 0.0

        return sum(source_scores.values()) / len(source_scores)