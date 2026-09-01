"""
Routing what somebody types to the thing that answers it.

The guards are the point of these tests. Each one exists because forgetting it
in a single command is how it gets forgotten, so they live in the router and a
command opts in by declaring what it is.

The duplicate guard is the one that costs money. Telegram redelivers an update
until its id is acknowledged, so a crash or a slow reply hands the same message
back. Harmless for /help, and a second purchase attempt for anything else.
"""

import os
import tempfile

import pytest

import tg_commands
from tg_transport import FakeTransport, TransportError


@pytest.fixture
def store(monkeypatch):
    """A fresh ledger, because linking is checked through the real module."""
    path = os.path.join(tempfile.mkdtemp(), "ledger.db")
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", path)
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)
    import tg_link
    tg_link._initialised = False
    tg_link.init()
    return tg_link


@pytest.fixture
def router():
    return tg_commands.Router()


@pytest.fixture
def fake():
    return FakeTransport()


def link_wallet(store, chat_id, wallet="Wa11etAddressForTesting1111111111111111111"):
    """Put a wallet in place without going through the signature flow."""
    import ledger_utils, time
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(ledger_utils._q(
            "INSERT INTO tg_wallets (chat_id, wallet, linked_at) VALUES (%s,%s,%s);"),
            (str(chat_id), wallet, time.time()))
    return wallet


# ── dispatch ────────────────────────────────────────────────────────────────

def test_a_registered_command_runs(router, fake):
    seen = []

    @router.command("ping", help="say pong")
    def _ping(ctx):
        seen.append(ctx.args)
        ctx.say("pong")

    router.dispatch(fake.receive(1, "/ping with args"), fake)

    assert seen == ["with args"]
    assert fake.last_text() == "pong"


def test_an_alias_reaches_the_same_command(router, fake):
    @router.command("help", help="h", aliases=("start",))
    def _help(ctx):
        ctx.say("helped")

    router.dispatch(fake.receive(1, "/start"), fake)
    assert fake.last_text() == "helped"


def test_an_unknown_command_suggests_a_near_miss(router, fake):
    @router.command("components", help="list them")
    def _c(ctx):
        ctx.say("here")

    router.dispatch(fake.receive(1, "/componets"), fake)

    assert fake.said("did you mean /components")


def test_an_unknown_command_in_a_group_is_ignored(router, fake):
    """
    An unknown slash command in a group is usually meant for another bot.
    Answering every one is how a bot gets removed from a chat.
    """
    router.dispatch(fake.receive(-100, "/somethingelse", is_group=True), fake)
    assert fake.sent == []


def test_plain_text_reaches_the_fallback(router, fake):
    caught = []

    @router.fallback
    def _fallback(ctx):
        caught.append(ctx.args)

    router.dispatch(fake.receive(1, "D3qncuGsa2iMKcaxnqZxUMeVqPztzyAr819nXfjypump"), fake)
    assert caught == ["D3qncuGsa2iMKcaxnqZxUMeVqPztzyAr819nXfjypump"]


def test_plain_text_with_no_fallback_does_nothing(router, fake):
    assert router.dispatch(fake.receive(1, "gm"), fake) is False
    assert fake.sent == []


# ── the duplicate guard, which is the one that costs money ──────────────────

def test_the_same_update_never_runs_twice(router, fake):
    runs = []

    @router.command("buy", help="buy something")
    def _buy(ctx):
        runs.append(1)

    update = fake.receive(1, "/buy agent")
    assert router.dispatch(update, fake) is True
    assert router.dispatch(update, fake) is False

    assert len(runs) == 1, "a redelivered update was handled twice"


def test_a_repeat_is_silent_rather_than_answered(router, fake):
    """
    Telling somebody their message was a duplicate is confusing when they only
    sent it once and we are the reason it arrived twice.
    """
    @router.command("buy", help="b")
    def _buy(ctx):
        ctx.say("working on it")

    update = fake.receive(1, "/buy agent")
    router.dispatch(update, fake)
    fake.clear()
    router.dispatch(update, fake)

    assert fake.sent == []


def test_two_different_messages_both_run(router, fake):
    runs = []

    @router.command("buy", help="b")
    def _buy(ctx):
        runs.append(1)

    router.dispatch(fake.receive(1, "/buy agent"), fake)
    router.dispatch(fake.receive(1, "/buy agent"), fake)
    assert len(runs) == 2, "two genuine messages were treated as one"


def test_the_seen_list_does_not_grow_without_bound(router, fake):
    @router.command("x", help="x")
    def _x(ctx):
        pass

    for _ in range(tg_commands.SEEN_UPDATES_KEPT + 200):
        router.dispatch(fake.receive(1, "/x"), fake)

    assert len(router._seen) <= tg_commands.SEEN_UPDATES_KEPT
    assert len(router._seen_set) == len(router._seen)


# ── keeping paid flows out of public chats ──────────────────────────────────

def test_a_private_command_refuses_to_run_in_a_group(router, fake):
    ran = []

    @router.command("buy", help="b", private_only=True)
    def _buy(ctx):
        ran.append(1)

    router.dispatch(fake.receive(-100, "/buy agent", is_group=True), fake)

    assert ran == []
    assert fake.said("direct message")


