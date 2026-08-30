"""
Which wallet a Telegram chat is allowed to act as.

This is the load bearing part of the bot. Every price the bot quotes is derived
from a wallet: `pricing.effective_usd` gives a previous mint holder 50% off and
an AETH payment a further 20%, and it decides that purely from the address. So
if a chat could name any wallet it liked, anyone could type in a legacy holder's
address and buy at half price on somebody else's entitlement. It would also let
them read that wallet's purchase history through the bot.

The website never had this problem, because there the wallet had to sign the
payment transaction anyway, so naming a wallet you did not control bought you
nothing. In a chat there is no such transaction, which is why linking has to
prove ownership on its own.

So a link is only ever created by signing a one time message. The pattern is
lifted deliberately from `grants.py`, which already does this for prize claims,
rather than inventing a second scheme: one nonce, stored server side, deleted on
first use whether or not the signature was any good, checked against the wallet
recorded with the nonce rather than the one the caller sends.

Signing costs nothing and moves nothing. The message says so, because a person
being asked to sign something by a Telegram bot is right to be suspicious, and
the wording is the only thing that answers that before they click.
"""

import os
import secrets
import time

from solders.pubkey import Pubkey
from solders.signature import Signature

import ledger_utils

# Long enough to open a wallet, read the message and sign it. Short enough that
# a challenge captured from somebody's screen is not useful an hour later.
CHALLENGE_TTL_SECONDS = int(os.getenv("TG_CHALLENGE_TTL", "600"))

# Base58, and the length a Solana address falls in. Checked before anything is
# stored so an obvious typo is answered immediately rather than after signing.
_B58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")

_initialised = False


class LinkError(Exception):
    """Something a person needs told, in words they can act on."""


def init():
    """Create the tables. Cheap, and called before every use."""
    global _initialised
    if _initialised:
        return

    with ledger_utils._cursor(commit=True) as cur:
        # One row per chat. The chat is the key, not the wallet, because a
        # person may relink and because two people may legitimately link the
        # same wallet, a shared treasury for instance.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tg_wallets (
                chat_id TEXT PRIMARY KEY,
                wallet TEXT NOT NULL,
                linked_at REAL NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tg_challenges (
                nonce TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                wallet TEXT NOT NULL,
                issued_at REAL NOT NULL
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_tg_challenges_chat "
            "ON tg_challenges (chat_id);"
        )

    _initialised = True


def looks_like_a_wallet(address: str) -> bool:
    """
    Whether this could be a Solana address at all.

    Shape only. It cannot tell a wallet from a token mint, which is why the
    bot asks for a signature rather than trusting this for anything.
    """
    address = (address or "").strip()
    if not 32 <= len(address) <= 44:
        return False
    if not set(address) <= _B58:
        return False
    try:
        Pubkey.from_string(address)
    except Exception:
        return False
    return True


def message_for(wallet: str, nonce: str) -> str:
    """
    The exact text to be signed.

    Built the same way on both sides and never sent by the client, so a person
    cannot be talked into signing one thing while the server checks another.
    It names what it is for and states plainly that it moves nothing, because
    that is the question anyone sensible asks before signing.
    """
    return (
        "Aetheron Telegram link\n"
        f"wallet: {wallet}\n"
        f"nonce: {nonce}\n"
        "This proves you own this wallet. It signs nothing on chain, "
        "moves no funds, and grants no spending permission."
    )


def challenge(chat_id, wallet: str) -> dict:
    """
    Issue a one time message for this chat to sign.

    Bound to the chat as well as the wallet, so a challenge answered in one
    conversation cannot be replayed to link a different one.
    """
    wallet = (wallet or "").strip()
    if not looks_like_a_wallet(wallet):
        raise LinkError(
            "That does not look like a Solana wallet address. It should be 32 to "
            "44 characters, and it is your wallet rather than a token address."
        )

    init()
    nonce = secrets.token_urlsafe(24)

    with ledger_utils._cursor(commit=True) as cur:
        # Only one challenge outstanding per chat. Without this, asking twice
        # would leave the first one valid, and a person who abandoned a link
        # attempt would have a live challenge sitting behind them.
        cur.execute(
            ledger_utils._q("DELETE FROM tg_challenges WHERE chat_id = %s;"),
            (str(chat_id),),
        )
        cur.execute(
            ledger_utils._q(
                "INSERT INTO tg_challenges (nonce, chat_id, wallet, issued_at) "
                "VALUES (%s, %s, %s, %s);"
            ),
            (nonce, str(chat_id), wallet, time.time()),
        )

    return {
        "nonce": nonce,
        "wallet": wallet,
        "message": message_for(wallet, nonce),
        "expires_in": CHALLENGE_TTL_SECONDS,
    }


