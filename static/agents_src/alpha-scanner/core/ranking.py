from typing import List

from schemas.narrative import Narrative


class NarrativeRanker:
    def rank(self, narratives: List[Narrative]) -> List[Narrative]:
        """
        Rank narratives by importance.
        V1: simple sort using fused strength and momentum.
        """

        def score(narrative: Narrative) -> float:
            fused_strength = narrative.metadata.get("fused_strength", 0.0)
            return fused_strength + narrative.momentum

        return sorted(narratives, key=score, reverse=True)
