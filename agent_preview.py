"""
Run an agent for a few seconds and show what it prints.

A buyer could read a description and a price, and then had to decide whether
$4.99 of code they had never seen would work against a wallet they care about.
Here they watch it run on live data first.

The code is ours: these are the templates we ship, not anything a visitor
supplies, so this is not arbitrary execution. What it does need is bounding,
because it spawns a process that makes outbound calls. Every preview therefore
runs against a fixed demo config, is killed on a deadline, has its output
truncated, and is rate limited per caller.

Configuration never comes from the request. A webhook URL supplied by a visitor
would make this endpoint fetch whatever they pointed it at from inside our
network, so the demo config is defined here and notifications stay off.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile

# Agents the worker can run as it stands. Only the Discord helper is left out:
# it needs discord.py and a bot token, so there is nothing to show without one.
#
# The trading assistant and the planner were excluded on the assumption that
# they needed PyNaCl and jsonschema. Both are listed in their requirements and
# neither is imported on the path a run actually takes, so both preview fine.
PREVIEWABLE = {
    "wallet-watcher",
    "market-tracker",
    "alpha-scanner",
    "prediction-market",
    "solana-sniper",
    "pumpfun-launcher",
    "solana-trading-assistant",
    "project-planner",
}

# What each preview runs against. Public, read-only, and interesting enough
# that the output shows the agent doing its job rather than idling.
DEMO_CONFIG = {
    "wallet-watcher": {
        # Raydium's AMM authority: its balances actually change on nearly
        # every transaction, so the preview shows real transfers within
        # seconds. An exchange hot wallet looks busier but is mostly only
        # mentioned by transactions rather than moved by them.
        "wallets_to_watch": ["5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"],
        "rpc": {"poll_interval_seconds": 3},
        "notifications": {"enabled": False, "webhook_url": ""},
    },
    "market-tracker": {"run_interval_seconds": 5},
    "alpha-scanner": {"general": {"run_interval_seconds": 5}},
    "prediction-market": {},
    "solana-trading-assistant": {"analysis": {"poll_interval_seconds": 10}},
    "project-planner": {},
    "solana-sniper": {"notifications": {"enabled": False, "webhook_url": ""}},
    "pumpfun-launcher": {"notifications": {"enabled": False, "webhook_url": ""}},
}

MAX_SECONDS = int(os.getenv("AGENT_PREVIEW_SECONDS", "25"))
MAX_OUTPUT = 12_000          # characters returned to the page


def _merge(base: dict, over: dict) -> dict:
    """Overlay the demo values on the agent's own defaults."""
    out = json.loads(json.dumps(base))
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def is_previewable(agent_id: str) -> bool:
    return agent_id in PREVIEWABLE


def run(agent_id: str, seconds: int = MAX_SECONDS) -> dict:
    """
    Run the agent in a scratch copy and return what it printed.

    Returns the captured output plus how it ended, so the page can tell a
    working agent that was stopped on its deadline from one that crashed.
    """
    import agent_setup

    if agent_id not in PREVIEWABLE:
        return {"ok": False, "output": "", "reason": "This agent has no live preview."}

    src = agent_setup.AGENT_PATHS.get(agent_id)
    if not src or not os.path.isdir(src):
        return {"ok": False, "output": "", "reason": "Agent not found."}

    seconds = max(5, min(int(seconds or MAX_SECONDS), MAX_SECONDS))
    workdir = tempfile.mkdtemp(prefix=f"preview-{agent_id}-")

    try:
        shutil.copytree(src, workdir, dirs_exist_ok=True)

        config_path = os.path.join(workdir, "config.json")
        if os.path.exists(config_path):
            with open(config_path) as handle:
                config = json.load(handle)
            config = _merge(config, DEMO_CONFIG.get(agent_id, {}))
            with open(config_path, "w") as handle:
                json.dump(config, handle, indent=2)

        entrypoint = agent_setup.entrypoint_for(workdir)

        # Its own process group, so the deadline kills the agent and anything
        # it spawned rather than leaving orphans on the worker.
        proc = subprocess.Popen(
            [sys.executable, "-u", entrypoint],
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        )

        stopped_on_deadline = False
        try:
            output, _ = proc.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            # Expected: these agents loop forever by design, so reaching the
            # deadline is the agent working, not failing.
            stopped_on_deadline = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                output, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                output, _ = proc.communicate()

        output = (output or "").strip()
        truncated = len(output) > MAX_OUTPUT
        if truncated:
            output = output[:MAX_OUTPUT] + "\n... output truncated ..."

        crashed = not stopped_on_deadline and proc.returncode not in (0, None)

        return {
            "ok": not crashed,
            "agent_id": agent_id,
            "output": output or "(the agent produced no output in this window)",
            "seconds": seconds,
            "stopped_on_deadline": stopped_on_deadline,
            "exit_code": proc.returncode,
            "truncated": truncated,
            "reason": (
                f"Stopped after {seconds}s. This agent runs continuously, so "
                "that is the preview ending, not the agent."
                if stopped_on_deadline else
                ("The agent exited with an error." if crashed else "The agent finished.")
            ),
        }
    except Exception as exc:
        return {"ok": False, "output": "", "reason": f"Preview failed: {str(exc)[:200]}"}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
