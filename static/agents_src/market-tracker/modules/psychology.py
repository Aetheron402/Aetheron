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
    """Sentiment and crowding, from the Fear and Greed index and positioning."""
    fear = data.get_fear_index()
    crowding = data.get_positioning_extremes()

    if fear is None and crowding is None:
        return _unavailable("Sentiment and positioning data were both unavailable.")

    snap = data.snapshot()["values"]
    notes = []
    parts = []

    if fear is not None:
        raw = snap.get("fear_raw", fear * 100)
        # Greed is a headwind, fear is support, so this scores inversely.
        parts.append(-(fear - 0.5) * 2)
        mood = ("extreme greed" if raw >= 75 else "greed" if raw >= 55 else
                "neutral" if raw >= 45 else "fear" if raw >= 25 else "extreme fear")
        notes.append(f"Fear and Greed index at {raw:.0f}, {mood}")
    else:
        notes.append("The Fear and Greed index was unavailable.")

    if crowding is not None:
        ratio = snap.get("long_short_ratio")
        parts.append(-crowding)
        if ratio:
            notes.append(
                f"Long/short account ratio {ratio:.2f}, "
                + ("longs crowded" if ratio > 1.15 else
                   "shorts crowded" if ratio < 0.87 else "positioning near parity")
            )
    else:
        notes.append("Positioning data was unavailable.")

    score = clamp(sum(parts) / len(parts), -1.0, 1.0)
    notes.append(f"Psychology score: {score:.2f}")

    return {
        "state": _classify(score, config, 0.25, -0.25),
        "score": score,
        "confidence": 0.7 if len(parts) == 2 else 0.45,
        "trend": _trend(score, 0.1),
        "notes": notes,
    }
