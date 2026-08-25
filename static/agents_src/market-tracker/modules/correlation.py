from utils.normalization import clamp


def run(config: dict):
    """
    Evaluates cross-asset correlation and market participation.
    """

    # --- placeholder inputs ---
    cross_asset_correlation = 0.7   # high = everything moving together
    participation_rate = 0.5        # % assets participating in moves

    # --- logic ---
    raw_score = (cross_asset_correlation + participation_rate) / 2
    score = clamp(raw_score, -1.0, 1.0)

    pos_th = config.get("positive_threshold", 0.4)
    neg_th = config.get("negative_threshold", 0.2)

    # NOTE: correlation is inverted logic
    # high correlation = regime-dominated (positive)
    # low correlation = fragmented (negative)

    if score >= pos_th:
        state = "positive"
    elif score <= neg_th:
        state = "negative"
    else:
        state = "neutral"

    trend = (
        "improving" if score > 0.5 else
        "deteriorating" if score < 0.3 else
        "stable"
    )

    confidence = min(1.0, 0.5 + abs(score - 0.5))

    return {
        "state": state,
        "score": score - 0.5,  # center around 0
        "confidence": confidence,
        "trend": trend,
        "notes": [
            "Cross-asset correlation and participation evaluated",
            f"Correlation regime score: {score:.2f}"
        ]
    }
