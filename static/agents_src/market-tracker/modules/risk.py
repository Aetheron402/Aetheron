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
    """Broad risk appetite, from how the majors actually moved."""
    crypto = data.get_crypto_momentum()
    equity = data.get_equity_momentum()      # no keyless source, so None

    if crypto is None:
        return _unavailable("Crypto momentum was unavailable from CoinGecko.")

    parts = [crypto] + ([equity] if equity is not None else [])
    score = clamp(sum(parts) / len(parts), -1.0, 1.0)

    moves = data.snapshot()["values"].get("crypto_moves_pct") or []
    notes = [
        "Risk appetite from 24h moves across BTC, ETH and SOL"
        + (f": {', '.join(f'{m:+.2f}%' for m in moves)}" if moves else ""),
        f"Combined momentum score: {score:.2f}",
    ]
    if equity is None:
        notes.append(
            "Equity momentum was not included: no keyless source is available, "
            "so this reads crypto only."
        )

    return {
        "state": _classify(score, config, 0.3, -0.3),
        "score": score,
        # Lower, because half the intended inputs are missing.
        "confidence": 0.55 if equity is None else 0.75,
        "trend": _trend(score),
        "notes": notes,
    }
