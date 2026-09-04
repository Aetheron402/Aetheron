"""
The Signal Desk agent.

It is a publisher rather than a watcher, so what matters is the editorial: it
must not post the same thing twice, must not post at three in the morning, and
must not lose what it held back. Those three are the difference between a feed
people read and a bot people mute.
"""

import json
import os
import sys
import tempfile

import pytest

SRC = os.path.join("static", "agents_src", "signal-desk")


@pytest.fixture
def desk(monkeypatch):
    """The agent's own modules, with state written somewhere disposable."""
    monkeypatch.syspath_prepend(SRC)
    for name in list(sys.modules):
        if name.startswith("utils"):
            del sys.modules[name]

    import utils.cards as cards
    import utils.editorial as editorial
    import utils.inbox as inbox
    return {"cards": cards, "editorial": editorial, "inbox": inbox,
            "tmp": tempfile.mkdtemp()}


class Log:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def rules(desk, **over):
    config = {"per_subject_cooldown_minutes": 60, "max_posts_per_hour": 6,
              "state_file": os.path.join(desk["tmp"], "state.json")}
    config.update(over)
    return desk["editorial"].Editorial(config, Log())


SIGNAL = {"title": "Liquidity pulled", "mint": "CSLP8Vp7", "score": 9}


# ── the editorial ───────────────────────────────────────────────────────────

def test_the_same_subject_is_not_posted_twice_in_the_cooldown(desk):
    """Two agents reporting one token is still one story."""
    editorial = rules(desk)
    assert editorial.judge(SIGNAL).hold is False
    editorial.remember(SIGNAL)

    again = editorial.judge({"title": "Down another 12 percent",
                             "mint": "CSLP8Vp7", "score": 9})
    assert again.hold is True


def test_a_different_subject_still_goes_out(desk):
    editorial = rules(desk)
    editorial.remember(SIGNAL)
    assert editorial.judge({"title": "Other token", "mint": "OTHER"}).hold is False


def test_an_hourly_limit_is_enforced(desk):
    editorial = rules(desk, max_posts_per_hour=2)
    for index in range(2):
        signal = {"title": f"one {index}", "mint": f"m{index}"}
        assert editorial.judge(signal).hold is False
        editorial.remember(signal)

    assert editorial.judge({"title": "third", "mint": "m3"}).hold is True


def test_quiet_hours_wrap_past_midnight(desk):
    """
    The normal case is 23:00 to 07:00, which is not a range a naive comparison
    gets right.
    """
    editorial = rules(desk, quiet_hours_from="23:00", quiet_hours_to="07:00")

    class At:
        def __init__(self, when): self.when = when
        def strftime(self, _): return self.when

    import utils.editorial as module
    real = module.datetime

    class Fake:
        @staticmethod
        def now(): return At(Fake.when)

    module.datetime = Fake
    try:
        for when, quiet in (("23:30", True), ("02:00", True), ("06:59", True),
                            ("07:00", False), ("12:00", False), ("22:59", False)):
            Fake.when = when
            assert editorial.in_quiet_hours() is quiet, when
    finally:
        module.datetime = real


def test_what_is_held_back_is_kept_not_dropped(desk):
    """Being quiet must never mean losing things."""
    editorial = rules(desk, max_posts_per_hour=1, digest_at="08:00")
    first = {"title": "first", "mint": "a"}
    editorial.judge(first)
    editorial.remember(first)

    editorial.judge({"title": "second", "mint": "b"})
    assert len(editorial.held) == 1


def test_holding_does_not_grow_without_end(desk):
    """A weekend of quiet hours must not carry a thousand items into Monday."""
    editorial = rules(desk, max_posts_per_hour=0)
    for index in range(120):
        editorial.judge({"title": f"s{index}", "mint": f"m{index}"})
    assert len(editorial.held) <= 50


def test_state_survives_a_restart(desk):
    """Otherwise every restart reposts whatever it had just posted."""
    editorial = rules(desk)
    editorial.remember(SIGNAL)

    again = rules(desk)
    assert again.judge(SIGNAL).hold is True


# ── the cards ───────────────────────────────────────────────────────────────

