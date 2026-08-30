"""
Buying something in a chat.

The gap in the middle is what these are really about. Somebody reads a price,
goes to their wallet, comes back with a signature, and any amount of time may
have passed with the bot restarting in between. So the tests care most about
what survives that gap and what happens when the same money is offered twice.
"""

import os
import tempfile
import time

import pytest

import tg_api
import tg_commands
import tg_flows
import tg_purchase
from tg_transport import FakeTransport, TransportError


@pytest.fixture
def store(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "ledger.db")
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", path)
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)

    import tg_link
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
SIG = "5" + "j" * 87          # base58, the right length to read as a signature


def linked(store, chat_id, wallet=WALLET):
    import ledger_utils
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(ledger_utils._q(
            "INSERT INTO tg_wallets (chat_id, wallet, linked_at) VALUES (%s,%s,%s);"),
            (str(chat_id), wallet, time.time()))
    return wallet


def router_with(api):
    router = tg_commands.build_router()
    tg_flows.register(router, api)
    return router


def ctx_for(fake, chat_id, wallet=WALLET, args=""):
    update = fake.receive(chat_id, "/buy x")
    return tg_commands.Context(update=update, transport=fake, args=args,
                               wallet=wallet)


# ── the quote ───────────────────────────────────────────────────────────────

def test_a_quote_says_the_amount_and_where_to_send_it(store, api, fake):
    ctx = ctx_for(fake, 1)
    record = tg_flows.start_purchase(ctx, api, "code-explainer", "def f(): pass")

    assert record["state"] == tg_purchase.AWAITING
    assert fake.said("0.25")
    assert fake.said(api.pay_wallet)
    assert fake.said("nothing is charged until you do")


def test_a_quote_repeats_the_discounts_the_server_reported(store, api, fake):
    """
    The bot never works a price out. Two implementations of a discount
    eventually disagree, and the wrong half takes somebody's money.
    """
    real = api.call_component
    api.call_component = lambda *a, **k: {
        **real(*a, **k), "discounts": ["legacy holder, 50%"]}

    tg_flows.start_purchase(ctx_for(fake, 2), api, "code-explainer", "x")
    assert fake.said("legacy holder, 50%")


def test_the_input_goes_under_the_key_that_component_expects(store, api, fake):
    tg_flows.start_purchase(ctx_for(fake, 3), api, "contract-intel", "SoMeMint")
    assert api.calls[0]["payload"]["contract_address"] == "SoMeMint"

    tg_flows.start_purchase(ctx_for(fake, 3), api, "code-explainer", "print(1)")
    assert api.calls[1]["payload"]["text"] == "print(1)"


def test_a_refusal_is_passed_on_rather_than_replaced(store, api, fake):
    """
    A generic apology wastes the one message that could have fixed it.
    """
    api.call_component = lambda *a, **k: {
        "_status": 404, "error": "No token found at that address on pump.fun."}

    assert tg_flows.start_purchase(ctx_for(fake, 4), api, "contract-intel", "x") is None
    assert fake.said("no token found at that address")


def test_an_unreachable_api_says_nothing_was_charged(store, api, fake):
    api.fail_next_call = "connection refused"
    assert tg_flows.start_purchase(ctx_for(fake, 5), api, "code-explainer", "x") is None
    assert fake.said("nothing was charged")


# ── paying ──────────────────────────────────────────────────────────────────

def test_a_signature_puts_the_job_in_flight(store, api, fake):
    ctx = ctx_for(fake, 10)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    fake.clear()

    record = tg_flows.submit_signature(ctx, api, SIG)

    assert record["state"] == tg_purchase.SUBMITTED
    assert record["task_id"]
    assert fake.said("payment confirmed")


def test_the_signature_reaches_the_api_as_a_header(store, api, fake):
    ctx = ctx_for(fake, 11)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)

    assert api.calls[0]["tx_sig"] is None
    assert api.calls[1]["tx_sig"] == SIG


def test_a_signature_with_nothing_owed_says_so(store, api, fake):
    assert tg_flows.submit_signature(ctx_for(fake, 12), api, SIG) is None
    assert fake.said("nothing waiting to be paid for")


def test_the_same_signature_cannot_be_spent_twice(store, api, fake):
    """
    The server rejects a reused signature on its own. The bot checks too, so it
    can say what happened rather than passing back a bare 402 that reads as
    though the payment was never seen.
    """
    ctx = ctx_for(fake, 13)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)

    tg_flows.start_purchase(ctx, api, "code-explainer", "y")
    fake.clear()
    assert tg_flows.submit_signature(ctx, api, SIG) is None
    assert fake.said("already been used")


def test_an_underpayment_says_what_is_left_rather_than_failed(store, api, fake):
    """
    The money is not lost, it is held against the wallet, so calling it a
    failure would be wrong.
    """
    ctx = ctx_for(fake, 14)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")

    api.call_component = lambda *a, **k: {
        "_status": 402, "paid": 0.10, "remaining": 0.15, "currency": "USDC"}

    fake.clear()
    assert tg_flows.submit_signature(ctx, api, SIG) is None
    assert fake.said("0.15 still to go")


