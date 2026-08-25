import json
import logging
import requests
from pathlib import Path
from typing import Any, Dict


# Load Configuration
def load_config(path: str = "config.json") -> Dict[str, Any]:
    """
    Loads and returns the configuration JSON file.
    """
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found at: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


# Logger Setup
def setup_logger(level: str = "INFO", to_file: bool = False, file_name: str = "pumpfun_assistant.log") -> logging.Logger:
    """
    Creates and configures a logger used by the Pump.fun Assistant.
    """
    logger = logging.getLogger("pumpfun-assistant")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers on rerun/reload
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


# Webhook Sending
def send_webhook(url: str, content: str, logger: logging.Logger):
    """
    Sends formatted event data to a webhook endpoint.
    """
    try:
        response = requests.post(url, json={"content": content}, timeout=5)
        response.raise_for_status()
        logger.info("Webhook notification sent.")
    except Exception as e:
        logger.error(f"Webhook failed: {e}")


# Opportunity Formatting (Pump.fun Specific)
def format_opportunity(token: Dict[str, Any]) -> str:
    """
    Creates a readable string describing a pump.fun token opportunity.
    """
    mint = token.get("mint", "Unknown")
    name = token.get("name", "Unknown")
    creator = token.get("creator", "Unknown")

    liq = token.get("liquidity_sol", "?")
    mc = token.get("market_cap_usd", "?")
    bc = token.get("bonding_curve_percent", "?")
    trades = token.get("trades_5m", "?")

    return (
        f"[TOKEN] {name} ({mint})\n"
        f"Creator: {creator}\n"
        f"Liquidity: {liq} SOL | MC: ${mc} | BC: {bc}% | Trades 5m: {trades}"
    )


# Filtering Logic
def token_passes_filters(token: Dict[str, Any], config: Dict[str, Any], logger: logging.Logger) -> bool:
    """
    Applies safety checks, blacklist rules, and pump.fun-specific filters.
    """

    pump_cfg = config["pumpfun"]
    filters_cfg = config["filters"]
    blacklist = config["blacklist"]

    mint = token.get("mint")
    creator = token.get("creator")

    # Blacklist checks
    if mint in blacklist["token_mints"]:
        logger.info(f"Rejected {mint}: blacklisted token mint.")
        return False

    if creator in blacklist["creator_addresses"]:
        logger.info(f"Rejected {mint}: creator address is blacklisted.")
        return False

    if token.get("program_id") in blacklist["program_ids"]:
        logger.info(f"Rejected {mint}: blacklisted program ID.")
        return False

    # Liquidity & bonding curve rules
    if token.get("liquidity_sol", 0) < pump_cfg["min_liquidity_sol"]:
        logger.debug(f"Rejected {mint}: liquidity below minimum.")
        return False

    if token.get("bonding_curve_percent", 0) < pump_cfg["min_bonding_curve_percent"]:
        logger.debug(f"Rejected {mint}: bonding curve too early.")
        return False

    # Market cap ceiling
    mc = token.get("market_cap_usd")
    if mc is not None and mc > pump_cfg["max_market_cap_usd"]:
        logger.debug(f"Rejected {mint}: market cap too high.")
        return False

    # Optional creator & safety filters
    if filters_cfg["block_renounced_only"] and not token.get("renounced", False):
        logger.debug(f"Rejected {mint}: renounce requirement not met.")
        return False

    if filters_cfg["require_locked_liquidity"] and not token.get("liquidity_locked", False):
        logger.debug(f"Rejected {mint}: liquidity not locked.")
        return False

    if filters_cfg["block_mint_authority"] and not token.get("mint_authority_disabled", False):
        logger.debug(f"Rejected {mint}: mint authority still active.")
        return False

    # Trading activity
    if token.get("trades_5m", 0) < filters_cfg["min_trades_5m"]:
        logger.debug(f"Rejected {mint}: insufficient trading activity.")
        return False

    # Passed all checks
    logger.info(f"{mint} passed all filters.")
    return True
