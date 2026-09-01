"""
Give wallets a free component run.

    python scripts/grant_build.py 8mtAJDQm... H3mZAFdi... B8rVaUB7...

Runs against whatever database the environment points at, so run it where the
prize is meant to exist. Safe to run twice: a wallet that already has one is
reported as such rather than given a second.

Defaults to the site builder. Pass --component to give something else.
"""

import sys

import grants


def main(argv):
    component = "site-builder"
    wallets = []

    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--component":
            if not args:
                print("--component needs a name")
                return 1
            component = args.pop(0)
        elif item.startswith("-"):
            print(f"Unknown option {item}")
            return 1
        else:
            wallets.append(item)

    if not wallets:
        print(__doc__.strip())
        return 1

    grants.init_grants()

    for wallet in wallets:
        wallet = wallet.strip()
        if grants.grant_component(wallet, component, reason="giveaway"):
            print(f"  granted   {wallet}  {component}")
        else:
            print(f"  already had one   {wallet}  {component}")

    print()
    for wallet in wallets:
        held = grants.unclaimed_components(wallet.strip())
        print(f"  {wallet.strip()}  unclaimed: {held or 'none'}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
