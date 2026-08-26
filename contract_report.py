"""
The deterministic half of the contract intelligence report.

Scores, holder tables and signal lists are computed here rather than asked for
in the prompt. They were previously specified as roughly two hundred lines of
arithmetic and branching for the model to carry out by hand: add two for this
flag, subtract one for that, clamp to a range, and pick between fixed sentences
depending on which fields were null. A model does that mostly right, which is
the problem. Nobody can tell a mostly-right score from a right one by reading
it, the same contract can score differently on two runs, and a wrong arithmetic
step is invisible in fluent prose.

Here the same rules are code: exact, identical between runs, and testable.
The model is left the part it is actually good at, which is reading the
evidence and explaining what it means.

Numbers that reach a customer are never invented. Every figure in this module
comes from the scan JSON or is derived from it by the rules below.
"""


def _clamp(value: int, low: int = 1, high: int = 10) -> int:
    return max(low, min(high, value))


def _errored(section) -> bool:
    """Whether a provider returned an error rather than data."""
    return isinstance(section, dict) and bool(section.get("error"))


def _holders_of(blob: dict) -> list:
    """The holder list for whichever network this is, or an empty list."""
    for key in ("sol_top_holders", "top_holders"):
        section = blob.get(key)
        if isinstance(section, dict) and isinstance(section.get("holders"), list):
            return section["holders"]
    return []


def _market(blob: dict) -> dict:
    meta = blob.get("token_metadata")
    return meta if isinstance(meta, dict) else {}


def _has_market_depth(blob: dict) -> bool:
    """Whether the token has enough market data to say anything about it."""
    m = _market(blob)
    return any(m.get(k) for k in ("price_usd", "liquidity_usd", "market_cap", "fdv"))


def score(blob: dict) -> dict:
    """
    The four report scores.

    Rules preserved from the brief that previously asked the model to apply
    them. Deterministic, so the same scan always produces the same numbers and
    a customer running twice is not shown two different risk levels.
    """
    net = (blob.get("network") or "").lower()
    base = blob.get("base_intel") or {}
    hints = blob.get("risk_hints") or {}
    exploit = blob.get("exploit_surface") or {}
    admin = blob.get("admin_risk") or {}
    honeypot = blob.get("honeypot_intel") or {}
    market = _market(blob)
    holders = _holders_of(blob)

    verified = bool(base.get("verified"))
    admin_level = str(admin.get("admin_control_level") or "").lower()
    flags = exploit.get("flags") or []
    dangerous = exploit.get("dangerous_functions") or []
    proxy = bool(base.get("proxy") or base.get("implementation"))

    hp_level = str(honeypot.get("summary_risk_level") or "").lower()
    hp_is = honeypot.get("is_honeypot") is True

    # ── overall risk ────────────────────────────────────────────────────────
    risk = 5
    if hp_level in ("high", "very high", "critical"):
        risk += 2
    if hp_is:
        risk += 2
    if flags or dangerous:
        risk += 2
    if admin_level == "high":
        risk += 2
    if not market.get("liquidity_usd"):
        risk += 1
    if net != "solana" and not verified:
        risk += 1
    if verified:
        risk -= 2
    if admin_level == "low":
        risk -= 1
    if not flags and not dangerous:
        risk -= 1
    if hp_level in ("low", "very low"):
        risk -= 1

    # ── centralization ──────────────────────────────────────────────────────
    central = 5
    powers = bool(flags or dangerous or hints.get("mint_authority") or hints.get("freeze_authority"))
    if powers:
        central += 2
    if admin_level == "high":
        central += 2
    elif admin_level == "moderate":
        central += 1
    if proxy:
        central += 1
    if verified:
        central -= 2
    if admin_level == "low":
        central -= 1
    if not powers and not proxy:
        central -= 1

    # ── data quality ────────────────────────────────────────────────────────
    quality = 5
    if verified:
        quality += 1
    if all(market.get(k) for k in ("price_usd", "market_cap", "fdv", "liquidity_usd")):
        quality += 1
    if holders:
        quality += 1
    if honeypot.get("simulation") and honeypot.get("holderAnalysis"):
        quality += 1
    if _errored(blob.get("top_holders")) or _errored(blob.get("sol_top_holders")):
        quality -= 2
    if not market.get("liquidity_usd") or not market.get("price_usd"):
        quality -= 2
    if _errored(honeypot):
        quality -= 1

    # ── data completeness ───────────────────────────────────────────────────
    complete = 5
    if all(market.get(k) for k in ("price_usd", "liquidity_usd", "market_cap", "fdv", "volume_24h")):
        complete += 2
    if holders:
        complete += 2
    if net != "solana" and honeypot and not _errored(honeypot):
        complete += 1
    if not holders:
        complete -= 2
    if not market.get("price_usd") or not market.get("liquidity_usd"):
        complete -= 2
    if _errored(honeypot):
        complete -= 1

    return {
        "overall_risk": _clamp(risk),
        "centralization": _clamp(central),
        "data_quality": _clamp(quality),
        "data_completeness": _clamp(complete),
    }


