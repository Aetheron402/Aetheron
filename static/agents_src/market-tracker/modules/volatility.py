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
    """Volatility regime: what the market expects against what it has done."""
    realized = data.get_realized_volatility()
    implied = data.get_implied_volatility()

    if realized is None and implied is None:
        return _unavailable("Neither realised nor implied volatility was available.")

    snap = data.snapshot()["values"]
    notes = []

    if realized is not None and implied is not None:
        # Implied above realised means the market is paying up for protection.
        score = clamp(-(implied - realized), -1.0, 1.0)
        notes.append(
            f"Realised {snap.get('realized_vol_annualised', 0) * 100:.1f}% annualised "
            f"against implied {snap.get('implied_vol_index', 0) * 100:.1f}% (DVOL)"
        )
        notes.append(
            "Implied above realised: the market is pricing more volatility than "
            "has occurred." if implied > realized else
            "Realised above implied: recent moves are larger than the market expects."
        )
        confidence = min(1.0, 0.6 + abs(score))
    else:
        present = realized if realized is not None else implied
        which = "Realised" if realized is not None else "Implied"
        score = clamp(-(present - 0.5) * 2, -1.0, 1.0)
        notes.append(f"{which} volatility only; the other source did not respond.")
        confidence = 0.4

    notes.append(f"Volatility pressure score: {score:.2f}")

    return {
        "state": _classify(score, config, 0.2, -0.2),
        "score": score,
        "confidence": confidence,
        "trend": _trend(score, 0.1),
        "notes": notes,
    }
