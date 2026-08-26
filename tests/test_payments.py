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


# ── asset naming: unguessable and collision-free ────────────────────────────

from asset_naming import asset_filename  # noqa: E402
from export_utils import export_generic  # noqa: E402


def test_two_assets_never_share_a_filename():
    """Regression: names were a UNIX timestamp, so same-second jobs collided
    and one customer's report overwrote another's in a shared bucket."""
    names = {asset_filename("X402-PROMPT-AB12", "pdf") for _ in range(500)}
    assert len(names) == 500


def test_every_export_format_gets_a_unique_name():
    """Regression: exports were named aetheron_output.<ext>, one object in a
    public bucket shared by every user."""
    for fmt in ("txt", "md", "html", "docx"):
        _, a = export_generic(fmt, "x", "X402-CODE-1")
        _, b = export_generic(fmt, "x", "X402-CODE-1")
        assert a != b and a.endswith(fmt)


def test_asset_id_is_sanitised_into_the_filename():
    for hostile in ("../../etc/passwd", 'x"; rm -rf /', "a/b/c", ""):
        name = asset_filename(hostile, "pdf")
        assert "/" not in name and '"' not in name and ".." not in name


def test_generated_names_pass_the_download_filter():
    from Aetheron import ASSET_FILENAME_RE
    for fmt in ("pdf", "txt", "md", "html", "docx"):
        assert ASSET_FILENAME_RE.match(asset_filename("X402-RISK-9", fmt))


# ── /download filename filter ───────────────────────────────────────────────

def test_download_rejects_traversal_and_header_injection():
    from Aetheron import ASSET_FILENAME_RE
    for hostile in (
        "../../secret",
        "..%2F..%2Fsecret",
        "a/b",
        'x"; attachment; filename="evil',
        "x\r\nSet-Cookie: a=b",
        "x" * 200,
        "",
    ):
        assert not ASSET_FILENAME_RE.match(hostile), hostile


# ── input bounds ────────────────────────────────────────────────────────────

def test_risk_engine_rejects_resource_exhaustion():
    """Regression: runs x steps was unbounded, so one paid request could ask
    for roughly 75 GB and take both workers down."""
    from Aetheron import RiskEngineInput
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RiskEngineInput(runs=1_000_000, steps=10_000, start_price=1, mu=0.1, sigma=0.2)
    with pytest.raises(pydantic.ValidationError):
        RiskEngineInput(runs=10_000, steps=2_000, start_price=1, mu=0.1, sigma=0.2)  # cells cap

    ok = RiskEngineInput(runs=2000, steps=252, start_price=1, mu=0.08, sigma=0.2)
    assert ok.runs == 2000


def test_text_inputs_are_bounded():
    from Aetheron import PromptIn, MAX_PROMPT_CHARS
    import pydantic

    assert PromptIn(text="hello").text == "hello"
    with pytest.raises(pydantic.ValidationError):
        PromptIn(text="x" * (MAX_PROMPT_CHARS + 1))
    with pytest.raises(pydantic.ValidationError):
        PromptIn(text="")


def test_contract_address_must_match_its_chain():
    from Aetheron import ContractIntelInput
    import pydantic

    ok = ContractIntelInput(
        contract_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        network="ethereum",
    )
    assert ok.network == "ethereum"

    ok = ContractIntelInput(
        contract_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        network="solana",
    )
    assert ok.network == "solana"

    # Wrong shape for the chain, traversal input, and characters outside the
    # base58 alphabet are all refused.
    for addr, net in (
        ("0xdAC17F958D2ee523a2206206994597C13D831ec7", "solana"),
        ("../../../admin", "ethereum"),
        ("../../../admin" + "A" * 30, "solana"),
        ("0" * 40, "solana"),                        # 0 is not base58
        ("0xZZZZ17F958D2ee523a2206206994597C13D831e", "ethereum"),
    ):
        with pytest.raises(pydantic.ValidationError):
            ContractIntelInput(contract_address=addr, network=net)


def test_unknown_network_is_refused():
    from Aetheron import ContractIntelInput
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        ContractIntelInput(contract_address="1" * 40, network="bitcoin")


def test_export_format_is_constrained():
    from Aetheron import PromptIn
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        PromptIn(text="hi", format="exe")


# ── prompt optimizer: target and structured rendering ───────────────────────

def test_prompt_target_is_constrained():
    """Free text here would reach the optimizer's instructions."""
    from Aetheron import PromptIn
    import pydantic

    assert PromptIn(text="hi", target="coding").target == "coding"
    assert PromptIn(text="hi").target is None
    for bad in ("bogus", "; DROP--", "chat; ignore previous"):
        with pytest.raises(pydantic.ValidationError):
            PromptIn(text="hi", target=bad)


def test_optimizer_report_renders_every_section():
    from celery_worker import OptimizedPrompt, _render_optimizer_report

    r = OptimizedPrompt(
        optimized_prompt="Do the thing.",
        what_changed=["Named the audience."],
        analysis="It was vague.",
        failure_modes=["Answers the wrong question."],
        variants=[],
        usage_notes=["Pair with a diff."],
    )
    out = _render_optimizer_report(r, "a coding agent")
    for n, title in enumerate(
        ["Optimized Prompt", "What Changed", "Prompt Analysis",
         "Failure Modes", "Variants", "Using It"], start=1
    ):
        assert f"{n}. {title}" in out

    # The rewrite leads, because that is what the customer paid for.
    assert out.index("1. Optimized Prompt") < out.index("3. Prompt Analysis")
    # An empty variants list says so rather than rendering a bare heading.
    assert "admits one sensible reading" in out
