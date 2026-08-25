from utils.normalization import clamp


def run(config: dict):
    """
    Determines broad risk appetite based on cross-market momentum.
    """

    # --- placeholder inputs (will come from data.py later) ---
    equity_momentum = 0.6     # S&P / global equity proxy
    crypto_momentum = 0.4     # BTC / crypto index proxy

    # --- combine signals ---
    raw_score = (equity_momentum + crypto_momentum) / 2

    score = clamp(raw_score, -1.0, 1.0)

    # --- state determination ---
    pos_th = config.get("positive_threshold", 0.3)
    neg_th = config.get("negative_threshold", -0.3)

    if score >= pos_th:
        state = "positive"
    elif score <= neg_th:
        state = "negative"
    else:
        state = "neutral"

    # --- trend logic (simple, but correct) ---
    trend = "improving" if score > 0 else "deteriorating" if score < 0 else "stable"

    return {
        "state": state,
        "score": score,
        "confidence": 0.7,
        "trend": trend,
        "notes": [
            "Equity and crypto momentum used as risk proxies",
            f"Combined momentum score: {score:.2f}"
        ]
    }
