import json
import time

from adapters.example import ExampleAdapter
from core.engine import PredictionMarketEngine
from core.strategy import ProbabilityThresholdStrategy
from core.sizing import FixedFractionSizer
from risk.exposure import ExposureManager
from schemas import AgentConfig
from core.lifecycle import LifecycleManager
from utils.logging import setup_logger

logger = setup_logger("main")


def load_config(path: str) -> AgentConfig:
    with open(path, "r") as f:
        data = json.load(f)

    return AgentConfig(
        bankroll=data["bankroll"],
        max_exposure=data["max_exposure"],
        strategy=data["strategy"],
        sizing=data["sizing"],
        run_interval=data["run_interval"],
        risk_limits=data["risk_limits"],
    )


def main():
    logger.info("Starting Prediction Market Agent")
    logger.info("This is a TEMPLATE agent.")
    logger.info("You are currently using ExampleAdapter (mock market data).")
    logger.info("To connect a real prediction market, replace ExampleAdapter with your own adapter.")

    config = load_config("config.json")
    logger.info(
        f"Loaded config | bankroll={config.bankroll} "
        f"max_exposure={config.max_exposure} "
        f"run_interval={config.run_interval}s"
    )

    adapter = ExampleAdapter()
    logger.info("Adapter initialized")

    strategy = ProbabilityThresholdStrategy(
        min_probability=0.3
    )
    logger.info("Strategy initialized: ProbabilityThresholdStrategy")

    sizer = FixedFractionSizer(
        fraction=0.05,
        max_size=100.0,
    )
    logger.info("Sizer initialized: FixedFractionSizer")

    risk_manager = ExposureManager(
        max_exposure=config.max_exposure
    )
    logger.info("Risk manager initialized")

    lifecycle_manager = LifecycleManager(
        max_hold_time_seconds=21600
    )
    logger.info("Lifecycle manager initialized (time-based exits enabled)")

    engine = PredictionMarketEngine(
        adapter=adapter,
        strategy=strategy,
        sizer=sizer,
        risk_manager=risk_manager,
        lifecycle_manager=lifecycle_manager,
        config=config,
    )
    logger.info("Engine initialized successfully")

    logger.info("Agent is now running. Press Ctrl+C to stop.")

    while True:
        engine.run()
        time.sleep(config.run_interval)


if __name__ == "__main__":
    main()