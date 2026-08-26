"""
Pre-configured agent downloads.

A buyer used to receive a zip, install Python, create a virtualenv, install
dependencies, then open config.json and replace ADD_YOUR_WALLET_HERE by hand
before anything ran. Every one of those is a place to give up, and the shop
never learned that they had.

Here the few values an agent genuinely needs are collected before the download,
written into its config, and the archive ships with a script that creates the
environment and starts the agent in one command.

Only fields a buyer must supply are asked for. The tuning knobs, thresholds and
weights keep their defaults: alpha-scanner has around thirty settings and
showing all of them would be worse than the file it replaces.

Secrets are never asked for here. A private key pasted into a web form travels
through this server and into its logs on the way to a file the buyer could have
edited themselves, so those fields stay in the config with their placeholder
and are called out in the quickstart instead.
"""

import io
import json
import os
import re
import zipfile

# Where each agent's source lives. One place, so a new agent is one line.
AGENT_PATHS = {
    "solana-sniper": "static/agents_src/solana-sniper",
    "wallet-watcher": "static/agents_src/wallet-watcher",
    "discord-helper": "static/agents_src/discord-helper",
    "pumpfun-launcher": "static/agents_src/pumpfun-launcher",
    "solana-trading-assistant": "static/agents_src/solana-trading-assistant",
    "market-tracker": "static/agents_src/market-tracker",
    "prediction-market": "static/agents_src/prediction-market",
    "alpha-scanner": "static/agents_src/alpha-scanner",
    "project-planner": "static/agents_src/project-planner",
}

# Field kinds the form knows how to render and this module knows how to check.
WALLET_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")   # base58, no 0OIl
URL_RE = re.compile(r"^https://\S+$")

# What each agent asks for. `path` is the dotted location in config.json, and
# `list` means the value is written as a single element list.
SETUP_FIELDS = {
    "wallet-watcher": [
        {
            "key": "wallet", "path": "wallets_to_watch", "list": True,
            "label": "Wallet address to watch", "kind": "wallet", "required": True,
            "help": "The Solana address whose activity you want alerts on.",
        },
        {
            "key": "webhook", "path": "notifications.webhook_url", "kind": "url",
            "label": "Discord or Slack webhook URL", "required": False,
            "help": "Leave blank to log to the console instead of sending alerts.",
        },
        {
            "key": "rpc", "path": "rpc.url", "kind": "url", "required": False,
            "label": "Solana RPC URL",
            "help": "Optional. The public endpoint is heavily rate limited, so a "
                    "Helius or QuickNode URL is worth using if you have one.",
        },
    ],
    "solana-trading-assistant": [
        {
            "key": "birdeye", "path": "birdeye.api_key", "kind": "text", "required": False,
            "label": "Birdeye API key",
            "help": "Optional. Without it the assistant falls back to public price data.",
        },
        {
            "key": "webhook", "path": "notifications.webhook_url", "kind": "url",
            "label": "Discord or Slack webhook URL", "required": False,
            "help": "Leave blank to log to the console.",
        },
    ],
    "solana-sniper": [
        {
            "key": "webhook", "path": "notifications.webhook_url", "kind": "url",
            "label": "Discord or Slack webhook URL", "required": False,
            "help": "Leave blank to log to the console.",
        },
        {
            "key": "rpc", "path": "rpc.url", "kind": "url", "required": False,
            "label": "Solana RPC URL",
            "help": "Optional. A private endpoint matters more here than elsewhere, "
                    "since the public one rate limits under fast polling.",
        },
    ],
    "pumpfun-launcher": [
        {
            "key": "webhook", "path": "notifications.webhook_url", "kind": "url",
            "label": "Discord or Slack webhook URL", "required": False,
            "help": "Leave blank to log to the console.",
        },
    ],
    "discord-helper": [
        {
            "key": "webhook", "path": "notifications.webhook_url", "kind": "url",
            "label": "Discord or Slack webhook URL", "required": False,
            "help": "Optional, for status notifications.",
        },
    ],
}

# Fields that stay in the file for the buyer to fill in locally, with the reason
# shown in the quickstart. Anything here is a credential we decline to handle.
LOCAL_ONLY = {
    "solana-sniper": [
        ("wallet.private_key",
         "Your wallet private key. Never paste this into a website, this one "
         "included. Open config.json and set it locally, and only if you have "
         "added real trading to the template."),
    ],
    "discord-helper": [
        ("discord.bot_token",
         "Your Discord bot token, from the Discord developer portal."),
        ("ai.openai_api_key",
         "Your model provider API key."),
    ],
    "solana-trading-assistant": [],
}


def fields_for(agent_id: str) -> list:
    """The questions to ask before downloading this agent."""
    return SETUP_FIELDS.get(agent_id, [])


def local_only_for(agent_id: str) -> list:
    return LOCAL_ONLY.get(agent_id, [])


class SetupError(ValueError):
    """A submitted value that would produce a broken config."""


