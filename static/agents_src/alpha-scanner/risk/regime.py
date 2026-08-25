from schemas.narrative import Narrative


class MarketRegimeFilter:
    def __init__(self, min_fused_strength: float = 0.5):
        self.min_fused_strength = min_fused_strength

    def allow(self, narrative: Narrative) -> bool:
        """
        Suppress narratives that don't fit the current market regime.
        """
        fused_strength = narrative.metadata.get("fused_strength", 0.0)
        return fused_strength >= self.min_fused_strength
