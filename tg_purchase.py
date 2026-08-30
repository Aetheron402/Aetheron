"""
One purchase, from the price being quoted to the file arriving.

Held in the database rather than in memory, and that is the whole point. A
purchase spans two messages with a person going off to their wallet in between,
so a restart in that gap would otherwise lose somebody who has already paid.
They would come back with a signature and the bot would have no idea what it was
for, which is the worst possible moment to have forgotten.

The states are deliberately few:

  awaiting_payment  quoted, waiting for a signature
  submitted         signature accepted, the job is running
  delivered         the file went out
  failed            it did not, and the reason is recorded

The rule that matters is that a chat has at most one purchase awaiting payment.
Without it, somebody who ran two commands and then pasted one signature would
have it applied to whichever we happened to find, which is a coin toss over
their money.
"""

import json
import os
import time
import uuid

import ledger_utils

# How long a quote is worth answering. The AETH conversion is held server side
# for ten minutes, so beyond that the amount quoted here may no longer be the
# amount that settles, and asking again is kinder than letting somebody pay a
# stale number.
QUOTE_TTL_SECONDS = int(os.getenv("TG_QUOTE_TTL", "600"))

# A job that has not finished in this long has gone wrong somewhere we cannot
# see. The record is closed so it stops being polled for ever, and the person
# is told, rather than left watching nothing happen.
JOB_TIMEOUT_SECONDS = int(os.getenv("TG_JOB_TIMEOUT", "900"))

AWAITING = "awaiting_payment"
SUBMITTED = "submitted"
DELIVERED = "delivered"
FAILED = "failed"

_initialised = False


def init():
    global _initialised
    if _initialised:
        return

    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tg_purchases (
                purchase_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                wallet TEXT NOT NULL,
                component TEXT NOT NULL,
                payload TEXT,
                price REAL,
                currency TEXT,
                pay_wallet TEXT,
                state TEXT NOT NULL,
                tx_signature TEXT,
                task_id TEXT,
                asset_id TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_tg_purchases_chat "
            "ON tg_purchases (chat_id, state);",
            "CREATE INDEX IF NOT EXISTS idx_tg_purchases_state "
            "ON tg_purchases (state);",
        ):
            cur.execute(statement)

    _initialised = True


def _row_to_dict(row):
    if not row:
        return None
    keys = ("purchase_id", "chat_id", "wallet", "component", "payload", "price",
            "currency", "pay_wallet", "state", "tx_signature", "task_id",
            "asset_id", "error", "created_at", "updated_at")
    record = dict(zip(keys, row))
    try:
        record["payload"] = json.loads(record["payload"]) if record["payload"] else {}
    except (TypeError, ValueError):
        record["payload"] = {}
    return record


_SELECT = (
    "SELECT purchase_id, chat_id, wallet, component, payload, price, currency, "
    "pay_wallet, state, tx_signature, task_id, asset_id, error, created_at, "
    "updated_at FROM tg_purchases "
)


def open_purchase(chat_id, wallet, component, payload, price, currency,
                  pay_wallet) -> dict:
    """
    Record a quote this chat has been given, replacing any earlier one.

    Replacing rather than adding is what keeps a pasted signature unambiguous.
    Two live quotes and one signature is a guess about which thing somebody
    paid for, and a wrong guess spends their money on the wrong component.
    """
    init()
    now = time.time()
    purchase_id = "TGP-" + uuid.uuid4().hex[:12].upper()

    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q(
                "DELETE FROM tg_purchases WHERE chat_id = %s AND state = %s;"),
            (str(chat_id), AWAITING),
        )
        cur.execute(
            ledger_utils._q(
                "INSERT INTO tg_purchases (purchase_id, chat_id, wallet, "
                "component, payload, price, currency, pay_wallet, state, "
                "created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);"
            ),
            (purchase_id, str(chat_id), wallet, component,
             json.dumps(payload or {}), float(price), currency, pay_wallet,
             AWAITING, now, now),
        )

    return get(purchase_id)


