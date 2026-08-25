from utils.normalization import clamp


def run(config: dict):
    """
    Evaluates liquidity conditions: expansion vs contraction.
    """

    # --- placeholder inputs (replace with real data later) ---
    funding_rate_pressure = -0.2   # negative = cheaper leverage
    stablecoin_flow = 0.4          # positive = inflows

    # --- combine signals ---
    raw_score = (funding_rate_pressure + stablecoin_flow) / 2
    score = clamp(raw_score, -1.0, 1.0)

    pos_th = config.get("positive_threshold", 0.25)
    neg_th = config.get("negative_threshold", -0.25)

    if score >= pos_th:
        state = "positive"
    elif score <= neg_th:
        state = "negative"
    else:
        state = "neutral"

    # --- trend ---
    if score > 0.1:
        trend = "improving"
    elif score < -0.1:
        trend = "deteriorating"
    else:
        trend = "stable"

    confidence = min(1.0, 0.6 + abs(score))

    return {
        "state": state,
        "score": score,
        "confidence": confidence,
        "trend": trend,
        "notes": [
            "Funding pressure and stablecoin flows used as liquidity proxies",
            f"Liquidity score: {score:.2f}"
        ]
    }