def test_the_same_command_works_in_a_direct_message(router, fake):
    @router.command("buy", help="b", private_only=True)
    def _buy(ctx):
        ctx.say("started")

    router.dispatch(fake.receive(55, "/buy agent"), fake)
    assert fake.last_text() == "started"


def test_group_help_hides_what_cannot_be_used_there(router, fake):
    @router.command("preview", help="watch an agent run")
    def _p(ctx):
        pass

    @router.command("buy", help="buy a component", private_only=True)
    def _b(ctx):
        pass

    text = router.help_text(is_group=True)
    assert "/preview" in text
    assert "/buy" not in text
    assert "direct message only" in text


# ── the wallet requirement ──────────────────────────────────────────────────

def test_a_command_needing_a_wallet_asks_for_one(router, fake, store):
    ran = []

    @router.command("assets", help="your files", requires_wallet=True)
    def _assets(ctx):
        ran.append(1)

    router.dispatch(fake.receive(70, "/assets"), fake)

    assert ran == []
    assert fake.said("/link")


def test_the_wallet_is_handed_to_the_command(router, fake, store):
    """
    Resolved by the router so a command cannot forget to check and end up
    quoting an unlinked chat a discounted price.
    """
    wallet = link_wallet(store, 71)
    seen = []

    @router.command("assets", help="a", requires_wallet=True)
    def _assets(ctx):
        seen.append(ctx.wallet)

    router.dispatch(fake.receive(71, "/assets"), fake)
    assert seen == [wallet]


def test_a_command_not_needing_a_wallet_gets_none(router, fake, store):
    link_wallet(store, 72)
    seen = []

    @router.command("help", help="h")
    def _help(ctx):
        seen.append(ctx.wallet)

    router.dispatch(fake.receive(72, "/help"), fake)
    assert seen == [None], "a command was handed a wallet it never asked for"


# ── the rate limit ──────────────────────────────────────────────────────────

def test_a_chat_is_slowed_down_after_too_many_commands(fake):
    router = tg_commands.Router(rate_limit=3, window=60)
    runs = []

    @router.command("x", help="x")
    def _x(ctx):
        runs.append(1)

    for _ in range(5):
        router.dispatch(fake.receive(80, "/x"), fake)

    assert len(runs) == 3
    assert fake.said("give it a minute")


def test_the_limit_is_per_chat(fake):
    router = tg_commands.Router(rate_limit=2, window=60)
    runs = []

    @router.command("x", help="x")
    def _x(ctx):
        runs.append(ctx.chat_id)

    for _ in range(3):
        router.dispatch(fake.receive(90, "/x"), fake)
    for _ in range(2):
        router.dispatch(fake.receive(91, "/x"), fake)

    assert runs.count(90) == 2
    assert runs.count(91) == 2, "one chat's limit blocked another"


def test_the_window_moves(fake, monkeypatch):
    router = tg_commands.Router(rate_limit=2, window=60)
    runs = []

    @router.command("x", help="x")
    def _x(ctx):
        runs.append(1)

    for _ in range(3):
        router.dispatch(fake.receive(95, "/x"), fake)
    assert len(runs) == 2

    later = tg_commands.time.time() + 61
    monkeypatch.setattr(tg_commands.time, "time", lambda: later)

    router.dispatch(fake.receive(95, "/x"), fake)
    assert len(runs) == 3


# ── containing a handler that breaks ────────────────────────────────────────

def test_a_handler_that_raises_does_not_take_the_bot_down(router, fake):
    @router.command("boom", help="b")
    def _boom(ctx):
        raise ValueError("internal detail nobody should see")

    @router.command("fine", help="f")
    def _fine(ctx):
        ctx.say("still here")

    assert router.dispatch(fake.receive(1, "/boom"), fake) is False
    router.dispatch(fake.receive(2, "/fine"), fake)

    assert fake.said("still here"), "one broken command stopped the next"


def test_a_failure_says_nothing_was_charged_and_leaks_nothing(router, fake):
    @router.command("boom", help="b")
    def _boom(ctx):
        raise ValueError("secret internal detail")

    router.dispatch(fake.receive(1, "/boom"), fake)

    assert fake.said("nothing was charged")
    assert not fake.said("secret internal detail")
    assert not fake.said("traceback")


def test_an_undeliverable_reply_is_not_retried_forever(router, fake):
    """
    If somebody blocked the bot there is no point telling them that they did.
    """
    @router.command("x", help="x")
    def _x(ctx):
        ctx.say("hello")

    fake.fail_next = "bot was blocked by the user"
    assert router.dispatch(fake.receive(1, "/x"), fake) is False
    assert fake.sent == []


# ── help, generated from the registry ───────────────────────────────────────

def test_help_lists_what_actually_exists(router):
    @router.command("price", help="What a component costs you.", usage="<component>")
    def _p(ctx):
        pass

    text = router.help_text()
    assert "/price <component>" in text
    assert "What a component costs you." in text


