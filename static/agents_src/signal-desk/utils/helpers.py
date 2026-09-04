# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

"""Loading the config and setting up the log. Nothing clever."""

import json
import logging
import sys
from typing import Any, Dict


def load_config(path: str = "config.json") -> Dict[str, Any]:
    with open(path) as handle:
        return json.load(handle)


def setup_logger(level: str = "INFO", to_file: bool = False,
                 file_name: str = "signal_desk.log") -> logging.Logger:
    logger = logging.getLogger("signal-desk")
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s",
                            "%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if to_file:
        handler = logging.FileHandler(file_name)
        handler.setFormatter(fmt)
        logger.addHandler(handler)

    return logger
