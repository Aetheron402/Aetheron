"""
What we know about a token, for the site builder.

The point of this component is that you paste a contract address rather than
describing your project, so this is the half that turns an address into
something worth building a page from: the real name, ticker, image, supply,
socials and market cap.

Sources are pump.fun for the token's own metadata and DexScreener for market
figures once a pair exists. Anything a source does not return comes back absent
rather than guessed, because a generated page that invents a market cap or a
Telegram link is worse than one that leaves the section out.
"""

import hashlib
import re

import requests

PUMP_API = "https://frontend-api-v3.pump.fun/coins"
DEXSCREENER = "https://api.dexscreener.com/latest/dex/tokens"
TIMEOUT = 20

# Base58, and the length range Solana mints fall in.
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class TokenLookupError(Exception):
    """The address is not usable, with a reason worth showing the buyer."""


def _get(url: str, params: dict | None = None):
    try:
        response = requests.get(url, params=params or {}, timeout=TIMEOUT,
                                headers={"User-Agent": "aetheron-site-builder"})
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _clean_url(value) -> str | None:
    """Only keep links that are actually links, so none reach the page broken."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        return None
    return value


def _ipfs(url) -> str | None:
    """
    Normalise IPFS links onto a gateway that serves CORS headers.

    Some tokens store ipfs:// URIs that a browser cannot load directly, which
    would leave the generated page with a broken hero image.
    """
    url = _clean_url(url) or (url if isinstance(url, str) else None)
    if not url:
        return None
    if url.startswith("ipfs://"):
        return "https://ipfs.io/ipfs/" + url[len("ipfs://"):]
    return _clean_url(url)


def lookup(mint: str) -> dict:
    """
    Everything known about a mint, with a note of what could not be read.

    Raises only when there is nothing to build a page from at all. A missing
    market cap or a missing Telegram is reported as absent and the page simply
    does without it.
    """
    mint = (mint or "").strip()
    if not MINT_RE.match(mint):
        raise TokenLookupError(
            "That does not look like a Solana mint address. It should be 32 to 44 "
            "base58 characters, and it is the token address rather than a wallet."
        )

    coin = _get(f"{PUMP_API}/{mint}")
    if not isinstance(coin, dict) or not coin.get("symbol"):
        raise TokenLookupError(
            "No token found at that address on pump.fun. Check the address, and "
            "note that tokens launched elsewhere are not covered yet."
        )

    missing = []

    market_cap = coin.get("usd_market_cap") or coin.get("market_cap_usd")
    liquidity = volume = price = None

    pairs = (_get(f"{DEXSCREENER}/{mint}") or {}).get("pairs") or []
    if pairs:
        best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        liquidity = (best.get("liquidity") or {}).get("usd")
        volume = (best.get("volume") or {}).get("h24")
        try:
            price = float(best.get("priceUsd"))
        except (TypeError, ValueError):
            price = None
        market_cap = best.get("fdv") or market_cap
    else:
        missing.append("market pair (no DexScreener listing yet)")

    supply = coin.get("total_supply")
    try:
        decimals = int(coin.get("base_decimals") or 6)
        supply = int(supply) / (10 ** decimals) if supply else None
    except (TypeError, ValueError):
        supply = None

    socials = {
        "twitter": _clean_url(coin.get("twitter")),
        "telegram": _clean_url(coin.get("telegram")),
        "website": _clean_url(coin.get("website")),
    }
    for name, value in socials.items():
        if not value:
            missing.append(f"{name} link (not set on the token)")

    image = _ipfs(coin.get("image_uri"))
    if not image:
        missing.append("token image")

    return {
        "mint": mint,
        "pre_launch": False,
        "name": (coin.get("name") or coin.get("symbol") or "").strip(),
        "symbol": (coin.get("symbol") or "").strip().upper(),
        "description": (coin.get("description") or "").strip() or None,
        "image": image,
        "banner": _ipfs(coin.get("banner_uri")),
        "socials": socials,
        "market_cap_usd": market_cap,
        "price_usd": price,
        "liquidity_usd": liquidity,
        "volume_24h_usd": volume,
        "supply": supply,
        "creator": coin.get("creator"),
        "graduated": bool(coin.get("complete")),
        "created_timestamp": coin.get("created_timestamp"),
        "missing": missing,
        "links": {
            "pumpfun": f"https://pump.fun/coin/{mint}",
            "dexscreener": f"https://dexscreener.com/solana/{mint}",
            "solscan": f"https://solscan.io/token/{mint}",
        },
    }


# Design directions, chosen by the mint rather than at random, so the same
# token always gets the same look and two tokens rarely get the same one. Every
# buyer receiving an identical page is the fastest way to kill this component.
DIRECTIONS = [
    {"name": "brutalist", "note": "heavy type, hard edges, high contrast, almost no curves"},
    {"name": "neon night", "note": "dark ground, saturated accent glow, tight mono type"},
    {"name": "clean editorial", "note": "generous whitespace, serif headline, restrained palette"},
    {"name": "retro terminal", "note": "monospace throughout, scanline texture, amber or green on black"},
    {"name": "soft gradient", "note": "pastel mesh gradients, rounded cards, friendly sans"},
    {"name": "cyber grid", "note": "grid lines, sharp cyan accents, technical readouts"},
    {"name": "playful sticker", "note": "bold flat colour, chunky shapes, oversized emoji free graphics"},
    {"name": "luxury minimal", "note": "black and gold, thin type, lots of empty space, slow reveals"},
]


def direction_for(mint: str, offset: int = 0) -> dict:
    """
    A stable design direction for this mint, and the next one along on a reroll.

    Hashed rather than summed. Adding character codes ignores order and lands
    in a narrow band, so different mints collided constantly: the first two real
    addresses tried both came out as the same direction, which is the failure
    this whole mechanism exists to prevent.

    `offset` walks the list rather than rehashing, so rerolling never lands back
    on the direction somebody has just rejected until every other one has been
    seen. Rehashing with a salt could return the same one twice in a row, which
    reads as the button not working.
    """
    digest = hashlib.sha256(mint.encode()).digest()
    return DIRECTIONS[(digest[0] + int(offset)) % len(DIRECTIONS)]


def exists(mint: str) -> bool:
    """
    Whether there is a token at this address at all.

    Shape cannot answer this: a wallet address is the same length and the same
    alphabet as a mint, so pasting one looks perfectly valid and only fails once
    the job runs, after the money has moved. This is the check that has to
    happen before payment is asked for.
    """
    if not MINT_RE.match((mint or "").strip()):
        return False
    coin = _get(f"{PUMP_API}/{mint.strip()}")
    return bool(isinstance(coin, dict) and coin.get("symbol"))


def from_details(name: str, symbol: str, description: str | None = None,
                 image: str | None = None, twitter: str | None = None,
                 telegram: str | None = None, website: str | None = None) -> dict:
    """
    The same shape as lookup(), for a token that does not exist yet.

    This is the ordinary case, not the exception. People need the site before
    they launch, not after, so requiring a mint address made the component
    useless to exactly the people it was built for.

    Nothing is guessed here either. There is no market cap, no supply and no
    holder count before a launch, so those come back absent and the page leaves
    them out rather than showing a zero or a placeholder that looks like data.
    """
    name = (name or "").strip()
    symbol = (symbol or "").strip().upper().lstrip("$")
    if not name or not symbol:
        raise TokenLookupError("A name and a ticker are needed to build the page.")

    socials = {
        "twitter": _clean_url(twitter),
        "telegram": _clean_url(telegram),
        "website": _clean_url(website),
    }

    missing = ["market figures (the token has not launched)"]
    missing += [f"{k} link (not supplied)" for k, v in socials.items() if not v]
    if not _ipfs(image):
        missing.append("token image")

    return {
        "mint": None,
        "pre_launch": True,
        "name": name,
        "symbol": symbol,
        "description": (description or "").strip() or None,
        "image": _ipfs(image),
        "banner": None,
        "socials": socials,
        "market_cap_usd": None, "price_usd": None,
        "liquidity_usd": None, "volume_24h_usd": None,
        "supply": None, "creator": None,
        "graduated": False, "created_timestamp": None,
        "missing": missing,
        "links": {},
    }


def direction_for_name(seed_text: str, offset: int = 0) -> dict:
    """A stable direction for a token with no mint to seed from."""
    digest = hashlib.sha256((seed_text or "").encode()).digest()
    return DIRECTIONS[(digest[0] + int(offset)) % len(DIRECTIONS)]


def direction_count() -> int:
    return len(DIRECTIONS)
