"""
Linking a Telegram chat to a wallet.

This is the part of the bot where getting it wrong costs real money. Every
price the bot quotes comes from a wallet, because pricing.effective_usd gives a
previous mint holder 50% off and an AETH payment a further 20% purely from the
address. A chat that could name any wallet could buy at half price on somebody
else's entitlement, and read their purchase history besides.

So these sign for real. Nothing here mocks the signature check, because the
signature check is the entire control.
"""

import os
import tempfile
import time

import pytest
from solders.keypair import Keypair


@pytest.fixture
def link(monkeypatch):
    """A fresh sqlite ledger per test, so no chat leaks into another."""
    path = os.path.join(tempfile.mkdtemp(), "ledger.db")
    monkeypatch.setattr("ledger_utils.SQLITE_PATH", path)
    monkeypatch.setattr("ledger_utils.USE_POSTGRES", False)

    import tg_link
    tg_link._initialised = False
    tg_link.init()
    return tg_link


def sign(module, keypair, chat_id, nonce):
    """Sign the exact message the server will rebuild and check against."""
    wallet = str(keypair.pubkey())
    message = module.message_for(wallet, nonce)
    return str(keypair.sign_message(message.encode()))


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_wallet_links_when_it_signs_the_challenge(link):
    kp = Keypair()
    wallet = str(kp.pubkey())

    issued = link.challenge(chat_id=101, wallet=wallet)
    assert issued["wallet"] == wallet
    assert wallet in issued["message"]
    assert issued["nonce"] in issued["message"]

    assert link.confirm(101, sign(link, kp, 101, issued["nonce"])) == wallet
    assert link.wallet_for(101) == wallet


def test_an_unlinked_chat_has_no_wallet(link):
    assert link.wallet_for(999) is None


def test_the_message_says_it_moves_nothing(link):
    """
    Somebody asked to sign something by a Telegram bot is right to be
    suspicious, and the wording is all that answers them before they click.
    """
    kp = Keypair()
    message = link.challenge(chat_id=1, wallet=str(kp.pubkey()))["message"]

    assert "signs nothing on chain" in message
    assert "moves no funds" in message
    assert "no spending permission" in message


# ── the attacks this exists to stop ─────────────────────────────────────────

def test_naming_someone_elses_wallet_gets_you_nothing(link):
    """
    The whole reason linking needs a signature. Typing in a legacy holder's
    address must not buy their 50% discount.
    """
    victim = str(Keypair().pubkey())
    attacker = Keypair()

    issued = link.challenge(chat_id=666, wallet=victim)

    # The attacker signs the right message with the wrong key.
    forged = str(attacker.sign_message(
        link.message_for(victim, issued["nonce"]).encode()))

    with pytest.raises(link.LinkError):
        link.confirm(666, forged)
    assert link.wallet_for(666) is None


def test_a_signature_for_one_wallet_cannot_link_another(link):
    """
    The wallet comes from the stored challenge, never from what the caller
    sends, so signing honestly for your own wallet cannot link a different one.
    """
    mine = Keypair()
    theirs = str(Keypair().pubkey())

    issued = link.challenge(chat_id=7, wallet=theirs)
    honest_but_wrong = str(mine.sign_message(
        link.message_for(str(mine.pubkey()), issued["nonce"]).encode()))

    with pytest.raises(link.LinkError):
        link.confirm(7, honest_but_wrong)
    assert link.wallet_for(7) is None


def test_a_challenge_is_worth_one_attempt(link):
    """
    Consumed whether or not the signature was any good. Otherwise a captured
    challenge is a guessing game rather than a proof.
    """
    kp = Keypair()
    issued = link.challenge(chat_id=8, wallet=str(kp.pubkey()))

    with pytest.raises(link.LinkError):
        link.confirm(8, "not a signature at all")

    # The real signature no longer helps, because the challenge is gone.
    with pytest.raises(link.LinkError) as exc:
        link.confirm(8, sign(link, kp, 8, issued["nonce"]))
    assert "nothing to confirm" in str(exc.value)


