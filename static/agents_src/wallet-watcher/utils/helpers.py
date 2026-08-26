import json
import logging
import requests
from pathlib import Path
from typing import Any, Dict


# Load Configuration
def load_config(path: str = "config.json") -> Dict[str, Any]:
    """
    Load configuration file.
    """
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found at: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


# Logger Setup
def setup_logger(level: str = "INFO", to_file: bool = False, file_name: str = "wallet_watcher.log") -> logging.Logger:
    """
    Create and configure the logger used for all output.
    """
    logger = logging.getLogger("wallet-watcher")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid double-registration
    if not logger.handlers:
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        if to_file:
            file_handler = logging.FileHandler(file_name)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger


# Webhook Notifications
def send_webhook(url: str, content: str, logger: logging.Logger):
    """
    Send event notification to a webhook.
    """
    try:
        response = requests.post(url, json={"content": content}, timeout=5)
        response.raise_for_status()
        logger.info("Webhook notification sent.")
    except Exception as e:
        logger.error(f"Webhook send failed: {e}")


# Event Formatting
# Mints whose ticker is a public constant. Anything not listed is shown as its
# mint address, because guessing a symbol is how a watcher starts lying.
KNOWN_MINTS = {
    "So11111111111111111111111111111111111111112": "wSOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP",
}


def format_event(event: Dict[str, Any]) -> str:
    """
    Format the event dictionary into a readable log string.
    """
    etype = event.get("type")

    # Transfers, native SOL and SPL tokens alike
    if etype in ("token_transfer", "sol_transfer"):
        direction = "Incoming" if event.get("incoming") else "Outgoing"
        token = event.get("token_symbol") or "unknown token"
        token = KNOWN_MINTS.get(token, token)
        # A mint address is 32 to 44 characters and unreadable in full. An
        # unknown one is shown truncated rather than given a made up ticker.
        if len(token) > 12:
            token = f"{token[:4]}...{token[-4:]}"
        amount = event.get("amount", 0)
        # Real balances span many orders of magnitude, so do not fix the
        # precision: a memecoin moves millions and an NFT moves one.
        shown = f"{amount:,.9f}".rstrip("0").rstrip(".") if amount < 1 else f"{amount:,.4f}".rstrip("0").rstrip(".")
        sig = event.get("signature")
        tail = f" | {sig[:12]}..." if sig else ""
        return (
            f"[EVENT] {direction} {shown} {token} | "
            f"Wallet: {event['wallet'][:8]}...{tail}"
        )

    # NFT Movements
    if etype == "nft_movement":
        return f"[EVENT] NFT Activity: {event['nft_name']} | Wallet: {event['wallet']}"

    # Swap/Trade Activity Signals
    if etype == "swap_signal":
        return f"[EVENT] Swap-Style Activity Signal | Wallet: {event['wallet']}"

    # Liquidity Activity Signals
    if etype == "liquidity_signal":
        return f"[EVENT] Liquidity-Style Activity Signal | Wallet: {event['wallet']}"

    # Unknown Event
    return f"[EVENT] Unclassified Activity: {event}"
