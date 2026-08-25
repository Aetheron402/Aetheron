# License Notice:
# This template is licensed for personal use only.
# Redistribution or resale is strictly prohibited.
# See LICENSE.txt for details.

import asyncio
from utils.helpers import load_config, setup_logger
from utils.discord_client import create_discord_client


def main():
    # Load config
    config = load_config()

    # Set up logger
    logger = setup_logger(
        level=config["logging"]["level"],
        to_file=config["logging"]["to_file"],
        file_name=config["logging"]["file_name"]
    )

    logger.info("Initializing Discord Support Agent...")

    # Create bot instance (fully functional bot with real moderation & AI support)
    bot = create_discord_client(config, logger)

    # Start bot
    try:
        asyncio.run(bot.start(config["discord"]["bot_token"]))
    except KeyboardInterrupt:
        logger.info("Discord Support Agent stopped by user.")
    except Exception as e:
        logger.error(f"Unexpected bot error: {e}")


if __name__ == "__main__":
    main()
