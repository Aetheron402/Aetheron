from datetime import datetime
from typing import List

from schemas.opportunity import Opportunity
from signals.base import BaseSignalGenerator
from core.narratives import NarrativeEngine
from core.fusion import SignalFusion
from core.ranking import NarrativeRanker
from core.confidence import ConfidenceCalculator
from core.state import AgentState
from utils import get_logger


class AlphaScannerEngine:
    def __init__(
        self,
        signal_generators: List[BaseSignalGenerator]
    ):
        self.logger = get_logger("alpha-scanner.engine")

        self.signal_generators = signal_generators
        self.state = AgentState()
        self.narrative_engine = NarrativeEngine()
        self.fusion = SignalFusion()
        self.ranker = NarrativeRanker()
        self.confidence = ConfidenceCalculator()

    def run(self, timestamp: datetime) -> List[Opportunity]:
        self.logger.info("Starting scan cycle at %s", timestamp.isoformat())

        # 1. Collect signals
        signals = []
        for generator in self.signal_generators:
            self.logger.info(
                "Scanning %s signals",
                generator.source.value
            )
            generated = generator.generate(timestamp)
            self.logger.info(
                "Collected %d %s signals",
                len(generated),
                generator.source.value
            )
            signals.extend(generated)

        if not signals:
            self.logger.info("No signals collected this cycle")
            return []

        # 2. Update narratives
        narratives = self.narrative_engine.update(signals, timestamp)
        self.logger.info(
            "Updated %d narratives",
            len(narratives)
        )

        # 3. Cross-signal validation
        fused = self.fusion.fuse(narratives)
        self.logger.info(
            "%d narratives passed cross-signal validation",
            len(fused)
        )

        if not fused:
            self.logger.info("No narratives passed fusion checks")
            return []

        # 4. Rank narratives
        ranked = self.ranker.rank(fused)
        self.logger.info(
            "Ranked %d narratives",
            len(ranked)
        )

        # 5. Produce opportunities
        opportunities: List[Opportunity] = []

        for narrative in ranked:
            confidence = self.confidence.compute(narrative)
            score = narrative.metadata.get("fused_strength", 0.0)

            self.logger.info(
                "Narrative '%s' | score=%.3f | confidence=%.3f",
                narrative.name,
                score,
                confidence
            )

            opportunities.append(
                Opportunity(
                    id=narrative.id,
                    narrative=narrative,
                    related_assets=narrative.keywords,
                    score=score,
                    confidence=confidence,
                    reasons=list(narrative.metadata.keys()),
                )
            )

        self.logger.info(
            "Scan cycle completed with %d opportunities",
            len(opportunities)
        )

        return opportunities