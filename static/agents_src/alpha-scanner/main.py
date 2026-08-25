import json
import time
from datetime import datetime, timezone
from pathlib import Path

from core import AlphaScannerEngine
from signals import (
    SocialSignalGenerator,
    OnchainSignalGenerator,
    MarketSignalGenerator,
)
from utils import get_logger


def load_config(path: Path) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def build_signal_generators(config: dict):
    generators = []

    enabled = config.get("signals", {}).get("enabled", {})

    if enabled.get("social", True):
        generators.append(SocialSignalGenerator())

    if enabled.get("onchain", True):
        generators.append(OnchainSignalGenerator())

    if enabled.get("market", True):
        generators.append(MarketSignalGenerator())

    return generators


def main():
    logger = get_logger("alpha-scanner")

    config_path = Path(__file__).parent / "config.json"
    config = load_config(config_path)

    interval_seconds = config.get("general", {}).get("run_interval_seconds", 60)

    logger.info("Alpha Scanner agent online")
    logger.info(
        "Enabled scans | social=%s onchain=%s market=%s",
        config.get("signals", {}).get("enabled", {}).get("social", True),
        config.get("signals", {}).get("enabled", {}).get("onchain", True),
        config.get("signals", {}).get("enabled", {}).get("market", True),
    )
    logger.info("Scan interval set to %s seconds", interval_seconds)

    signal_generators = build_signal_generators(config)
    engine = AlphaScannerEngine(signal_generators=signal_generators)

    cycle = 0

    while True:
        cycle += 1
        now = datetime.now(timezone.utc)

        logger.info(
            "Starting scan cycle #%d at %s",
            cycle,
            now.isoformat(),
        )

        try:
            opportunities = engine.run(timestamp=now)

            if not opportunities:
                logger.info(
                    "Scan cycle #%d completed | no opportunities surfaced",
                    cycle,
                )
            else:
                logger.info(
                    "Scan cycle #%d completed | %d opportunities surfaced",
                    cycle,
                    len(opportunities),
                )

                for opportunity in opportunities:
                    logger.info(
                        "Opportunity detected | id=%s score=%.3f confidence=%.3f",
                        opportunity.id,
                        opportunity.score,
                        opportunity.confidence,
                    )

        except Exception as e:
            logger.exception(
                "Error during scan cycle #%d: %s",
                cycle,
                e,
            )

        logger.info(
            "Sleeping %d seconds before next scan",
            interval_seconds,
        )
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