def test_a_used_signature_cannot_be_replayed(link):
    kp = Keypair()
    issued = link.challenge(chat_id=9, wallet=str(kp.pubkey()))
    signature = sign(link, kp, 9, issued["nonce"])

    assert link.confirm(9, signature) == str(kp.pubkey())

    with pytest.raises(link.LinkError):
        link.confirm(9, signature)


def test_one_chat_cannot_answer_another_chats_challenge(link):
    """
    Challenges are stored against the chat, so a nonce seen in a group cannot
    be used to link a private conversation.
    """
    kp = Keypair()
    issued = link.challenge(chat_id=100, wallet=str(kp.pubkey()))

    with pytest.raises(link.LinkError):
        link.confirm(200, sign(link, kp, 100, issued["nonce"]))
    assert link.wallet_for(200) is None


def test_an_expired_challenge_is_refused(link, monkeypatch):
    kp = Keypair()
    issued = link.challenge(chat_id=11, wallet=str(kp.pubkey()))
    signature = sign(link, kp, 11, issued["nonce"])

    later = time.time() + link.CHALLENGE_TTL_SECONDS + 5
    monkeypatch.setattr(link.time, "time", lambda: later)

    with pytest.raises(link.LinkError) as exc:
        link.confirm(11, signature)
    assert "expired" in str(exc.value)
    assert link.wallet_for(11) is None


def test_signing_a_different_message_does_not_link(link):
    """
    The text is rebuilt server side and never taken from the client, so a
    person cannot be talked into signing something harmless looking that the
    server treats as a link.
    """
    kp = Keypair()
    issued = link.challenge(chat_id=12, wallet=str(kp.pubkey()))
    wrong = str(kp.sign_message(b"gm"))

    with pytest.raises(link.LinkError):
        link.confirm(12, wrong)
    assert link.wallet_for(12) is None


def test_asking_twice_leaves_only_the_newer_challenge_live(link):
    """
    An abandoned link attempt must not leave a live challenge behind it.
    """
    kp = Keypair()
    first = link.challenge(chat_id=13, wallet=str(kp.pubkey()))
    second = link.challenge(chat_id=13, wallet=str(kp.pubkey()))

    with pytest.raises(link.LinkError):
        link.confirm(13, sign(link, kp, 13, first["nonce"]))

    link.challenge(chat_id=13, wallet=str(kp.pubkey()))
    assert second["nonce"] != first["nonce"]


# ── the shape check, which stops a typo before anyone signs ─────────────────

@pytest.mark.parametrize("bad", [
    "", "   ", "not-a-wallet", "0x1234567890abcdef1234567890abcdef12345678",
    "IlO0" * 10, "abc",
])
def test_an_address_that_cannot_be_a_wallet_is_refused_immediately(link, bad):
    assert link.looks_like_a_wallet(bad) is False
    with pytest.raises(link.LinkError):
        link.challenge(chat_id=14, wallet=bad)


def test_a_real_address_passes_the_shape_check(link):
    assert link.looks_like_a_wallet(str(Keypair().pubkey())) is True


# ── living with a link ──────────────────────────────────────────────────────

def test_relinking_replaces_rather_than_adds(link):
    """
    A chat has exactly one wallet, so there is never a question of which one
    is in force when a price is quoted.
    """
    first, second = Keypair(), Keypair()

    a = link.challenge(chat_id=15, wallet=str(first.pubkey()))
    link.confirm(15, sign(link, first, 15, a["nonce"]))

    b = link.challenge(chat_id=15, wallet=str(second.pubkey()))
    link.confirm(15, sign(link, second, 15, b["nonce"]))

    assert link.wallet_for(15) == str(second.pubkey())


def test_unlinking_forgets_the_wallet_and_any_pending_challenge(link):
    kp = Keypair()
    issued = link.challenge(chat_id=16, wallet=str(kp.pubkey()))
    link.confirm(16, sign(link, kp, 16, issued["nonce"]))

    assert link.unlink(16) is True
    assert link.wallet_for(16) is None
    assert link.unlink(16) is False


