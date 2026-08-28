"""
What a given caller pays for a given component, in a given currency.

Every price in the system comes from here. The 402 quote, the AETH conversion
endpoint the shop calls, and the on-chain settlement check all read the same
function, because the failure mode of a per-wallet price is the quote and the
settlement disagreeing: quote high and settle low and anybody pays less than
they should, quote low and settle high and an eligible buyer's payment is
rejected as short.

Two adjustments stack, in this order:

1. The legacy holder discount, for wallets that held the first AETH mint.
2. The AETH fee tier, when paying in AETH rather than USDC.

The component's list price is looked up here by name rather than passed in by
the caller. The AETH quote endpoint used to take the price as a query
parameter, which meant the browser told the server what a thing cost. Nothing
was stealable that way, since settlement priced independently, but a discounted
wallet was quoted the full amount, paid it, and had the overpayment silently
accepted.
"""

import math
import os

import legacy_holders

# Paying in AETH costs this much less than paying in USDC. Applied at the
# protocol layer rather than run as a promotion, so there is no code to enter
# and nothing to expire.
AETH_FEE_DISCOUNT = float(os.getenv("AETH_FEE_DISCOUNT", "0.20"))

# The list prices, from the same environment the app reads them from, so there
# is one definition rather than a copy that can drift.
COMPONENT_PRICES = {
    "prompt-optimizer": os.getenv("PRICE_PROMPT_OPTIMIZER", "0.25"),
    "code-explainer": os.getenv("PRICE_CODE_EXPLAINER", "0.50"),
    "prompt-tester": os.getenv("PRICE_PROMPT_TESTER", "0.50"),
    "contract-intel": os.getenv("PRICE_CONTRACT_INTEL", "1.00"),
    "risk-engine": os.getenv("PRICE_RISK_ENGINE", "0.75"),
    "agent": os.getenv("PRICE_AGENT", "4.99"),
}


def list_price(component: str) -> float:
    """The undiscounted price of a component. Raises on an unknown name."""
    if component not in COMPONENT_PRICES:
        raise KeyError(f"unknown component: {component}")
    return float(COMPONENT_PRICES[component])


def _floor_cent(value: float) -> float:
    """
    Down to the cent USDC settles at, and never to zero.

    Down rather than nearest, so a discount is never quietly smaller than it
    promises. Never zero, because an expected amount of zero is rejected by the
    settlement check, which would lock that component's buyers out entirely.
    """
    return max(0.01, math.floor(value * 100) / 100)


def effective_usd(base_price, wallet: str | None = None,
                  method: str = "USDC") -> float:
    """
    The USD amount this caller owes, before any conversion into AETH.

    Both adjustments are derived here from the wallet and the chosen method.
    Neither is ever taken from the request.
    """
    price = legacy_holders.price_for(wallet, base_price)
    if str(method).upper() == "AETH":
        price = _floor_cent(price * (1.0 - AETH_FEE_DISCOUNT))
    return price


def quote(component: str, wallet: str | None = None,
          method: str = "USDC") -> dict:
    """
    A full quote for one component: what it lists at, what this caller pays,
    and why the two differ.
    """
    base = list_price(component)
    price = effective_usd(base, wallet, method)

    reasons = []
    if legacy_holders.is_legacy_holder(wallet):
        reasons.append(f"legacy holder, {int(legacy_holders.LEGACY_DISCOUNT * 100)}%")
    if str(method).upper() == "AETH":
        reasons.append(f"AETH fee tier, {int(AETH_FEE_DISCOUNT * 100)}%")

    return {
        "component": component,
        "method": str(method).upper(),
        "list_price": base,
        "price": price,
        "discounts": reasons,
    }
