"""
The transport that talks to Telegram, and the runner that drives it.

Nothing here touches the network. What matters about this layer is that it
turns Telegram's payloads into the shape the rest of the bot expects, that it
never leaks the token, and that an instance without a token behaves exactly as
it did before the bot existed.
"""

import os

import pytest

import tg_bot
import tg_http
from tg_transport import TransportError


# ── what comes in ───────────────────────────────────────────────────────────

def test_a_plain_message_becomes_an_update():
    update = tg_http.normalise({
        "update_id": 12,
        "message": {"message_id": 3, "text": "/help",
                    "chat": {"id": 55, "type": "private"},
                    "from": {"id": 7, "username": "someone"}},
    })
    assert update.command == "help"
    assert update.chat_id == 55
    assert update.user_id == 7
    assert update.is_group is False


def test_a_group_is_marked_as_one():
    """
    Purchase flows refuse to run in groups, so getting this wrong would show
    everybody what somebody bought.
    """
    for kind in ("group", "supergroup"):
        update = tg_http.normalise({
            "update_id": 1,
            "message": {"message_id": 1, "text": "/buy x",
                        "chat": {"id": -100, "type": kind},
                        "from": {"id": 7}},
        })
        assert update.is_group is True


def test_a_button_press_carries_its_data_and_its_id():
    update = tg_http.normalise({
        "update_id": 4,
        "callback_query": {
            "id": "cb99", "data": "pay:7",
            "from": {"id": 7},
            "message": {"message_id": 2, "chat": {"id": 55, "type": "private"}},
        },
    })
    assert update.callback_data == "pay:7"
    assert update.callback_id == "cb99"
    assert update.chat_id == 55


def test_things_with_nothing_to_answer_are_dropped():
    """
    Stickers, photos and join notices arrive in any group. Passing them on
    would mean every handler has to check for text it was promised.
    """
    assert tg_http.normalise({"update_id": 1, "message": {
        "chat": {"id": 5, "type": "private"}}}) is None
    assert tg_http.normalise({"update_id": 2}) is None


def test_a_caption_counts_as_text():
    """Someone sending a file with the command in the caption still meant it."""
    update = tg_http.normalise({
        "update_id": 5,
        "message": {"message_id": 1, "caption": "/help",
                    "chat": {"id": 5, "type": "private"}, "from": {"id": 7}},
    })
    assert update.command == "help"


# ── the token ───────────────────────────────────────────────────────────────

def test_no_token_means_no_transport():
    """Rather than a client that fails on its first call."""
    old = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    try:
        with pytest.raises(TransportError):
            tg_http.HttpTransport()
    finally:
        if old:
            os.environ["TELEGRAM_BOT_TOKEN"] = old


def test_a_failure_never_carries_the_token_out():
    """
    The token sits in the URL, so a requests exception repeated verbatim would
    put it in the logs. This is the one secret the bot holds.
    """
    source = open("tg_http.py").read()
    call = source.split("def _call")[1].split("def send_document")[0]
    # The raised message is built from the method name, never from the url or
    # the underlying error.
    assert "self._url" not in call.split("raise TransportError")[1]
    assert "{error}" not in call
    assert "_url" not in " ".join(
        line for line in call.split("\n") if "TransportError" in line)


# ── the runner ──────────────────────────────────────────────────────────────

def test_without_a_token_the_bot_does_not_start():
    """An instance with no bot configured must behave as it always did."""
    old = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    try:
        assert tg_bot.start() is None
    finally:
        if old:
            os.environ["TELEGRAM_BOT_TOKEN"] = old


def test_the_offset_only_moves_past_handled_updates():
    """
    Telegram keeps returning an update until it is acknowledged. Moving the
    offset before dispatch would lose a message whenever a handler threw.
    """
    source = open("tg_bot.py").read()
    loop = source.split("def poll_forever")[1]
    dispatch_at = loop.index("router\"].dispatch")
    offset_at = loop.index("offset = max(offset")
    # The offset is set inside the same iteration, and dispatch is wrapped, so
    # a failing handler cannot stop the loop.
    assert offset_at < dispatch_at
    assert "logger.exception" in loop[dispatch_at:]


def test_one_bad_update_does_not_stop_the_bot():
    source = open("tg_bot.py").read()
    loop = source.split("def poll_forever")[1]
    assert "except Exception" in loop
    assert "backoff" in loop


def test_work_carries_on_when_half_of_it_fails():
    """A purchase that cannot be delivered must not hold up every preview."""
    source = open("tg_bot.py").read()
    body = source.split("def work_once")[1].split("def poll_forever")[0]
    assert body.count("except Exception") == 2


def test_the_bot_never_takes_the_shop_down_with_it():
    """
    It runs inside the web service, so anything it raises on startup would be
    a shop that does not boot.
    """
    source = open("Aetheron.py").read()
    block = source.split("import tg_bot")[1].split("app.mount")[0]
    assert "except Exception" in block
    assert "tg_bot.start()" in block


def test_the_bot_calls_the_service_it_runs_inside():
    """
    A fixed port is right on a laptop and wrong everywhere it is deployed, and
    the failure looks like the bot silently answering nothing.
    """
    import importlib
    import tg_api
    os.environ["PORT"] = "9123"
    os.environ.pop("AETHERON_API_BASE", None)
    importlib.reload(tg_api)
    try:
        assert tg_api.BASE_URL == "http://127.0.0.1:9123"
    finally:
        os.environ.pop("PORT", None)
        importlib.reload(tg_api)


# ── the two clients have to agree ───────────────────────────────────────────

def test_the_fake_client_promises_nothing_the_real_one_lacks():
    """
    /agents read an attribute that only the test double had, so the deployed
    bot listed no agents at all while every test passed. Anything the handlers
    can reach on the fake has to exist on the real one too, or the tests are
    checking a bot nobody runs.
    """
    import re

    from tg_api import ApiClient, FakeApiClient, HttpApiClient

    def surface(obj):
        # An instance rather than the class, because the attribute that caused
        # this is set in __init__ and a class only check never sees it.
        return {name for name in dir(obj) if not name.startswith("_")}

    # Things the fake has purely to arrange a test are declared here rather
    # than being allowed to drift into being read by real code.
    scaffolding = (surface(FakeApiClient())
                   - surface(ApiClient)
                   - surface(HttpApiClient))

    source = " ".join(
        open(name).read() for name in
        ("tg_free.py", "tg_flows.py", "tg_assets.py", "tg_commands.py"))

    for name in sorted(scaffolding):
        # Both ways a handler can reach it. The name on its own is not enough
        # to go on, since command names collide with attribute names.
        # Whole word, or prices() matches a fake attribute called price.
        assert not re.search(rf"\bapi\.{name}\b", source), (
            f"api.{name} exists only on the test double, so it is empty in "
            "production")
        assert f'getattr(api, "{name}"' not in source, (
            f"api.{name} is reached by name and only the test double has it")


def test_every_call_the_handlers_make_exists_on_the_real_client():
    import re

    from tg_api import HttpApiClient

    source = " ".join(
        open(name).read() for name in
        ("tg_free.py", "tg_flows.py", "tg_assets.py"))

    for name in sorted(set(re.findall(r"\bapi\.([a-z_]+)\(", source))):
        assert hasattr(HttpApiClient, name), (
            f"handlers call api.{name}() and the real client has no such thing")
