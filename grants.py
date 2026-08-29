"""
Free agent claims, for giveaway winners.

A winner's wallet is granted an agent, they connect that wallet on the site, and
they download it without paying. The grant is spent on the first successful
download so it cannot be used twice.

The part that needed care is proving the wallet is theirs.

Everywhere else the wallet is proven by the payment: you cannot settle a
transaction you did not sign, so the address on a purchase is necessarily the
buyer's. A free claim has no transaction, and the site reads the wallet from a
request header, which is just a string the caller chose. Winners announce their
addresses publicly in the giveaway comments, so anyone reading the thread could
have claimed somebody else's prize by typing their address into a header.

So a claim is signed. The server issues a one time challenge, the wallet signs
it, and the signature is checked against that exact wallet. The challenge is
consumed whether or not it verifies, so a captured one is worth nothing, and it
expires quickly.
"""

import os
import secrets
import time

from solders.pubkey import Pubkey
from solders.signature import Signature

import ledger_utils

# Long enough to approve a wallet prompt, short enough that a challenge left on
# screen is not worth stealing.
CHALLENGE_TTL_SECONDS = int(os.getenv("CLAIM_CHALLENGE_TTL", "300"))


class ClaimError(Exception):
    """The claim was refused, with a reason safe to show the caller."""


def init_grants() -> None:
    """Create the grant and challenge tables. Safe to call repeatedly."""
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_grants (
                wallet TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                reason TEXT,
                granted_at REAL NOT NULL,
                claimed_at REAL,
                PRIMARY KEY (wallet, agent_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS claim_challenges (
                nonce TEXT PRIMARY KEY,
                wallet TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                issued_at REAL NOT NULL
            );
            """
        )


def grant(wallet: str, agent_id: str, reason: str = "giveaway") -> bool:
    """
    Give a wallet one free download of an agent.

    Returns False if that wallet already has a grant for it, so running the
    same list twice cannot hand out two.
    """
    init_grants()
    try:
        with ledger_utils._cursor(commit=True) as cur:
            cur.execute(
                ledger_utils._q(
                    "INSERT INTO agent_grants (wallet, agent_id, reason, granted_at) "
                    "VALUES (%s, %s, %s, %s);"
                ),
                (wallet.strip(), agent_id, reason, time.time()),
            )
        return True
    except ledger_utils.INTEGRITY_ERRORS:
        return False


def unclaimed(wallet: str | None) -> list:
    """Every agent this wallet can still download for free."""
    if not wallet:
        return []
    try:
        init_grants()
        with ledger_utils._cursor() as cur:
            cur.execute(
                ledger_utils._q(
                    "SELECT agent_id FROM agent_grants "
                    "WHERE wallet = %s AND claimed_at IS NULL;"
                ),
                (wallet.strip(),),
            )
            return [row[0] for row in cur.fetchall()]
    except Exception:
        # A lookup that fails must not offer a free download, and must not stop
        # a paid one either.
        return []


def challenge(wallet: str, agent_id: str) -> dict:
    """
    Issue a one time message for this wallet to sign.

    Bound to the agent as well as the wallet, so a signature collected for one
    prize cannot be replayed to collect another.
    """
    if not wallet:
        raise ClaimError("Connect a wallet first")
    if agent_id not in unclaimed(wallet):
        raise ClaimError("No unclaimed prize on this wallet for that agent")

    init_grants()
    nonce = secrets.token_urlsafe(24)
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q(
                "INSERT INTO claim_challenges (nonce, wallet, agent_id, issued_at) "
                "VALUES (%s, %s, %s, %s);"
            ),
            (nonce, wallet.strip(), agent_id, time.time()),
        )

    message = (
        f"Aetheron free agent claim\n"
        f"agent: {agent_id}\n"
        f"wallet: {wallet}\n"
        f"nonce: {nonce}\n"
        f"This signs nothing on chain and moves no funds."
    )
    return {"message": message, "nonce": nonce,
            "expires_in": CHALLENGE_TTL_SECONDS}


def _take_challenge(nonce: str) -> tuple | None:
    """
    Fetch and delete a challenge in one go.

    Deleted whether or not the signature turns out to be valid, so a captured
    challenge is worth one attempt rather than unlimited ones.
    """
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q(
                "SELECT wallet, agent_id, issued_at FROM claim_challenges WHERE nonce = %s;"),
            (nonce,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                ledger_utils._q("DELETE FROM claim_challenges WHERE nonce = %s;"),
                (nonce,),
            )
    return row


def verify_claim(wallet: str, agent_id: str, nonce: str, signature: str) -> None:
    """
    Confirm the wallet really signed this challenge. Raises if it did not.

    Nothing here trusts the caller's own account of who they are: the signature
    is checked against the wallet named in the stored challenge, not the one
    sent with the request.
    """
    init_grants()
    row = _take_challenge((nonce or "").strip())
    if not row:
        raise ClaimError("That claim has expired, start again")

    stored_wallet, stored_agent, issued_at = row[0], row[1], float(row[2])

    if time.time() - issued_at > CHALLENGE_TTL_SECONDS:
        raise ClaimError("That claim has expired, start again")
    if stored_wallet != (wallet or "").strip() or stored_agent != agent_id:
        raise ClaimError("That claim does not match this wallet")

    message = (
        f"Aetheron free agent claim\n"
        f"agent: {stored_agent}\n"
        f"wallet: {stored_wallet}\n"
        f"nonce: {nonce}\n"
        f"This signs nothing on chain and moves no funds."
    )

    try:
        ok = Signature.from_string(signature.strip()).verify(
            Pubkey.from_string(stored_wallet), message.encode())
    except Exception:
        raise ClaimError("That signature could not be read")

    if not ok:
        raise ClaimError("That signature is not from this wallet")

    if stored_agent not in unclaimed(stored_wallet):
        raise ClaimError("That prize has already been claimed")


def mark_claimed(wallet: str, agent_id: str) -> bool:
    """
    Spend the grant, once the download has actually been built.

    Conditional on it still being unclaimed, so two requests racing cannot both
    succeed: whichever updates first takes it.
    """
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q(
                "UPDATE agent_grants SET claimed_at = %s "
                "WHERE wallet = %s AND agent_id = %s AND claimed_at IS NULL;"
            ),
            (time.time(), wallet.strip(), agent_id),
        )
        return bool(cur.rowcount)


def purge_expired_challenges() -> int:
    init_grants()
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q("DELETE FROM claim_challenges WHERE issued_at < %s;"),
            (time.time() - CHALLENGE_TTL_SECONDS,),
        )
        return max(0, cur.rowcount or 0)
