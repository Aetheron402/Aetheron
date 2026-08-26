"""
System health probes.

Every figure this module reports is measured at the moment it is asked for, or
counted from the ledger. Nothing is estimated, remembered or padded. A status
page that invents an uptime percentage is worth less than no status page at
all, because the first person to catch it stops believing the rest.

Where something genuinely cannot be checked cheaply, it is reported as
configured or not configured rather than dressed up as a live check. A model
probe would cost money on every page load, so the page says the key is present,
which is the honest claim.
"""

import os
import time
from datetime import datetime, timezone

import requests

# Process start, so uptime is real rather than a number someone picked.
_BOOT_TS = time.time()

OK = "operational"
DEGRADED = "degraded"
DOWN = "down"
UNCONFIGURED = "not configured"


def _timed(fn):
    """Run a probe, returning its verdict plus how long it actually took."""
    start = time.perf_counter()
    try:
        status, detail = fn()
    except Exception as exc:
        status, detail = DOWN, str(exc)[:120]
    return {
        "status": status,
        "detail": detail,
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
    }


def check_solana(solana_client):
    def probe():
        resp = solana_client.get_latest_blockhash()
        if not resp or not resp.value:
            return DOWN, "no blockhash returned"
        slot = getattr(getattr(resp, "context", None), "slot", None)
        return OK, f"slot {slot}" if slot else "reachable"
    return _timed(probe)


def check_redis(redis_url):
    def probe():
        if not redis_url:
            return UNCONFIGURED, "REDIS_URL is not set"
        import redis as redis_lib
        client = redis_lib.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
        client.ping()
        info = client.info("server")
        return OK, f"redis {info.get('redis_version', 'connected')}"
    return _timed(probe)


def ephemeral_sqlite() -> bool:
    """
    True when we are running on SQLite inside a deployment platform.

    SQLite is the right default for a local clone and the wrong one in a
    container. The file is not shared with the worker, so a report the worker
    writes cannot be found by the web process that has to serve it, and the
    disk is replaced on every deploy, taking the payment ledger with it.

    Worth detecting precisely because nothing else notices. Every probe passes,
    every write succeeds, and the fault only surfaces when a paying customer
    follows a download link. The usual cause is a DATABASE_URL reference that
    resolved to an empty string.
    """
    from ledger_utils import USE_POSTGRES

    hosted = any(
        os.getenv(v)
        for v in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "DYNO", "FLY_APP_NAME")
    )
    return hosted and not USE_POSTGRES


def database_env_hint() -> str:
    """
    Describe the database variables this process can actually see.

    A platform reference like ${{Postgres.DATABASE_URL}} that names a service
    or key which does not exist is substituted with an empty string rather than
    rejected, so a misconfigured variable looks exactly like an unset one from
    inside the container. Distinguishing the two is the whole diagnosis, and it
    cannot be done from the dashboard, which shows the reference rather than
    what it resolved to.

    Names and emptiness only. Connection strings carry the database password,
    so no value from this set is ever put in a response.
    """
    seen = sorted(
        name
        for name in os.environ
        if name.startswith(("DATABASE", "PG", "POSTGRES")) or name.startswith("DB_")
    )
    if not seen:
        return "no DATABASE/PG/DB_ variables reached this service"

    parts = [f"{n}={'set' if os.environ[n].strip() else 'EMPTY'}" for n in seen]
    return "visible: " + ", ".join(parts)


def check_ledger(ledger_utils):
    def probe():
        ledger_utils.get_recent(limit=1)
        if ephemeral_sqlite():
            return DEGRADED, (
                "sqlite on a disk that is wiped each deploy; "
                f"DATABASE_URL is unset or empty. {database_env_hint()}"
            )
        return OK, ledger_utils.backend_name()
    return _timed(probe)


def check_workers():
    """
    Ask the workers to identify themselves and say where they store reports.

    Zero replies means queued jobs will never run. A reply naming a different
    store than this process uses is worse than that, because it fails silently:
    jobs complete, payments settle, and only the download 404s.
    """
    def probe():
        import storage
        from celery_worker import celery

        replies = celery.control.broadcast(
            "storage_backend", reply=True, timeout=2.0
        ) or []
        count = len(replies)

        if count == 0:
            # An older worker will not know the command. Fall back to a plain
            # ping so a version skew reads as "cannot verify" and not "down".
            if celery.control.inspect(timeout=1.2).ping():
                return DEGRADED, "responding, but too old to report its storage"
            return DOWN, "no workers responding"

        ours = storage.backend_name()
        theirs = {
            backend
            for reply in replies
            for result in reply.values()
            if (backend := (result or {}).get("backend"))
        }
        plural = "s" if count != 1 else ""

        mismatched = theirs - {ours}
        if mismatched:
            return DEGRADED, (
                f"{count} worker{plural} responding, but storing reports in "
                f"{', '.join(sorted(mismatched))} while downloads are served "
                f"from {ours}; paid reports would 404"
            )

        return OK, f"{count} worker{plural} responding, sharing {ours}"
    return _timed(probe)


def check_price_oracle():
    def probe():
        r = requests.get(
            "https://api.dexscreener.com/latest/dex/tokens/"
            "So11111111111111111111111111111111111111112",
            timeout=6,
        )
        if r.status_code != 200:
            return DEGRADED, f"HTTP {r.status_code}"
        pairs = (r.json() or {}).get("pairs") or []
        return OK, f"{len(pairs)} pairs quoted"
    return _timed(probe)


