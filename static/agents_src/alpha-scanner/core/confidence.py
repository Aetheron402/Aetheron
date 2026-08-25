from schemas.narrative import Narrative


class ConfidenceCalculator:
    def compute(self, narrative: Narrative) -> float:
        """
        Compute confidence score for a narrative.
        V1: combine signal confidence and cross-source agreement.
        """

        signals = narrative.signals
        if not signals:
            return 0.0

        avg_signal_confidence = sum(
            signal.confidence for signal in signals
        ) / len(signals)

        source_count = len(
            narrative.metadata.get("source_scores", {})
        )

        # simple confidence heuristic
        confidence = avg_signal_confidence * min(1.0, source_count / 3)

        return round(confidence, 3)
