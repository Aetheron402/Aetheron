"""
Payment verification tests.

Every case here corresponds to a defect that reached production. They exist so
that a refactor of verify_payment cannot silently reopen a way to obtain paid
components without paying for them.

Run with:  pytest -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure before importing the app: it reads these at module scope.
os.environ.setdefault("PAYMENT_WALLET", "AetheronReceivingWallet11111111111111111111")
os.environ.setdefault("AETH_MINT_ADDRESS", "")
os.environ.setdefault("DB_HOST", "")

import Aetheron  # noqa: E402
import ledger_utils  # noqa: E402

USDC = Aetheron.USDC_MINT
AETHERON = Aetheron.PAYMENT_WALLET
ATTACKER = "AttackerWallet2222222222222222222222222222"
OUTSIDER = "SomeoneElse333333333333333333333333333333"
AETH = "AethMint44444444444444444444444444444444444"


def bal(index, mint, owner, amount):
    return {
        "accountIndex": index,
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {"amount": str(amount)},
    }


def tx(pre, post, signer=ATTACKER, err=None):
    return {
        "meta": {"err": err, "preTokenBalances": pre, "postTokenBalances": post},
        "transaction": {"message": {"accountKeys": [{"pubkey": signer, "signer": True}]}},
    }


# ── extract_received_amount: only our wallet's gain counts ──────────────────

def test_self_transfer_credits_nothing():
    """Moving tokens between two wallets an attacker owns is not a payment."""
    t = tx(
        pre=[bal(1, USDC, ATTACKER, 1_000_000), bal(2, USDC, ATTACKER, 0)],
        post=[bal(1, USDC, ATTACKER, 0), bal(2, USDC, ATTACKER, 1_000_000)],
    )
    assert Aetheron.extract_received_amount(t, USDC, AETHERON) == 0


def test_buying_the_token_credits_nothing():
    """A DEX buy raises the buyer's own balance and must not count as payment."""
    t = tx(
        pre=[bal(1, AETH, ATTACKER, 0)],
        post=[bal(1, AETH, ATTACKER, 5_000_000_000)],
    )
    assert Aetheron.extract_received_amount(t, AETH, AETHERON) == 0


def test_payment_to_a_third_party_credits_nothing():
    t = tx(
        pre=[bal(1, USDC, OUTSIDER, 0)],
        post=[bal(1, USDC, OUTSIDER, 250_000)],
    )
    assert Aetheron.extract_received_amount(t, USDC, AETHERON) == 0


def test_real_payment_is_credited():
    t = tx(
        pre=[bal(1, USDC, ATTACKER, 1_000_000), bal(2, USDC, AETHERON, 0)],
        post=[bal(1, USDC, ATTACKER, 750_000), bal(2, USDC, AETHERON, 250_000)],
    )
    assert Aetheron.extract_received_amount(t, USDC, AETHERON) == 250_000


def test_credit_is_the_delta_not_the_whole_balance():
    """A pre-existing balance must not be counted as newly received."""
    t = tx(
        pre=[bal(1, USDC, AETHERON, 9_000_000)],
        post=[bal(1, USDC, AETHERON, 9_250_000)],
    )
    assert Aetheron.extract_received_amount(t, USDC, AETHERON) == 250_000


def test_new_token_account_counts_full_balance():
    """No pre-balance means the account was created by this transaction."""
    t = tx(pre=[], post=[bal(3, USDC, AETHERON, 250_000)])
    assert Aetheron.extract_received_amount(t, USDC, AETHERON) == 250_000


def test_wrong_mint_is_ignored():
    t = tx(
        pre=[bal(1, "SomeOtherMint555555555555555555555555555", AETHERON, 0)],
        post=[bal(1, "SomeOtherMint555555555555555555555555555", AETHERON, 999_000_000)],
    )
    assert Aetheron.extract_received_amount(t, USDC, AETHERON) == 0


def test_malformed_balance_entries_do_not_crash():
    t = tx(pre=[], post=[{"accountIndex": 1, "mint": USDC, "owner": AETHERON}])
    assert Aetheron.extract_received_amount(t, USDC, AETHERON) == 0


# ── replay protection ───────────────────────────────────────────────────────

@pytest.fixture()
def clean_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_utils, "SQLITE_PATH", str(tmp_path / "t.db"))
    ledger_utils.init_ledger()
    yield


def test_signature_can_only_be_claimed_once(clean_db):
    assert ledger_utils.consume_signature("sig_a", "W", "c", 100, "USDC") is True
    assert ledger_utils.consume_signature("sig_a", "W", "c", 100, "USDC") is False


def test_a_different_signature_is_still_accepted(clean_db):
    assert ledger_utils.consume_signature("sig_a", "W", "c", 100, "USDC") is True
    assert ledger_utils.consume_signature("sig_b", "W", "c", 100, "USDC") is True


def test_claiming_the_same_signature_for_another_component_fails(clean_db):
    """One transfer pays for one thing, not for every component."""
    assert ledger_utils.consume_signature("sig_a", "W", "prompt", 100, "USDC") is True
    assert ledger_utils.consume_signature("sig_a", "W", "risk", 100, "USDC") is False


# ── partial payments ────────────────────────────────────────────────────────

def test_partial_payments_accumulate_and_read_back(clean_db):
    """Regression: the stored key is 'paid'; reading 'amount' raised KeyError."""
    ledger_utils.add_partial("W", "prompt", "USDC", 100_000, 250_000)
    entry = ledger_utils.get_partial("W", "prompt", "USDC")
    assert entry["paid"] == 100_000

    ledger_utils.add_partial("W", "prompt", "USDC", 150_000, 250_000)
    assert ledger_utils.get_partial("W", "prompt", "USDC")["paid"] == 250_000


def test_partial_state_is_isolated_per_component(clean_db):
    ledger_utils.add_partial("W", "prompt", "USDC", 100_000, 250_000)
    assert ledger_utils.get_partial("W", "risk", "USDC") is None


def test_clearing_a_partial_removes_it(clean_db):
    ledger_utils.add_partial("W", "prompt", "USDC", 100_000, 250_000)
    ledger_utils.clear_partial("W", "prompt", "USDC")
    assert ledger_utils.get_partial("W", "prompt", "USDC") is None


def test_partial_survives_a_reconnect(clean_db):
    """It lives in the database, so a restart or a second process still sees it."""
    ledger_utils.add_partial("W", "prompt", "USDC", 100_000, 250_000)
    assert ledger_utils.get_partial("W", "prompt", "USDC")["paid"] == 100_000


# ── verify_payment guards that need no network ──────────────────────────────

def test_missing_signature_or_wallet_is_rejected():
    assert Aetheron.verify_payment(None, "W", 0.25) is False
    assert Aetheron.verify_payment("sig", None, 0.25) is False


def test_unknown_payment_method_is_rejected():
    assert Aetheron.verify_payment("sig", "W", 0.25, payment_method="DOGE") is False


def test_aeth_is_rejected_while_no_mint_is_configured():
    """Regression: this reached Pubkey.from_string(None) and returned a 500."""
    assert Aetheron.AETH_MINT is None
    assert Aetheron.verify_payment("sig", "W", 0.25, payment_method="AETH") is False


def test_malformed_signature_is_rejected_not_raised():
    for bad in ("DEMO_OK", "!!!!", "x" * 200):
        assert Aetheron.verify_payment(bad, "W", 0.25) is False
