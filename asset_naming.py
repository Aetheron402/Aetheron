"""
Names for generated assets.

Generated reports are delivered from a public R2 bucket, so the filename is the
only thing standing between a customer's report and anyone else. It therefore
has to be unguessable, and it has to be unique.

Both properties were previously missing. PDFs and TXT files were named
``aetheron_asset_<unix timestamp>``, which is roughly 86,400 candidates per day
and trivial to enumerate; two jobs finishing in the same second also produced
the same name, so one customer's report silently overwrote another's. Every
other export format used a single fixed name, ``aetheron_output.txt`` and
friends, meaning all users shared one object in the bucket and downloading it
returned whatever the last job happened to write.
"""

import re
import secrets

# 16 bytes of entropy. Enumerating the namespace is not a realistic attack.
_TOKEN_BYTES = 16

_SAFE_ASSET_ID = re.compile(r"[^A-Za-z0-9_-]")


def _safe(asset_id: str) -> str:
    """Keep asset ids readable in a URL without trusting their contents."""
    cleaned = _SAFE_ASSET_ID.sub("", asset_id or "")
    return cleaned[:64] or "asset"


def asset_filename(asset_id: str, extension: str) -> str:
    """
    Build an unguessable, collision-free filename for a generated asset.

    The asset id is kept as a human-readable prefix so files remain traceable
    in the bucket and in the ledger; the random suffix is what actually makes
    the name unguessable.
    """
    ext = (extension or "").lstrip(".").lower() or "bin"
    return f"aetheron_{_safe(asset_id)}_{secrets.token_urlsafe(_TOKEN_BYTES)}.{ext}"
