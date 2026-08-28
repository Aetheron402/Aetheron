"""
Payment verification tests.

Every case here corresponds to a defect that reached production. They exist so
that a refactor of verify_payment cannot silently reopen a way to obtain paid
components without paying for them.

Run with:  pytest -q
"""

import json
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


# ── report storage ──────────────────────────────────────────────────────────

def test_report_survives_a_round_trip(clean_db):
    """A customer paid for this file; it has to come back byte for byte."""
    import storage
    storage.init_storage()

    data = b"%PDF-1.4 fake report bytes \x00\x01\x02 with nulls"
    url = storage.store_asset(data, "aetheron_X402-PROMPT-A1_tok.pdf")

    assert url == "/download/aetheron_X402-PROMPT-A1_tok.pdf"
    back, ctype = storage.fetch_asset("aetheron_X402-PROMPT-A1_tok.pdf")
    assert back == data
    assert ctype == "application/pdf"


def test_content_type_follows_the_extension(clean_db):
    import storage
    storage.init_storage()
    for ext, expected in [
        ("pdf", "application/pdf"),
        ("txt", "text/plain"),
        ("md", "text/markdown"),
        ("html", "text/html"),
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ]:
        storage.store_asset(b"x", f"a_{ext}.{ext}")
        assert storage.fetch_asset(f"a_{ext}.{ext}")[1] == expected


def test_missing_report_returns_none(clean_db):
    import storage
    storage.init_storage()
    assert storage.fetch_asset("aetheron_nothing_here.pdf") is None


def test_oversized_report_is_refused(clean_db):
    """A runaway generation should fail loudly, not fill the database."""
    import storage
    storage.init_storage()
    with pytest.raises(ValueError):
        storage.store_asset(b"x" * (storage.MAX_ASSET_BYTES + 1), "huge.pdf")


def test_retention_drops_old_reports_only(clean_db):
    import storage, time
    from ledger_utils import _cursor, _q
    storage.init_storage()

    storage.store_asset(b"recent", "recent.pdf")
    storage.store_asset(b"ancient", "ancient.pdf")
    # backdate one past the window
    with _cursor(commit=True) as cur:
        cur.execute(_q("UPDATE assets SET created_at = %s WHERE filename = %s;"),
                    (time.time() - 60 * 86400, "ancient.pdf"))

    storage.purge_expired(max_age_days=30)
    assert storage.fetch_asset("ancient.pdf") is None
    assert storage.fetch_asset("recent.pdf") is not None


# ── contract report: figures a customer pays for ────────────────────────────

def test_failed_holder_fetch_is_not_reported_as_low_risk():
    """
    A provider error must never read as a favourable finding.

    The brief this replaces required exactly that on Ethereum: when the holder
    lookup failed it had to print that distribution was "broad with minimal
    concentration risk" and never mention the failure, on a report someone buys
    to assess risk.
    """
    import contract_report as cr
    out = cr.holder_table({
        "network": "ethereum",
        "top_holders": {"error": "rate limited"},
        "token_metadata": {"liquidity_usd": 900000, "market_cap": 4e7, "price_usd": 1.0},
    })
    assert "minimal concentration risk" not in out
    assert "inferred to be broad" not in out
    assert "unmeasured" in out


def test_scores_are_identical_across_runs():
    """Same scan, same numbers. A score that moves measures nothing."""
    import contract_report as cr
    blob = {
        "network": "solana",
        "risk_hints": {"mint_authority": True},
        "token_metadata": {"price_usd": 0.01, "liquidity_usd": 5000},
        "sol_top_holders": {"holders": [{"address": "A", "percentage": 42.5}]},
    }
    assert len({tuple(sorted(cr.score(blob).items())) for _ in range(25)}) == 1


def test_scores_stay_inside_their_range():
    import contract_report as cr
    worst = {
        "network": "ethereum",
        "honeypot_intel": {"summary_risk_level": "Critical", "is_honeypot": True},
        "exploit_surface": {"flags": ["mint"], "dangerous_functions": ["drain"]},
        "admin_risk": {"admin_control_level": "High"},
        "base_intel": {}, "token_metadata": {},
    }
    for value in cr.score(worst).values():
        assert 1 <= value <= 10


def test_holder_percentages_are_never_invented():
    """Rows come from the provider; a null percentage says so."""
    import contract_report as cr
    table = cr.holder_table({
        "network": "solana",
        "sol_top_holders": {"holders": [
            {"address": "AAA", "percentage": 42.5},
            {"address": "BBB", "percentage": None},
        ]},
    })
    assert "| 1 | AAA | 42.50% |" in table
    assert "| 2 | BBB | not available |" in table


def test_suspicious_clusters_always_reach_the_negative_signals():
    import contract_report as cr
    out = cr.signals({
        "signal_indicators": {"positives": [], "negatives": []},
        "bubblemap_analysis": {"summary": {"suspicious_clusters_count": 2}},
    })
    assert "2 holder clusters flagged as suspicious" in out


# ── risk engine: the simulation behind a paid report ────────────────────────

def test_a_seeded_run_is_reproducible():
    """The modal offers a seed field, which is a promise of repeatability."""
    import risk_metrics as rm
    a = rm.simulate(500, 40, 0.05, 0.4, 1.0, seed=42)
    b = rm.simulate(500, 40, 0.05, 0.4, 1.0, seed=42)
    assert a["final_prices"] == b["final_prices"]


def test_seeding_survives_other_code_using_the_shared_generator():
    import random, risk_metrics as rm
    expected = rm.simulate(500, 40, 0.05, 0.4, 1.0, seed=42)["final_prices"]
    random.seed(999)
    random.gauss(0, 1)
    assert rm.simulate(500, 40, 0.05, 0.4, 1.0, seed=42)["final_prices"] == expected


def test_zero_volatility_is_the_deterministic_drift_curve():
    import math, risk_metrics as rm
    s = rm.simulate(50, 10, 0.10, 0.0, 100.0, seed=1)
    assert abs(s["min_final"] - s["max_final"]) < 1e-9
    assert abs(s["min_final"] - 100.0 * math.exp(0.10)) < 1e-6
    assert s["worst_drawdown"] == 0.0


def test_drawdown_catches_falls_the_final_price_hides():
    """
    A path can recover before the end. Measuring only where paths finish
    reports that holder as untroubled, which is the omission this fixes.
    """
    import risk_metrics as rm
    s = rm.simulate(3000, 100, 0.0, 0.8, 1.0, seed=7)
    assert s["prob_drawdown_20"] >= s["prob_loss_20"]
    assert s["worst_drawdown"] > 0


def test_expected_shortfall_is_never_better_than_value_at_risk():
    import risk_metrics as rm
    s = rm.simulate(2000, 50, 0.0, 0.6, 1.0, seed=5)
    assert s["cvar5_price"] <= s["p5"] + 1e-9


def test_percentiles_are_ordered():
    import risk_metrics as rm
    s = rm.simulate(2000, 50, 0.05, 0.4, 1.0, seed=3)
    assert s["p5"] <= s["p25"] <= s["p50"] <= s["p75"] <= s["p95"]


def test_only_the_plotted_paths_are_retained():
    """Keeping every path to draw twenty of them is what pressures worker memory."""
    import risk_metrics as rm
    s = rm.simulate(4000, 60, 0.05, 0.4, 1.0, seed=3, keep_paths=20)
    assert len(s["sample_paths"]) == 20
    assert len(s["final_prices"]) == 4000


def test_invalid_simulation_inputs_are_rejected():
    import risk_metrics as rm
    for args in [(0, 10, 0.05, 0.4, 1.0), (10, 0, 0.05, 0.4, 1.0),
                 (10, 10, 0.05, 0.4, 0.0), (10, 10, 0.05, -0.1, 1.0)]:
        with pytest.raises(ValueError):
            rm.simulate(*args)


# ── PDF rendering of a copy-pasteable deliverable ───────────────────────────

def test_a_fenced_prompt_is_one_block_not_many_sections():
    """
    The optimizer's deliverable is a prompt that itself contains markdown
    headings. Splitting on blank lines tore its fence apart, so every
    '# Heading' inside it was read as a report section: a six section report
    rendered as fourteen, with the real sections renumbered after the
    fragments, and the line breaks that made it copy-pasteable reflowed away.
    """
    import celery_worker as w
    from pdf_utils import split_blocks

    r = w.OptimizedPrompt(
        optimized_prompt="# Role\nYou are an analyst.\n\n# Task\nDo the thing.",
        what_changed=["Named the role."],
        analysis="The original was vague.",
        failure_modes=["Invents a competitor set."],
        variants=[],
        usage_notes=["Fill the inputs first."],
    )
    blocks = split_blocks(w._render_optimizer_report(r, "an agent"))

    fenced = [b for b in blocks if b.strip().startswith("```")]
    assert len(fenced) == 1, "the prompt must survive as exactly one block"
    assert "# Role" in fenced[0] and "# Task" in fenced[0]

    # Nothing outside the fence may start with a heading marker taken from
    # inside the prompt, which is what became a spurious section.
    outside = [b for b in blocks if not b.strip().startswith("```")]
    assert not any(b.lstrip().startswith("# Task") for b in outside)

    titles = [b.strip() for b in outside if b.strip()[:2] in
              ("1.", "2.", "3.", "4.", "5.", "6.", "7.")]
    numbers = sorted({t[0] for t in titles})
    assert numbers == ["1", "2", "3", "4", "5", "6"], numbers


