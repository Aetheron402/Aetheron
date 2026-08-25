"""
Data access layer.

This file defines how market data enters the agent.
Replace these functions with real API calls as needed.
"""


def get_equity_momentum() -> float:
    """
    Proxy for global equity momentum.
    Expected range: -1.0 → +1.0
    """
    return 0.6


def get_crypto_momentum() -> float:
    """
    Proxy for crypto market momentum.
    Expected range: -1.0 → +1.0
    """
    return 0.4


def get_realized_volatility() -> float:
    """
    Proxy for realized volatility.
    Higher = more volatile.
    """
    return 0.6


def get_implied_volatility() -> float:
    """
    Proxy for implied volatility.
    Higher = market pricing more risk.
    """
    return 0.7


def get_funding_pressure() -> float:
    """
    Proxy for funding / rates pressure.
    Negative = easier liquidity.
    """
    return -0.2


def get_stablecoin_flows() -> float:
    """
    Proxy for stablecoin inflows/outflows.
    Positive = inflows.
    """
    return 0.4


def get_cross_asset_correlation() -> float:
    """
    Proxy for cross-asset correlation.
    0 → uncorrelated, 1 → fully correlated.
    """
    return 0.7


def get_participation_rate() -> float:
    """
    Proxy for market participation / breadth.
    """
    return 0.5


def get_fear_index() -> float:
    """
    Proxy for fear/complacency indicator.
    Higher = complacency.
    """
    return 0.3


def get_positioning_extremes() -> float:
    """
    Proxy for crowded positioning.
    Higher = more crowded.
    """
    return 0.7