def holder_table(blob: dict) -> str:
    """
    The holder concentration table, or a plain statement of what is missing.

    Built here so no wallet address or percentage can be invented, reordered or
    rounded differently between runs. The rows are exactly the provider's rows.

    When holder data is absent this says so. The brief this replaces required
    the opposite on Ethereum: on a provider error it had to print that
    distribution "is inferred to be broad with minimal concentration risk" and
    never mention the failure. That turns a fetch error into a favourable
    finding on a report someone buys to assess risk, which is the one thing it
    must never do. Unfetched is not the same as safe.
    """
    net = (blob.get("network") or "").lower()
    holders = _holders_of(blob)
    label = "Holder Concentration Table (Top 10, Ethereum)" if net != "solana" \
        else "Holder Concentration Table (Top 10)"

    if not holders:
        if _has_market_depth(blob):
            return (
                "Holder distribution was not returned by the indexers used in this "
                "scan, so concentration could not be measured. The token does trade "
                "with visible market data, but that says nothing about how supply is "
                "held: treat concentration as unmeasured rather than as low."
            )
        return "Holder distribution data was unavailable from all providers in this scan."

    lines = [label, "", "| Rank | Wallet Address | % of Supply |", "|------|----------------|-------------|"]
    for i, h in enumerate(holders[:10], 1):
        if not isinstance(h, dict):
            continue
        wallet = h.get("address") or h.get("wallet") or h.get("owner") or "not available"
        pct = h.get("percentage")
        if pct is None:
            pct = h.get("percent_of_supply")
        cell = f"{float(pct):.2f}%" if isinstance(pct, (int, float)) else "not available"
        lines.append(f"| {i} | {wallet} | {cell} |")

    return "\n".join(lines)


def signals(blob: dict) -> str:
    """
    Positive and negative signals, rendered verbatim from the scan.

    The brief asked for these "rewritten but not changed", which is an
    instruction to paraphrase evidence. Passing them through removes the
    opportunity for a rewrite to soften or sharpen a finding.
    """
    ind = blob.get("signal_indicators") or {}
    positives = [s for s in (ind.get("positives") or []) if s]
    negatives = [s for s in (ind.get("negatives") or []) if s]

    # A flagged cluster is a negative signal, and it was previously left to the
    # model to remember to add one.
    bubble = blob.get("bubblemap_analysis")
    if isinstance(bubble, dict) and not bubble.get("error"):
        count = (bubble.get("summary") or {}).get("suspicious_clusters_count") or 0
        if count:
            word = "cluster" if count == 1 else "clusters"
            negatives = negatives + [
                f"{count} holder {word} flagged as suspicious in transfer graph analysis."
            ]

    out = ["Positive Signals:", ""]
    out += [f"• {s}" for s in positives] or ["• None identified in this scan."]
    out += ["", "Negative Signals:", ""]
    out += [f"• {s}" for s in negatives] or ["• None identified in this scan."]
    return "\n".join(out)


def evidence_notes(blob: dict) -> list[str]:
    """
    What the scan could not see, stated once for the model to work around.

    Given to the prompt so gaps are acknowledged plainly a single time rather
    than either repeated in every section or, worse, papered over.
    """
    notes = []
    net = (blob.get("network") or "").lower()

    if not _holders_of(blob):
        notes.append("Holder distribution was not returned by any provider.")
    if not _market(blob).get("price_usd"):
        notes.append("No price data: the token may be new or not indexed yet.")
    if not _market(blob).get("liquidity_usd"):
        notes.append("No liquidity figure was returned.")
    if net != "solana":
        if not (blob.get("base_intel") or {}).get("verified"):
            notes.append(
                "The contract is unverified, so there is no source or ABI to inspect. "
                "This is normal for many tokens and is not by itself a finding."
            )
        if not blob.get("honeypot_intel") or _errored(blob.get("honeypot_intel")):
            notes.append("Honeypot simulation data was not provided in this scan.")

    lp = (blob.get("lp_lock_status") or {})
    if isinstance(lp, dict) and str(lp.get("status") or "").lower() == "unknown":
        notes.append("LP lock information was not provided by the data sources used in this scan.")

    return notes