def test_a_fence_survives_a_blank_line_inside_it():
    from pdf_utils import split_blocks
    blocks = split_blocks("1. Heading\n\n```\nline one\n\nline three\n```\n\n2. Second")
    fenced = [b for b in blocks if b.strip().startswith("```")]
    assert len(fenced) == 1
    assert "line one" in fenced[0] and "line three" in fenced[0]


def test_a_fence_language_tag_is_not_treated_as_content():
    from pdf_utils import strip_fence
    assert strip_fence("```python\nprint(1)\n```") == "print(1)"
    # No tag: the first line is content, even when it looks like one word.
    assert strip_fence("```\nprint(1)\n```") == "print(1)"
    assert strip_fence("```\n#!/bin/sh\necho hi\n```") == "#!/bin/sh\necho hi"
    # Prose first lines survive too.
    assert strip_fence("```\nYou are an analyst.\nDo it.\n```").startswith("You are")
    # Blank lines inside are preserved.
    assert strip_fence("```\na\n\nb\n```") == "a\n\nb"


def test_a_section_title_is_its_own_block():
    """
    Titles are detected per block, and the whole block is drawn in the heading
    style. A title emitted with a single trailing newline kept its content in
    the same block, so an entire bullet list rendered as one bold run-on
    heading. Every renderer has to leave a blank line after the title.
    """
    import celery_worker as w
    from pdf_utils import split_blocks

    r = w.OptimizedPrompt(
        optimized_prompt="# Role\nBe an analyst.",
        what_changed=["First change.", "Second change."],
        analysis="Some analysis.",
        failure_modes=["A failure."],
        variants=[],
        usage_notes=["A note."],
    )
    blocks = [b.strip() for b in split_blocks(w._render_optimizer_report(r, "an agent"))]

    for title in ["1. Optimized Prompt", "2. What Changed", "3. Prompt Analysis",
                  "4. Failure Modes", "5. Variants", "6. Using It"]:
        assert title in blocks, f"{title} is not a block of its own"

    # The bullets must be a separate block, not absorbed into the heading.
    assert any(b.startswith("• First change.") for b in blocks)


def test_every_renderer_separates_titles_from_content():
    import re
    import celery_worker as w
    from pdf_utils import split_blocks

    persona = w.Persona(name="P", interpretation="i", strength="s", weakness="wk",
                        predicted_output="a sample line", risks=["r"])
    cases = [
        w._render_tester_report(w.PersonaTest(
            interpretation="x",
            ambiguities=[w.Ambiguity(phrase="brief", problem="no target",
                                     readings=["one page", "five bullets"], impact="high")],
            personas=[persona], cross_persona="c",
            quality_score=5, quality_reasoning="q",
            divergence_score=5, divergence_reasoning="d",
            improvements=["imp"], improved_prompt="better prompt",
            projected_quality_score=8, projected_divergence_score=2,
            projected_reasoning="closes the length gap")),
        w._render_code_report(w.CodeAudit(
            language="python", verdict="needs fixes before use",
            summary="s", how_it_works="h", strengths=["st"],
            weaknesses=[_finding("high", 2, "wk")], complexity="c",
            security=[_finding("critical", 3, "sec")],
            edge_cases=[_finding("medium", 4, "ec")], refactors=["rf"],
            patches=["print(1)"], tests=["assert True"],
            recommendations=["rec"])),
        w._render_risk_report(w.RiskInterpretation(
            verdict="v", downside=["d"], upside=["u"], drawdown_reading="dd",
            assumptions=["a"], parameter_notes=["p"]),
            "Parameters:\n• Runs: 10", "| Change | Median |\n|---|---|\n| As entered | 1.0 |"),
    ]
    for md in cases:
        for block in split_blocks(md):
            t = block.strip()
            if re.match(r"^\d+\.\s", t) and not t.startswith("```"):
                assert "\n" not in t, f"title carries content: {t[:60]!r}"


# ── code audit findings ─────────────────────────────────────────────────────

def _finding(sev, line, title="t", detail="d"):
    import celery_worker as w
    return w.Finding(severity=sev, line=line, title=title, detail=detail)


def test_findings_are_ordered_worst_first():
    """
    A flat list left the reader meeting the naming nit before the injection.
    Order is imposed here rather than trusted from the model.
    """
    import celery_worker as w
    out = w._fmt_findings(
        [_finding("low", 3, "nit"), _finding("critical", 9, "injection"),
         _finding("medium", 1, "middling"), _finding("high", 5, "leak")],
        "none",
    )
    positions = [out.index(t) for t in ("injection", "leak", "middling", "nit")]
    assert positions == sorted(positions), out


def test_a_finding_carries_its_severity_and_line():
    import celery_worker as w
    out = w._fmt_findings([_finding("critical", 42, "SQL injection")], "none")
    assert "[CRITICAL]" in out
    assert "(line 42)" in out


def test_a_whole_file_finding_omits_the_line():
    import celery_worker as w
    out = w._fmt_findings([_finding("medium", None, "Relative path")], "none")
    assert "(line" not in out


def test_an_unknown_severity_sorts_last_rather_than_crashing():
    import celery_worker as w
    out = w._fmt_findings(
        [_finding("banana", 1, "odd"), _finding("critical", 2, "real")], "none")
    assert out.index("real") < out.index("odd")


def test_empty_finding_lists_state_that_plainly():
    import celery_worker as w
    assert w._fmt_findings([], "no security surface") == "no security surface"


def test_code_is_numbered_for_citation():
    """A model counting lines itself cites the wrong ones."""
    import celery_worker as w
    out = w._numbered("import os\nimport sys\n\ndef f():\n    pass")
    assert "1 | import os" in out
    assert "5 |     pass" in out


def test_line_numbering_width_stays_aligned_past_nine():
    import celery_worker as w
    out = w._numbered("\n".join(f"line{i}" for i in range(1, 12)))
    assert " 1 | line1" in out
    assert "11 | line11" in out


# ── persona test: quoted ambiguities and a measurable before/after ──────────

def _persona(name, out="sample"):
    import celery_worker as w
    return w.Persona(name=name, interpretation="i", strength="s",
                     weakness="wk", predicted_output=out, risks=[])


def _test_result(**over):
    import celery_worker as w
    base = dict(
        interpretation="x",
        ambiguities=[
            w.Ambiguity(phrase="the important parts", problem="no purpose",
                        readings=["what is new", "what is risky"], impact="high"),
            w.Ambiguity(phrase="brief", problem="no target",
                        readings=["one page", "five bullets"], impact="medium"),
        ],
        personas=[_persona("A", "Bullet list, 5 items"), _persona("B", "One page of prose")],
        cross_persona="c", quality_score=2, quality_reasoning="q",
        divergence_score=8, divergence_reasoning="d",
        improvements=["imp"], improved_prompt="better prompt",
        projected_quality_score=8, projected_divergence_score=2,
        projected_reasoning="closes the length gap, leaves tone open",
    )
    base.update(over)
    return w.PersonaTest(**base)


def test_ambiguities_quote_the_prompt_and_rank_by_impact():
    """A finding the author cannot locate in their own prompt is not actionable."""
    import celery_worker as w
    md = w._render_tester_report(_test_result())
    section = md[md.index("2. Where It Splits"):md.index("3. PersonaBench")]
    assert '"the important parts"' in section
    assert '"brief"' in section
    assert section.index("the important parts") < section.index("brief"), "high impact first"
    assert "[HIGH]" in section and "[MEDIUM]" in section


def test_a_prompt_with_no_ambiguity_says_so():
    import celery_worker as w
    md = w._render_tester_report(_test_result(ambiguities=[]))
    assert "No phrase in this prompt admits more than one working reading." in md


def test_each_persona_shows_what_it_would_return():
    """Two samples side by side demonstrate the split; a description asserts it."""
    import celery_worker as w
    md = w._render_tester_report(_test_result())
    assert "Bullet list, 5 items" in md
    assert "One page of prose" in md
    assert md.count("Would return:") == 2