def _validate(field: dict, raw):
    """Check one submitted value, returning it cleaned."""
    value = (raw or "").strip() if isinstance(raw, str) else raw
    label = field["label"]

    if not value:
        if field.get("required"):
            raise SetupError(f"{label} is required.")
        return None

    if len(str(value)) > 400:
        raise SetupError(f"{label} is too long.")

    kind = field.get("kind")
    if kind == "wallet" and not WALLET_RE.match(str(value)):
        raise SetupError(
            f"{label} does not look like a Solana address. "
            "Expected 32 to 44 base58 characters."
        )
    if kind == "url" and not URL_RE.match(str(value)):
        raise SetupError(f"{label} must be an https URL.")
    return value


def _assign(config: dict, dotted: str, value, as_list: bool = False):
    """Write a value into a nested config by dotted path, creating as needed."""
    parts = dotted.split(".")
    node = config
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = [value] if as_list else value


def apply_config(agent_id: str, source_config: dict, answers: dict) -> tuple:
    """
    Merge the buyer's answers into the agent's config.

    Returns the new config and the list of fields that were actually set, which
    the quickstart uses so the buyer can see what was filled in for them.
    """
    config = json.loads(json.dumps(source_config))   # never mutate the template
    applied = []

    answers = answers if isinstance(answers, dict) else {}
    # No answers at all is the buyer choosing defaults, which the form offers
    # explicitly. Enforcing a required field there would block a download
    # someone has already paid for, over a value they can edit in the file.
    if not any(str(v).strip() for v in answers.values()):
        return config, applied

    for field in fields_for(agent_id):
        value = _validate(field, answers.get(field["key"]))
        if value is None:
            continue
        _assign(config, field["path"], value, field.get("list", False))
        applied.append(field["label"])

    return config, applied


def quickstart(agent_id: str, applied: list) -> str:
    """The README the archive opens with."""
    lines = [
        f"# {agent_id}",
        "",
        "## Run it",
        "",
        "macOS or Linux:",
        "",
        "    ./run.sh",
        "",
        "Windows:",
        "",
        "    run.bat",
        "",
        "That creates a virtual environment, installs the dependencies and starts",
        "the agent. Python 3.9 or newer is the only prerequisite.",
        "",
    ]

    if applied:
        lines += ["## Already configured", "",
                  "These were set from what you entered at download:", ""]
        lines += [f"- {name}" for name in applied]
        lines += ["", "They live in config.json and can be changed there at any time.", ""]

    pending = local_only_for(agent_id)
    if pending:
        lines += ["## Still to fill in", "",
                  "Open config.json and set these before the first run:", ""]
        for path, why in pending:
            lines += [f"- `{path}`", f"  {why}", ""]

    lines += [
        "## If something goes wrong",
        "",
        "- `python3: command not found`: install Python 3 and run the script again.",
        "- Permission denied on run.sh: `chmod +x run.sh`.",
        "- Rate limit errors: the public Solana RPC is shared and throttled. Use your",
        "  own endpoint in config.json.",
        "",
        "The full documentation is in README.md.",
        "",
    ]
    return "\n".join(lines)


RUN_SH = """#!/usr/bin/env bash
# Creates the environment and starts the agent. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required but was not found. Install it from python.org." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if [ -f requirements.txt ]; then
  echo "Installing dependencies..."
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
fi

echo "Starting {agent_id}..."
exec python {entrypoint}
"""

RUN_BAT = """@echo off
REM Creates the environment and starts the agent. Safe to re-run.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 is required but was not found. Install it from python.org.
  exit /b 1
)

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)

call .venv\\Scripts\\activate.bat

if exist requirements.txt (
  echo Installing dependencies...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet -r requirements.txt
)

echo Starting {agent_id}...
python {entrypoint}
"""


def build_zip(agent_id: str, answers: dict) -> bytes:
    """
    Build the archive: the agent, its filled-in config, and the run scripts.

    Assembled in memory rather than on disk. Two buyers downloading the same
    agent at the same time previously raced over a shared path, and a config
    holding someone's endpoints has no reason to be written to a server's
    filesystem at all.
    """
    src = AGENT_PATHS.get(agent_id)
    if not src or not os.path.isdir(src):
        raise SetupError("Unknown agent")

    config_path = os.path.join(src, "config.json")
    source_config = {}
    if os.path.exists(config_path):
        with open(config_path) as handle:
            source_config = json.load(handle)

    config, applied = apply_config(agent_id, source_config, answers)
    entrypoint = "app.py" if os.path.exists(os.path.join(src, "app.py")) else "main.py"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, dirs, files in os.walk(src):
            # A shipped virtualenv or cache is megabytes of someone else's paths.
            dirs[:] = [d for d in dirs if d not in
                       (".venv", "venv", "__pycache__", ".git", "node_modules")]
            for name in files:
                if name.endswith((".pyc", ".pyo")) or name == "config.json":
                    continue
                full = os.path.join(root, name)
                archive.write(full, os.path.relpath(full, src))

        if source_config or config:
            archive.writestr("config.json", json.dumps(config, indent=2) + "\n")

        archive.writestr("QUICKSTART.md", quickstart(agent_id, applied))

        # Executable bit, so ./run.sh works without chmod on macOS and Linux.
        info = zipfile.ZipInfo("run.sh")
        info.external_attr = 0o755 << 16
        archive.writestr(info, RUN_SH.format(agent_id=agent_id, entrypoint=entrypoint))
        archive.writestr("run.bat", RUN_BAT.format(agent_id=agent_id, entrypoint=entrypoint))

    return buffer.getvalue()