def get(purchase_id) -> dict | None:
    init()
    with ledger_utils._cursor() as cur:
        cur.execute(ledger_utils._q(_SELECT + "WHERE purchase_id = %s;"),
                    (purchase_id,))
        return _row_to_dict(cur.fetchone())


def awaiting_for(chat_id) -> dict | None:
    """
    The quote this chat still owes money on, if it has not gone stale.

    An expired quote is reported as absent rather than returned, because
    answering it would let somebody pay an amount that no longer matches what
    the server will ask for at settlement.
    """
    init()
    with ledger_utils._cursor() as cur:
        cur.execute(
            ledger_utils._q(
                _SELECT + "WHERE chat_id = %s AND state = %s "
                "ORDER BY created_at DESC LIMIT 1;"),
            (str(chat_id), AWAITING),
        )
        record = _row_to_dict(cur.fetchone())

    if not record:
        return None
    if time.time() - record["created_at"] > QUOTE_TTL_SECONDS:
        return None
    return record


def mark_submitted(purchase_id, tx_signature, task_id, asset_id=None) -> bool:
    """
    Move a purchase on once the payment has been accepted.

    Conditional on it still being awaiting payment, so two messages arriving at
    once cannot both submit the same purchase and start two jobs against one
    payment. The loser is told nothing new is happening, which is correct.
    """
    init()
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q(
                "UPDATE tg_purchases SET state = %s, tx_signature = %s, "
                "task_id = %s, asset_id = %s, updated_at = %s "
                "WHERE purchase_id = %s AND state = %s;"
            ),
            (SUBMITTED, tx_signature, task_id, asset_id, time.time(),
             purchase_id, AWAITING),
        )
        return bool(cur.rowcount)


def mark_delivered(purchase_id) -> bool:
    init()
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q(
                "UPDATE tg_purchases SET state = %s, updated_at = %s "
                "WHERE purchase_id = %s AND state = %s;"),
            (DELIVERED, time.time(), purchase_id, SUBMITTED),
        )
        return bool(cur.rowcount)


def mark_failed(purchase_id, reason) -> bool:
    init()
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(
            ledger_utils._q(
                "UPDATE tg_purchases SET state = %s, error = %s, updated_at = %s "
                "WHERE purchase_id = %s AND state <> %s;"),
            (FAILED, str(reason)[:400], time.time(), purchase_id, DELIVERED),
        )
        return bool(cur.rowcount)


def running() -> list:
    """
    Every purchase with a job still in flight.

    This is what makes delivery survive a restart. The bot comes back, asks
    what was in progress, and carries on polling, instead of leaving somebody
    who paid waiting on a message that is never coming.
    """
    init()
    with ledger_utils._cursor() as cur:
        cur.execute(
            ledger_utils._q(_SELECT + "WHERE state = %s ORDER BY created_at;"),
            (SUBMITTED,),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def history(chat_id, limit=10) -> list:
    init()
    with ledger_utils._cursor() as cur:
        cur.execute(
            ledger_utils._q(
                _SELECT + "WHERE chat_id = %s ORDER BY created_at DESC LIMIT %s;"),
            (str(chat_id), limit),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def is_stale(record) -> bool:
    """Whether a running job has been running long enough to give up on."""
    return (record["state"] == SUBMITTED
            and time.time() - record["updated_at"] > JOB_TIMEOUT_SECONDS)


def signature_already_used(signature) -> str | None:
    """
    Whether this signature has been offered before, and for what.

    The server rejects a reused signature on its own, since it is the primary
    key of the consumed table. This is here so the bot can say something true
    and specific rather than passing back a bare 402, which reads as though the
    payment was never seen at all.
    """
    init()
    signature = (signature or "").strip()
    if not signature:
        return None
    with ledger_utils._cursor() as cur:
        cur.execute(
            ledger_utils._q(
                "SELECT purchase_id FROM tg_purchases WHERE tx_signature = %s "
                "LIMIT 1;"),
            (signature,),
        )
        row = cur.fetchone()
    return row[0] if row else None