def test_the_rewrite_is_scored_against_the_original():
    import celery_worker as w
    md = w._render_tester_report(_test_result())
    after = md[md.index("10. After The Rewrite"):]
    assert "Prompt Quality Score: 2/10 → 8/10" in after
    assert "Persona Divergence Score: 8/10 → 2/10" in after
    assert "leaves tone open" in after


def test_metric_lines_keep_their_exact_format():
    import re
    import celery_worker as w
    md = w._render_tester_report(_test_result())
    assert re.search(r"^Prompt Quality Score: 2/10$", md, re.M)
    assert re.search(r"^Persona Divergence Score: 8/10$", md, re.M)


def test_readings_stay_on_separate_lines():
    """
    The house style strips em and en dashes together with the whitespace
    around them, so an en dash used as a list marker pulled every reading back
    onto one run-on line. Renderers must not use one as punctuation.
    """
    import celery_worker as w
    md = w.clean_markdown(w._render_tester_report(_test_result()))
    section = md[md.index("2. Where It Splits"):md.index("3. PersonaBench")]
    assert section.count("- reads as:") == 4
    for line in section.splitlines():
        assert line.count("reads as:") <= 1, f"readings collapsed onto one line: {line[:80]}"


def test_an_escaped_sample_gets_real_line_breaks():
    """Models sometimes write backslash-n instead of a newline; it renders literally."""
    import celery_worker as w
    assert w._unescape_sample(r"TL;DR: decide\nOwners: ...") == "TL;DR: decide\nOwners: ..."


def test_an_already_formatted_sample_is_left_alone():
    """A real backslash-n inside a formatted code sample must survive."""
    import celery_worker as w
    original = 'print("a\\nb")\nprint("done")'
    assert w._unescape_sample(original) == original


# ── contract scoring: capabilities weighed by who they can be used against ──

def _eth_blob(**over):
    blob = {
        "network": "ethereum",
        "base_intel": {"verified": False},
        "exploit_surface": {"dangerous_functions": [], "flags": []},
        "admin_risk": {"admin_control_level": "Low", "signals": []},
        "honeypot_intel": {"summary_risk_level": "Low", "is_honeypot": False,
                           "simulation": 1, "holderAnalysis": 1},
        "token_metadata": {"price_usd": 5e-6, "liquidity_usd": 2.7e6,
                           "market_cap": 3e9, "fdv": 5e9, "volume_24h": 78853},
    }
    blob.update(over)
    return blob


def test_a_self_scoped_burn_is_not_scored_as_a_risk():
    """
    A holder burning their own balance is no lever over anyone else. Scoring it
    as dangerous put the arithmetic at odds with the prose: a fixed supply
    token with no owner and immutable code came out at 7/10.
    """
    import contract_report as cr
    blob = _eth_blob(exploit_surface={"dangerous_functions": ["burn(uint256)"], "flags": []})
    assert cr._hostile_capabilities(blob) == set()
    assert cr.score(blob)["overall_risk"] <= 3


def test_real_powers_do_raise_the_score():
    import contract_report as cr
    blob = _eth_blob(
        exploit_surface={"dangerous_functions": ["mint(address,uint256)", "blacklist(address)"],
                         "flags": ["upgrade"]},
        admin_risk={"admin_control_level": "High", "signals": ["owner can mint"]},
    )
    assert {"mint", "blacklist", "upgrade"} <= cr._hostile_capabilities(blob)
    assert cr.score(blob)["overall_risk"] >= 8


def test_solana_authorities_count_as_powers():
    import contract_report as cr
    live = {"network": "solana", "risk_hints": {"mint_authority": "SomeKey"},
            "token_metadata": {"price_usd": 1, "liquidity_usd": 1000}}
    revoked = {"network": "solana", "risk_hints": {"mint_authority": None},
               "token_metadata": {"price_usd": 1, "liquidity_usd": 1000}}
    assert "mint" in cr._hostile_capabilities(live)
    assert cr._hostile_capabilities(revoked) == set()
    assert cr.score(live)["overall_risk"] > cr.score(revoked)["overall_risk"]


def test_coverage_separates_checked_from_unavailable():
    import contract_report as cr
    out = cr.coverage(_eth_blob(top_holders={"error": "rate limited"}))
    assert "Holder distribution: NOT CHECKED" in out
    assert "Market data: checked" in out
    assert "unmeasured, not clear" in out


def test_a_change_since_the_last_scan_is_reported():
    import contract_report as cr
    out = cr.snapshot_delta(_eth_blob(
        risk_hints={"mint_authority": False},
        lp_lock_status={"status": "unlocked"},
        previous_snapshot={"risk_hints": {"mint_authority": True},
                           "lp_lock_status": {"status": "locked"},
                           "token_metadata": {"liquidity_usd": 1e7, "price_usd": 5e-6}},
    ))
    assert "Mint authority: True -> False" in out
    assert "LP lock status: 'locked' -> 'unlocked'" in out
    assert "Liquidity (USD)" in out


def test_a_field_the_previous_scan_lacked_is_not_a_change():
    """None -> False would cry wolf on the first rescan of every contract."""
    import contract_report as cr
    out = cr.snapshot_delta(_eth_blob(previous_snapshot={"token_metadata": {"price_usd": 5e-6}}))
    assert "->" not in out


def test_no_previous_scan_produces_no_section():
    import contract_report as cr
    assert cr.snapshot_delta(_eth_blob()) == ""


def test_flags_dict_only_counts_capabilities_that_are_present():
    """
    exploit_surface.flags is a dict keyed by every capability the detector
    knows about, False for the absent ones. Reading its keys scored mint and
    upgrade against every Ethereum token regardless of what it could do, which
    an earlier list-shaped test did not catch.
    """
    import contract_report as cr
    blob = _eth_blob(exploit_surface={
        "dangerous_functions": ["burn(uint256)"],
        "flags": {"mint": False, "pause": False, "blacklist": False,
                  "upgrade": False, "burn": True},
    })
    assert cr._hostile_capabilities(blob) == set()
    assert cr.score(blob)["overall_risk"] <= 3


def test_flags_dict_with_a_real_capability_is_counted():
    import contract_report as cr
    blob = _eth_blob(exploit_surface={
        "dangerous_functions": [],
        "flags": {"mint": True, "pause": False, "blacklist": False},
    })
    assert "mint" in cr._hostile_capabilities(blob)


def test_risk_hints_booleans_are_read():
    """risk_hints answers each capability directly and is the most reliable source."""
    import contract_report as cr
    clean = _eth_blob(risk_hints={"has_mint": False, "has_pausing": False,
                                  "has_blacklist": False, "is_proxy": False})
    risky = _eth_blob(risk_hints={"has_mint": True, "has_pausing": True,
                                  "has_blacklist": False, "is_proxy": True})
    assert cr._hostile_capabilities(clean) == set()
    assert {"mint", "pause", "upgrade"} <= cr._hostile_capabilities(risky)


def test_present_names_handles_every_container_shape():
    import contract_report as cr
    assert cr._present_names({"a": True, "b": False}) == ["a"]
    assert cr._present_names(["a", "b"]) == ["a", "b"]
    assert cr._present_names(None) == []
    assert cr._present_names("nonsense") == []


# ── markdown tables in the PDF ──────────────────────────────────────────────

def test_a_markdown_table_becomes_a_real_table():
    """
    The holder concentration table is the main quantitative display in the
    contract report. Joining its cells with bullets made ranks and percentages
    impossible to scan down a column.
    """
    from reportlab.platypus import Table
    from pdf_utils import build_aetheron_pdf
    import pdf_utils

    captured = []
    original = Table.__init__

    def spy(self, data, *a, **kw):
        captured.append(data)
        return original(self, data, *a, **kw)

    Table.__init__ = spy
    try:
        build_aetheron_pdf("T", "2026-01-01", "w", "t", "s", (
            "1. Holders\n\n"
            "| Rank | Wallet | % |\n"
            "|---|---|---|\n"
            "| 1 | AAA | 8.17% |\n"
            "| 2 | BBB | not available |\n"
        ))
    finally:
        Table.__init__ = original

    grids = [d for d in captured if len(d) == 3 and len(d[0]) == 3]
    assert grids, "the markdown table did not reach a Table flowable"
    rendered = " ".join(cell.text for row in grids[0] for cell in row)
    assert "AAA" in rendered and "8.17%" in rendered
    assert "not available" in rendered
    assert "Rank" in rendered


def test_table_columns_are_sized_by_their_widest_cell():
    """A 44 character Solana address needs the room; a Rank column does not."""
    from reportlab.lib.styles import StyleSheet1, ParagraphStyle
    from pdf_utils import _markdown_table

    styles = StyleSheet1()
    styles.add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=11))
    table = _markdown_table(
        [["Rank", "Wallet Address", "%"],
         ["1", "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM", "8.17%"]],
        styles, 468,
    )
    rank, wallet, pct = table._argW
    assert wallet > rank * 3, (rank, wallet)
    assert wallet > pct * 3
    assert abs(sum(table._argW) - 468) < 1


