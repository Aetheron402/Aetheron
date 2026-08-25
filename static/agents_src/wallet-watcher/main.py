# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

import time
from utils.helpers import load_config, setup_logger, send_webhook, format_event
from utils.rpc import WalletWatcherClient


class WalletWatcher:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.client = WalletWatcherClient(config["rpc"], logger)

        self.wallets = config["wallets_to_watch"]
        self.notifications = config["notifications"]

        self.logger.info(f"WalletWatcher initialized for {len(self.wallets)} wallet(s).")

    def run(self):
        """
        Main loop — poll RPC, detect activity, classify events, log them, and notify if enabled.
        """
        poll_interval = self.config["rpc"]["poll_interval_seconds"]
        self.logger.info(f"Starting wallet watcher loop (poll every {poll_interval}s)...")

        while True:
            try:
                # Fetch activity events from RPC and event detectors
                events = self.client.fetch_events(self.wallets)

                # Process detected events
                for event in events:
                    self.handle_event(event)

                time.sleep(poll_interval)

            except Exception as e:
                self.logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(1)

    def handle_event(self, event):
        """
        Handle a single wallet event: log it and notify if needed.
        """
        formatted = format_event(event)
        self.logger.info(formatted)

        # Optional webhook notifications
        if self.notifications["enabled"] and self.notifications["webhook_url"]:
            send_webhook(self.notifications["webhook_url"], formatted, self.logger)


def main():
    # Load config
    config = load_config()

    # Set up logger
    logger = setup_logger(
        level=config["logging"]["level"],
        to_file=config["logging"]["to_file"],
        file_name=config["logging"]["file_name"]
    )

    logger.info("Initializing Wallet Watcher Bot...")

    # Start the watcher
    watcher = WalletWatcher(config, logger)
    watcher.run()


if __name__ == "__main__":
    main()
