from utils.normalization import clamp


def run(config: dict):
    """
    Evaluates volatility regime: expansion vs compression.
    """

    # --- placeholder inputs ---
    realized_vol = 0.6     # higher = more realized volatility
    implied_vol = 0.7      # higher = market pricing more risk

    # vol expansion proxy
    vol_pressure = (implied_vol - realized_vol)

    # invert logic: higher vol = negative environment
    raw_score = -vol_pressure
    score = clamp(raw_score, -1.0, 1.0)

    pos_th = config.get("positive_threshold", 0.2)
    neg_th = config.get("negative_threshold", -0.2)

    if score >= pos_th:
        state = "positive"
    elif score <= neg_th:
        state = "negative"
    else:
        state = "neutral"

    # trend logic
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
            "Volatility expansion/compression evaluated",
            f"Volatility pressure score: {score:.2f}"
        ]
    }
