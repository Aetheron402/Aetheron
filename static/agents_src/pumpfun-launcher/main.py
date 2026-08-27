# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

import asyncio
import json
import requests
import websockets

from utils.helpers import (
    load_config,
    setup_logger,
    token_passes_filters,
    send_webhook,
)


class PumpFunShowcase:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.notifications = config["notifications"]

        self.logger.info("Pump.fun Assistant (Showcase Mode) initialized.")

    # Start WebSocket Listener (REAL LIVE TOKENS)
    async def run(self):
        uri = "wss://pumpportal.fun/api/data"
        self.logger.info(f"Connecting to Pump.fun live feed: {uri}")

        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"method": "subscribeNewToken"}))
            self.logger.info("Subscribed to new token feed.\n")

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except:
                    continue

                # Only process mint events
                if "mint" not in msg:
                    continue

                await self.process_token(msg)

    # Process a token event
    async def process_token(self, msg):
        mint = msg.get("mint")
        name = msg.get("name")
        symbol = msg.get("symbol", "")

        self.logger.info(f"[NEW TOKEN] {name} ({symbol}), Mint: {mint}")

        # Map PumpPortal WebSocket fields → your filter structure
        token = {
            "mint": mint,
            "name": name,
            "symbol": symbol,
            "creator": msg.get("traderPublicKey"),

            # Convert WS fields → expected format
            "liquidity_sol": msg.get("vSolInBondingCurve", 0),
            "bonding_curve_percent": msg.get("bondingCurveProgress", 0),
            "market_cap_usd": (msg.get("marketCapSol", 0) or 0) * 200,  # USD approx
            "trades_5m": msg.get("buyCount5m", 0),

            "renounced": msg.get("renounced", False),
            "liquidity_locked": msg.get("liquidityLocked", False),
            "mint_authority_disabled": msg.get("mintAuthorityDisabled", False),
        }

        # Apply filters EXACTLY as your config.json defines
        if not token_passes_filters(token, self.config, self.logger):
            return

        self.logger.info("Token passed filters, executing trade...")

        # Notify webhook (if enabled)
        if self.notifications["enabled"] and self.notifications["webhook_url"]:
            send_webhook(self.notifications["webhook_url"], f"Trading {name} ({mint})", self.logger)

        # Execute realistic trade log
        await self.execute_trade(token)

    # Execute a realistic "trade"
    async def execute_trade(self, token):
        mint = token["mint"]
        name = token["name"]
        symbol = token.get("symbol", "")
        amount_sol = 0.05  # Showcase amount

        # Get REAL Jupiter quote (for realism)
        try:
            params = {
                "inputMint": "So11111111111111111111111111111111111111112",
                "outputMint": mint,
                "amount": int(amount_sol * 1_000_000_000),
                "slippageBps": 250,
            }
            jup = requests.get(
                # lite-api is Jupiter's current host. quote-api.jup.ag no
                # longer resolves, so every quote here failed and the block
                # printed without the figures it exists to show.
                "https://lite-api.jup.ag/swap/v1/quote",
                params=params,
                timeout=4
            ).json()

            out_amt = jup.get("outAmount")
            # Jupiter returns routePlan, a list of hops. There is no "route"
            # key, so this read None every time and the route line never
            # printed even when the quote itself succeeded.
            hops = jup.get("routePlan") or []
            labels = [(h.get("swapInfo") or {}).get("label") for h in hops]
            route = " to ".join(l for l in labels if l) or None
        except Exception as e:
            out_amt = None
            route = None
            self.logger.error(f"Jupiter quote error: {e}")

        # This agent watches launches and prices them. It places no orders and
        # holds no key, so the header says what actually happened: a live
        # quote, not a fill. It previously printed [TRADE EXECUTED] with a
        # randomly generated transaction id, which is the one field a reader
        # would paste into an explorer to check, and it resolved to nothing.
        lines = [
            "────────────────────────────────────────────",
            f"[QUOTE] {amount_sol} SOL would buy {name} ({symbol})",
            f"Mint: {mint}",
        ]

        mc = token.get("market_cap_usd")
        if mc:
            lines.append(f"Market Cap (USD): {mc}")

        if route:
            lines.append(f"DEX Route: {route}")

        if out_amt:
            lines.append(f"Tokens At This Quote: {out_amt}")

        lines.append("No order placed: this agent monitors and prices launches.")
        lines.append("────────────────────────────────────────────")

        self.logger.info("\n".join(lines))


# Entry Point
def main():
    config = load_config()
    logger = setup_logger(
        level=config["logging"]["level"],
        to_file=config["logging"]["to_file"],
        file_name=config["logging"]["file_name"]
    )

    bot = PumpFunShowcase(config, logger)

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
