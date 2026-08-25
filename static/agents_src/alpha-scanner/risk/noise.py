from schemas.narrative import Narrative


class NoiseFilter:
    def __init__(self, min_strength: float = 0.5, min_signals: int = 2):
        self.min_strength = min_strength
        self.min_signals = min_signals

    def allow(self, narrative: Narrative) -> bool:
        """
        Suppress narratives that are too weak or too sparse.
        """
        if narrative.strength < self.min_strength:
            return False

        if len(narrative.signals) < self.min_signals:
            return False

        return True