def test_a_rejected_payment_says_what_to_check(store, api, fake):
    ctx = ctx_for(fake, 15)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    api.reject_signature = True

    fake.clear()
    assert tg_flows.submit_signature(ctx, api, SIG) is None
    assert fake.said("from the wallet you linked")


def test_an_unreachable_api_leaves_the_quote_open(store, api, fake):
    """
    The person may already have paid. Closing the quote would strand them.
    """
    ctx = ctx_for(fake, 16)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    api.fail_next_call = "timeout"

    fake.clear()
    assert tg_flows.submit_signature(ctx, api, SIG) is None
    assert fake.said("still open")
    assert tg_purchase.awaiting_for(16) is not None


def test_two_signatures_at_once_only_start_one_job(store, api, fake):
    """
    The move to submitted is conditional on still awaiting payment, so a
    duplicate cannot start a second job against one payment.
    """
    ctx = ctx_for(fake, 17)
    record = tg_flows.start_purchase(ctx, api, "code-explainer", "x")

    assert tg_purchase.mark_submitted(record["purchase_id"], SIG, "task-a") is True
    assert tg_purchase.mark_submitted(record["purchase_id"], SIG, "task-b") is False
    assert tg_purchase.get(record["purchase_id"])["task_id"] == "task-a"


# ── the gap, which is the whole reason this is in the database ──────────────

def test_a_quote_survives_a_restart(store, api, fake):
    ctx = ctx_for(fake, 20)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")

    tg_purchase._initialised = False        # as though the process went away
    tg_purchase.init()

    assert tg_purchase.awaiting_for(20)["component"] == "code-explainer"


def test_a_running_job_survives_a_restart(store, api, fake):
    ctx = ctx_for(fake, 21)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)

    tg_purchase._initialised = False
    tg_purchase.init()

    assert [r["chat_id"] for r in tg_purchase.running()] == ["21"]


def test_a_stale_quote_is_not_answerable(store, api, fake, monkeypatch):
    """
    Beyond the hold, the amount quoted may no longer be what settles, so
    asking again is kinder than letting somebody pay a stale number.
    """
    ctx = ctx_for(fake, 22)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")

    later = time.time() + tg_purchase.QUOTE_TTL_SECONDS + 10
    monkeypatch.setattr(tg_purchase.time, "time", lambda: later)

    assert tg_purchase.awaiting_for(22) is None


def test_a_chat_has_only_one_open_quote(store, api, fake):
    """
    Two live quotes and one pasted signature is a guess about which thing
    somebody paid for.
    """
    ctx = ctx_for(fake, 23)
    tg_flows.start_purchase(ctx, api, "code-explainer", "first")
    tg_flows.start_purchase(ctx, api, "prompt-optimizer", "second")

    open_now = [r for r in tg_purchase.history(23)
                if r["state"] == tg_purchase.AWAITING]
    assert len(open_now) == 1
    assert open_now[0]["component"] == "prompt-optimizer"


# ── delivery ────────────────────────────────────────────────────────────────

def test_a_finished_job_is_delivered_as_a_file(store, api, fake):
    ctx = ctx_for(fake, 30)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)
    fake.clear()

    assert tg_flows.advance_all(api, fake) == {"delivered": 1}

    docs = fake.documents(30)
    assert len(docs) == 1
    assert docs[0].document == api.file_bytes
    assert tg_purchase.history(30)[0]["state"] == tg_purchase.DELIVERED


def test_a_job_still_running_is_left_alone(store, api, fake):
    api.polls_before_done = 3
    ctx = ctx_for(fake, 31)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)
    fake.clear()

    assert tg_flows.advance_all(api, fake) == {"pending": 1}
    assert fake.sent == []

    tg_flows.advance_all(api, fake)
    assert tg_flows.advance_all(api, fake) == {"delivered": 1}


def test_a_failed_job_says_the_reason_and_that_payment_is_recorded(store, api, fake):
    ctx = ctx_for(fake, 32)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)

    api.job_error = "The model declined this request"
    fake.clear()

    assert tg_flows.advance_all(api, fake) == {"failed": 1}
    assert fake.said("the model declined this request")
    assert fake.said("payment is recorded")


def test_a_job_is_not_delivered_twice(store, api, fake):
    ctx = ctx_for(fake, 33)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)

    tg_flows.advance_all(api, fake)
    fake.clear()
    assert tg_flows.advance_all(api, fake) == {}
    assert fake.documents(33) == []


