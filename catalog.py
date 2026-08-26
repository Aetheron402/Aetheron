"""
The catalogue: every component and agent Aetheron sells, defined once.

Before this module the same list lived in five places, the shop route, the
agents API, the agents page, the docs and the status page, and they had already
drifted. Adding a component meant remembering all five. Everything now reads
from here, so a change lands everywhere at once.

Prices come from the environment, so the Railway variables remain the only
place a price is set.
"""

import os

CURRENCY = "USDC"


def _price(env_name: str, default: str) -> str:
    return os.getenv(env_name, default)


# Output formats every component can render into.
ALL_FORMATS = ["pdf", "docx", "html", "md", "txt"]


COMPONENTS = [
    {
        "id": 1,
        "slug": "prompt-optimizer",
        "name": "Prompt Optimizer",
        "tagline": "Rough notes into a prompt that works",
        "description": (
            "Turns unstructured ideas into a polished prompt with explicit goals, "
            "context and formatting, ready to hand to an agent."
        ),
        "category": "Prompting",
        "price_env": "PRICE_PROMPT_OPTIMIZER",
        "price_default": "0.25",
        "action": "Optimise",
        "modal": "modal-bg",
        "on_open": "updatePromptPayAmount",
        "input": "Any prompt or rough notes",
        "returns": "A rewritten prompt with reasoning",
        "formats": ALL_FORMATS,
        "depends_on": ["ai", "workers"],
        "needs": "OpenAI",
        "glyph": "◇",
    },
    {
        "id": 2,
        "slug": "code-explainer",
        "name": "Code Explainer",
        "tagline": "Read any file, fast",
        "description": (
            "Explains a snippet or a whole file in plain language, rates its "
            "complexity and proposes concrete refactors."
        ),
        "category": "Development",
        "price_env": "PRICE_CODE_EXPLAINER",
        "price_default": "0.50",
        "action": "Explain",
        "modal": "modal-code-bg",
        "on_open": "updateCodePayAmount",
        "input": "A code snippet or file",
        "returns": "Explanation, complexity rating, refactors",
        "formats": ALL_FORMATS,
        "depends_on": ["ai", "workers"],
        "needs": "OpenAI",
        "glyph": "❯",
    },
    {
        "id": 3,
        "slug": "prompt-tester",
        "name": "Prompt Tester",
        "tagline": "Find the holes before your users do",
        "description": (
            "Runs one prompt past several personas, developer, sceptic and "
            "adversary, and reports how each of them reacts."
        ),
        "category": "Prompting",
        "price_env": "PRICE_PROMPT_TESTER",
        "price_default": "0.50",
        "action": "Run test",
        "modal": "modal-tester-bg",
        "on_open": "updateTesterPayAmount",
        "input": "A prompt to stress test",
        "returns": "Per-persona reactions and weak points",
        "formats": ALL_FORMATS,
        "depends_on": ["ai", "workers"],
        "needs": "OpenAI",
        "glyph": "◈",
    },
    {
        "id": 4,
        "slug": "risk-engine",
        "name": "Risk & Simulation Engine",
        "tagline": "Monte Carlo, charted",
        "description": (
            "Runs a geometric Brownian motion simulation over your parameters and "
            "returns charted price paths with an outcome distribution."
        ),
        "category": "Markets",
        "price_env": "PRICE_RISK_ENGINE",
        "price_default": "0.75",
        "action": "Simulate",
        "modal": "modal-risk-bg",
        "on_open": "updateRiskPayAmount",
        "input": "Runs, steps, drift, volatility, start price",
        "returns": "Charted paths and a distribution",
        "formats": ALL_FORMATS,
        "depends_on": ["ai", "workers"],
        "needs": "OpenAI, matplotlib",
        "glyph": "◧",
    },
    {
        "id": 5,
        "slug": "contract-intel",
        "name": "Contract Intelligence",
        "tagline": "Read a token before you touch it",
        "description": (
            "Holder concentration, LP lock state, admin powers and honeypot checks "
            "for any Solana or Ethereum token."
        ),
        "category": "Markets",
        "price_env": "PRICE_CONTRACT_INTEL",
        "price_default": "1.00",
        "action": "Analyse",
        "modal": "modal-contract-bg",
        "on_open": "updateContractPayAmount",
        "input": "A Solana or Ethereum address",
        "returns": "Holders, LP lock, admin risk, honeypot",
        "formats": ALL_FORMATS,
        "depends_on": ["ai", "workers", "solana"],
        "needs": "OpenAI, chain data",
        "glyph": "◉",
    },
]


