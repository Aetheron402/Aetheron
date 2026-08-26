"""
Temporary preview harness for judging component output.

Runs each component against a fixed sample input and returns the finished
report, so the five outputs can be read side by side and improved without
paying for every iteration.

Deliberately self-contained. It touches no part of the payment path: nothing
here calls verify_payment, no route it defines is reachable from a paid
endpoint, and the ledger is never written to. Deleting this file and the three
lines that mount it in Aetheron.py removes the feature completely, which is the
point, since it is meant to come out before launch.

It stays off unless DEV_TOKEN is set, and being set is not enough on its own:
a caller has to present that token once at /dev/unlock to receive a cookie.
Without both, every route here answers 404 rather than admitting it exists.
"""

import logging
import os
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

DEV_TOKEN = (os.getenv("DEV_TOKEN") or "").strip()
COOKIE = "aeth_dev"

router = APIRouter(prefix="/dev", include_in_schema=False)


def enabled() -> bool:
    return bool(DEV_TOKEN)


def is_unlocked(request: Request) -> bool:
    """Whether this caller has presented the token."""
    if not DEV_TOKEN:
        return False
    # Constant time, so a wrong cookie cannot be refined one character at a
    # time by measuring how long the comparison takes.
    return secrets.compare_digest(request.cookies.get(COOKIE, ""), DEV_TOKEN)


def _guard(request: Request):
    """404 rather than 403, so a locked instance does not confirm the feature."""
    if not is_unlocked(request):
        raise HTTPException(status_code=404, detail="Not Found")


# The sample inputs. Fixed rather than random so two runs of the same component
# are comparable and a change in output came from the prompt, not the input.
SAMPLES = {
    "prompt-optimizer": {
        "label": "AI Prompt Optimizer",
        "input": "write me something about my startup for social media, make it good",
    },
    "code-explainer": {
        "label": "Code Explainer",
        "input": (
            "def fib(n, memo={}):\n"
            "    if n in memo: return memo[n]\n"
            "    if n <= 1: return n\n"
            "    memo[n] = fib(n-1, memo) + fib(n-2, memo)\n"
            "    return memo[n]\n"
        ),
    },
    "prompt-tester": {
        "label": "Smart Prompt Tester",
        "input": "You are a helpful assistant. Answer the user's question about our refund policy.",
    },
    "risk-engine": {
        "label": "Risk Engine",
        "input": "1000 runs, 30 steps, mu 0.05, sigma 0.4, start 1.0",
    },
    "contract-intel": {
        "label": "Contract Intelligence",
        # Canonical mainnet USDC. A real, stable, well-known token, so the
        # report can be judged against something whose answers are known.
        "input": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    },
}


@router.get("/unlock")
def unlock(request: Request, token: str = ""):
    """Exchange the token for a cookie. The only route that works while locked."""
    if not DEV_TOKEN or not secrets.compare_digest(token, DEV_TOKEN):
        raise HTTPException(status_code=404, detail="Not Found")

    response = RedirectResponse(url="/shop", status_code=303)
    response.set_cookie(
        COOKIE, DEV_TOKEN,
        httponly=True,      # not readable from page scripts
        samesite="strict",  # not sent on requests originating elsewhere
        # Secure would be right on the deployed site and wrong on a local
        # http://localhost run, where the browser would silently drop the
        # cookie and the buttons would never appear. Follow the scheme in use.
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 12,
    )
    logger.warning("Dev preview unlocked; component runs are free for this browser")
    return response


@router.get("/lock")
def lock():
    response = RedirectResponse(url="/shop", status_code=303)
    response.delete_cookie(COOKIE)
    return response


@router.post("/run/{slug}")
async def run(slug: str, request: Request):
    """
    Dispatch one component and return the task id.

    Takes the same JSON body the paid endpoint takes, so the existing modals
    can post their real inputs here and get their own text back rather than the
    canned sample. An empty body falls back to the sample, which is what the
    Test buttons send.

    The response deliberately matches the paid endpoint's 202 shape, so the
    page's existing polling and download code works against it unchanged.
    """
    _guard(request)

    sample = SAMPLES.get(slug)
    if not sample:
        raise HTTPException(status_code=404, detail="Unknown component")

    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    def text_of(*keys, default=None):
        for k in keys:
            v = body.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return default if default is not None else sample["input"]

    def number(key, default):
        v = body.get(key)
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default

    fmt = body.get("format") if body.get("format") in ("pdf", "txt", "md", "docx", "html") else "pdf"
    asset_id = "DEV-" + slug.upper()[:12] + "-" + secrets.token_hex(4).upper()
    wallet = "dev-preview"
    used = None

    import celery_worker as w

    if slug == "prompt-optimizer":
        used = text_of("text")
        target = body.get("target") if body.get("target") in (
            "chat", "coding", "agent", "image", "extraction") else None
        task = w.process_prompt.delay(asset_id, used, fmt, wallet, target)

    elif slug == "code-explainer":
        used = text_of("text", "code")
        task = w.process_code.delay(asset_id, used, fmt, wallet, None)

    elif slug == "prompt-tester":
        used = text_of("text", "prompt")
        task = w.process_tester.delay(asset_id, used, fmt, wallet)

    elif slug == "risk-engine":
        runs = int(number("runs", 1000))
        steps = int(number("steps", 30))
        seed = body.get("seed")
        seed = int(seed) if isinstance(seed, (int, float)) and not isinstance(seed, bool) else 42
        used = f"{runs} runs, {steps} steps"
        task = w.process_risk_engine.delay(
            asset_id, runs, steps,
            float(number("mu", 0.05)), float(number("sigma", 0.4)),
            float(number("start_price", 1.0)), seed, fmt, wallet,
        )

    elif slug == "contract-intel":
        used = text_of("contract_address", "address")
        network = body.get("network") if body.get("network") in ("solana", "ethereum") else "solana"
        task = w.process_contract_intel.delay(asset_id, used, network, fmt, wallet)

    else:  # unreachable while SAMPLES and this block agree
        raise HTTPException(status_code=404, detail="Unknown component")

    logger.warning("Dev preview run (unpaid): %s -> %s", slug, asset_id)
    return JSONResponse(status_code=202, content={
        "message": "Queued by the dev preview, no payment taken",
        "task_id": task.id,
        "asset_id": asset_id,
        "component": sample["label"],
        "sample_input": used,
    })


@router.get("/result/{task_id}")
def result(task_id: str, request: Request):
    """Poll one preview run."""
    _guard(request)

    from celery.result import AsyncResult
    from celery_worker import celery

    res = AsyncResult(task_id, app=celery)
    if not res.ready():
        return JSONResponse({"state": res.state, "ready": False})

    if res.failed():
        return JSONResponse({"state": "FAILURE", "ready": True, "error": str(res.result)[:500]})

    payload = res.result if isinstance(res.result, dict) else {"result": str(res.result)}
    return JSONResponse({"state": "SUCCESS", "ready": True, **payload})