def test_a_ragged_table_row_does_not_crash():
    from reportlab.lib.styles import StyleSheet1, ParagraphStyle
    from pdf_utils import _markdown_table
    styles = StyleSheet1()
    styles.add(ParagraphStyle(name="Body", fontName="Helvetica", fontSize=11))
    table = _markdown_table([["A", "B", "C"], ["1", "2"]], styles, 400)
    assert len(table._cellvalues[1]) == 3


# ── risk engine: sensitivity and time underwater ────────────────────────────

def test_sensitivity_shows_which_input_the_answer_rests_on():
    """
    The report told readers the conclusion depended on sigma. That was a claim
    about a model we are holding, and checking it costs a few thousand paths.
    """
    import risk_metrics as rm
    grid = rm.sensitivity(0.15, 0.9, 60, 1.0, seed=42, runs=800)
    rows = [r for r in grid.splitlines() if r.startswith("|") and "---" not in r]

    assert rows[0].startswith("| Change |")
    labels = [r.split("|")[1].strip() for r in rows[1:]]
    assert labels[0] == "As entered"
    assert any("Sigma half" in l for l in labels)
    assert any("Mu double" in l for l in labels)

    def median_of(label):
        row = next(r for r in rows[1:] if label in r)
        return float(row.split("|")[2].strip())

    # Halving volatility must move the median more than doubling drift does,
    # which is the claim the table exists to substantiate.
    base = median_of("As entered")
    assert median_of("Sigma half") - base > median_of("Mu double") - base


def test_sensitivity_rows_are_comparable_to_each_other():
    """A shared seed means differences between rows come from the inputs."""
    import risk_metrics as rm
    a = rm.sensitivity(0.1, 0.5, 30, 1.0, seed=7, runs=500)
    b = rm.sensitivity(0.1, 0.5, 30, 1.0, seed=7, runs=500)
    assert a == b


def test_time_underwater_is_tracked():
    """A brief dip and a whole horizon below entry give the same max drawdown."""
    import risk_metrics as rm
    s = rm.simulate(2000, 60, 0.0, 0.8, 1.0, seed=3)
    assert 0.0 <= s["median_time_underwater"] <= 1.0
    assert s["p95_time_underwater"] >= s["median_time_underwater"]

    # Strong upward drift with no volatility never goes underwater at all.
    calm = rm.simulate(50, 20, 0.5, 0.0, 1.0, seed=1)
    assert calm["median_time_underwater"] == 0.0


def test_drawdowns_are_available_for_charting():
    import risk_metrics as rm
    s = rm.simulate(500, 30, 0.05, 0.4, 1.0, seed=2)
    assert len(s["drawdowns"]) == 500
    assert all(0.0 <= d <= 1.0 for d in s["drawdowns"])


def test_a_chart_keeps_its_aspect_ratio():
    """
    The chart was placed at a fixed 5.7 by 3.8 inches whatever shape it was.
    Three stacked panels are twice as tall as they are wide, so a landscape box
    squashed them threefold and clipped every panel title.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from reportlab.lib.units import inch
    from pdf_utils import _fitted_image
    import tempfile, os

    path = os.path.join(tempfile.mkdtemp(), "tall.png")
    fig = plt.figure(figsize=(4, 10))
    plt.plot([1, 2, 3])
    fig.savefig(path)
    plt.close(fig)

    img = _fitted_image(path, 5.7 * inch, 8.2 * inch)
    assert abs(img.drawWidth / img.drawHeight - 4 / 10) < 0.02
    assert img.drawHeight <= 8.2 * inch + 1
    assert img.drawWidth <= 5.7 * inch + 1


def test_a_wide_chart_is_bounded_by_width():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from reportlab.lib.units import inch
    from pdf_utils import _fitted_image
    import tempfile, os

    path = os.path.join(tempfile.mkdtemp(), "wide.png")
    fig = plt.figure(figsize=(12, 3))
    plt.plot([1, 2, 3])
    fig.savefig(path)
    plt.close(fig)

    img = _fitted_image(path, 5.7 * inch, 8.2 * inch)
    assert abs(img.drawWidth - 5.7 * inch) < 1
    assert abs(img.drawWidth / img.drawHeight - 4.0) < 0.05


def test_an_unreadable_image_does_not_crash_the_report():
    from reportlab.lib.units import inch
    from pdf_utils import _fitted_image
    img = _fitted_image("/nonexistent/chart.png", 5.7 * inch, 8.2 * inch)
    assert img is not None


def test_a_tall_chart_still_builds_a_pdf():
    """
    Sizing the image correctly is not enough: a flowable taller than the frame
    raises rather than shrinking, so the whole report fails to build. The risk
    report's three stacked panels are exactly that shape.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import tempfile, os
    from pdf_utils import build_aetheron_pdf

    path = os.path.join(tempfile.mkdtemp(), "tall.png")
    fig = plt.figure(figsize=(6, 13))
    plt.plot([1, 2, 3])
    fig.savefig(path)
    plt.close(fig)

    res = build_aetheron_pdf("T", "2026-01-01", "w", "t", "s",
                             "1. Section\n\nbody text", chart_path=path)
    buf = res[0] if isinstance(res, tuple) else res
    assert buf.getvalue().startswith(b"%PDF")


# ── agent store: pre-configured downloads ───────────────────────────────────

def _open_zip(data):
    import io, zipfile
    return zipfile.ZipFile(io.BytesIO(data))


def test_the_placeholder_is_gone_from_a_configured_download():
    """
    The buyer previously had to open config.json and replace
    ADD_YOUR_WALLET_HERE before anything would run.
    """
    import json
    import agent_setup
    z = _open_zip(agent_setup.build_zip("wallet-watcher", {
        "wallet": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    }))
    cfg = json.loads(z.read("config.json"))
    assert cfg["wallets_to_watch"] == ["9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"]
    assert "ADD_YOUR_WALLET_HERE" not in json.dumps(cfg)


def test_untouched_settings_keep_their_defaults():
    """Only fields the buyer supplies are changed; tuning knobs are left alone."""
    import json
    import agent_setup
    z = _open_zip(agent_setup.build_zip("wallet-watcher", {
        "wallet": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    }))
    cfg = json.loads(z.read("config.json"))
    assert cfg["rpc"]["poll_interval_seconds"] == 8
    assert cfg["notifications"]["webhook_url"] == ""


def test_every_archive_ships_a_runner():
    import agent_setup
    for agent_id in agent_setup.AGENT_PATHS:
        z = _open_zip(agent_setup.build_zip(agent_id, {}))
        names = z.namelist()
        assert "run.sh" in names, agent_id
        assert "run.bat" in names, agent_id
        assert "QUICKSTART.md" in names, agent_id
        # ./run.sh has to work without the buyer running chmod first.
        assert z.getinfo("run.sh").external_attr >> 16 & 0o111, agent_id


def test_the_runner_starts_the_right_entrypoint():
    """
    Every agent here starts from main.py. project-planner also has an app.py,
    which is the module defining the application rather than a way to run it,
    and preferring it produced a run script that started nothing and exited
    zero.
    """
    import agent_setup
    for agent_id in agent_setup.AGENT_PATHS:
        script = _open_zip(agent_setup.build_zip(agent_id, {})).read("run.sh").decode()
        assert "exec python main.py" in script, agent_id


def test_an_agent_with_both_entrypoints_prefers_main():
    import os
    import tempfile
    import agent_setup

    directory = tempfile.mkdtemp()
    for name in ("app.py", "main.py"):
        open(os.path.join(directory, name), "w").close()
    assert agent_setup.entrypoint_for(directory) == "main.py"

    os.remove(os.path.join(directory, "main.py"))
    assert agent_setup.entrypoint_for(directory) == "app.py"


def test_a_bad_wallet_address_is_rejected():
    """Caught before payment, and the message says what is wrong."""
    import agent_setup
    with pytest.raises(agent_setup.SetupError) as exc:
        agent_setup.build_zip("wallet-watcher", {"wallet": "not-an-address"})
    assert "base58" in str(exc.value)


def test_a_webhook_must_be_https():
    import agent_setup
    with pytest.raises(agent_setup.SetupError):
        agent_setup.build_zip("wallet-watcher", {
            "wallet": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
            "webhook": "http://insecure.example.com/hook",
        })


def test_a_required_field_left_blank_is_refused():
    import agent_setup
    with pytest.raises(agent_setup.SetupError) as exc:
        agent_setup.build_zip("wallet-watcher", {"webhook": "https://example.com/h"})
    assert "required" in str(exc.value)