def _take_challenge(chat_id) -> tuple | None:
    """
    Fetch and delete this chat's challenge in one go.

    Deleted whether or not the signature turns out to be valid, so a captured
    challenge is worth one attempt rather than unlimited ones. That is the
    difference between a signature being proof and being a guessing game.
    """
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q(
                "SELECT nonce, wallet, issued_at FROM tg_challenges "
                "WHERE chat_id = %s;"
            ),
            (str(chat_id),),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                ledger_utils._q("DELETE FROM tg_challenges WHERE chat_id = %s;"),
                (str(chat_id),),
            )
        return row


def confirm(chat_id, signature: str) -> str:
    """
    Check the signature and link the wallet. Returns the wallet, or raises.

    Nothing here trusts the caller's account of who they are. The wallet comes
    from the stored challenge, never from the message being answered, so a
    person cannot sign for a wallet they own and then have a different one
    linked.
    """
    init()
    row = _take_challenge(chat_id)
    if not row:
        raise LinkError(
            "There is nothing to confirm. Send /link with your wallet address "
            "to start again."
        )

    nonce, wallet, issued_at = row[0], row[1], float(row[2])

    if time.time() - issued_at > CHALLENGE_TTL_SECONDS:
        raise LinkError("That link request expired. Send /link to start again.")

    try:
        ok = Signature.from_string((signature or "").strip()).verify(
            Pubkey.from_string(wallet), message_for(wallet, nonce).encode()
        )
    except Exception:
        raise LinkError(
            "That signature could not be read. Paste the whole signature your "
            "wallet produced, and nothing else."
        )

    if not ok:
        raise LinkError(
            "That signature does not match this wallet. Make sure you signed "
            "with the wallet you are linking."
        )

    now = time.time()
    with ledger_utils._cursor(commit=True) as cur:
        # Relinking replaces rather than adds, so a chat always has exactly one
        # wallet and there is never a question of which is in force.
        cur.execute(
            ledger_utils._q("DELETE FROM tg_wallets WHERE chat_id = %s;"),
            (str(chat_id),),
        )
        cur.execute(
            ledger_utils._q(
                "INSERT INTO tg_wallets (chat_id, wallet, linked_at) "
                "VALUES (%s, %s, %s);"
            ),
            (str(chat_id), wallet, now),
        )

    return wallet


def wallet_for(chat_id) -> str | None:
    """
    The wallet this chat may act as, or None.

    Returns None on a database failure rather than raising. Everything that
    charges money asks this first, and the safe answer to not knowing is to
    treat the chat as unlinked, which quotes full price and shows nobody's
    history. Failing open would do the opposite of both.
    """
    try:
        init()
        with ledger_utils._cursor() as cur:
            cur.execute(
                ledger_utils._q("SELECT wallet FROM tg_wallets WHERE chat_id = %s;"),
                (str(chat_id),),
            )
            row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def unlink(chat_id) -> bool:
    """
    Forget this chat's wallet. True if there was one.

    Worth having for its own sake: somebody who linked a wallet in a chat they
    later share, or on a phone they are selling, needs a way out that does not
    involve asking us.
    """
    init()
    existing = wallet_for(chat_id)
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q("DELETE FROM tg_wallets WHERE chat_id = %s;"),
            (str(chat_id),),
        )
        cur.execute(
            ledger_utils._q("DELETE FROM tg_challenges WHERE chat_id = %s;"),
            (str(chat_id),),
        )
    return existing is not None


def purge_expired_challenges() -> int:
    """
    Drop challenges nobody answered. Returns how many went.

    They are already useless once expired, since confirm() checks the age, so
    this is housekeeping rather than a control.
    """
    init()
    cutoff = time.time() - CHALLENGE_TTL_SECONDS
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q("DELETE FROM tg_challenges WHERE issued_at < %s;"),
            (cutoff,),
        )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
