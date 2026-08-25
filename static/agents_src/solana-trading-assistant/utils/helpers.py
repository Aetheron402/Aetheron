# utils/helpers.py

import json
import logging
import requests


# CONFIG & LOGGER
def load_config(path):
    """Load JSON config file."""
    with open(path, "r") as f:
        return json.load(f)


def setup_logger(logging_cfg: dict) -> logging.Logger:
    """Configure logger used throughout the assistant."""
    logger = logging.getLogger("SolanaTradingAssistant")
    logger.setLevel(logging_cfg.get("level", "INFO"))

    formatter = logging.Formatter(
        "[%(levelname)s] %(asctime)s — %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Optional file logging
    if logging_cfg.get("to_file", False):
        fh = logging.FileHandler(logging_cfg.get("file_name", "trading_assistant.log"))
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


# MARKET ANALYSIS LOGIC
def analyze_token(
    mint_address: str,
    birdeye_client,
    rpc_client,
    analysis_config: dict,
    logger: logging.Logger,
) -> dict:
    """
    Core market analysis for a token.
    Pulls price, liquidity, volume, candles and evaluates trend strength,
    volume acceleration, liquidity stability, and volatility.
    """

    # Fetch data from Birdeye
    price_info = birdeye_client.get_token_price(mint_address)
    liquidity_info = birdeye_client.get_token_liquidity(mint_address)
    volume_info = birdeye_client.get_token_volume(mint_address)
    candles = birdeye_client.get_token_candles(
        mint_address, analysis_config.get("timeframes", ["5m", "15m", "1h"])
    )

    # Trend analysis (simple linear price momentum)
    trend_scores = {}

    for tf, candle_data in candles.items():
        if not candle_data or len(candle_data) < 2:
            trend_scores[tf] = 0
            continue

        open_price = candle_data[-1]["o"]  # newest candle
        close_price = candle_data[-1]["c"]

        if open_price > 0:
            pct_change = ((close_price - open_price) / open_price) * 100
        else:
            pct_change = 0

        trend_scores[tf] = pct_change

    # Aggregate price trend score
    price_trend_score = sum(trend_scores.values()) / max(len(trend_scores), 1)

    # Volume acceleration: compare 5m candle to older candles
    vol_accel = 0
    if "5m" in candles and candles["5m"]:
        recent_vol = candles["5m"][-1]["v"]
        older_vols = [c["v"] for c in candles["5m"][-5:] if "v" in c]
        avg_old_vol = sum(older_vols[:-1]) / max(len(older_vols) - 1, 1)

        if avg_old_vol > 0:
            vol_accel = (recent_vol - avg_old_vol) / avg_old_vol * 100

    # Liquidity stability (Birdeye returns USD liquidity)
    liquidity_usd = liquidity_info.get("liquidity", 0)
    min_liquidity = analysis_config.get("thresholds", {}).get("min_liquidity_usd", 0)
    liquidity_stable = liquidity_usd >= min_liquidity

    # Volatility estimation (basic candle range)
    volatility = 0
    if "15m" in candles and candles["15m"]:
        cndl = candles["15m"][-1]
        high, low = cndl["h"], cndl["l"]
        if low > 0:
            volatility = ((high - low) / low) * 100

    # Scoring system (weighted)
    weights = analysis_config.get("scores", {})
    score_price = price_trend_score * weights.get("weight_price_trend", 0.35)
    score_volume = vol_accel * weights.get("weight_volume_trend", 0.35)
    score_liquidity = (
        (1 if liquidity_stable else -1) * 10 * weights.get("weight_liquidity_stability", 0.20)
    )
    score_volatility = ((-volatility) * weights.get("weight_volatility", 0.10))

    total_score = score_price + score_volume + score_liquidity + score_volatility

    # Structured JSON-friendly analysis object
    result = {
        "mint": mint_address,
        "price": price_info.get("price", 0),
        "liquidity_usd": liquidity_usd,
        "volume_24h_usd": volume_info.get("volume_24h", 0),
        "trend": trend_scores,
        "price_trend_score": price_trend_score,
        "volume_acceleration_percent": vol_accel,
        "volatility_percent": volatility,
        "liquidity_ok": liquidity_stable,
        "total_score": total_score,
    }

    return result


# PRINTING / OUTPUT
def pretty_print_analysis(mint_address: str, analysis: dict, logger: logging.Logger):
    """Human-friendly output formatting."""
    logger.info(
        "\n──────────────────────────────────────────────────────────\n"
        f"Token: {mint_address}\n"
        f"Price: {analysis['price']:.6f} USD\n"
        f"Liquidity: ${analysis['liquidity_usd']:,}\n"
        f"24h Volume: ${analysis['volume_24h_usd']:,}\n"
        f"Trend (%%): {analysis['trend']}\n"
        f"Price Trend Score: {analysis['price_trend_score']:.2f}\n"
        f"Volume Acceleration: {analysis['volume_acceleration_percent']:.2f}%\n"
        f"Volatility: {analysis['volatility_percent']:.2f}%\n"
        f"Liquidity Stable: {analysis['liquidity_ok']}\n"
        f"TOTAL SCORE: {analysis['total_score']:.2f}\n"
        "──────────────────────────────────────────────────────────"
    )


# WEBHOOKS
def send_webhook_notification(webhook_url: str, mint_address: str, analysis: dict, logger: logging.Logger):
    """Send webhook with JSON payload."""
    payload = {
        "content": f"📊 **Market Analysis for {mint_address}**",
        "embeds": [
            {
                "title": "Token Market Summary",
                "fields": [
                    {"name": "Price", "value": f"{analysis['price']:.6f} USD", "inline": True},
                    {"name": "Liquidity", "value": f"${analysis['liquidity_usd']:,}", "inline": True},
                    {"name": "24h Volume", "value": f"${analysis['volume_24h_usd']:,}", "inline": True},
                    {"name": "Volatility", "value": f"{analysis['volatility_percent']:.2f}%", "inline": True},
                    {"name": "Trend Score", "value": f"{analysis['price_trend_score']:.2f}", "inline": True},
                    {"name": "Total Score", "value": f"{analysis['total_score']:.2f}", "inline": True}
                ],
                "color": 3752061
            }
        ]
    }

    try:
        r = requests.post(webhook_url, json=payload, timeout=5)
        r.raise_for_status()
        logger.info("Webhook sent successfully.")
    except Exception as exc:
        logger.error(f"Failed to send webhook: {exc}")
