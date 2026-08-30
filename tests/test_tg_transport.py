"""
The seam between the bot and Telegram.

The point of these is that the rest of the bot can be built and trusted before
a token exists. So the fake has to refuse what Telegram refuses: a fake that
accepts a 9000 character message or a 60MB file would teach the bot habits that
break the first time it runs for real.
"""

import pytest

import tg_transport as T


# ── parsing what arrives ────────────────────────────────────────────────────

def test_a_command_is_read_without_its_slash():
    update = T.Update(update_id=1, chat_id=5, user_id=5, text="/price agent")
    assert update.command == "price"
    assert update.args == "agent"


def test_a_command_typed_in_a_group_still_matches():
    """
    Telegram appends @thebot to commands in groups. Matching on raw text works
    in a direct message and quietly fails in exactly the place group commands
    are used.
    """
    update = T.Update(update_id=1, chat_id=5, user_id=5,
                      text="/price@AetheronBot agent", is_group=True)
    assert update.command == "price"
    assert update.args == "agent"


def test_ordinary_text_is_not_a_command():
    update = T.Update(update_id=1, chat_id=5, user_id=5,
                      text="D3qncuGsa2iMKcaxnqZxUMeVqPztzyAr819nXfjypump")
    assert update.command is None
    assert update.args == ""


def test_a_command_with_no_arguments_gives_an_empty_string():
    update = T.Update(update_id=1, chat_id=5, user_id=5, text="/help")
    assert update.command == "help"
    assert update.args == ""


def test_case_does_not_matter_on_a_command():
    update = T.Update(update_id=1, chat_id=5, user_id=5, text="/HELP")
    assert update.command == "help"


# ── the length limit, which is where a real send fails ──────────────────────

def test_a_short_message_is_left_alone():
    assert T.split_text("hello") == ["hello"]


def test_an_empty_message_produces_nothing_to_send():
    assert T.split_text("") == []


def test_a_long_message_is_split_into_sendable_pieces():
    text = "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(60))
    pieces = T.split_text(text)

    assert len(pieces) > 1
    assert all(len(p) <= T.MAX_TEXT for p in pieces)


def test_splitting_prefers_paragraph_breaks():
    """
    A report cut mid sentence reads as broken. Cut between paragraphs it reads
    as a message that carried on.
    """
    text = "\n\n".join("x" * 2000 for _ in range(3))
    pieces = T.split_text(text)

    assert len(pieces) == 2
    assert not any(p.startswith("x" * 5) and p.endswith("x" * 5) and "\n" not in p
                   for p in pieces[:1]), "a paragraph was cut when it did not need to be"


def test_a_single_line_too_long_to_split_is_cut_rather_than_dropped():
    """
    One unbroken 10,000 character line has no natural break in it. Cutting is
    ugly; losing it is worse, and silently failing to send is worst.
    """
    pieces = T.split_text("y" * 10_000)

    assert all(len(p) <= T.MAX_TEXT for p in pieces)
    assert "".join(pieces) == "y" * 10_000, "content was lost in the split"


def test_nothing_is_lost_when_a_message_is_split():
    text = "\n\n".join(f"block {i}\nsecond line {i}" for i in range(400))
    assert "".join(T.split_text(text)).replace("\n", "") == text.replace("\n", "")


# ── markdown, which is how a wallet address breaks a message ────────────────

def test_a_wallet_address_is_escaped_for_markdown():
    """
    Underscores, dots and dashes are all markdown syntax. One unescaped
    character makes Telegram reject the entire message, so the failure is not
    a stray italic, it is nothing arriving.
    """
    escaped = T.escape_markdown("D3qncu_Gsa2i-Mkc.pump")
    assert "\\_" in escaped and "\\-" in escaped and "\\." in escaped


def test_plain_text_is_the_default():
    """
    Because the common case is a message containing an address or a filename,
    and plain text cannot be rejected for syntax.
    """
    fake = T.FakeTransport()
    fake.send_text(1, "wallet D3qncu_Gsa2i.pump")
    assert fake.sent[0].markdown is False


# ── the fake refuses what Telegram refuses ──────────────────────────────────

def test_the_fake_splits_exactly_as_a_real_send_would():
    fake = T.FakeTransport()
    fake.send_text(7, "\n\n".join("z" * 1500 for _ in range(6)))

    assert len(fake.sent) > 1
    assert all(len(m.text) <= T.MAX_TEXT for m in fake.sent)


