"""
Record a burn that has already happened on chain.

    python scripts/record_burn.py <transaction signature>

This does not burn anything. You burn from your own wallet, off this machine,
and then hand the signature to this script so the public ledger can account for
it. Nothing is taken on trust: the transaction is fetched from Solana and has
to actually contain a burn instruction for our mint, or it is refused.

Keeping the signing outside the server is deliberate. A key that can burn can
also transfer, so a key here would mean a server compromise drains every
payment ever received, and would make the non-custodial claim on the site
untrue.
"""

import sys

sys.path.insert(0, ".")

import burn_ledger  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    signature = sys.argv[1].strip()
    if not burn_ledger.AETH_MINT:
        print("AETH_MINT_ADDRESS is not set, so there is no mint to verify against")
        return 1

    try:
        result = burn_ledger.record_burn(signature)
    except burn_ledger.BurnVerificationError as exc:
        print(f"refused: {exc}")
        return 1

    amount = result["amount_raw"] / (10 ** burn_ledger.AETH_DECIMALS)
    if result["already_recorded"]:
        print(f"already recorded: {amount:,.6f} AETH")
    else:
        print(f"recorded {amount:,.6f} AETH burned in {signature}")

    totals = burn_ledger.summary()
    print(f"  taken in:    {totals['received']:,.6f} AETH over {totals['payments']} payments")
    print(f"  burned:      {totals['burned']:,.6f} AETH over {totals['burns']} burns")
    print(f"  outstanding: {totals['outstanding']:,.6f} AETH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