def test_private_keys_are_never_asked_for():
    """
    A key pasted into a web form travels through this server on the way to a
    file the buyer could have edited themselves.
    """
    import agent_setup
    keys = [f["key"] for f in agent_setup.fields_for("solana-sniper")]
    paths = [f["path"] for f in agent_setup.fields_for("solana-sniper")]
    assert not any("private" in k or "secret" in k for k in keys)
    assert not any("private_key" in p for p in paths)
    local = [p for p, _ in agent_setup.local_only_for("solana-sniper")]
    assert "wallet.private_key" in local


def test_the_template_config_is_never_mutated():
    """Two buyers must not see each other's settings."""
    import json
    import agent_setup
    before = json.load(open("static/agents_src/wallet-watcher/config.json"))
    agent_setup.build_zip("wallet-watcher", {
        "wallet": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        "webhook": "https://example.com/hook",
    })
    after = json.load(open("static/agents_src/wallet-watcher/config.json"))
    assert before == after
    assert after["wallets_to_watch"] == ["ADD_YOUR_WALLET_HERE"]


def test_caches_and_virtualenvs_are_not_shipped():
    import agent_setup
    for agent_id in agent_setup.AGENT_PATHS:
        names = _open_zip(agent_setup.build_zip(agent_id, {})).namelist()
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names), agent_id
        assert not any(n.startswith(".venv") for n in names), agent_id


def test_an_unknown_agent_is_refused():
    import agent_setup
    with pytest.raises(agent_setup.SetupError):
        agent_setup.build_zip("../../etc", {})


def test_skipping_setup_yields_the_template_unchanged():
    """
    The form offers 'Skip, use defaults'. Enforcing a required field there
    would block a download already paid for, over a value the buyer can edit
    in the file they just received.
    """
    import json
    import agent_setup
    z = _open_zip(agent_setup.build_zip("wallet-watcher", {}))
    cfg = json.loads(z.read("config.json"))
    assert cfg["wallets_to_watch"] == ["ADD_YOUR_WALLET_HERE"]
    assert "QUICKSTART.md" in z.namelist()


def test_a_partial_submission_still_validates_required_fields():
    """Someone who filled the form in must not omit the field that matters."""
    import agent_setup
    with pytest.raises(agent_setup.SetupError):
        agent_setup.build_zip("wallet-watcher", {"webhook": "https://example.com/h"})


# ── agent preview ───────────────────────────────────────────────────────────

def test_preview_never_takes_configuration_from_the_request():
    """
    A webhook URL supplied by a visitor would make this endpoint fetch whatever
    they pointed it at from inside our network. The demo config is fixed here.
    """
    import inspect
    import agent_preview
    src = inspect.getsource(agent_preview.run)
    assert "request" not in src
    for agent, cfg in agent_preview.DEMO_CONFIG.items():
        notif = cfg.get("notifications", {})
        assert not notif.get("enabled", False), agent
        assert not notif.get("webhook_url"), agent


def test_only_agents_the_worker_can_actually_run_are_previewable():
    """
    The Discord helper needs discord.py and a bot token, so it has no preview.

    The trading assistant and the planner were excluded on the assumption they
    needed PyNaCl and jsonschema. Both are listed in their requirements and
    neither is imported on the path a run takes, so both preview fine.
    """
    import agent_preview
    assert not agent_preview.is_previewable("discord-helper")
    for agent in ("wallet-watcher", "solana-trading-assistant", "project-planner"):
        assert agent_preview.is_previewable(agent), agent


def test_a_preview_is_bounded_in_time():
    """These agents loop forever by design, so the deadline is what ends it."""
    import time
    import agent_preview
    started = time.time()
    result = agent_preview.run("wallet-watcher", seconds=6)
    elapsed = time.time() - started
    assert elapsed < 25, elapsed
    assert result["stopped_on_deadline"] is True
    assert result["ok"] is True


def test_hitting_the_deadline_is_not_reported_as_failure():
    import agent_preview
    result = agent_preview.run("wallet-watcher", seconds=5)
    assert result["ok"] is True
    assert "not the agent" in result["reason"]


def test_a_preview_cannot_be_asked_to_run_forever():
    import agent_preview
    result = agent_preview.run("wallet-watcher", seconds=99999)
    assert result["seconds"] <= agent_preview.MAX_SECONDS


def fresh_wallet(name: str) -> str:
    """
    A wallet with no view history, whatever previous runs left behind.

    The view tables live in the real ledger, so a test that claims views under a
    fixed name passes once and then fails on every later run: the wallet has
    already spent its allowance. Clearing first keeps the tests deterministic
    without letting the table grow a new wallet per run.
    """
    ledger_utils.init_examples()
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(ledger_utils._q("DELETE FROM example_views WHERE wallet = %s;"),
                    (name,))
    return name


def test_watching_an_agent_run_is_metered_per_wallet():
    """
    Previews were limited only by a per IP cooldown, so a visitor could watch
    every agent in the store for free by waiting twenty seconds between clicks.
    The allowance is per wallet and spans the whole store, the same way the
    report examples do.
    """
    from fastapi.testclient import TestClient
    import agent_preview
    client = TestClient(Aetheron.app)
    wallet = {"X-USER-WALLET": fresh_wallet("MeteredPreviewWallet")}
    agents = sorted(agent_preview.PREVIEWABLE)[:ledger_utils.PREVIEW_ALLOWANCE + 1]

    for agent in agents[:-1]:
        Aetheron._preview_last_seen.clear()
        assert client.post(f"/api/agents/{agent}/preview", headers=wallet).status_code == 202

    Aetheron._preview_last_seen.clear()
    spent = client.post(f"/api/agents/{agents[-1]}/preview", headers=wallet)
    assert spent.status_code == 429
    assert "watched all" in spent.json()["detail"]


def test_rewatching_an_agent_costs_nothing_further():
    """The allowance limits how many agents are seen, not how often."""
    from fastapi.testclient import TestClient
    import agent_preview
    client = TestClient(Aetheron.app)
    wallet = {"X-USER-WALLET": fresh_wallet("RewatchWallet")}
    agent = sorted(agent_preview.PREVIEWABLE)[0]

    Aetheron._preview_last_seen.clear()
    first = client.post(f"/api/agents/{agent}/preview", headers=wallet).json()
    Aetheron._preview_last_seen.clear()
    again = client.post(f"/api/agents/{agent}/preview", headers=wallet).json()

    assert first["already_seen"] is False
    assert again["already_seen"] is True
    assert again["remaining"] == first["remaining"]


def test_a_preview_needs_a_wallet_to_meter_against():
    from fastapi.testclient import TestClient
    Aetheron._preview_last_seen.clear()
    assert TestClient(Aetheron.app).post("/api/agents/wallet-watcher/preview").status_code == 401


def test_report_examples_and_agent_runs_do_not_share_an_allowance():
    """
    They are separate products. Spending every report example must not also
    take away the agent runs.
    """
    wallet = fresh_wallet("SeparatePoolsWallet")
    for slug in list(Aetheron.EXAMPLE_SLUGS)[:ledger_utils.EXAMPLE_ALLOWANCE]:
        ledger_utils.claim_view(wallet, slug, "example")

    assert ledger_utils.claim_view(wallet, "one-more-report", "example")["allowed"] is False
    assert ledger_utils.claim_view(wallet, "wallet-watcher", "preview")["allowed"] is True


def test_an_unpreviewable_agent_is_refused_cleanly():
    import agent_preview
    result = agent_preview.run("discord-helper", seconds=5)
    assert result["ok"] is False
    assert "no live preview" in result["reason"]


def test_every_previewable_agent_has_a_button():
    """
    The preview function and its modal shipped without anything calling them,
    so the feature was unreachable dead code on a page that looked finished.
    """
    import re
    import agent_preview
    from fastapi.testclient import TestClient
    import Aetheron

    html = TestClient(Aetheron.app).get("/agents").text
    wired = set(re.findall(r"previewAgent\('([a-z-]+)'\)", html))
    assert wired == agent_preview.PREVIEWABLE, agent_preview.PREVIEWABLE ^ wired


def test_agents_without_a_preview_have_no_button():
    """A button that always errors is worse than no button."""
    import re
    from fastapi.testclient import TestClient
    import Aetheron
    html = TestClient(Aetheron.app).get("/agents").text
    wired = set(re.findall(r"previewAgent\('([a-z-]+)'\)", html))
    # Only the Discord helper cannot be previewed: it needs a bot token, so
    # there is nothing to show without one.
    assert "discord-helper" not in wired


