"""
Getting back something already paid for.

A report bought in a chat arrives once, in a conversation that will scroll.
This is the difference between selling a file and selling access to a file,
and the second is what people think they are buying.
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


# ── step 7: getting a file back ─────────────────────────────────────────────

def assets_router(api, listed=None):
    router = tg_commands.build_router()
    tg_assets.register(router, api, listed if listed is not None else {})
    return router


def test_assets_lists_what_was_bought(store, api, fake):
    linked(store, 10)
    api.assets = {"entries": [
        {"component": "code-explainer", "price": 0.5, "currency": "USDC",
         "filename": "a.pdf", "status": "success"},
        {"component": "contract-intel", "price": 1.0, "currency": "USDC",
         "filename": "b.pdf", "status": "success"},
    ]}

    assets_router(api).dispatch(fake.receive(10, "/assets"), fake)

    assert fake.said("code explainer")
    assert fake.said("contract intelligence")
    assert fake.said("/get")


def test_rows_with_no_file_are_left_out(store, api, fake):
    """
    A pending or failed job is not something to offer back, and listing it
    puts a broken entry next to working ones with no way to tell which.
    """
    linked(store, 11)
    api.assets = {"entries": [
        {"component": "code-explainer", "filename": None, "status": "pending"},
        {"component": "contract-intel", "filename": "b.pdf", "status": "success"},
    ]}

    assets_router(api).dispatch(fake.receive(11, "/assets"), fake)

    assert fake.said("contract intelligence")
    assert not fake.said("code explainer")


def test_an_empty_history_points_at_the_free_tier(store, api, fake):
    linked(store, 12)
    assets_router(api).dispatch(fake.receive(12, "/assets"), fake)

    assert fake.said("nothing bought on this wallet yet")
    assert fake.said("/example")


def test_a_file_can_be_sent_again_by_its_number(store, api, fake):
    linked(store, 13)
    api.assets = {"entries": [
        {"component": "code-explainer", "filename": "a.pdf", "status": "success"}]}

    listed = {}
    router = assets_router(api, listed)
    router.dispatch(fake.receive(13, "/assets"), fake)

    fake.clear()
    router.dispatch(fake.receive(13, "/get 1"), fake)

    docs = fake.documents(13)
    assert len(docs) == 1
    assert docs[0].filename == "a.pdf"


def test_a_number_off_the_end_says_the_range(store, api, fake):
    linked(store, 14)
    api.assets = {"entries": [
        {"component": "code-explainer", "filename": "a.pdf", "status": "success"}]}

    listed = {}
    router = assets_router(api, listed)
    router.dispatch(fake.receive(14, "/assets"), fake)

    fake.clear()
    router.dispatch(fake.receive(14, "/get 9"), fake)
    assert fake.said("goes from 1 to 1")


def test_get_before_assets_says_to_list_first(store, api, fake):
    linked(store, 15)
    assets_router(api).dispatch(fake.receive(15, "/get 1"), fake)
    assert fake.said("send /assets first")


def test_a_file_that_is_no_longer_stored_says_so(store, api, fake):
    """
    Assets are purged after a retention window, so an old enough one is
    genuinely gone and saying that beats a generic failure.
    """
    linked(store, 16)
    api.assets = {"entries": [
        {"component": "code-explainer", "filename": "old.pdf", "status": "success"}]}

    def gone(url):
        raise tg_api.ApiError("404")
    api.download = gone

    listed = {}
    router = assets_router(api, listed)
    router.dispatch(fake.receive(16, "/assets"), fake)
    fake.clear()
    router.dispatch(fake.receive(16, "/get 1"), fake)

    assert fake.said("may no longer be stored")


def test_your_history_is_not_shown_in_a_group(store, api, fake):
    linked(store, -900)
    assets_router(api).dispatch(fake.receive(-900, "/assets", is_group=True), fake)
    assert fake.said("direct message")