def test_a_card_is_drawn_and_is_a_png(desk):
    card = desk["cards"].render_card(
        {"title": "Liquidity pulled", "lines": ["one", "two"],
         "facts": {"liquidity": "$4.2k"}, "mint": "CSLP8Vp7"}, {}, Log())
    assert card and card[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_card_sizes_itself_to_what_it_says(desk):
    """A fixed frame left a third of every short card empty."""
    from io import BytesIO

    from PIL import Image

    short = desk["cards"].render_card({"title": "Short"}, {}, Log())
    long = desk["cards"].render_card(
        {"title": "Longer", "lines": ["a", "b", "c", "d"],
         "facts": {"x": "1"}, "mint": "m"}, {}, Log())

    assert Image.open(BytesIO(long)).height > Image.open(BytesIO(short)).height


def test_a_card_that_cannot_be_drawn_does_not_stop_the_post(desk):
    """A missing picture is better than a missing alert."""
    card = desk["cards"].render_card(None, {}, Log())
    assert card is None


# ── the inbox ───────────────────────────────────────────────────────────────

def test_an_existing_file_is_not_republished_on_startup(desk):
    """Turning the desk on must not dump a week of history into a channel."""
    path = os.path.join(desk["tmp"], "signals.jsonl")
    with open(path, "w") as handle:
        handle.write(json.dumps({"title": "old"}) + "\n")

    box = desk["inbox"].Inbox({"mode": "file", "file": path}, Log())
    assert box.read() == []

    with open(path, "a") as handle:
        handle.write(json.dumps({"title": "new"}) + "\n")
    assert [s["title"] for s in box.read()] == ["new"]


def test_a_prepared_backlog_can_be_read_from_the_top(desk):
    path = os.path.join(desk["tmp"], "backlog.jsonl")
    with open(path, "w") as handle:
        handle.write(json.dumps({"title": "old"}) + "\n")

    box = desk["inbox"].Inbox(
        {"mode": "file", "file": path, "start_at": "beginning"}, Log())
    assert [s["title"] for s in box.read()] == ["old"]


def test_a_half_written_line_is_ignored_rather_than_shouted_about(desk):
    """A producer mid append writes one, and it completes next time round."""
    path = os.path.join(desk["tmp"], "partial.jsonl")
    box = desk["inbox"].Inbox(
        {"mode": "file", "file": path, "start_at": "beginning"}, Log())

    with open(path, "w") as handle:
        handle.write('{"title": "half')
    assert box.read() == []


# ── what the buyer's answers do to the config ───────────────────────────────

def test_the_setup_form_can_actually_reach_every_field_it_offers():
    """
    The config writer walks dictionaries. A path through a list, which is what
    channels used to be, replaced the whole list with a dictionary keyed by
    index, and the agent then published nowhere.
    """
    import json

    import agent_setup

    source = json.load(open(os.path.join(SRC, "config.json")))
    # Values that pass each field's own validation, since the point here is
    # where they land rather than whether they are accepted.
    samples = {"url": "https://example.com/hook", "wallet": "1" * 40}
    answers = {field["key"]: samples.get(field["kind"], "filled")
               for field in agent_setup.SETUP_FIELDS["signal-desk"]}
    result, applied = agent_setup.apply_config("signal-desk", source, answers)

    assert len(applied) == len(answers), "a field was offered and not applied"

    # Every path resolves to the value that was written, and nothing above it
    # got replaced on the way down.
    for field in agent_setup.SETUP_FIELDS["signal-desk"]:
        node = result
        for part in field["path"].split("."):
            assert isinstance(node, dict), f"{field['path']} broke the config"
            node = node[part]
        assert node == answers[field["key"]] or node == [answers[field["key"]]]


def test_a_channel_is_live_once_it_has_its_credentials(desk):
    """
    There was a separate enabled flag, which meant filling in a token and
    seeing nothing happen, with no way to tell why.
    """
    monkeypatch = None
    sys.path.insert(0, SRC)
    import utils.publish as publish

    live = publish.Publisher(
        {"telegram": {"token": "t", "chat_id": "c"},
         "discord": {"webhook_url": ""}}, Log())
    assert live.count() == 1

    empty = publish.Publisher(
        {"telegram": {"token": "", "chat_id": ""}}, Log())
    assert empty.count() == 0


def test_facts_never_land_on_top_of_the_body(desk):
    """
    They were clamped against the bottom of the card, which on a short one put
    them back above the body and printed the two over each other.
    """
    from io import BytesIO

    from PIL import Image

    # One body line and two facts is the shape that broke.
    card = desk["cards"].render_card(
        {"title": "Tracked wallet bought 180 SOL",
         "lines": ["First buy from this wallet in eleven days"],
         "facts": {"size": "180 SOL", "wallet": "2Tp4"}}, {}, Log())

    image = Image.open(BytesIO(card)).convert("RGB")
    # The body sits on one row and the facts on another, so between them there
    # has to be a band of card with no text on it at all.
    def ink(row):
        return sum(1 for x in range(110, 900)
                   if sum(image.getpixel((x, row))) > 260)

    rows = [ink(y) for y in range(240, image.height - 140)]
    assert any(count == 0 for count in rows), "no clear band between them"
