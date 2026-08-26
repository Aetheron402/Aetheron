from utils.normalization import clamp
from utils import data


def _classify(score, config, pos_default, neg_default):
    """Score to state, using the thresholds from config."""
    pos = config.get("positive_threshold", pos_default)
    neg = config.get("negative_threshold", neg_default)
    if score >= pos:
        return "positive"
    if score <= neg:
        return "negative"
    return "neutral"


def _trend(score, band=0.0):
    if score > band:
        return "improving"
    if score < -band:
        return "deteriorating"
    return "stable"


def _unavailable(reason):
    """
    What a module returns when its inputs did not arrive.

    Reported rather than defaulted. Every module here used to score from
    constants, so a reader could not tell a measurement from a placeholder,
    and the whole point of the change is that they now can.
    """
    return {
        "state": "unknown",
        "score": 0.0,
        "confidence": 0.0,
        "trend": "stable",
        "notes": [reason, "No score produced: this input was not measured."],
    }


def run(config: dict):
    """How much the market is moving as one thing."""
    correlation = data.get_cross_asset_correlation()
    participation = data.get_participation_rate()

    if correlation is None or participation is None:
        return _unavailable("Price data for the majors was unavailable from CoinGecko.")

    snap = data.snapshot()["values"]
    moves = snap.get("crypto_moves_pct") or []

    # High correlation with narrow participation is fragile: everything is
    # moving together and few names are carrying it.
    score = clamp((participation - 0.5) * 2 - (correlation - 0.5), -1.0, 1.0)

    return {
        "state": _classify(score, config, 0.4, 0.2),
        "score": score,
        "confidence": 0.65,
        "trend": _trend(score, 0.1),
        "notes": [
            f"Majors moved {', '.join(f'{m:+.2f}%' for m in moves)} over 24h"
            if moves else "24h moves across the majors",
            f"Dispersion implies correlation {correlation:.2f}, "
            f"participation {participation:.0%}",
            f"Correlation score: {score:.2f}",
        ],
    }
