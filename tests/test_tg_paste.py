"""
What somebody pastes into the chat.

Two things arrive with no command in front of them and both are worth
catching: a signature, because somebody has just come back from their wallet
with it on the clipboard, and a contract address, because that is what gets
typed all day in a room full of traders.

Everything else is ignored. A bot that answers ordinary conversation is a bot
that gets muted.
"""

import os
import tempfile
import time

import pytest

import tg_api
import tg_assets
import tg_channel
import tg_commands
import tg_flows
from tg_transport import FakeTransport, TransportError


@pytest.fixture
def store(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "ledger.db")
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", path)
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)
    import tg_link, tg_purchase
    tg_link._initialised = False
    tg_purchase._initialised = False
    tg_link.init()
    tg_purchase.init()
    return tg_link


@pytest.fixture
def api():
    return tg_api.FakeApiClient()


@pytest.fixture
def fake():
    return FakeTransport()


WALLET = "Wa11etAddressForTesting1111111111111111111"
MINT = "D3qncuGsa2iMKcaxnqZxUMeVqPztzyAr819nXfjypump"
SIG = "5" + "j" * 87


def linked(store, chat_id, wallet=WALLET):
    import ledger_utils
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(ledger_utils._q(
            "INSERT INTO tg_wallets (chat_id, wallet, linked_at) VALUES (%s,%s,%s);"),
            (str(chat_id), wallet, time.time()))
    return wallet


def buying_router(api):
    router = tg_commands.build_router()
    tg_flows.register(router, api)
    return router


# ── step 6: what somebody pastes ────────────────────────────────────────────

def test_a_pasted_address_is_offered_a_report(store, api, fake):
    linked(store, 1)
    buying_router(api).dispatch(fake.receive(1, MINT), fake)

    assert api.calls[0]["slug"] == "contract-intel"
    assert api.calls[0]["payload"]["contract_address"] == MINT
    assert fake.said("amount")


def test_a_pasted_address_with_no_wallet_explains_first(store, api, fake):
    buying_router(api).dispatch(fake.receive(2, MINT), fake)

    assert api.calls == [], "it quoted before knowing whose wallet it was"
    assert fake.said("/link")
    assert fake.said("token address")


def test_a_pasted_address_in_a_group_is_ignored(store, api, fake):
    """
    A room where people paste addresses all day would get an offer every time,
    which is how a bot gets removed from a chat.
    """
    linked(store, -800)
    buying_router(api).dispatch(fake.receive(-800, MINT, is_group=True), fake)

    assert fake.sent == []
    assert api.calls == []


def test_a_pasted_signature_pays_the_open_quote(store, api, fake):
    linked(store, 3)
    router = buying_router(api)
    router.dispatch(fake.receive(3, "/buy code-explainer print(1)"), fake)

    fake.clear()
    router.dispatch(fake.receive(3, SIG), fake)

    assert fake.said("payment confirmed")


def test_a_pasted_signature_in_a_group_is_ignored(store, api, fake):
    """
    A signature is a payment, so it belongs in the same place as the purchase.
    """
    linked(store, -801)
    buying_router(api).dispatch(fake.receive(-801, SIG, is_group=True), fake)
    assert fake.sent == []


def test_a_signature_with_no_wallet_says_what_is_missing(store, api, fake):
    buying_router(api).dispatch(fake.receive(4, SIG), fake)
    assert fake.said("no wallet is linked")


def test_ordinary_conversation_is_ignored(store, api, fake):
    """
    A bot that answers everything is a bot that gets muted.
    """
    linked(store, 5)
    router = buying_router(api)
    for message in ("gm", "wen moon", "is this thing on?", "0x1234"):
        router.dispatch(fake.receive(5, message), fake)

    assert fake.sent == []


def test_a_signature_and_a_mint_are_not_confused():
    assert tg_flows.looks_like_a_signature(SIG) is True
    assert tg_flows.looks_like_a_mint(SIG) is False
    assert tg_flows.looks_like_a_mint(MINT) is True
    assert tg_flows.looks_like_a_signature(MINT) is False
