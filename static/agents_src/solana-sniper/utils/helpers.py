import json
import logging
from pathlib import Path
from typing import Any, Dict


# Load Configuration
def load_config(path: str = "config.json") -> Dict[str, Any]:
    """
    Loads and returns the JSON configuration file.
    """
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found at: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


# Logger Setup
def setup_logger(level: str = "INFO", to_file: bool = False, file_name: str = "sniper.log") -> logging.Logger:
    """
    Creates and configures the global logger used by the sniper agent.
    """
    logger = logging.getLogger("sniper-agent")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate log handlers on reload or rerun
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


# Token Filtering Logic
def token_passes_filters(token: Dict[str, Any], config: Dict[str, Any], logger: logging.Logger) -> bool:
    """
    Evaluates a discovered token against the configured filters and safety rules.

    Expected fields that RPC or discovery sources should provide:
    - mint
    - creator
    - program_id
    - liquidity_sol
    - market_cap_usd
    - trades_1m
    - renounced
    - liquidity_locked
    - mint_authority_disabled
    """

    sniper_cfg = config["sniper"]
    filters_cfg = config["filters"]
    blacklist = config["blacklist"]

    mint = token.get("mint")
    creator = token.get("creator")

    # Blacklist validation
    if mint in blacklist["token_mints"]:
        logger.info(f"Rejected {mint}: token mint is blacklisted.")
        return False

    if creator in blacklist["creator_addresses"]:
        logger.info(f"Rejected {mint}: creator address is blacklisted.")
        return False

    if token.get("program_id") in blacklist["program_ids"]:
        logger.info(f"Rejected {mint}: program ID is blacklisted.")
        return False

    # Liquidity rules
    liquidity = token.get("liquidity_sol", 0)
    if liquidity < sniper_cfg["min_liquidity_sol"]:
        logger.debug(f"Rejected {mint}: liquidity {liquidity} < minimum {sniper_cfg['min_liquidity_sol']}.")
        return False

    # Market cap rules
    market_cap = token.get("market_cap_usd", None)
    if market_cap is not None and market_cap > sniper_cfg["max_market_cap_usd"]:
        logger.debug(f"Rejected {mint}: market cap {market_cap} > maximum {sniper_cfg['max_market_cap_usd']}.")
        return False

    # Optional safety settings. A value of None means the discovery source did
    # not answer, which is rejected the same as a failure but reported
    # differently: not knowing and knowing it is unsafe are not the same
    # finding, and a log that conflates them teaches the wrong thing.
    for key, flag, unsafe, unknown in (
        ("renounced", "require_renounced",
         "ownership is not renounced", "renounce status could not be read"),
        ("liquidity_locked", "require_locked_liquidity",
         "liquidity is not locked", "lock status could not be read"),
        ("mint_authority_disabled", "block_mint_authority",
         "mint authority is still active", "mint authority could not be read"),
    ):
        if not filters_cfg[flag]:
            continue
        value = token.get(key)
        if value is None:
            logger.debug(f"Rejected {mint}: {unknown}.")
            return False
        if not value:
            logger.debug(f"Rejected {mint}: {unsafe}.")
            return False

    # 1-minute trading activity
    trades_1m = token.get("trades_1m", 0)
    if trades_1m < filters_cfg["min_trades_1m"]:
        logger.debug(f"Rejected {mint}: only {trades_1m} trades in last 1m (< {filters_cfg['min_trades_1m']}).")
        return False

    # Passed every filter
    logger.info(f"{mint} passed all filters.")
    return True
