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
            assumptions=["a"], parameter_notes=["p"]), "Parameters:\n• Runs: 10"),
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
