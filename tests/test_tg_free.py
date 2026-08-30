"""
The free tier in a chat.

The case worth caring about is running out. Somebody in a Telegram group did
not choose to visit anything, they are being shown something, and the message
they get when the allowance is spent is the last thing they read before
deciding whether to pay. A bare error there reads as the bot being broken.

Nothing here counts anything. The server allows three per wallet, shared across
the shop, and counting again in the bot would give two numbers that drift, with
the wrong one either giving away more than intended or refusing somebody who
still had an allowance.
"""

import os
import tempfile
import time

import pytest

import tg_api
import tg_commands
import tg_free
from tg_transport import FakeTransport, MAX_TEXT, TransportError


@pytest.fixture
def store(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(), "ledger.db")
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", path)
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)
    import tg_link
    tg_link._initialised = False
    tg_link.init()
    return tg_link


@pytest.fixture
def api():
    return tg_api.FakeApiClient()


@pytest.fixture
def fake():
    return FakeTransport()


WALLET = "Wa11etAddressForTesting1111111111111111111"


def linked(store, chat_id, wallet=WALLET):
    import ledger_utils
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(ledger_utils._q(
            "INSERT INTO tg_wallets (chat_id, wallet, linked_at) VALUES (%s,%s,%s);"),
            (str(chat_id), wallet, time.time()))
    return wallet


def ctx_for(fake, chat_id, wallet=WALLET, args=""):
    update = fake.receive(chat_id, "/example x")
    return tg_commands.Context(update=update, transport=fake, args=args,
                               wallet=wallet)


def router_with(api, pending=None):
    router = tg_commands.build_router()
    tg_free.register(router, api, pending if pending is not None else [])
    return router


# ── examples ────────────────────────────────────────────────────────────────

def test_an_example_comes_back_as_a_real_report(store, api, fake):
    api.example_text = "SHORT REPORT\n\nthe body"
    assert tg_free.show_example(ctx_for(fake, 1), api, "code-explainer") is True

    assert fake.said("short report")
    assert fake.said("code explainer")


def test_it_says_how_many_are_left(store, api, fake):
    api.example_text = "short"
    tg_free.show_example(ctx_for(fake, 2), api, "code-explainer")
    assert fake.said("2 free examples left")


def test_reopening_one_already_read_is_free_and_says_so(store, api, fake):
    api.example_text = "short"
    ctx = ctx_for(fake, 3)

    tg_free.show_example(ctx, api, "code-explainer")
    fake.clear()
    tg_free.show_example(ctx, api, "code-explainer")

    assert fake.said("costs nothing")


def test_running_out_reads_as_an_offer_rather_than_an_error(store, api, fake):
    """
    The last thing somebody reads before deciding whether to pay.
    """
    ctx = ctx_for(fake, 4)
    for slug in ("code-explainer", "prompt-optimizer", "prompt-tester"):
        tg_free.show_example(ctx, api, slug)

    fake.clear()
    assert tg_free.show_example(ctx, api, "contract-intel") is False

    assert fake.said("you have opened all 3")
    assert fake.said("stay free to look at again")
    assert fake.said("/components")


def test_a_long_example_is_sent_as_a_file_with_the_opening_quoted(store, api, fake):
    """
    Six consecutive messages is not reading material.
    """
    api.example_text = "x" * (MAX_TEXT * 2)
    tg_free.show_example(ctx_for(fake, 5), api, "code-explainer")

    assert fake.said("here it is as a file")
    docs = fake.documents(5)
    assert len(docs) == 1
    assert docs[0].filename == "code-explainer-example.txt"


def test_a_file_that_will_not_send_falls_back_to_the_text(store, api, fake):
    """
    The report exists and cost nothing, so the opening beats an apology about
    a file that would not upload.
    """
    api.example_text = "y" * (MAX_TEXT * 2)

    # Only the upload fails. Failing the header instead would prove nothing,
    # since a transport that cannot send text has already lost the chat.
    def refuse(*args, **kwargs):
        raise TransportError("file upload failed")
    fake.send_document = refuse

    tg_free.show_example(ctx_for(fake, 6), api, "code-explainer")

    assert fake.documents(6) == []
    assert any(len(t) > 1000 for t in fake.texts(6)), "the report was not fallen back to"


