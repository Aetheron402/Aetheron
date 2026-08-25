# ============================================================
# Market Tracker Agent (Template)
#
# Aetheron Components – License Agreement
#
# You are granted a personal, non-transferable,
# non-commercial license to use this software.
#
# You MAY:
# - Use this software for personal or research purposes
# - Modify the code for your own private use
#
# You may NOT:
# - Resell, redistribute, or repackage this software
# - Publish it online or in paid groups
# - Include it in external products or commercial tools
#   without explicit permission
#
# Copyright © 2025 Aetheron.
# All rights reserved.
#
# See LICENSE.txt in the project root for full terms.
# ============================================================

import json
import time

from core.engine import MarketEngine
from core.aggregation import aggregate_scores, aggregate_confidence
from core.regime import determine_regime

from modules.risk import run as risk
from modules.volatility import run as volatility
from modules.liquidity import run as liquidity
from modules.correlation import run as correlation
from modules.psychology import run as psychology


def load_config():
    with open("config.json", "r") as f:
        return json.load(f)


def main():
    config = load_config()

    modules = {
        "risk": risk,
        "volatility": volatility,
        "liquidity": liquidity,
        "correlation": correlation,
        "psychology": psychology
    }

    engine = MarketEngine(modules, config)

    interval = config.get("run_interval_seconds", 60)

    print(f"Market Tracker running every {interval}s. Press Ctrl+C to stop.\n")

    try:
        while True:
            module_results = engine.run()

            score = aggregate_scores(
                module_results,
                config.get("weights", {})
            )

            confidence = aggregate_confidence(module_results)

            environment = determine_regime(
                score,
                config.get("regime_thresholds", {})
            )

            output = {
                "timestamp": engine.timestamp(),
                "environment": environment,
                "confidence": confidence,
                "modules": module_results,
                "summary": [
                    f"{name}: {r['state']} ({r['trend']})"
                    for name, r in module_results.items()
                ]
            }

            print(json.dumps(output, indent=2))
            print("-" * 60)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nMarket Tracker stopped.")


if __name__ == "__main__":
    main()
