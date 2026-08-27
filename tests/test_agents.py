"""
Tests for the store agents themselves.

Every defect found in these agents was found by running one by hand: a
truncated entry point, retired API endpoints, a trend guard that wanted two
candles and read one, positions that were never closed, invented transaction
ids. None of it would have survived a test, and none of it was covered by one,
because the suite only tested the shop around the agents.

These run against the agent source directly. Anything needing the network is
fed a recorded payload rather than reaching out, so the suite stays fast and
does not fail because an exchange is having a bad afternoon.
"""

import importlib
import json
import logging
import os
import sys

import pytest

AGENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "static", "agents_src")
LOG = logging.getLogger("test")


_LOADED = None
PACKAGES = ("utils", "core", "signals", "adapters", "schemas", "modules", "risk")


def load(agent, *modules):
    """
    Import one or more modules from inside an agent.

    Loading them together matters: popping between calls left a module and its
    dependency as two separate instances, so patching one had no effect on the
    other and the test silently measured the wrong object.
    """
    global _LOADED
    root = os.path.join(AGENTS, agent)

    if _LOADED != agent:
        for name in [m for m in sys.modules if m.split(".")[0] in PACKAGES]:
            sys.modules.pop(name, None)
        _LOADED = agent

    sys.path.insert(0, root)
    try:
        loaded = tuple(importlib.import_module(m) for m in modules)
    finally:
        sys.path.remove(root)
    return loaded[0] if len(loaded) == 1 else loaded


def config_of(agent):
    with open(os.path.join(AGENTS, agent, "config.json")) as handle:
        return json.load(handle)


# ── every agent must be startable ───────────────────────────────────────────

@pytest.mark.parametrize("agent", sorted(os.listdir(AGENTS)))
def test_entrypoint_is_complete(agent):
    """
    The trading assistant's main.py stopped partway through its entry point:
    it loaded config, built a logger and exited zero without starting the
    agent, which looks exactly like software that is broken.
    """
    path = os.path.join(AGENTS, agent, "main.py")
    if not os.path.exists(path):
        pytest.skip(f"{agent} has no main.py")
    source = open(path).read()

    assert 'if __name__ == "__main__":' in source, agent
    tail = source.split('if __name__ == "__main__":')[1]
    # The block has to do more than build objects: something must be invoked.
    assert any(call in tail or call in source for call in
               ("main()", ".run()", ".start()", "asyncio.run")), agent


@pytest.mark.parametrize("agent", sorted(os.listdir(AGENTS)))
def test_no_fabricated_values_ship(agent):
    """
    pumpfun-launcher printed a transaction id from a function called
    fake_txid, in a block headed TRADE EXECUTED. A transaction id is the one
    field a reader would check, and it resolved to nothing.
    """
    banned = ("fake_txid", "def fake_", "random_txid")
    for root, dirs, files in os.walk(os.path.join(AGENTS, agent)):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".venv")]
        for name in files:
            if not name.endswith(".py"):
                continue
            source = open(os.path.join(root, name), errors="replace").read()
            for token in banned:
                assert token not in source, f"{agent}/{name} contains {token}"


# ── wallet-watcher: real balances ───────────────────────────────────────────

def test_wallet_watcher_reads_real_amounts():
    """
    Every alert used to report a hardcoded 1.23 TOKEN whatever moved.
    """
    rpc = load("wallet-watcher", "utils.rpc")
    client = rpc.WalletWatcherClient(
        {"url": "http://unused", "timeout_seconds": 1}, LOG)

    wallet = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
    client.rpc_post = lambda method, params: {"result": {
        "meta": {
            "preTokenBalances": [{"accountIndex": 3, "owner": wallet,
                                  "mint": "MINT", "uiTokenAmount": {"uiAmount": 10.0}}],
            "postTokenBalances": [{"accountIndex": 3, "owner": wallet,
                                   "mint": "MINT", "uiTokenAmount": {"uiAmount": 42.5}}],
            "preBalances": [0], "postBalances": [0],
        },
        "transaction": {"message": {"accountKeys": [{"pubkey": wallet}]}},
    }}

    events = client.describe_transaction("SIG", wallet)
    assert len(events) == 1
    assert events[0]["amount"] == pytest.approx(32.5)
    assert events[0]["incoming"] is True
    assert events[0]["token_symbol"] == "MINT"


