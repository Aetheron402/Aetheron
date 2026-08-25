# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

import time
from utils.helpers import load_config, setup_logger, token_passes_filters
from utils.rpc import SolanaClient


class SniperAgent:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.client = SolanaClient(config["rpc"], logger)

        self.auto_buy_enabled = config["sniper"]["auto_buy_enabled"]
        self.max_positions = config["wallet"]["max_open_positions"]
        self.open_positions = []

        self.logger.info("SniperAgent initialized.")

    def run(self):
        """
        Main loop: fetch opportunities, filter them, and optionally
        trigger a customizable execution hook.
        """
        poll_interval = self.config["rpc"]["poll_interval_seconds"]
        self.logger.info(f"Starting sniper loop (polling every {poll_interval}s)...")

        while True:
            try:
                opportunities = self.client.fetch_new_opportunities()

                if opportunities:
                    for token in opportunities:
                        self.process_token(token)
                else:
                    self.logger.debug("No new opportunities this cycle.")

                time.sleep(poll_interval)

            except Exception as e:
                self.logger.error(f"Unexpected error in main loop: {e}")
                time.sleep(1)

    def process_token(self, token):
        """
        Process and evaluate a detected token opportunity.
        """
        self.logger.info(
            f"Token detected: {token.get('mint', 'Unknown')} | "
            f"Liquidity: {token.get('liquidity_sol')} SOL | "
            f"Market Cap: {token.get('market_cap_usd')} USD"
        )

        # Apply safety and blacklist filters
        if not token_passes_filters(token, self.config, self.logger):
            return

        # Enforce max open positions
        if len(self.open_positions) >= self.max_positions:
            self.logger.info("Max open positions reached, execution skipped.")
            return

        # Auto-execution logic
        if self.auto_buy_enabled:
            self.execute_buy(token)
        else:
            self.logger.info("Auto-buy disabled, logging opportunity only.")

    def execute_buy(self, token):
        """
        Execution hook: called when a token passes filters and auto-buy is enabled.
        This function simulates a position entry and is the extension point
        for integrating your preferred DEX or routing solution.
        """
        spend_amount = self.config["wallet"]["max_spend_sol"]
        self.logger.info(
            f"Executing entry signal for {token['mint']} | Spend up to {spend_amount} SOL."
        )

        # Simulated "position" entry for template purposes.
        # Replace this logic with calls to Jupiter/Raydium/etc.
        self.open_positions.append({
            "mint": token["mint"],
            "entry_price": token.get("price"),
            "timestamp": time.time()
        })

        self.logger.info(f"Position opened for {token['mint']} (simulation entry).")


def main():
    # Load configuration
    config = load_config()

    # Initialize logger
    logger = setup_logger(
        level=config["logging"]["level"],
        to_file=config["logging"]["to_file"],
        file_name=config["logging"]["file_name"]
    )

    logger.info("Initializing Sniper Trade Agent...")

    # Start bot
    agent = SniperAgent(config, logger)
    agent.run()


if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