def test_a_file_that_cannot_be_delivered_is_kept_for_another_try(store, api, fake):
    """
    It exists and it is paid for. A chat that blocked the bot may unblock, and
    dropping the record would lose the thing somebody bought.
    """
    ctx = ctx_for(fake, 34)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)

    fake.fail_next = "bot was blocked by the user"
    assert tg_flows.advance_all(api, fake) == {"undelivered": 1}
    assert len(tg_purchase.running()) == 1

    assert tg_flows.advance_all(api, fake) == {"delivered": 1}


def test_a_job_that_never_finishes_is_closed_and_explained(store, api, fake, monkeypatch):
    ctx = ctx_for(fake, 35)
    tg_flows.start_purchase(ctx, api, "code-explainer", "x")
    tg_flows.submit_signature(ctx, api, SIG)

    later = time.time() + tg_purchase.JOB_TIMEOUT_SECONDS + 10
    monkeypatch.setattr(tg_purchase.time, "time", lambda: later)
    monkeypatch.setattr(tg_flows.tg_purchase.time, "time", lambda: later)
    fake.clear()

    assert tg_flows.advance_all(api, fake) == {"timeout": 1}
    assert fake.said("stopped waiting")
    assert tg_purchase.running() == []


def test_one_broken_record_does_not_stop_the_others(store, api, fake):
    # A signature each: the same one twice is correctly refused as reused,
    # which would leave only one record running and prove nothing here.
    for chat, sig in ((40, "5" + "a" * 87), (41, "5" + "b" * 87)):
        ctx = ctx_for(fake, chat)
        tg_flows.start_purchase(ctx, api, "code-explainer", "x")
        tg_flows.submit_signature(ctx, api, sig)

    assert len(tg_purchase.running()) == 2

    calls = {"n": 0}
    real = api.job_status

    def flaky(task_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("something unexpected")
        return real(task_id)

    api.job_status = flaky
    counts = tg_flows.advance_all(api, fake)

    assert counts.get("error") == 1
    assert counts.get("delivered") == 1


# ── the commands ────────────────────────────────────────────────────────────

def test_buy_needs_a_linked_wallet(store, api, fake):
    router_with(api).dispatch(fake.receive(50, "/buy code-explainer hello"), fake)
    assert fake.said("/link")


def test_buy_refuses_in_a_group(store, api, fake):
    linked(store, -600)
    router_with(api).dispatch(
        fake.receive(-600, "/buy code-explainer hello", is_group=True), fake)
    assert fake.said("direct message")


def test_buy_with_no_input_asks_for_it(store, api, fake):
    linked(store, 51)
    router_with(api).dispatch(fake.receive(51, "/buy code-explainer"), fake)
    assert fake.said("paste the code")


def test_buy_with_an_unknown_component_says_what_exists(store, api, fake):
    linked(store, 52)
    router_with(api).dispatch(fake.receive(52, "/buy nonsense hello"), fake)
    assert fake.said("/components")


def test_a_short_name_reaches_the_right_component(store, api, fake):
    linked(store, 53)
    router_with(api).dispatch(fake.receive(53, "/buy code print(1)"), fake)
    assert api.calls[0]["slug"] == "code-explainer"


def test_components_lists_what_can_be_bought(store, api, fake):
    router_with(api).dispatch(fake.receive(54, "/components"), fake)
    for slug in tg_api.COMPONENTS:
        assert fake.said(slug)


def test_cancel_drops_an_unpaid_quote(store, api, fake):
    linked(store, 55)
    router = router_with(api)
    router.dispatch(fake.receive(55, "/buy code-explainer hello"), fake)

    fake.clear()
    router.dispatch(fake.receive(55, "/cancel"), fake)

    assert fake.said("nothing was charged")
    assert tg_purchase.awaiting_for(55) is None


def test_pending_reports_what_is_in_flight(store, api, fake):
    linked(store, 56)
    router = router_with(api)
    router.dispatch(fake.receive(56, "/buy code-explainer hello"), fake)

    fake.clear()
    router.dispatch(fake.receive(56, "/pending"), fake)
    assert fake.said("waiting on payment")


# ── noticing a pasted signature ─────────────────────────────────────────────

def test_a_signature_is_recognised_without_a_command():
    """
    Somebody has just come back from their wallet with it on the clipboard.
    Asking for ceremony there is how a purchase gets abandoned.
    """
    assert tg_flows.looks_like_a_signature(SIG) is True


@pytest.mark.parametrize("not_a_sig", [
    "", "hello", "0x" + "a" * 64,
    "D3qncuGsa2iMKcaxnqZxUMeVqPztzyAr819nXfjypump",   # a mint, too short
    "l" * 88 + "O0",                                   # not base58
])
def test_other_text_is_not_mistaken_for_a_signature(not_a_sig):
    assert tg_flows.looks_like_a_signature(not_a_sig) is False


def test_nothing_here_needs_a_telegram_token():
    for path in ("tg_flows.py", "tg_purchase.py", "tg_api.py"):
        source = open(path).read()
        assert "api.telegram.org" not in source
        assert "import telegram" not in source