def test_no_component_calls_a_retired_endpoint():
    """
    Endpoints rot silently. Birdeye retired every /public route and answers
    404, and api.solscan.io stopped resolving, so a holder lookup paid a full
    timeout waiting for a host that was gone before falling through to one
    that answers.
    """
    import os
    RETIRED = ("birdeye.so/public", "api.solscan.io", "quote-api.jup.ag")

    for name in sorted(f for f in os.listdir(".") if f.endswith(".py")):
        for line in open(name, errors="replace").read().splitlines():
            if line.strip().startswith("#"):
                continue                      # a comment may explain a removal
            for dead in RETIRED:
                assert dead not in line, f"{name} still calls {dead}"


def test_birdeye_calls_declare_their_chain():
    """The /defi routes pick a network from the header, not the address."""
    source = open("celery_worker.py", errors="replace").read()
    for block in source.split("public-api.birdeye.so")[1:]:
        window = block[:600]
        assert "x-chain" in window or "headers=headers" in window, \
            "a Birdeye call is missing its chain header"


def test_every_provider_can_die_without_crashing_a_paid_report():
    """
    A report someone has already paid for must not be lost because a third
    party is having a bad afternoon. Every fetcher returns a dict describing
    what it could not get, and the renderer says so, rather than raising.
    """
    import importlib
    import requests

    original_get, original_post = requests.get, requests.post

    def dead(*args, **kwargs):
        raise requests.exceptions.ConnectionError("simulated provider outage")

    requests.get = requests.post = dead
    try:
        worker = importlib.import_module("celery_worker")
        for call in (
            lambda: worker.fetch_solana_account_info("So11111111111111111111111111111111111111112"),
            lambda: worker.fetch_birdeye_full("So11111111111111111111111111111111111111112"),
            lambda: worker.fetch_honeypot_analysis("0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE", 1),
            lambda: worker.fetch_etherscan_contract_intel("0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE"),
            lambda: worker.fetch_top_erc20_holders("0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE"),
            lambda: worker.fetch_market_data_dexscreener("So11111111111111111111111111111111111111112"),
        ):
            assert isinstance(call(), dict)
    finally:
        requests.get, requests.post = original_get, original_post


def test_a_report_with_no_data_at_all_still_scores_and_renders():
    """Nothing available is a reportable state, not a failure."""
    import contract_report as cr

    blob = {"network": "solana", "contract_address": "X", "base_intel": {},
            "token_metadata": {}, "risk_hints": {}, "signal_indicators": {}}

    scores = cr.score(blob)
    assert all(1 <= v <= 10 for v in scores.values())
    # With nothing to go on, completeness must bottom out rather than flatter.
    assert scores["data_completeness"] <= 2

    assert "unavailable" in cr.holder_table(blob).lower()
    assert "NOT CHECKED" in cr.coverage(blob)


def test_examples_are_metered_per_wallet_across_the_shop():
    """
    The allowance is shared across every component rather than granted per
    component, so choosing which report to open is a real decision.
    """
    import ledger_utils
    from fastapi.testclient import TestClient
    import Aetheron

    client = TestClient(Aetheron.app)
    wallet = {"X-USER-WALLET": "MeteringTestWallet1"}

    allowed = []
    for slug in ("risk-engine", "prompt-optimizer", "code-explainer",
                 "contract-intel", "prompt-tester"):
        response = client.get(f"/api/examples/{slug}", headers=wallet)
        if response.status_code == 200:
            allowed.append(slug)
            assert len(response.json()["report"]) > 2000, slug
        else:
            assert response.status_code == 429

    assert len(allowed) == ledger_utils.EXAMPLE_ALLOWANCE

    # Reopening one already chosen is free: the limit is on how many different
    # reports a wallet sees, not on how often it reads them.
    again = client.get(f"/api/examples/{allowed[0]}", headers=wallet)
    assert again.status_code == 200
    assert again.json()["already_seen"] is True

    # The allowance is per wallet, not global.
    other = client.get("/api/examples/contract-intel",
                       headers={"X-USER-WALLET": "MeteringTestWallet2"})
    assert other.status_code == 200


def test_an_example_needs_a_connected_wallet():
    """Without one there is nothing to meter against."""
    from fastapi.testclient import TestClient
    import Aetheron
    assert TestClient(Aetheron.app).get("/api/examples/risk-engine").status_code == 401


def test_every_paid_component_offers_an_example():
    """
    An agent can be watched running for nothing. A component costs money and
    showed nothing until after payment, which is the same problem without the
    answer.
    """
    import re
    from fastapi.testclient import TestClient
    import Aetheron

    html = TestClient(Aetheron.app).get("/shop").text
    wired = set(re.findall(r'example-btn" data-slug="([a-z-]+)"', html))
    assert wired == {"prompt-optimizer", "code-explainer", "prompt-tester",
                     "contract-intel", "risk-engine"}

    # In the component's own modal, not crowding the price on the card.
    cards = html[html.index("components-grid"):html.index("my-assets-section")]
    assert "example-btn" not in cards


def test_the_dev_preview_harness_is_gone():
    """
    The harness ran components on a sample input without payment, gated only by
    DEV_TOKEN. It was always meant to come out before the source went public,
    because publishing documents the route and its gate for everyone. This
    keeps it out.
    """
    import os

    assert not os.path.exists("dev_preview.py")

    for path in ("Aetheron.py", "templates/shop.html", ".env.example"):
        source = open(path).read()
        assert "dev_preview" not in source, path
        assert "DEV_TOKEN" not in source, path


def test_the_examples_outlived_the_harness():
    """
    The example viewer was first written inside the dev-only block, so it would
    have been deleted along with it. It has to stand on its own.
    """
    source = open("templates/shop.html").read()
    assert "example-btn" in source
    assert 'id="example-bg"' in source


# ── the token page ──────────────────────────────────────────────────────────

def test_the_token_page_shows_no_address_until_a_mint_exists():
    """
    This is the page somebody checks before buying. Before the mint exists it
    must show nothing that could be read as an address: no placeholder, no
    example, no truncated stand-in. The panel is empty or it is the real thing.
    """
    from fastapi.testclient import TestClient
    html = TestClient(Aetheron.app).get("/token").text

    assert "not yet issued" in html
    assert "Not launched" in html
    # No explorer deep links can exist without a mint to link to.
    assert "solscan.io/token/" not in html
    assert "pump.fun/coin/" not in html
    # Nothing base58 shaped long enough to be mistaken for a Solana address.
    import re
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    for candidate in re.findall(r"[1-9A-HJ-NP-Za-km-z]{32,44}", body):
        assert False, f"address shaped string on an unlaunched token page: {candidate}"


def test_the_token_page_is_reachable_from_the_header():
    from fastapi.testclient import TestClient
    html = TestClient(Aetheron.app).get("/shop").text
    # Desktop nav and mobile nav both.
    assert html.count('href="/token"') >= 2


# ── social preview metadata ─────────────────────────────────────────────────

def test_every_shareable_page_previews_as_a_card():
    """
    Without these, a posted link renders as a bare URL. The home page is the
    one that gets pasted most and it does not extend base.html, so it needs its
    own set and was the only page missing them.
    """
    from fastapi.testclient import TestClient
    client = TestClient(Aetheron.app)

    for path in ("/", "/shop", "/token", "/roadmap", "/agents"):
        html = client.get(path).text
        for tag in ('property="og:title"', 'property="og:image"',
                    'property="og:url"', 'name="twitter:card"'):
            assert tag in html, f"{path} is missing {tag}"
        # Scrapers reject a relative og:image, so it has to be absolute.
        assert 'property="og:image" content="http' in html, path


def test_page_titles_are_not_all_the_same_card():
    """A shared component link and a shared token link should not read alike."""
    from fastapi.testclient import TestClient
    import re
    client = TestClient(Aetheron.app)

    def og_title(path):
        html = client.get(path).text
        return re.search(r'property="og:title" content="([^"]*)"', html).group(1)

    assert og_title("/token") != og_title("/shop")
    assert "AETH" in og_title("/token")


def test_documented_curl_examples_are_https():
    """
    Railway terminates TLS at its proxy, so request.base_url reports http even
    though the site is served over https. Rendered straight into the docs, that
    told every reader to post their payload, and later their payment signature,
    to a plaintext URL.
    """
    from fastapi.testclient import TestClient
    html = TestClient(Aetheron.app, base_url="https://aetheronprotocol.com").get("/docs").text
    assert "curl -sX POST https://" in html
    assert "curl -sX POST http://" not in html


def test_every_modal_can_actually_be_closed():
    """
    The close buttons were drawn with a multiplication glyph, which an emoji
    sweep treated as decoration and stripped, leaving five empty buttons and no
    way out of a purchase modal but reloading the page.
    """
    import re
    for path in ("templates/shop.html", "templates/agents.html"):
        source = open(path).read()
        assert "></button>" not in source, f"{path} has an empty button"
        for handler in set(re.findall(r'onclick="(close\w+)\(\)"', source)):
            assert f"function {handler}(" in source, f"{path}: {handler} is not defined"