def test_an_example_that_does_not_exist_says_so(store, api, fake):
    assert tg_free.show_example(ctx_for(fake, 7), api, "site-builder") is False
    assert fake.said("no example for that component")


def test_an_unreachable_api_does_not_look_like_a_refusal(store, api, fake):
    def boom(*a, **k):
        raise tg_api.ApiError("connection refused")
    api.example = boom

    assert tg_free.show_example(ctx_for(fake, 8), api, "code-explainer") is False
    assert fake.said("try again shortly")


def test_nothing_is_counted_twice_by_the_bot(store, api, fake):
    """
    The allowance is the server's to keep. Two counters drift, and the wrong
    one either gives away more than intended or refuses somebody who still had
    an allowance left.
    """
    source = open("tg_free.py").read()

    # The metering lives in ledger_utils. Importing it here would be the first
    # step towards a second count.
    assert "ledger_utils" not in source
    assert "claim_example" not in source and "claim_view" not in source

    # The number shown comes from the answer rather than from anything kept.
    assert 'answer.get("remaining")' in source


def test_the_count_shown_is_whatever_the_server_said(store, api, fake):
    api.example_text = "short"

    def pretend(slug, wallet):
        return {"_status": 200, "slug": slug, "report": "short",
                "already_seen": False, "remaining": 1}
    api.example = pretend

    tg_free.show_example(ctx_for(fake, 9), api, "code-explainer")
    assert fake.said("1 free example left"), "the bot invented its own count"


# ── watching an agent run ───────────────────────────────────────────────────

def test_a_preview_starts_and_says_what_is_happening(store, api, fake):
    task_id = tg_free.watch_agent(ctx_for(fake, 10), api, "wallet-watcher")

    assert task_id
    assert fake.said("running wallet-watcher on live data")
    assert fake.said("25 seconds")


def test_a_preview_reports_what_the_agent_printed(store, api, fake):
    api.preview_output = "[12:00:01] watching\n[12:00:04] transfer seen"
    task = tg_free.watch_agent(ctx_for(fake, 11), api, "wallet-watcher")
    fake.clear()

    assert tg_free.deliver_preview(api, fake, 11, task, "wallet-watcher") == "delivered"
    assert fake.said("transfer seen")


def test_a_preview_still_running_is_left_alone(store, api, fake):
    api.polls_before_done = 3
    task = tg_free.watch_agent(ctx_for(fake, 12), api, "wallet-watcher")
    fake.clear()

    assert tg_free.deliver_preview(api, fake, 12, task, "wallet-watcher") == "pending"
    assert fake.sent == []


def test_a_run_that_printed_nothing_says_so_rather_than_sending_nothing(store, api, fake):
    """
    Somebody deciding whether an agent is worth five dollars is owed the truth
    about what it did in front of them.
    """
    api.preview_output = "   "
    task = tg_free.watch_agent(ctx_for(fake, 13), api, "wallet-watcher")
    fake.clear()

    assert tg_free.deliver_preview(api, fake, 13, task, "wallet-watcher") == "empty"
    assert fake.said("printed nothing")
    assert fake.said("chain is quiet")


def test_a_crashed_run_reports_the_reason(store, api, fake):
    api.preview_failure = "The preview failed to run."
    task = tg_free.watch_agent(ctx_for(fake, 14), api, "wallet-watcher")
    fake.clear()

    assert tg_free.deliver_preview(api, fake, 14, task, "wallet-watcher") == "failed"
    assert fake.said("did not finish")
    assert fake.said("cost nothing")


def test_a_long_run_is_sent_as_a_file(store, api, fake):
    api.preview_output = "z" * (MAX_TEXT * 2)
    task = tg_free.watch_agent(ctx_for(fake, 15), api, "wallet-watcher")
    fake.clear()

    tg_free.deliver_preview(api, fake, 15, task, "wallet-watcher")
    assert len(fake.documents(15)) == 1