def test_wallet_watcher_ignores_other_peoples_balances():
    """A change to somebody else's account must not be reported as yours."""
    rpc = load("wallet-watcher", "utils.rpc")
    client = rpc.WalletWatcherClient({"url": "http://unused", "timeout_seconds": 1}, LOG)

    client.rpc_post = lambda method, params: {"result": {
        "meta": {
            "preTokenBalances": [],
            "postTokenBalances": [{"accountIndex": 1, "owner": "SOMEONE_ELSE",
                                   "mint": "M", "uiTokenAmount": {"uiAmount": 99.0}}],
            "preBalances": [0], "postBalances": [0],
        },
        "transaction": {"message": {"accountKeys": [{"pubkey": "ME"}]}},
    }}
    assert client.describe_transaction("SIG", "ME") == []


def test_wallet_watcher_known_mints_resolve_and_others_do_not():
    helpers = load("wallet-watcher", "utils.helpers")
    usdc = helpers.format_event({
        "type": "token_transfer", "wallet": "W" * 40, "incoming": True,
        "amount": 1.5, "token_symbol": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"})
    assert "USDC" in usdc

    unknown = helpers.format_event({
        "type": "token_transfer", "wallet": "W" * 40, "incoming": True,
        "amount": 1.5, "token_symbol": "Zq" + "x" * 40})
    # Truncated, never renamed into a ticker it does not have.
    assert "..." in unknown


# ── market-tracker: missing data is reported, not filled in ─────────────────

def test_market_tracker_reports_a_missing_input():
    data, modules = load("market-tracker", "utils.data", "modules.risk")
    data._CACHE.update({"at": 9e9, "data": {"values": {}, "sources": [], "missing": ["all"]}})
    result = modules.run({})
    assert result["state"] == "unknown"
    assert result["confidence"] == 0.0
    assert "not measured" in " ".join(result["notes"]).lower()


def test_market_tracker_scores_when_data_is_present():
    data, modules = load("market-tracker", "utils.data", "modules.risk")
    data._CACHE.update({"at": 9e9, "data": {
        "values": {"crypto_momentum": 0.8, "crypto_moves_pct": [8.0, 7.5, 9.0]},
        "sources": ["test"], "missing": []}})
    result = modules.run({})
    assert result["state"] == "positive"
    assert result["score"] > 0
    # Equities have no keyless source, so the note has to say so.
    assert any("equity" in n.lower() for n in result["notes"])


# ── alpha-scanner: narratives age out ───────────────────────────────────────

def test_alpha_scanner_narratives_decay_and_are_dropped():
    """
    utils/decay.py shipped a correct half-life function that was never called,
    and freshness was computed against a timestamp set on the line above, so it
    was always exactly 1.0 and nothing ever aged.
    """
    from datetime import datetime, timedelta, timezone
    narratives = load("alpha-scanner", "core.narratives")
    signal_mod = load("alpha-scanner", "schemas.signal")

    engine = narratives.NarrativeEngine()
    now = datetime.now(timezone.utc)
    sig = signal_mod.Signal(source=signal_mod.SignalSource.MARKET, key="TOK",
                            value=0.8, confidence=0.9, timestamp=now)

    engine.update([sig], now)
    first = engine.get_active()[0].strength

    later = now + timedelta(seconds=narratives.HALF_LIFE_SECONDS)
    engine.update([], later)
    assert engine.get_active()[0].strength == pytest.approx(first / 2, rel=0.02)

    engine.update([], now + timedelta(seconds=narratives.HALF_LIFE_SECONDS * 12))
    assert engine.get_active() == []


def test_alpha_scanner_momentum_reads_direction():
    """The mean of every signal ever seen is a level, not momentum."""
    from datetime import datetime, timezone
    narratives = load("alpha-scanner", "core.narratives")
    signal_mod = load("alpha-scanner", "schemas.signal")
    now = datetime.now(timezone.utc)

    def run(values):
        engine = narratives.NarrativeEngine()
        for v in values:
            engine.update([signal_mod.Signal(source=signal_mod.SignalSource.MARKET,
                                             key="T", value=v, confidence=0.9,
                                             timestamp=now)], now)
        return engine.get_active()[0].momentum

    assert run([0.1, 0.1, 0.1, 0.8, 0.9, 0.9]) > 0
    assert run([0.9, 0.9, 0.8, 0.1, 0.1, 0.1]) < 0


def test_alpha_scanner_social_emits_nothing_rather_than_inventing():
    from datetime import datetime, timezone
    social = load("alpha-scanner", "signals.social")
    assert social.SocialSignalGenerator().generate(datetime.now(timezone.utc)) == []


# ── solana-sniper: the loop keeps working ───────────────────────────────────

def test_sniper_positions_are_released_and_capped():
    """
    open_positions was appended to and never cleared, with a default cap of
    two, so the agent stopped acting after its second token.
    """
    import time
    sniper = load("solana-sniper", "main")

    cfg = config_of("solana-sniper")
    cfg["wallet"]["max_open_positions"] = 2
    cfg["sniper"]["max_hold_seconds"] = 0
    agent = sniper.SniperAgent(cfg, LOG)

    for i in range(5):
        agent.execute_buy({"mint": f"M{i}", "price": 0.01})
    assert len(agent.open_positions) == 2, "the cap must hold"

    time.sleep(0.05)
    agent.review_positions()
    assert agent.open_positions == [], "held positions must be released"

    agent.execute_buy({"mint": "M9", "price": 0.01})
    assert len(agent.open_positions) == 1, "the loop must keep working"


# ── trading assistant: works without a key ──────────────────────────────────

def test_trading_assistant_trend_reads_a_single_candle():
    """
    The guard wanted two candles and the calculation reads one, so any source
    serving a percentage move rather than a series was silently zeroed.
    """
    helpers = load("solana-trading-assistant", "utils.helpers")

    class Stub:
        def get_token_price(self, mint): return {"price": 100.0}
        def get_token_liquidity(self, mint): return {"liquidity": 1_000_000.0}
        def get_token_volume(self, mint): return {"volume_24h": 500_000.0}
        def get_token_candles(self, mint, timeframes):
            return {tf: [{"o": 100.0, "h": 110.0, "l": 99.0, "c": 110.0, "v": 1.0}]
                    for tf in timeframes}

    result = helpers.analyze_token("MINT", Stub(), None,
                                   {"timeframes": ["5m"], "thresholds": {}}, LOG)
    assert result["trend"]["5m"] == pytest.approx(10.0, rel=0.01)


def test_trading_assistant_uses_a_keyless_source_by_default():
    """Birdeye retired the endpoints this was written against and needs a key."""
    cfg = config_of("solana-trading-assistant")
    key = (cfg.get("birdeye", {}).get("api_key") or "")
    assert not key or key.upper().startswith("YOUR_")

    rpc = load("solana-trading-assistant", "utils.rpc")
    assert hasattr(rpc, "DexScreenerClient")
    for method in ("get_token_price", "get_token_liquidity",
                   "get_token_volume", "get_token_candles"):
        assert hasattr(rpc.DexScreenerClient, method), method


# ── discord helper: startable without optional providers ────────────────────

def test_discord_helper_does_not_hard_import_a_provider():
    """
    utils/ai.py imported the OpenAI client at module load, so the bot could not
    start without that package even with AI switched off, which is the default.
    """
    source = open(os.path.join(AGENTS, "discord-helper", "utils", "ai.py")).read()
    header = source.split("def ")[0]
    assert "from openai import" not in header
    assert "import openai" not in header


def test_discord_helper_defaults_to_claude():
    cfg = config_of("discord-helper")
    assert cfg["ai"]["provider"] == "anthropic"
    assert "claude" in cfg["ai"]["model"]
