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
        # Pump.fun's fee account. It takes a cut of every trade on the
        # platform, so its balance moves constantly and the preview shows real
        # transfers within seconds.
        #
        # Raydium's AMM authority was the obvious pick and was wrong: it holds
        # enough token accounts that the public RPC spent 21 seconds on the
        # opening balance read and then failed, which used the whole preview
        # window before the watch loop had started. This one answers in under
        # two seconds and still holds hundreds of real balances.
        "wallets_to_watch": ["CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM"],
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

# Some agents read their state from a file rather than from an API, and ship
# that file empty because a buyer's project is their own. The planner is the
# one that matters: previewed against its shipped database it printed
# "0 open / 0 done" and then sat silent for the rest of the window, which shows
# a buyer nothing. These files are written into the scratch copy so the preview
# has something to organise. The agent itself is untouched and still ships
# empty.
def _demo_project() -> str:
    """A small project, dated relative to now so reminders fire on screen."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    stamp = lambda minutes: (now + timedelta(minutes=minutes)).isoformat()

    def task(num, title, status, due, priority, description=""):
        return {
            "id": f"task_demo{num}", "title": title, "description": description,
            "status": status, "priority": priority, "due_at": stamp(due),
            "created_at": stamp(-60 * 24 * 9), "updated_at": stamp(-90),
            "completed_at": stamp(-120) if status == "done" else None,
        }

    return json.dumps({
        # Two are inside the hour, so the reminder service has something to
        # report while the preview is still on screen.
        "tasks": [
            task(1, "Ship the payment retry path", "open", 25, 1,
                 "Retry on a dropped confirmation instead of failing the order."),
            task(2, "Write the migration rollback note", "open", 45, 2),
            task(3, "Review the API rate limits", "open", 60 * 26, 3),
            task(4, "Cut the staging release", "done", -60 * 20, 2),
            task(5, "Draft the launch checklist", "done", -60 * 40, 3),
        ],
        "milestones": [
            {"id": "ms_demo1", "title": "Public beta", "due_at": stamp(60 * 24 * 12),
             "status": "open", "created_at": stamp(-60 * 24 * 30)},
            {"id": "ms_demo2", "title": "Internal cutover", "due_at": stamp(-60 * 24 * 3),
             "status": "done", "created_at": stamp(-60 * 24 * 40)},
        ],
        "notes": [
            {"id": "note_demo1", "title": "Retry semantics",
             "body": "A dropped confirmation is not a failed payment. Retry reads "
                     "before touching the order.",
             "created_at": stamp(-60 * 30), "updated_at": stamp(-60 * 30)},
            {"id": "note_demo2", "title": "Rate limit findings",
             "body": "The public endpoint is the bottleneck, not our own limiter.",
             "created_at": stamp(-60 * 6), "updated_at": stamp(-60 * 6)},
        ],
    }, indent=2)


DEMO_FILES = {
    "project-planner": {"data/db.json": _demo_project},
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
    if not src:
        return {"ok": False, "output": "", "reason": "Agent not found."}

    seconds = max(5, min(int(seconds or MAX_SECONDS), MAX_SECONDS))
    workdir = tempfile.mkdtemp(prefix=f"preview-{agent_id}-")

    try:
        # A preview starts a real process, so the sources have to exist as real
        # files. They come from the folder when it is checked out and from
        # storage when it is not, which is the case on the public deployment.
        import agent_store
        try:
            agent_store.materialise(agent_id, workdir, src)
        except agent_store.AgentStoreError:
            return {"ok": False, "output": "",
                    "reason": "That agent's sources are not available here."}

        config_path = os.path.join(workdir, "config.json")
        if os.path.exists(config_path):
            with open(config_path) as handle:
                config = json.load(handle)
            config = _merge(config, DEMO_CONFIG.get(agent_id, {}))
            with open(config_path, "w") as handle:
                json.dump(config, handle, indent=2)

        for relative, builder in DEMO_FILES.get(agent_id, {}).items():
            target = os.path.join(workdir, *relative.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as handle:
                handle.write(builder())

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
