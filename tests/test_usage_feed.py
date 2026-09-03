"""
The component usage feed.

The whole point of it is that the numbers can be checked rather than believed,
so what matters is that they only count things that actually settled, that the
window cannot be used to pull the whole table, and that publishing it does not
hand out anything about a buyer that the ledger page does not.
"""

import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def ledger(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "ledger.db")
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", path)
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)

    import ledger_utils
    ledger_utils.init_ledger()
    return ledger_utils


WALLET = "8mtAJDQmNBkVM7n63Xz6eCDhfc2UjzEoqiopcAubPDQa"


def add(ledger, component, price, status="success", asset=None, when=None):
    # A signature each, since the ledger refuses to credit one twice.
    asset_id = asset or f"{component}-{price}-{status}-{when}"
    ledger.add_entry(asset_id=asset_id,
                     wallet=WALLET, tx_sig=f"sig-{asset_id}",
                     component=component, price=price, currency="USDC",
                     status=status, filename="f.pdf")
    if when is not None:
        with ledger._cursor(commit=True) as cur:
            cur.execute(ledger._q("UPDATE ledger SET timestamp = %s "
                                  "WHERE asset_id = %s;"),
                        (when, asset_id))


def test_only_settled_runs_are_counted(ledger):
    """
    A pending row is somebody part way through. Counting it would mean the
    number goes down when a build fails, which is the one thing a public
    figure must never do.
    """
    add(ledger, "site-builder", 2.5)
    add(ledger, "site-builder", 2.5, status="pending", asset="p1")

    summary = ledger.usage_summary(0)
    assert summary["overall"]["runs"] == 1
    assert summary["overall"]["volume"] == 2.5


def test_the_window_actually_excludes_older_rows(ledger):
    now = time.time()
    add(ledger, "site-builder", 2.5, asset="old", when=now - 86400 * 10)
    add(ledger, "site-builder", 2.5, asset="new", when=now - 60)

    assert ledger.usage_summary(now - 3600)["overall"]["runs"] == 1
    assert ledger.usage_summary(0)["overall"]["runs"] == 2


def test_components_are_broken_down_and_ordered_by_use(ledger):
    add(ledger, "prompt-optimizer", 0.25, asset="a")
    add(ledger, "site-builder", 2.5, asset="b")
    add(ledger, "site-builder", 2.5, asset="c")

    components = ledger.usage_summary(0)["components"]
    assert components[0]["component"] == "site-builder"
    assert components[0]["runs"] == 2
    assert components[0]["volume"] == 5.0


def test_a_free_giveaway_run_counts_as_a_run_but_not_as_money(ledger):
    """
    A prize is real usage and no revenue. Counting it as revenue would report
    money that never arrived.
    """
    ledger.add_entry(asset_id="prize", wallet=WALLET, tx_sig=None,
                     component="site-builder", price=0.0, currency="GIVEAWAY",
                     status="success", filename="f.html")

    summary = ledger.usage_summary(0)
    assert summary["overall"]["runs"] == 1
    assert summary["overall"]["volume"] == 0.0


# ── what the endpoint gives out ─────────────────────────────────────────────

def test_the_endpoint_never_publishes_a_whole_wallet(ledger):
    add(ledger, "site-builder", 2.5)

    import Aetheron
    body = TestClient(Aetheron.app).get("/api/usage").json()
    for row in body["recent"]:
        assert WALLET not in str(row)
        if row.get("wallet"):
            assert "…" in row["wallet"]


def test_the_endpoint_does_not_hand_out_filenames(ledger):
    """
    A filename is a download link for something somebody paid for. It has no
    business in a public feed.
    """
    add(ledger, "site-builder", 2.5)

    import Aetheron
    body = TestClient(Aetheron.app).get("/api/usage").json()
    for row in body["recent"]:
        assert "filename" not in row


def test_the_window_is_bounded(ledger):
    """It is a query parameter, so somebody will ask for a decade of it."""
    import Aetheron
    client = TestClient(Aetheron.app)
    assert client.get("/api/usage?hours=999999").json()["hours"] == 24 * 90
    assert client.get("/api/usage?hours=0").json()["hours"] == 1
    assert client.get("/api/usage?hours=-5").json()["hours"] == 1


