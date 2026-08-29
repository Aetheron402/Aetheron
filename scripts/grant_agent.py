"""
Give a wallet a free agent download.

    python scripts/grant_agent.py <wallet> <agent-id> [reason]
    python scripts/grant_agent.py --list <wallet>

Used for giveaway winners. The winner then connects that wallet on the site,
signs a one time message to prove it is theirs, and downloads without paying.

Granting the same pair twice is refused rather than handing out two, so running
a winners list again after a mistake is safe.
"""

import sys

sys.path.insert(0, ".")

import agent_setup  # noqa: E402
import grants  # noqa: E402


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        print("agents:", ", ".join(sorted(agent_setup.AGENT_PATHS)))
        return 2

    if args[0] == "--list":
        if len(args) != 2:
            print(__doc__)
            return 2
        held = grants.unclaimed(args[1])
        print(f"{args[1]}: {', '.join(held) if held else 'nothing unclaimed'}")
        return 0

    if len(args) < 2:
        print(__doc__)
        return 2

    wallet, agent_id = args[0].strip(), args[1].strip()
    reason = args[2] if len(args) > 2 else "giveaway"

    if agent_id not in agent_setup.AGENT_PATHS:
        print(f"unknown agent: {agent_id}")
        print("agents:", ", ".join(sorted(agent_setup.AGENT_PATHS)))
        return 1

    if grants.grant(wallet, agent_id, reason):
        print(f"granted {agent_id} to {wallet}")
    else:
        print(f"{wallet} already has {agent_id}, nothing changed")

    held = grants.unclaimed(wallet)
    print(f"  unclaimed on this wallet: {', '.join(held) if held else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