def test_running_out_of_runs_reads_as_an_offer(store, api, fake):
    ctx = ctx_for(fake, 16)
    for agent in ("wallet-watcher", "market-tracker", "alpha-scanner"):
        tg_free.watch_agent(ctx, api, agent)

    api.previewable.add("solana-sniper")
    fake.clear()
    assert tg_free.watch_agent(ctx, api, "solana-sniper") is None

    assert fake.said("you have watched all 3")
    assert fake.said("/components")


def test_an_agent_with_no_preview_says_so(store, api, fake):
    assert tg_free.watch_agent(ctx_for(fake, 17), api, "discord-helper") is None
    assert fake.said("invalid agent id")


# ── the polling loop ────────────────────────────────────────────────────────

def test_previews_are_delivered_by_the_loop(store, api, fake):
    pending = []
    router = router_with(api, pending)
    linked(store, 20)

    router.dispatch(fake.receive(20, "/preview wallet-watcher"), fake)
    assert len(pending) == 1

    fake.clear()
    assert tg_free.advance_previews(pending, api, fake) == {"delivered": 1}
    assert pending == [], "a delivered preview stayed in the queue"


def test_a_preview_that_never_reports_back_is_dropped(store, api, fake):
    api.polls_before_done = 10_000
    pending = [{"chat_id": 21, "task_id": "t", "agent_id": "wallet-watcher",
                "polls": tg_free.PREVIEW_POLLS - 1}]

    counts = tg_free.advance_previews(pending, api, fake)

    assert counts == {"gave_up": 1}
    assert pending == []
    assert fake.said("did not report back in time")
    assert fake.said("cost nothing")


def test_one_broken_preview_does_not_stop_the_others(store, api, fake):
    calls = {"n": 0}
    real = api.preview_result

    def flaky(task_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("unexpected")
        return real(task_id)

    api.preview_result = flaky
    pending = [
        {"chat_id": 22, "task_id": "a", "agent_id": "wallet-watcher", "polls": 0},
        {"chat_id": 23, "task_id": "b", "agent_id": "market-tracker", "polls": 0},
    ]

    counts = tg_free.advance_previews(pending, api, fake)
    assert counts.get("error") == 1
    assert counts.get("delivered") == 1


# ── the commands ────────────────────────────────────────────────────────────

def test_example_needs_a_linked_wallet(store, api, fake):
    router_with(api).dispatch(fake.receive(30, "/example code-explainer"), fake)
    assert fake.said("/link")


def test_example_with_no_argument_points_at_the_list(store, api, fake):
    linked(store, 31)
    router_with(api).dispatch(fake.receive(31, "/example"), fake)
    assert fake.said("/components")


def test_a_short_name_finds_the_example(store, api, fake):
    linked(store, 32)
    api.example_text = "short"
    router_with(api).dispatch(fake.receive(32, "/example code"), fake)
    assert api.example_calls[0][0] == "code-explainer"


def test_agents_lists_what_can_be_watched(store, api, fake):
    router_with(api).dispatch(fake.receive(33, "/agents"), fake)
    for agent in api.previewable:
        assert fake.said(agent)


def test_watching_works_in_a_group(store, api, fake):
    """
    The free tier is the reason to be in a group at all. A purchase is private
    and this deliberately is not.
    """
    linked(store, -700)
    pending = []
    router_with(api, pending).dispatch(
        fake.receive(-700, "/preview wallet-watcher", is_group=True), fake)

    assert len(pending) == 1
    assert not fake.said("direct message")


def test_watch_is_an_alias_for_preview(store, api, fake):
    linked(store, 34)
    pending = []
    router_with(api, pending).dispatch(fake.receive(34, "/watch wallet-watcher"), fake)
    assert len(pending) == 1


def test_nothing_here_needs_a_telegram_token():
    source = open("tg_free.py").read()
    assert "api.telegram.org" not in source
    assert "import telegram" not in source