def test_an_empty_message_is_refused_rather_than_sent():
    fake = T.FakeTransport()
    with pytest.raises(T.TransportError):
        fake.send_text(7, "")


def test_a_file_over_the_upload_limit_is_refused():
    fake = T.FakeTransport()
    with pytest.raises(T.TransportError) as exc:
        fake.send_document(7, b"x" * (T.MAX_DOCUMENT_BYTES + 1), "big.zip")
    assert "limit" in str(exc.value)


def test_an_empty_file_is_refused():
    """
    A generation that produced nothing must not be delivered as though it were
    the thing somebody paid for.
    """
    fake = T.FakeTransport()
    with pytest.raises(T.TransportError):
        fake.send_document(7, b"", "empty.zip")


def test_a_caption_too_long_for_a_file_is_refused():
    """
    Captions cap far lower than messages. Attaching a whole report to a
    document silently loses most of it on a real send.
    """
    fake = T.FakeTransport()
    with pytest.raises(T.TransportError) as exc:
        fake.send_document(7, b"data", "r.pdf", caption="x" * (T.MAX_CAPTION + 1))
    assert "own message" in str(exc.value)


def test_a_delivery_failure_can_be_forced_and_is_raised_not_swallowed():
    """
    People block bots. A flow that has already taken money has to cope with
    being unable to hand over the result, so failure has to be visible.
    """
    fake = T.FakeTransport()
    fake.fail_next = "bot was blocked by the user"

    with pytest.raises(T.TransportError):
        fake.send_text(7, "here is your report")

    # Once, not for ever, so a retry can be tested too.
    fake.send_text(7, "here is your report")
    assert fake.last_text() == "here is your report"


# ── driving the bot without Telegram ────────────────────────────────────────

def test_updates_can_be_queued_and_are_drained_once():
    fake = T.FakeTransport()
    fake.receive(chat_id=42, text="/help")
    fake.receive(chat_id=42, text="/components")

    first = fake.get_updates()
    assert [u.command for u in first] == ["help", "components"]
    assert fake.get_updates() == [], "an update was delivered twice"


def test_each_update_gets_its_own_id():
    fake = T.FakeTransport()
    a = fake.receive(chat_id=1, text="/help")
    b = fake.receive(chat_id=1, text="/help")
    assert a.update_id != b.update_id


def test_a_group_message_is_marked_as_one():
    """
    A purchase flow in a public chat shows everyone what somebody bought and
    what they paid, so commands have to be able to refuse.
    """
    fake = T.FakeTransport()
    update = fake.receive(chat_id=-100, text="/buy agent", is_group=True)
    assert update.is_group is True


def test_sent_messages_can_be_read_back_per_chat():
    fake = T.FakeTransport()
    fake.send_text(1, "for one")
    fake.send_text(2, "for two")

    assert fake.texts(1) == ["for one"]
    assert fake.texts(2) == ["for two"]
    assert fake.said("for one", chat_id=1)
    assert not fake.said("for one", chat_id=2)


def test_documents_are_kept_apart_from_messages():
    fake = T.FakeTransport()
    fake.send_text(1, "your file is ready")
    fake.send_document(1, b"zipdata", "agent.zip", caption="wallet watcher")

    assert fake.texts(1) == ["your file is ready"]
    assert len(fake.documents(1)) == 1
    assert fake.documents(1)[0].filename == "agent.zip"
    assert fake.documents(1)[0].document == b"zipdata"


def test_the_interface_is_small_enough_to_implement_twice():
    """
    Every method here has to exist in the real client as well, and every extra
    one is something the tests can only pretend to know. Kept deliberately
    small, so growing it is a decision rather than a drift.
    """
    surface = {name for name in vars(T.Transport)
               if not name.startswith("_")}
    assert surface == {"send_text", "send_document", "answer_callback", "get_updates"}


def test_the_base_transport_refuses_to_be_used_directly():
    """
    So a missing real implementation fails loudly at the call rather than
    silently doing nothing in production.
    """
    base = T.Transport()
    for call in (lambda: base.send_text(1, "x"),
                 lambda: base.send_document(1, b"x", "f"),
                 lambda: base.answer_callback("id"),
                 lambda: base.get_updates()):
        with pytest.raises(NotImplementedError):
            call()


def test_no_telegram_library_is_imported_yet():
    """
    Step one is the seam only. Nothing here should need a dependency or a
    token, which is the whole reason the rest of the bot can be built first.
    """
    source = open("tg_transport.py").read()
    assert "import telegram" not in source
    assert "aiogram" not in source
    assert "api.telegram.org" not in source