def test_two_chats_may_link_the_same_wallet(link):
    """
    A shared treasury is a real thing, and both people proved ownership, so
    there is no reason to refuse it.
    """
    kp = Keypair()
    for chat in (20, 21):
        issued = link.challenge(chat_id=chat, wallet=str(kp.pubkey()))
        link.confirm(chat, sign(link, kp, chat, issued["nonce"]))

    assert link.wallet_for(20) == link.wallet_for(21) == str(kp.pubkey())


def test_expired_challenges_can_be_cleared_out(link):
    kp = Keypair()
    link.challenge(chat_id=30, wallet=str(kp.pubkey()))
    assert link.purge_expired_challenges() == 0

    import tg_link
    original = tg_link.time.time
    try:
        tg_link.time.time = lambda: original() + link.CHALLENGE_TTL_SECONDS + 60
        assert link.purge_expired_challenges() == 1
    finally:
        tg_link.time.time = original


def test_a_broken_database_reports_no_wallet_rather_than_guessing(link, monkeypatch):
    """
    Everything that charges money asks this first. Not knowing has to mean
    unlinked, which quotes full price and shows nobody's history. Failing the
    other way would do the opposite of both.
    """
    kp = Keypair()
    issued = link.challenge(chat_id=40, wallet=str(kp.pubkey()))
    link.confirm(40, sign(link, kp, 40, issued["nonce"]))
    assert link.wallet_for(40) == str(kp.pubkey())

    def broken(*a, **k):
        raise RuntimeError("database is down")

    monkeypatch.setattr("ledger_utils._cursor", broken)
    assert link.wallet_for(40) is None


def test_nothing_here_needs_a_telegram_token(link):
    """
    Step two, like step one, has to be finishable before a token exists.
    """
    source = open("tg_link.py").read()
    assert "TELEGRAM" not in source.upper() or "TELEGRAM_TOKEN" not in source.upper()
    assert "api.telegram.org" not in source


# ── linking from a page ─────────────────────────────────────────────────────
# The chat hands out a code and the page finishes it, so nobody types a wallet
# address into Telegram. These cover the things that would let somebody link a
# wallet that is not theirs.

def test_a_code_is_bound_to_the_chat_that_asked_for_it(link):
    code = link.start_code(4242)
    assert link.pending_code(code)["chat_id"] == "4242"


def test_asking_again_replaces_the_old_code(link):
    """An abandoned attempt must not leave a live code behind it."""
    first = link.start_code(77)
    second = link.start_code(77)
    assert link.pending_code(first) is None
    assert link.pending_code(second) is not None


def test_a_code_is_spent_even_when_the_signature_is_wrong(link):
    """
    Otherwise a code survives every failed attempt and can be guessed at
    forever.
    """
    code = link.start_code(88)
    wallet = "FZtoQTD7MLHvJzxxSPcUaQkXB5yP6qKYBZ8tUV18hHo1"

    with pytest.raises(link.LinkError):
        link.complete_code(code, wallet, "not a signature")

    assert link.pending_code(code) is None


def test_an_unknown_code_links_nothing(link):
    with pytest.raises(link.LinkError):
        link.complete_code("nothing-like-this", 
                              "FZtoQTD7MLHvJzxxSPcUaQkXB5yP6qKYBZ8tUV18hHo1",
                              "sig")


def test_a_real_signature_links_the_chat_the_code_came_from(link):
    """
    The chat comes from the stored code, never from the page, so a page that
    lied about which chat it was finishing would link nothing.
    """
    keypair = Keypair()
    wallet = str(keypair.pubkey())

    code = link.start_code(31337)
    message = link.message_for(wallet, code)
    signature = str(keypair.sign_message(message.encode()))

    assert link.complete_code(code, wallet, signature) == "31337"
    assert link.wallet_for(31337) == wallet


def test_signing_the_right_message_for_the_wrong_wallet_links_nothing(link):
    mine, theirs = Keypair(), Keypair()
    code = link.start_code(555)

    # Signed correctly, but claiming to be somebody else's wallet.
    message = link.message_for(str(theirs.pubkey()), code)
    signature = str(mine.sign_message(message.encode()))

    with pytest.raises(link.LinkError):
        link.complete_code(code, str(theirs.pubkey()), signature)