def test_a_hidden_command_stays_out_of_help(router):
    @router.command("debug", help="internals", hidden=True)
    def _d(ctx):
        pass

    assert "/debug" not in router.help_text()


def test_an_alias_is_not_listed_twice(router):
    @router.command("help", help="h", aliases=("start",))
    def _h(ctx):
        pass

    assert router.help_text().count("What I can do") == 0
    assert router.help_text().count("/help") == 1


# ── the commands that exist at this step ────────────────────────────────────

def test_the_default_router_answers_help(fake, store):
    router = tg_commands.build_router()
    router.dispatch(fake.receive(1, "/help"), fake)

    assert fake.said("pay for per call")
    assert fake.said("/wallet")


def test_wallet_reports_nothing_when_unlinked(fake, store):
    router = tg_commands.build_router()
    router.dispatch(fake.receive(2, "/wallet"), fake)
    assert fake.said("no wallet linked")


def test_wallet_reports_the_linked_address(fake, store):
    wallet = link_wallet(store, 3)
    router = tg_commands.build_router()
    router.dispatch(fake.receive(3, "/wallet"), fake)
    assert fake.said(wallet)


def test_unlink_forgets_and_says_so(fake, store):
    link_wallet(store, 4)
    router = tg_commands.build_router()

    router.dispatch(fake.receive(4, "/unlink"), fake)
    assert fake.said("forgotten")

    fake.clear()
    router.dispatch(fake.receive(4, "/unlink"), fake)
    assert fake.said("there was no wallet")


def test_your_own_wallet_is_not_shown_in_a_group(fake, store):
    """
    Which wallet somebody uses is theirs to share.
    """
    link_wallet(store, -500)
    router = tg_commands.build_router()
    router.dispatch(fake.receive(-500, "/wallet", is_group=True), fake)

    assert fake.said("direct message")


def test_nothing_here_needs_a_telegram_token():
    source = open("tg_commands.py").read()
    assert "api.telegram.org" not in source
    assert "import telegram" not in source


# ── linking a wallet ────────────────────────────────────────────────────────
# The help text has always pointed people at /link. For a while it pointed at
# nothing, which left every paid command unreachable, so these keep the two
# halves of linking present and reachable.

def test_link_hands_out_a_page_rather_than_asking_for_an_address():
    """
    A wallet address is forty odd characters of base58 typed on a phone. The
    page takes it from the connected wallet, so there is nothing to type.
    """
    source = open("tg_commands.py").read()
    body = source.split('@router.command("link"')[1].split("@router.command")[0]
    assert "tg_link.start_code" in body
    assert "link_url(code)" in body
    # And it never asks for the address it used to.
    assert "ctx.args" not in body


def test_link_promises_no_private_key():
    """The one thing it must never do is ask for one."""
    source = open("tg_commands.py").read()
    body = source.split('@router.command("link"')[1].split("@router.command")[0]
    flat = " ".join(body.split())
    assert "never ask for a private key" in flat
    assert "seed" not in flat.lower()


def test_the_link_page_is_a_public_address_not_the_local_port():
    """
    The bot runs inside the service, but it is telling a person where to go in
    a browser. Handing them 127.0.0.1 would be a link nobody can open.
    """
    import tg_commands as module
    assert module.link_url("x").startswith("https://")
    assert "127.0.0.1" not in module.link_url("x")


def test_help_is_grouped_by_what_you_are_trying_to_do():
    import tg_assets
    import tg_flows
    import tg_free
    from tg_api import FakeApiClient

    router = tg_commands.build_router()
    api = FakeApiClient()
    tg_flows.register(router, api)
    tg_free.register(router, api, [])
    tg_assets.register(router, api, {})

    text = router.help_text()
    for heading in ("START HERE", "TRY IT FOR NOTHING", "BUYING",
                    "YOUR FILES", "YOUR WALLET"):
        assert heading in text

    # Nothing is left in the catch-all, which would mean a command was added
    # without saying where it belongs.
    assert "EVERYTHING ELSE" not in text

    # And the first thing under the first heading is the first thing to do.
    assert text.split("START HERE\n")[1].startswith("/components")


def test_every_command_still_appears_somewhere():
    """Grouping must never be a way for a command to fall out of the list."""
    import tg_assets
    import tg_flows
    import tg_free
    from tg_api import FakeApiClient

    router = tg_commands.build_router()
    api = FakeApiClient()
    tg_flows.register(router, api)
    tg_free.register(router, api, [])
    tg_assets.register(router, api, {})

    text = router.help_text()
    for name, entry in router.commands.items():
        if entry.hidden or name != entry.name:
            continue
        assert f"/{name}" in text, f"/{name} is missing from help"


def test_nothing_tells_people_to_type_an_address_any_more():
    """
    /link takes no arguments. Wording that says otherwise sends somebody off
    to copy a forty character address for a command that ignores it.
    """
    for name in ("tg_commands.py", "tg_free.py", "tg_flows.py", "tg_assets.py"):
        flat = " ".join(open(name).read().split())
        assert "/link followed by" not in flat, name
