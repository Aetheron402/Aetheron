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
    "site-builder": os.getenv("PRICE_SITE_BUILDER", "2.50"),
    # The ceiling on changing a page, not the price of it. What a revision
    # actually costs is worked out by revision_quote below, which prices the
    # change rather than counting changes.
    "site-revision": os.getenv("PRICE_SITE_REVISION", "0.99"),
}


# What a change costs, as a fraction of building the page from nothing.
#
# Held as fractions rather than amounts so the three stay in proportion if the
# build price ever moves, and because the honest way to describe them is "a bit
# of what a build costs" rather than three numbers to memorise.
#
# The tiers exist because the work genuinely differs. Rewriting a headline is
# a line of the file. Restyling one element is a rule or two. Adding a section
# or changing the whole page is most of a rebuild.
WORDING_SHARE = float(os.getenv("SITE_EDIT_WORDING", "0.04"))    # 0.10 of 2.50
LOOK_SHARE = float(os.getenv("SITE_EDIT_LOOK", "0.08"))          # 0.20
PAGE_SHARE = float(os.getenv("SITE_EDIT_PAGE", "0.20"))          # 0.50

# A batch never costs more than this share of a build, however many changes are
# in it. One revision is one generation call whatever it is asked to do, so
# charging per change past a point would be charging for work nobody does. It
# also means the cheapest way to fix a page is never to rebuild it.
BATCH_CAP_SHARE = float(os.getenv("SITE_EDIT_CAP", "0.40"))      # 1.00

# Each extra change in one batch after the first, since they ride along on the
# same generation. Small enough to be worth queueing rather than paying twice.
EXTRA_SHARE = float(os.getenv("SITE_EDIT_EXTRA", "0.02"))        # 0.05

# Rewriting one section outright, which sits between a page wide edit and a
# whole build.
SECTION_SHARE = float(os.getenv("SITE_SECTION", "0.28"))         # 0.70

_LOOK_WORDS = {
    "colour", "color", "red", "blue", "green", "gold", "black", "white",
    "orange", "purple", "pink", "yellow", "grey", "gray", "background",
    "font", "size", "bigger", "smaller", "larger", "bold", "italic",
    "spacing", "padding", "margin", "align", "center", "centre", "round",
    "rounded", "border", "shadow", "gradient", "darker", "lighter", "style",
    "brighter", "dimmer", "underline", "uppercase", "lowercase",
}

_PAGE_WORDS = {
    "add", "remove", "delete", "section", "move", "reorder", "swap",
    "layout", "everywhere", "entire", "whole", "redesign", "rebuild",
    "restructure", "rearrange", "all", "throughout", "every",
}


def classify_edit(edit) -> str:
    """
    Which tier one change falls in.

    Ties break cheap. These are keyword guesses about free text, so they will
    sometimes be wrong, and being wrong in the buyer's favour costs us a few
    cents where being wrong the other way charges somebody five times over for
    renaming a heading.

    A change with nothing pointed at is page wide by definition: it has to be
    applied by reading the whole file rather than one element of it.
    """
    if not isinstance(edit, dict):
        edit = {"description": str(edit or "")}

    words = set((edit.get("description") or "").lower().replace(",", " ")
                .replace(".", " ").split())

    if not (edit.get("selector") or "").strip():
        return "page"
    if words & _PAGE_WORDS:
        return "page"
    if words & _LOOK_WORDS:
        return "look"
    return "wording"


def revision_quote(edits, base_price=None) -> dict:
    """
    What a batch of changes costs, and why.

    Priced by what the changes are rather than by how many, because one
    revision is one generation call whatever is asked of it. The dearest change
    in the batch sets the price and the rest ride along for very little, which
    is both what it costs us and what makes queueing four small fixes better
    than paying for four separate rounds.
    """
    base = float(base_price if base_price is not None else list_price("site-builder"))
    edits = [e for e in (edits or []) if e]

    if not edits:
        return {"price": 0.0, "changes": 0, "tiers": [], "capped": False,
                "base": base}

    shares = {"wording": WORDING_SHARE, "look": LOOK_SHARE, "page": PAGE_SHARE}
    tiers = [classify_edit(e) for e in edits]

    # The dearest one sets it, the others are extras.
    dearest = max(shares[t] for t in tiers)
    share = dearest + EXTRA_SHARE * (len(edits) - 1)

    capped = share > BATCH_CAP_SHARE
    share = min(share, BATCH_CAP_SHARE)

    return {
        "price": _floor_cent(base * share),
        "changes": len(edits),
        "tiers": tiers,
        "capped": capped,
        "base": base,
    }


def section_price(base_price=None) -> float:
    """
    What rewriting one section costs.

    Between a page wide edit and a build, because that is the work: more than
    changing a line, far less than writing a document. Held as a share so it
    stays in proportion if the build price moves.
    """
    base = float(base_price if base_price is not None else list_price("site-builder"))
    return _floor_cent(base * SECTION_SHARE)


def describe_tier(tier: str) -> str:
    """Plain words for what a tier is, for showing somebody before they pay."""
    return {
        "wording": "wording",
        "look": "look of one part",
        "page": "page wide",
    }.get(tier, tier)


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
