"""
What a channel posts without being asked.

The judgement being tested is when to stay quiet. A channel that posts every
purchase becomes noise nobody reads, and one that says all systems operational
every ten minutes trains people to ignore the post that matters.
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


# ── step 8: what a channel posts by itself ──────────────────────────────────

def test_a_burn_post_carries_a_link_to_check_it():
    text = tg_channel.burn_post({
        "burned": 125000, "outstanding": 300,
        "recent": [{"signature": "abc123", "amount": 5000}]})

    assert "5,000 AETH" in text
    assert "125,000 AETH" in text
    assert "solscan.io/tx/abc123" in text


def test_the_same_burn_is_not_posted_twice():
    burns = {"burned": 1, "recent": [{"signature": "same", "amount": 1}]}
    assert tg_channel.burn_post(burns) is not None
    assert tg_channel.burn_post(burns, last_signature="same") is None


def test_no_burns_yet_posts_nothing():
    """
    A burn channel that says nothing happened every day is worse than one that
    only speaks when something did.
    """
    assert tg_channel.burn_post({"burned": 0, "recent": []}) is None
    assert tg_channel.burn_post(None) is None


def test_the_purchases_feed_sums_rather_than_lists():
    """
    Ten lines saying the same thing is noise. One line saying it ten times is
    information.
    """
    entries = [{"id": i, "component": "code-explainer", "status": "success"}
               for i in range(1, 11)]
    text, newest = tg_channel.ledger_post(entries)

    assert "10x code-explainer" in text
    assert newest == 10


def test_the_feed_only_reports_what_is_new():
    entries = [{"id": 3, "component": "a", "status": "success"},
               {"id": 2, "component": "a", "status": "success"}]
    text, newest = tg_channel.ledger_post(entries, last_id=2)

    assert "1 component run" in text
    assert newest == 3

    assert tg_channel.ledger_post(entries, last_id=3)[0] is None


def test_unpaid_rows_are_not_announced_as_purchases():
    entries = [{"id": 1, "component": "a", "status": "pending"}]
    assert tg_channel.ledger_post(entries)[0] is None


def test_no_wallet_is_ever_posted_in_full():
    """
    A feed of whole addresses turns a channel into a list of who bought what,
    which nobody agreed to when they paid.
    """
    entries = [{"id": 1, "component": "a", "status": "success", "wallet": WALLET}]
    text, _ = tg_channel.ledger_post(entries)
    assert WALLET not in text

    short = tg_channel.shorten(WALLET)
    assert short.startswith(WALLET[:4]) and short.endswith(WALLET[-4:])
    assert len(short) < len(WALLET)


def test_status_speaks_on_the_way_down_and_back_up():
    down = tg_channel.status_post(
        {"ok": False, "services": {"workers": {"status": "down"}}}, was_ok=True)
    assert "workers" in down
    assert "will run" in down

    up = tg_channel.status_post({"ok": True, "services": {}}, was_ok=False)
    assert "back to normal" in up


def test_status_stays_quiet_when_nothing_changed():
    """
    A channel posting all systems operational every ten minutes trains people
    to ignore it, which means ignoring the one that matters.
    """
    assert tg_channel.status_post({"ok": True}, was_ok=True) is None
    assert tg_channel.status_post({"ok": False}, was_ok=False) is None


def test_the_poster_only_speaks_when_something_moved(fake):
    poster = tg_channel.ChannelPoster(fake, channel_id=-1001)

    burns = {"burned": 10, "recent": [{"signature": "s1", "amount": 10}]}
    assert poster.post_burns(burns) is True
    assert poster.post_burns(burns) is False

    entries = [{"id": 1, "component": "a", "status": "success"}]
    assert poster.post_ledger(entries) is True
    assert poster.post_ledger(entries) is False

    assert poster.post_status({"ok": True}) is False
    assert poster.post_status({"ok": False, "services": {}}) is True
    assert poster.post_status({"ok": False, "services": {}}) is False


def test_a_poster_with_no_channel_configured_does_nothing(fake):
    poster = tg_channel.ChannelPoster(fake, channel_id=None)
    assert poster.post_burns(
        {"burned": 1, "recent": [{"signature": "x", "amount": 1}]}) is False
    assert fake.sent == []


def test_a_channel_that_refuses_a_post_does_not_break_the_loop(fake):
    poster = tg_channel.ChannelPoster(fake, channel_id=-1001)
    fake.fail_next = "bot is not a member of the channel"

    assert poster.post_burns(
        {"burned": 1, "recent": [{"signature": "x", "amount": 1}]}) is False


def test_nothing_here_needs_a_telegram_token():
    source = open("tg_channel.py").read()
    assert "api.telegram.org" not in source
    assert "import telegram" not in source


# ── the page that explains it ───────────────────────────────────────────────

def test_the_telegram_page_is_reachable_and_in_the_header():
    from fastapi.testclient import TestClient
    import Aetheron

    client = TestClient(Aetheron.app)
    assert client.get("/telegram").status_code == 200

    # Present on every page that uses the shared header.
    shop = client.get("/shop").text
    assert '"/telegram"' in shop


def test_the_page_says_what_the_bot_will_not_do():
    """
    The thing people most want to know about a bot that takes payments is what
    it will not do, and saying it before it ships is worth more than saying it
    after somebody asks.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    # Collapsed, because the template wraps and a sentence spans two lines.
    html = " ".join(TestClient(Aetheron.app).get("/telegram").text.split())

    assert "never asks for a private key or a seed phrase" in html
    assert "signs no transaction" in html
    assert "private chat only" in html.lower()


def test_the_page_does_not_claim_the_bot_is_live():
    """
    It is built and waiting on a token. A page that reads as though it already
    works would have people trying to find it.
    """
    from fastapi.testclient import TestClient
    import Aetheron

    # Collapsed, because the template wraps and these sentences span lines.
    html = " ".join(TestClient(Aetheron.app).get("/telegram").text.split())

    assert "opens Tuesday" in html
    assert "not a list of intentions" in html
    # And it must not read as though it already works.
    assert "join the bot" not in html.lower()


def test_the_page_carries_no_emoji_and_no_em_dashes():
    import re

    from fastapi.testclient import TestClient
    import Aetheron

    body = TestClient(Aetheron.app).get("/telegram").text
    assert "—" not in body
    assert not re.findall(r"[\U0001F300-\U0001FAFF]", body)


def test_the_command_column_cannot_overrun_its_description():
    """
    At 210px the longest entry, a pasted contract address, ran out of its
    column and sat on top of the text beside it.
    """
    template = open("templates/telegram.html").read()
    css = template.split("<style>")[1].split("</style>")[0]

    assert "white-space: nowrap" not in css.split(".cmd code")[1].split("}")[0]
    assert "overflow-wrap: anywhere" in css