# ── the legacy holder discount ──────────────────────────────────────────────

def _legacy_wallet():
    import legacy_holders
    legacy_holders.load({"LegacyTestWallet1111111111111111111111111": 0.0})
    return "LegacyTestWallet1111111111111111111111111"


def test_the_quote_and_the_settlement_agree_on_the_discounted_price():
    """
    The whole risk in a per wallet price is the two halves disagreeing. Quote
    high and settle low and anyone pays half; quote low and settle high and an
    eligible buyer's payment is rejected as short. Both must come from the same
    function.
    """
    import inspect, legacy_holders, Aetheron as A

    wallet = _legacy_wallet()
    quoted = json.loads(A.payment_required("x", "y", 0.25, wallet).body)["required"]
    assert quoted == legacy_holders.price_for(wallet, 0.25)

    # And the settlement path prices through the same module, not its own copy.
    assert "pricing.effective_usd" in inspect.getsource(A.verify_payment)


def test_the_discount_is_applied_only_after_the_signer_is_proven():
    """
    Pricing against a wallet the caller merely named would let anybody claim a
    stranger's discount by putting their address in a header.
    """
    import inspect, Aetheron as A
    src = inspect.getsource(A.verify_payment)
    assert src.index("user_wallet not in signers") < src.index("pricing.effective_usd")


def test_an_ordinary_wallet_pays_full_price():
    import legacy_holders
    _legacy_wallet()
    for base in (0.25, 0.5, 4.99):
        assert legacy_holders.price_for("NotAHolderWallet", base) == base
        assert legacy_holders.price_for(None, base) == base
        assert legacy_holders.price_for("", base) == base


def test_the_discount_never_rounds_against_the_buyer_or_to_zero():
    """
    Rounding to nearest made 4.99 settle at 2.50, half a cent over half. And a
    price that floored to zero would make the expected amount zero, which the
    settlement check rejects, locking that component's buyers out entirely.
    """
    import legacy_holders
    wallet = _legacy_wallet()
    assert legacy_holders.price_for(wallet, 4.99) == 2.49
    assert legacy_holders.price_for(wallet, 0.25) == 0.12
    for base in (0.01, 0.02, 0.001):
        assert legacy_holders.price_for(wallet, base) >= 0.01


def test_eligibility_cannot_be_claimed_through_the_api():
    """
    The list comes from a snapshot taken before any of this was announced. No
    header, body or query a caller sends may add to it.
    """
    from fastapi.testclient import TestClient
    import legacy_holders

    _legacy_wallet()
    client = TestClient(Aetheron.app)
    stranger = "StrangerWallet99999999999999999999999999"

    for headers in (
        {"X-USER-WALLET": stranger},
        {"X-USER-WALLET": stranger, "X-LEGACY-HOLDER": "true"},
        {"X-USER-WALLET": stranger, "X-DISCOUNT": "0.5"},
    ):
        body = client.post("/api/prompt-optimizer", json={"text": "hi"}, headers=headers).json()
        assert body["required"] == 0.25, headers
        assert body["discount"] is None, headers

    assert stranger not in legacy_holders._eligible_set()


# ── the AETH fee tier, and quote/settlement agreement ───────────────────────

def test_the_aeth_fee_tier_stacks_under_the_legacy_discount():
    import pricing
    wallet = _legacy_wallet()

    assert pricing.effective_usd(1.00, "stranger", "USDC") == 1.00
    assert pricing.effective_usd(1.00, "stranger", "AETH") == 0.80   # 20% tier
    assert pricing.effective_usd(1.00, wallet, "USDC") == 0.50       # 50% legacy
    assert pricing.effective_usd(1.00, wallet, "AETH") == 0.40       # both


def test_settlement_prices_through_the_same_function_as_the_quote():
    """
    The failure mode of a per wallet price is these two disagreeing. Quote low
    and settle high and an eligible buyer is rejected as short; quote high and
    settle low and anybody underpays.
    """
    import inspect, Aetheron as A
    src = inspect.getsource(A.verify_payment)
    assert "pricing.effective_usd(price_usdc, user_wallet, payment_method)" in src
    # And the method has to reach it, or the AETH tier silently applies to USDC.
    assert "payment_method" in src.split("pricing.effective_usd")[1][:80]


def test_the_aeth_quote_is_priced_by_the_server_not_the_caller():
    """
    The endpoint took the price as a query parameter, so the browser decided
    what a component cost. Settlement priced independently, so nothing was
    stealable, but a discounted wallet was quoted the full amount, paid it, and
    the overpayment was accepted without comment.
    """
    source = open("templates/shop.html").read()
    assert "usdc_price=" not in source, "a client still sends its own price"
    assert source.count("/api/price/aeth?component=") == 5


def test_every_component_price_has_one_definition():
    """
    The shop had the prices written into it as literals as well. Two copies of
    a price is one drift away from quoting something the settlement check will
    reject.
    """
    import pricing, Aetheron as A
    assert pricing.list_price("prompt-optimizer") == float(A.PROMPT_OPTIMIZER_PRICE_USDC)
    assert pricing.list_price("code-explainer") == float(A.CODE_EXPLAINER_PRICE_USDC)
    assert pricing.list_price("prompt-tester") == float(A.PROMPT_TESTER_PRICE_USDC)
    assert pricing.list_price("contract-intel") == float(A.CONTRACT_INTEL_PRICE_USDC)
    assert pricing.list_price("risk-engine") == float(A.RISK_ENGINE_PRICE_USDC)
    assert pricing.list_price("agent") == float(A.AGENT_PRICE_USDC)


def test_a_discount_never_floors_to_zero_in_either_currency():
    import pricing
    wallet = _legacy_wallet()
    for base in (0.01, 0.02, 0.03):
        for method in ("USDC", "AETH"):
            assert pricing.effective_usd(base, wallet, method) >= 0.01


# ── burn on use ─────────────────────────────────────────────────────────────

def _tx(instructions, err=None, block_time=1700000000):
    return {"transaction": {"message": {"instructions": instructions}},
            "meta": {"err": err, "innerInstructions": []}, "blockTime": block_time}


def _burn_ix(mint, amount, kind="burnChecked"):
    return {"parsed": {"type": kind, "info": {"mint": mint, "amount": str(amount)}}}


def test_a_burn_is_only_counted_for_our_own_mint():
    """
    Somebody else's burn, or a burn of a different token in the same
    transaction, must not inflate our figure.
    """
    import burn_ledger
    burn_ledger.AETH_MINT = "OurMint111111111111111111111111111111111111"

    tx = _tx([_burn_ix("OurMint111111111111111111111111111111111111", 500),
              _burn_ix("SomeOtherMint2222222222222222222222222222222", 9_000_000)])
    assert burn_ledger._burned_in(tx) == 500


def test_a_transfer_is_not_a_burn():
    """
    Sending tokens to a dead address looks like a burn and is not one: the
    supply does not go down. Only a burn instruction counts.
    """
    import burn_ledger
    burn_ledger.AETH_MINT = "OurMint111111111111111111111111111111111111"

    transfer = {"parsed": {"type": "transfer", "info": {
        "mint": "OurMint111111111111111111111111111111111111", "amount": "1000"}}}
    assert burn_ledger._burned_in(_tx([transfer])) == 0


def test_inner_instructions_count_too():
    """A burn made through a program shows up nested, not at the top level."""
    import burn_ledger
    burn_ledger.AETH_MINT = "OurMint111111111111111111111111111111111111"

    tx = _tx([])
    tx["meta"]["innerInstructions"] = [
        {"instructions": [_burn_ix("OurMint111111111111111111111111111111111111", 250)]}]
    assert burn_ledger._burned_in(tx) == 250


def test_a_transaction_with_no_burn_is_refused_rather_than_recorded_as_zero():
    """
    A mistyped signature must fail loudly. Recording it as a burn of nothing
    would put a row in the public ledger that never happened.
    """
    import burn_ledger, pytest as pt
    burn_ledger.AETH_MINT = "OurMint111111111111111111111111111111111111"

    burn_ledger._rpc = lambda *a, **k: {"result": _tx([])}
    with pt.raises(burn_ledger.BurnVerificationError):
        burn_ledger.verify_burn("SomeSignature")


def test_a_failed_transaction_is_refused():
    import burn_ledger, pytest as pt
    burn_ledger.AETH_MINT = "OurMint111111111111111111111111111111111111"

    burn_ledger._rpc = lambda *a, **k: {"result": _tx(
        [_burn_ix("OurMint111111111111111111111111111111111111", 100)],
        err={"InstructionError": [0, "Custom"]})}
    with pt.raises(burn_ledger.BurnVerificationError):
        burn_ledger.verify_burn("FailedSignature")


