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
    """Liquidity conditions, from funding costs and stablecoin peg drift."""
    funding = data.get_funding_pressure()
    stables = data.get_stablecoin_flows()

    if funding is None and stables is None:
        return _unavailable("Funding rates and stablecoin data were both unavailable.")

    snap = data.snapshot()["values"]
    notes = []
    parts = []

    if funding is not None:
        # Cheap leverage is supportive, so the sign inverts.
        parts.append(-funding)
        notes.append(
            f"Mean perpetual funding {snap.get('funding_rate', 0) * 100:.4f}% across "
            f"BTC, ETH and SOL"
            + (", longs are paying to hold" if snap.get("funding_rate", 0) > 0
               else ", shorts are paying to hold")
        )
    else:
        notes.append("Funding rates were unavailable.")

    if stables is not None:
        parts.append(stables)
        supply = snap.get("stablecoin_supply_usd")
        if supply:
            notes.append(f"USDT and USDC supply ${supply / 1e9:.1f}bn, peg drift used as a flow proxy")
    else:
        notes.append("Stablecoin data was unavailable.")

    score = clamp(sum(parts) / len(parts), -1.0, 1.0)
    notes.append(f"Liquidity score: {score:.2f}")

    return {
        "state": _classify(score, config, 0.25, -0.25),
        "score": score,
        "confidence": 0.7 if len(parts) == 2 else 0.4,
        "trend": _trend(score, 0.1),
        "notes": notes,
    }
