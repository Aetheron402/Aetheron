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
def format_event(event: Dict[str, Any]) -> str:
    """
    Format the event dictionary into a readable log string.
    """
    etype = event.get("type")

    # Token Transfers
    if etype == "token_transfer":
        direction = "Incoming" if event.get("incoming") else "Outgoing"
        return (
            f"[EVENT] {direction} Token Transfer: {event['amount']} {event['token_symbol']} | "
            f"Wallet: {event['wallet']}"
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
