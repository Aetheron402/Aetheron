from utils.normalization import clamp


def run(config: dict):
    """
    Evaluates market psychology: fear vs complacency.
    """

    # --- placeholder inputs ---
    fear_index = 0.3        # lower = fear, higher = complacency
    positioning_extreme = 0.7  # higher = crowded positioning

    # psychology balance:
    # complacency + crowding = negative
    raw_score = 1.0 - ((fear_index + positioning_extreme) / 2)

    score = clamp(raw_score * 2 - 1, -1.0, 1.0)

    pos_th = config.get("positive_threshold", 0.25)
    neg_th = config.get("negative_threshold", -0.25)

    if score >= pos_th:
        state = "positive"
    elif score <= neg_th:
        state = "negative"
    else:
        state = "neutral"

    # psychology is slow-moving
    if score > 0.4:
        trend = "improving"
    elif score < -0.4:
        trend = "deteriorating"
    else:
        trend = "stable"

    confidence = min(1.0, 0.5 + abs(score))

    return {
        "state": state,
        "score": score,
        "confidence": confidence,
        "trend": trend,
        "notes": [
            "Fear/complacency and positioning evaluated",
            f"Psychology score: {score:.2f}"
        ]
    }