def test_the_page_links_every_settlement_to_its_transaction():
    """
    The claim is that this can be checked. A figure with nothing behind it is
    a figure people have to take on trust, which is the thing being avoided.
    """
    page = open("templates/usage.html").read()
    assert "solscan.io/tx/" in page
    assert "tx_signature" in page


def test_the_page_is_reachable_without_crowding_the_header():
    """
    The desktop nav was already full, so this is reached from the hamburger,
    the footer, and the home page, where its own numbers do the asking.
    """
    base = open("templates/base.html").read()
    desktop_nav = base.split("mobile-nav")[0]
    assert 'href="/usage"' not in desktop_nav, "the desktop header is full"

    # The hamburger and the footer both carry it.
    assert base.count('href="/usage"') >= 2

    home = open("templates/index.html").read()
    assert 'href="/usage"' in home
    assert "settled on chain" in home


def test_the_home_page_says_nothing_rather_than_zero():
    """
    A hero announcing no runs and no money is worse than a hero that does not
    mention it.
    """
    home = open("templates/index.html").read()
    strip = home.split('id="usage-strip"')[1]
    assert "if (!runs) return;" in strip
    # It starts hidden and is only revealed once there is a figure to show.
    assert 'class="hidden' in strip.split(">")[0] or "hidden" in strip[:200]
    assert 'classList.remove("hidden")' in strip


def test_a_run_with_no_transaction_is_usage_and_not_revenue(ledger):
    """
    A free build has a price on its ledger row and nothing on chain behind it.
    Adding that to a figure labelled settled on chain would be publishing money
    that never arrived, on the one page whose point is being checkable.
    """
    ledger.add_entry(asset_id="free1", wallet=WALLET, tx_sig=None,
                     component="site-builder", price=2.5, currency="USDC",
                     status="success", filename="f.html")
    add(ledger, "site-builder", 2.5, asset="paid1")

    summary = ledger.usage_summary(0)
    assert summary["overall"]["runs"] == 2
    assert summary["overall"]["volume"] == 2.5


def test_the_page_does_not_promise_every_line_has_a_transaction():
    """Because free and development runs do not have one."""
    page = " ".join(open("templates/usage.html").read().split())
    assert "Each line below links to the transaction that paid for it." not in page


def test_the_transaction_link_survives_on_a_phone():
    """
    The wallet column can go on a narrow screen. The link cannot: it is the
    only thing on the page that makes the figure checkable, and hiding it on
    mobile hides it from most of the people reading.
    """
    page = open("templates/usage.html").read()
    row = page.split("node.innerHTML = ")[1].split("feedEl.prepend")[0]
    link_line = [l for l in row.split("\n") if "${link}" in l][0]
    assert "hide-narrow" not in link_line


def test_the_feed_scrolls_rather_than_growing_the_page():
    """Forty rows made the page four thousand pixels tall on a phone."""
    page = open("templates/usage.html").read()
    feed_css = page.split("#feed {")[1].split("}")[0]
    assert "max-height" in feed_css
    assert "overflow-y: auto" in feed_css


def test_prepending_does_not_move_what_somebody_is_reading():
    page = open("templates/usage.html").read()
    body = page.split("function drawFeed")[1].split("async function refresh")[0]
    assert "feedEl.scrollTop = wasAt" in body


def test_the_page_is_reachable_from_every_menu():
    """
    The hamburger keeps its own list, so adding a link to the desktop nav
    leaves it missing for everybody on a phone.
    """
    base = open("templates/base.html").read()
    # Desktop nav, mobile menu and footer are three separate lists.
    assert base.count('href="/usage"') >= 3


def test_the_roadmap_does_not_claim_a_quarter_is_done_with_work_left_in_it():
    """
    Marking a quarter complete is a public claim. It has to be true against
    the list underneath it rather than set by hand and forgotten.
    """
    import re

    page = open("templates/roadmap.html").read()
    quarters = re.split(r"<h2[^>]*>\s*(Q\d 20\d\d)", page)

    for index in range(1, len(quarters), 2):
        name, body = quarters[index], quarters[index + 1]
        block = body.split("</ul>")[0]
        items = block.count("<li>")
        done = block.count(">Done<")
        if "(complete)" in body.split("</h2>")[0]:
            assert items and done == items, (
                f"{name} says complete with {items - done} items not marked done")
