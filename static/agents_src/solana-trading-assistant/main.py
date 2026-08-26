# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

import time
import logging
from pathlib import Path

from utils.helpers import (
    load_config,
    setup_logger,
    analyze_token,
    pretty_print_analysis,
    send_webhook_notification,
)
from utils.rpc import SolanaRPCClient, BirdeyeClient, DexScreenerClient


class SolanaTradingAssistant:
    """
    High-level manager for the Solana Trading Assistant.

    Responsibilities:
    - Load config & initialize dependencies
    - Periodically fetch token data (price, liquidity, volume, candles)
    - Run analysis (trend, momentum, volume acceleration, liquidity stability)
    - Output results in human-friendly format
    - Optionally send structured webhook reports
    """

    def __init__(self, config: dict, logger: logging.Logger):
        self.config = config
        self.logger = logger

        # CONFIG SECTIONS
        rpc_cfg = config.get("rpc", {})
        birdeye_cfg = config.get("birdeye", {})
        analysis_cfg = config.get("analysis", {})
        notifications_cfg = config.get("notifications", {})

        self.tokens_to_watch = analysis_cfg.get("tokens_to_watch", [])
        self.poll_interval = analysis_cfg.get("poll_interval_seconds", 30)

        if not self.tokens_to_watch:
            self.logger.warning(
                "No tokens configured in analysis.tokens_to_watch. "
                "Add one or more token mint addresses in config.json."
            )

        # Initialize RPC Client
        self.rpc_client = SolanaRPCClient(
            rpc_url=rpc_cfg.get("url", ""),
            timeout_seconds=rpc_cfg.get("timeout_seconds", 10),
            logger=self.logger,
        )

        # Market data. Birdeye when a key is configured, DexScreener otherwise,
        # which needs none. Without this fallback an agent shipped without a key
        # produced only request failures, and the endpoints it was written
        # against have since been retired anyway.
        birdeye_key = (birdeye_cfg.get("api_key") or "").strip()
        if birdeye_key and not birdeye_key.upper().startswith("YOUR_"):
            self.birdeye_client = BirdeyeClient(
                api_key=birdeye_key,
                base_url=birdeye_cfg.get("base_url", "https://public-api.birdeye.so"),
                timeout_seconds=birdeye_cfg.get("timeout_seconds", 10),
                logger=self.logger,
            )
            self.logger.info("Market data source: Birdeye.")
        else:
            self.birdeye_client = DexScreenerClient(
                timeout_seconds=birdeye_cfg.get("timeout_seconds", 10),
                logger=self.logger,
            )
            self.logger.info(
                "Market data source: DexScreener, no API key required. "
                "Add birdeye.api_key to config.json to use Birdeye instead."
            )

        # Notification System
        self.notifications_enabled = bool(notifications_cfg.get("enabled", False))
        self.webhook_url = notifications_cfg.get("webhook_url", "")

        if self.notifications_enabled and not self.webhook_url:
            self.logger.warning(
                "Notifications enabled but no webhook URL provided. "
                "Disabling notifications."
            )
            self.notifications_enabled = False

        self.analysis_cfg = analysis_cfg

    # MAIN LOOP
    def run(self) -> None:
        """Main monitoring loop."""
        self.logger.info("Starting Solana Trading Assistant...")
        self.logger.info(
            "Watching %d token(s) every %d seconds.",
            len(self.tokens_to_watch),
            self.poll_interval,
        )

        try:
            while True:
                if not self.tokens_to_watch:
                    time.sleep(self.poll_interval)
                    continue

                for mint_address in self.tokens_to_watch:
                    try:
                        # Generate full market analysis for token
                        analysis = analyze_token(
                            mint_address=mint_address,
                            birdeye_client=self.birdeye_client,
                            rpc_client=self.rpc_client,
                            analysis_config=self.analysis_cfg,
                            logger=self.logger,
                        )

                        # Console Output
                        pretty_print_analysis(
                            mint_address=mint_address,
                            analysis=analysis,
                            logger=self.logger,
                        )

                        # Webhook Output
                        if self.notifications_enabled:
                            send_webhook_notification(
                                webhook_url=self.webhook_url,
                                mint_address=mint_address,
                                analysis=analysis,
                                logger=self.logger,
                            )

                    except Exception as exc:
                        self.logger.exception(
                            "Error analyzing token %s: %s", mint_address, exc
                        )

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            self.logger.info("Received KeyboardInterrupt. Shutting down.")

        except Exception as exc:
            self.logger.exception("FATAL ERROR in main loop: %s", exc)

        finally:
            self.logger.info("Solana Trading Assistant stopped.")
            return


# ENTRY POINT
if __name__ == "__main__":
    config_path = Path(__file__).parent / "config.json"

    config = load_config(config_path)
    logger = setup_logger(config.get("logging", {}))

    # This block stopped here. The class above was fully written and never
    # instantiated, so running main.py loaded a config, built a logger and
    # exited zero without printing anything, which is indistinguishable from
    # an agent that is broken.
    assistant = SolanaTradingAssistant(config, logger)
    assistant.run()
