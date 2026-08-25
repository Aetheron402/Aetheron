from schemas import EnvironmentState


def determine_regime(
    score: float,
    thresholds: dict
) -> EnvironmentState:
    risk_on = thresholds.get("risk_on", 0.35)
    risk_off = thresholds.get("risk_off", -0.35)

    if score >= risk_on:
        return "risk_on"
    if score <= risk_off:
        return "risk_off"
    return "neutral"
