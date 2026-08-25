import json
import logging
from pathlib import Path
from typing import Any, Dict


# Load Configuration
def load_config(path: str = "config.json") -> Dict[str, Any]:
    """
    Loads and returns the JSON configuration file.
    """
    config_path = Path(path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found at: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


# Logger Setup
def setup_logger(level: str = "INFO", to_file: bool = False, file_name: str = "discord_agent.log") -> logging.Logger:
    """
    Sets up the logger for the Discord Support Agent.
    """

    logger = logging.getLogger("discord-support-agent")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers when bot restarts or reloads
    if not logger.handlers:
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")

        # Console output
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # File logging (optional)
        if to_file:
            file_handler = logging.FileHandler(file_name)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger

# Safe Message Sender
async def safe_send(channel, content: str, logger: logging.Logger):
    """
    Safely sends a message to a Discord channel with error handling.
    """
    try:
        await channel.send(content)
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