def test_the_same_burn_cannot_be_counted_twice():
    import burn_ledger
    burn_ledger.AETH_MINT = "OurMint111111111111111111111111111111111111"
    burn_ledger._rpc = lambda *a, **k: {"result": _tx(
        [_burn_ix("OurMint111111111111111111111111111111111111", 1_000_000)])}

    import ledger_utils
    # Clear first and after, so this never leaves a burn that did not happen in
    # the ledger the site reads from.
    def wipe():
        with ledger_utils._cursor(commit=True) as cur:
            cur.execute(ledger_utils._q(
                "DELETE FROM burns WHERE tx_signature = %s;"), ("DoubleCountSig",))

    burn_ledger.init_burns()
    wipe()
    try:
        first = burn_ledger.record_burn("DoubleCountSig")
        second = burn_ledger.record_burn("DoubleCountSig")
        assert first["already_recorded"] is False
        assert second["already_recorded"] is True
        assert burn_ledger.summary()["burns"] >= 1
    finally:
        wipe()


def test_the_server_still_cannot_sign_anything():
    """
    The burn is accounted for here and signed elsewhere. A key that can burn
    can also transfer, so one in the web process would put every payment ever
    received behind whatever protects this container.
    """
    import burn_ledger, inspect
    src = inspect.getsource(burn_ledger)
    for forbidden in ("Keypair", "from_secret_key", "sign_message",
                      "PRIVATE_KEY", "SECRET_KEY", "sendTransaction"):
        assert forbidden not in src, f"burn_ledger references {forbidden}"


def test_the_parser_matches_a_real_solana_burn():
    """
    Every other burn test builds its own transaction dict, which only proves the
    parser agrees with my idea of the format. This is the exact shape Solana
    returned for a real burn, signature 2Ue9TjPWTLDZ..., taken from mainnet:
    type "burn" rather than "burnChecked", and the amount as a string under
    info, not under a tokenAmount object.
    """
    import burn_ledger
    burn_ledger.AETH_MINT = "DGNicx6qMPKSL1deR3fZfbHYjnm5ZJWmHNdY2NhDpump"

    real = {
        "transaction": {"message": {"instructions": [{
            "parsed": {
                "type": "burn",
                "info": {
                    "account": "6Y5TWyRJin9bCohmShtLynWGY9Ba7Ub3DoifeJVVR3LF",
                    "amount": "2",
                    "authority": "AS6akYpZQydPnhrphuEkzAajBLYmkByHcKcMuLHfbEg8",
                    "mint": "DGNicx6qMPKSL1deR3fZfbHYjnm5ZJWmHNdY2NhDpump",
                },
            }}]}},
        "meta": {"err": None, "innerInstructions": []},
        "blockTime": 1786645337,
    }
    assert burn_ledger._burned_in(real) == 2


def test_the_token_page_survives_having_no_mint_configured():
    """
    The burn section slices the mint address to build a Solscan link. With no
    mint set that value is None, and the slice took the whole page down with a
    TypeError rather than just hiding a link.
    """
    from fastapi.testclient import TestClient
    client = TestClient(Aetheron.app)
    response = client.get("/token")
    assert response.status_code == 200
    # And the burn panel is still there, saying nothing has been burned.
    assert "No burns yet" in response.text or "Burned so far" in response.text


# ── locked AETH quotes ──────────────────────────────────────────────────────

def test_a_correct_aeth_payment_survives_the_price_moving():
    """
    The bug a real buyer hit. They were quoted an amount, sent exactly that,
    and settlement recomputed the requirement against a fresh rate. AETH is on
    a bonding curve so the rate had moved, the new requirement was higher, and
    their correct payment was rejected as short. They had paid and got nothing.
    """
    import aeth_quotes, aeth_price, Aetheron as A

    wallet = "QuotedBuyerWallet11111111111111111111111"
    aeth_quotes.init_quotes()
    aeth_quotes.clear(wallet, "prompt-optimizer")

    # Quoted while AETH is worth 0.0000170 each: 0.20 buys 11,764.7 of them.
    quoted_raw = 11_764_705_882
    aeth_quotes.record(wallet, "prompt-optimizer", quoted_raw, 0.20)

    # The price then moves. Whatever it moves to, the promise stands.
    assert aeth_quotes.live(wallet, "prompt-optimizer") == quoted_raw

    # And settlement reads the promise rather than recomputing.
    import inspect
    src = inspect.getsource(A.verify_payment)
    assert "aeth_quotes.live(user_wallet, component)" in src
    assert src.index("aeth_quotes.live") < src.index("calculate_required_aeth")
    aeth_quotes.clear(wallet, "prompt-optimizer")


def test_a_quote_expires_so_a_stale_rate_cannot_be_farmed():
    """
    Holding the promise forever would let somebody quote during a dip and use
    it whenever it became worth using.
    """
    import aeth_quotes, time, ledger_utils

    wallet = "StaleQuoteWallet111111111111111111111111"
    aeth_quotes.init_quotes()
    aeth_quotes.record(wallet, "risk-engine", 5_000_000, 0.60)
    assert aeth_quotes.live(wallet, "risk-engine") == 5_000_000

    # Age it past the window.
    with ledger_utils._cursor(commit=True) as cur:
        cur.execute(ledger_utils._q(
            "UPDATE aeth_quotes SET issued_at = %s WHERE wallet = %s;"),
            (time.time() - aeth_quotes.QUOTE_TTL_SECONDS - 10, wallet))

    assert aeth_quotes.live(wallet, "risk-engine") is None
    aeth_quotes.clear(wallet, "risk-engine")


def test_a_quote_is_spent_once_it_settles():
    """Otherwise one quote could cover a second purchase at an old rate."""
    import aeth_quotes
    wallet = "SpentQuoteWallet111111111111111111111111"
    aeth_quotes.record(wallet, "code-explainer", 9_000_000, 0.40)
    assert aeth_quotes.live(wallet, "code-explainer") == 9_000_000
    aeth_quotes.clear(wallet, "code-explainer")
    assert aeth_quotes.live(wallet, "code-explainer") is None


def test_a_quote_is_keyed_to_the_wallet_that_was_quoted():
    """One person's quote must not price another person's payment."""
    import aeth_quotes
    mine = "MyQuoteWallet1111111111111111111111111111"
    yours = "YourQuoteWallet22222222222222222222222222"
    aeth_quotes.record(mine, "contract-intel", 1_234_000, 0.80)
    assert aeth_quotes.live(yours, "contract-intel") is None
    assert aeth_quotes.live(None, "contract-intel") is None
    aeth_quotes.clear(mine, "contract-intel")


def test_the_eligible_set_refreshes_without_a_restart():
    """
    The set was cached for the life of the process, on the assumption that
    loading a snapshot meant a deploy. It did not: the wallets were loaded into
    a running production deployment and every eligible buyer carried on paying
    full price, because the process had already cached an empty table.
    """
    import legacy_holders, time

    legacy_holders.load({"CacheRefreshWallet111111111111111111111": 0.0})
    assert legacy_holders.is_legacy_holder("CacheRefreshWallet111111111111111111111")

    # Add one behind the cache's back, the way the loader script does.
    ledger = __import__("ledger_utils")
    with ledger._cursor(commit=True) as cur:
        cur.execute(ledger._q(
            "INSERT INTO legacy_holders (wallet, first_held_at) VALUES (%s, %s);"),
            ("AddedBehindTheCache1111111111111111111", 0.0))

    # Not visible yet, which is the cache doing its job.
    assert not legacy_holders.is_legacy_holder("AddedBehindTheCache1111111111111111111")

    # Age the cache past its window rather than sleeping through it.
    legacy_holders._cached_at = time.time() - legacy_holders.CACHE_TTL_SECONDS - 1
    assert legacy_holders.is_legacy_holder("AddedBehindTheCache1111111111111111111")


def test_a_database_blip_does_not_start_charging_eligible_buyers_full_price():
    """Losing the connection should hold the last known set, not empty it."""
    import legacy_holders, ledger_utils, time

    legacy_holders.load({"BlipWallet11111111111111111111111111111": 0.0})
    assert legacy_holders.is_legacy_holder("BlipWallet11111111111111111111111111111")

    original = ledger_utils._cursor
    legacy_holders._cached_at = time.time() - legacy_holders.CACHE_TTL_SECONDS - 1
    def broken(*a, **k):
        raise RuntimeError("database unavailable")
    ledger_utils._cursor = broken
    try:
        assert legacy_holders.is_legacy_holder("BlipWallet11111111111111111111111111111")
    finally:
        ledger_utils._cursor = original