def check_storage():
    """
    Reports fall back to the ledger database when R2 is absent, so missing R2
    is a choice rather than a fault. What matters is whether the backend in use
    can actually be reached.
    """
    def probe():
        import storage
        usage = storage.usage()
        if ephemeral_sqlite():
            # The worker's file and ours are different files. Reports would be
            # generated, charged for, and then 404 on download.
            return DEGRADED, (
                "sqlite is not shared with the worker; paid reports would not "
                "be downloadable. Set DATABASE_URL"
            )
        if not usage.get("counted"):
            return OK, usage["backend"]
        mb = usage["bytes"] / 1024 / 1024
        return OK, f"{usage['backend']}, {usage['assets']} reports, {mb:.1f} MB"
    return _timed(probe)


def check_inference():
    """Configuration only. Probing it would bill a request per page view."""
    def probe():
        if not os.getenv("ANTHROPIC_API_KEY"):
            return UNCONFIGURED, "ANTHROPIC_API_KEY is not set"
        return OK, "credentials present"
    return _timed(probe)


def _configured(name, label, missing, key_optional=False):
    """
    Report a data provider, and say what its absence actually costs.

    `missing` describes the consequence rather than repeating the variable
    name, because "not set" alone gives no way to judge whether it matters.

    `key_optional` marks a provider that works unauthenticated, where a key
    only raises the rate limit. Showing those as awaiting setup claims a
    feature is unavailable when it is running.
    """
    present = bool(os.getenv(name))
    if present:
        return {"status": OK, "detail": "key present", "label": label}
    if key_optional:
        return {"status": OK, "detail": missing, "label": label}
    return {"status": UNCONFIGURED, "detail": missing, "label": label}


def integrations():
    """Optional data providers. Absent ones degrade a feature, not the service."""
    return [
        _configured(
            "HELIUS_API_KEY", "Helius RPC",
            "falls back to the public Solana RPC, which is heavily rate limited",
        ),
        _configured(
            "ETHERSCAN_API_KEY", "Etherscan",
            "Ethereum contract source and ABI unavailable, so admin risk and "
            "exploit surface are skipped on ETH addresses",
        ),
        _configured(
            "HONEYPOT_API_KEY", "Honeypot.is",
            "running unauthenticated; a key only raises the rate limit",
            key_optional=True,
        ),
        _configured(
            "BIRDEYE_API_KEY", "Birdeye",
            "Solana token metadata and one holder source fall back to Helius "
            "and DexScreener",
        ),
    ]


def ledger_stats(ledger_utils):
    """Counts taken from the ledger itself, not from a counter we keep."""
    try:
        rows = ledger_utils.get_recent(limit=500)
        now = time.time()
        day = [r for r in rows if (now - float(r[9])) < 86400]
        return {
            "available": True,
            "assets_total": len(rows),
            "assets_24h": len(day),
            "succeeded": sum(1 for r in rows if r[7] == "success"),
            "pending": sum(1 for r in rows if r[7] == "pending"),
        }
    except Exception:
        return {"available": False}


def uptime():
    seconds = int(time.time() - _BOOT_TS)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        text = f"{d}d {h}h"
    elif h:
        text = f"{h}h {m}m"
    elif m:
        text = f"{m}m {s}s"
    else:
        text = f"{s}s"
    return {"seconds": seconds, "text": text}


# An environment that has not been given its credentials is not a broken one.
# Reporting "service disruption" for a missing key trains people to ignore the
# banner, so incomplete setup gets its own verdict.
INCOMPLETE = "incomplete"


def overall(services):
    """
    Worst genuine failure wins.

    down       something configured is not answering
    degraded   something configured is answering badly
    incomplete only missing configuration, nothing is actually failing
    """
    states = [s["status"] for s in services.values()]
    if DOWN in states:
        return DOWN
    if DEGRADED in states:
        return DEGRADED
    if UNCONFIGURED in states:
        return INCOMPLETE
    return OK


# Every page polls this from its header, and a snapshot reaches out to Solana,
# DexScreener and the worker pool. Without a short cache, opening three tabs
# would triple the outbound traffic for no extra information.
_CACHE_TTL = 10.0
_cached = None
_cached_at = 0.0


def snapshot(*, solana_client, ledger_utils, redis_url, force=False):
    global _cached, _cached_at
    now = time.time()
    if not force and _cached is not None and (now - _cached_at) < _CACHE_TTL:
        return _cached
    data = _build_snapshot(solana_client, ledger_utils, redis_url)
    _cached, _cached_at = data, now
    return data


def _build_snapshot(solana_client, ledger_utils, redis_url):
    services = {
        "api":     {"status": OK, "detail": "serving this response", "latency_ms": 0.0},
        "solana":  check_solana(solana_client),
        "redis":   check_redis(redis_url),
        "ledger":  check_ledger(ledger_utils),
        "workers": check_workers(),
        "oracle":  check_price_oracle(),
        "storage": check_storage(),
        "ai":      check_inference(),
    }
    return {
        "ok": overall(services) in (OK, INCOMPLETE),
        "overall": overall(services),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "uptime": uptime(),
        "services": services,
        "integrations": integrations(),
        "ledger": ledger_stats(ledger_utils),
    }
