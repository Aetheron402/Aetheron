"""
Load the legacy holder snapshot into the database.

Run once against production, and again only if the snapshot is ever retaken.
Reads the JSON produced by the snapshot, which lists every wallet that held the
first AETH mint before the cutoff, along with when each first acquired it.

    python scripts/load_legacy_holders.py path/to/old_holders_snapshot.json

Eligibility is fixed by that snapshot. It is deliberately not recomputed at
request time: the point of a cutoff taken before any announcement is that
nobody can qualify by buying now.
"""

import datetime as dt
import json
import sys

sys.path.insert(0, ".")

import legacy_holders  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    with open(sys.argv[1]) as handle:
        snapshot = json.load(handle)

    if snapshot.get("mint") != legacy_holders.LEGACY_MINT:
        print(f"refusing: snapshot is for {snapshot.get('mint')}, "
              f"expected {legacy_holders.LEGACY_MINT}")
        return 1

    wallets = {w: 0.0 for w in snapshot["qualified"]}
    if not wallets:
        print("refusing: snapshot lists no qualified wallets")
        return 1

    count = legacy_holders.load(wallets)
    taken = snapshot.get("taken_at", "unknown")
    print(f"loaded {count} wallets from a snapshot taken {taken}")
    print(f"cutoff: {snapshot.get('cutoff')}")
    print(f"discount: {int(legacy_holders.LEGACY_DISCOUNT * 100)}% off everything")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