AGENTS = [
    {
        "id": "solana-sniper",
        "title": "Solana Sniper Bot",
        "description": "Snipes new Pump.fun tokens instantly with adjustable timing, filters, and blacklist protection.",
        "category": "Trading",
        "tags": ["Solana", "Pump.fun", "Realtime"],
    },
    {
        "id": "wallet-watcher",
        "title": "Wallet Watcher",
        "description": "Tracks any wallet in real time and alerts on buys, sells, transfers, approvals and liquidity changes.",
        "category": "Monitoring",
        "tags": ["Solana", "Alerts", "Webhooks"],
    },
    {
        "id": "discord-helper",
        "title": "Discord AI Helper",
        "description": "A customisable AI bot for Discord: moderation, auto-replies, chat, commands and wallet verification.",
        "category": "Community",
        "tags": ["Discord", "OpenAI", "Moderation"],
    },
    {
        "id": "pumpfun-launcher",
        "title": "Pump.fun Launch Assistant",
        "description": "Monitors new Pump.fun launches, liquidity events and early momentum signals.",
        "category": "Trading",
        "tags": ["Solana", "Pump.fun", "Signals"],
    },
    {
        "id": "solana-trading-assistant",
        "title": "Solana Trading Assistant",
        "description": "Analyses Solana tokens, identifies trends and volume shifts, and supports trading decisions.",
        "category": "Trading",
        "tags": ["Solana", "Analysis", "Signals"],
    },
    {
        "id": "market-tracker",
        "title": "Market Tracker",
        "description": "Tracks market regimes using risk, volatility, liquidity, correlation and psychology signals.",
        "category": "Markets",
        "tags": ["Regimes", "Volatility", "Modular"],
    },
    {
        "id": "prediction-market",
        "title": "Prediction Market Agent",
        "description": "Analyses prediction markets, implied probabilities, sentiment and mispricing opportunities.",
        "category": "Markets",
        "tags": ["Probabilities", "Adapters", "Sizing"],
    },
    {
        "id": "alpha-scanner",
        "title": "Alpha Scanner",
        "description": "Scans social, on-chain and market signals to surface emerging narratives and ranked opportunities.",
        "category": "Research",
        "tags": ["Signals", "Ranking", "On-chain"],
    },
    {
        "id": "project-planner",
        "title": "Project Planner",
        "description": "A modular coordination framework for tasks, notes, milestones, reminders and structured summaries.",
        "category": "Productivity",
        "tags": ["Tasks", "Milestones", "Reminders"],
    },
]

# The agent bundles on disk. Kept here so the download route and the storefront
# cannot disagree about which agents exist.
AGENT_SOURCE_ROOT = "static/agents_src"


def agent_price() -> str:
    return _price("PRICE_AGENT", "4.99")


def components():
    """Components with their prices resolved from the environment."""
    out = []
    for c in COMPONENTS:
        item = dict(c)
        amount = _price(c["price_env"], c["price_default"])
        item["amount"] = amount
        item["price"] = f"{amount} {CURRENCY}"
        out.append(item)
    return out


def agents():
    """Agents with the shared price and their source path resolved."""
    amount = agent_price()
    out = []
    for a in AGENTS:
        item = dict(a)
        item["amount"] = amount
        item["price"] = f"{amount} {CURRENCY}"
        item["path"] = f"{AGENT_SOURCE_ROOT}/{a['id']}"
        out.append(item)
    return out


def agent_paths():
    """The allowlist the download route checks an id against."""
    return {a["id"]: f"{AGENT_SOURCE_ROOT}/{a['id']}" for a in AGENTS}


def categories():
    """Filter labels, derived rather than maintained by hand."""
    seen = []
    for c in COMPONENTS:
        if c["category"] not in seen:
            seen.append(c["category"])
    return seen


def summary():
    """Counts and price range, for headers that would otherwise hardcode them."""
    amounts = [float(_price(c["price_env"], c["price_default"])) for c in COMPONENTS]
    return {
        "component_count": len(COMPONENTS),
        "agent_count": len(AGENTS),
        "price_min": f"{min(amounts):.2f}",
        "price_max": agent_price(),
        "currency": CURRENCY,
    }
