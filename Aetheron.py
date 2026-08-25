from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    FileResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import requests

from pydantic import BaseModel, Field, model_validator
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from solana.rpc.api import Client
from datetime import datetime
from solders.pubkey import Pubkey

from ledger_utils import (
    init_ledger,
    add_entry,
    get_recent,
    get_by_wallet,
    get_by_wallet_paginated,
    get_wallet_entry_count,
    row_to_dict,
    consume_signature,
    add_partial,
    get_partial,
    clear_partial,
)
from aeth_price import calculate_required_aeth, AethPricingError

from celery.result import AsyncResult
from solders.signature import Signature

import json
import secrets
import string
import os
import time
import shutil
import traceback
import re

load_dotenv()


def _require_env(name: str, why: str) -> str:
    """Fetch a mandatory setting, failing at boot with an actionable message."""
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set — {why}. "
            f"Set it in your .env (see .env.example) or in the Railway variables."
        )
    return value


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")

# Unset until the relaunched token exists. Every AETH code path is gated on this
# being truthy, so setting it in the Railway variables activates AETH payments
# with no redeploy and no code change.
AETH_MINT = (os.getenv("AETH_MINT_ADDRESS") or "").strip() or None

SOLANA_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
solana_client = Client(SOLANA_RPC)

client = None

def get_openai_client():
    global client
    if client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set")
        client = OpenAI(api_key=OPENAI_API_KEY)
    return client

# Canonical SPL USDC mint on Solana mainnet. Overridable so the stack can be
# pointed at devnet, but the default is a public network constant, not config.
USDC_MINT = os.getenv("USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")

# Deliberately mandatory: a payment app that boots without knowing where funds
# are sent would silently route them to whatever wallet was last hardcoded.
PAYMENT_WALLET = _require_env(
    "PAYMENT_WALLET",
    "it is the Solana wallet that receives every payment",
)

# Priced via env so they can be tuned from the Railway variables without a deploy.
PROMPT_OPTIMIZER_PRICE_USDC = os.getenv("PRICE_PROMPT_OPTIMIZER", "0.25")
CODE_EXPLAINER_PRICE_USDC = os.getenv("PRICE_CODE_EXPLAINER", "0.50")
PROMPT_TESTER_PRICE_USDC = os.getenv("PRICE_PROMPT_TESTER", "0.50")
CONTRACT_INTEL_PRICE_USDC = os.getenv("PRICE_CONTRACT_INTEL", "1.00")
RISK_ENGINE_PRICE_USDC = os.getenv("PRICE_RISK_ENGINE", "0.75")
AGENT_PRICE_USDC = os.getenv("PRICE_AGENT", "4.99")
PAYMENT_NETWORK = "Solana"
PAYMENT_CURRENCY = "USDC"

USDC_DECIMALS = 6

app = FastAPI(
    title="Aetheron",
    description="AI Component Shop powered by X402",
    version="0.1"
)
templates = Jinja2Templates(directory="templates")

init_ledger()

templates.env.filters["fmt_ts"] = lambda ts: datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")

# Exposed as globals so no template ever restates a payment address as a literal.
# These are the values users copy and send funds to, so they must come from the
# same source the backend verifies against — never from a second, drifting copy.
templates.env.globals["payment_wallet"] = PAYMENT_WALLET
templates.env.globals["payment_network"] = PAYMENT_NETWORK
templates.env.globals["aeth_enabled"] = bool(AETH_MINT)
templates.env.globals["aeth_mint"] = AETH_MINT or ""


def payment_required(component: str, message: str, price_usdc) -> JSONResponse:
    """
    Build the X402 challenge.

    The bundled web UI gets the amount and destination from its template, but
    every other client — the SDK above all — learns them only from this body.
    Returning just a message told an integrator that payment was needed while
    withholding how much, in which currency, and to which wallet.
    """
    return JSONResponse(
        status_code=402,
        content={
            "status": 402,
            "message": message,
            "component": component,
            "required": float(price_usdc),
            "currency": PAYMENT_CURRENCY,
            "network": PAYMENT_NETWORK,
            "wallet": PAYMENT_WALLET,
            # AETH appears only once a mint is configured, so the field stays
            # truthful before the token exists.
            "accepted_methods": ["USDC"] + (["AETH"] if AETH_MINT else []),
        },
    )

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("static/favicon/favicon.ico")

def _extract_signers(message: dict) -> list[str]:
    """
    Solana jsonParsed responses can represent accountKeys in two formats:
    - list[str]
    - list[{"pubkey": str, "signer": bool, ...}]
    This helper returns a best-effort list of signers.
    """
    keys = message.get("accountKeys", []) or []

    if keys and isinstance(keys[0], str):
        return [keys[0]]

    out = []
    for k in keys:
        if isinstance(k, dict) and k.get("signer") is True:
            pk = k.get("pubkey")
            if pk:
                out.append(pk)
    return out
    
def guess_media_type(filename: str) -> str:
    ext = filename.lower().split(".")[-1]

    if ext == "pdf":
        return "application/pdf"
    if ext == "txt":
        return "text/plain"
    if ext == "md":
        return "text/markdown"
    if ext == "html":
        return "text/html"
    if ext == "docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    return "application/octet-stream"

def _iter_token_instructions(tx: dict):
    """
    Yield both top-level and inner instructions.
    Many token transfers happen in meta.innerInstructions (CPI).
    """
    msg = (tx.get("transaction") or {}).get("message") or {}
    meta = tx.get("meta") or {}

    for ix in (msg.get("instructions") or []):
        yield ix

    for inner in (meta.get("innerInstructions") or []):
        for ix in (inner.get("instructions") or []):
            yield ix

def get_mint_decimals(mint: str) -> int:
    resp = solana_client.get_token_supply(Pubkey.from_string(mint))
    if not resp or not resp.value:
        raise ValueError("Failed to fetch mint supply")
    return resp.value.decimals

def detect_code_features(code: str) -> dict:
    contains_jsx = bool(re.search(r"<[A-Z][A-Za-z0-9_]*(\s|>)", code))
    contains_html = bool(re.search(r"<[a-z][^>]*>", code)) and not contains_jsx

    return {
        "contains_html": contains_html,
        "contains_jsx": contains_jsx,
        "contains_dom": any(
            k in code for k in (
                "document.",
                "window.",
                "innerHTML",
                "querySelector",
                "getElementById"
            )
        )
    }

def extract_received_amount(tx: dict, target_mint: str, recipient: str) -> int:
    """
    How much of `target_mint` actually landed in `recipient`'s token accounts,
    in base units.

    This asks the only question that matters for a payment: did *our* balance
    go up, and by how much. The previous implementation summed every positive
    balance delta for the mint regardless of owner, so a transfer between two
    wallets an attacker controlled — or simply buying the token on a DEX —
    registered as a payment to us while we received nothing.
    """
    meta = tx.get("meta") or {}
    post_balances = meta.get("postTokenBalances") or []
    pre_balances = meta.get("preTokenBalances") or []

    def _amount(entry) -> int:
        try:
            return int(entry["uiTokenAmount"]["amount"])
        except (KeyError, TypeError, ValueError):
            return 0

    # Only the recipient's own prior balances are eligible as a baseline.
    pre_by_index = {
        pre.get("accountIndex"): pre
        for pre in pre_balances
        if pre.get("mint") == target_mint and pre.get("owner") == recipient
    }

    received = 0
    for post in post_balances:
        if post.get("mint") != target_mint:
            continue
        if post.get("owner") != recipient:
            continue

        pre = pre_by_index.get(post.get("accountIndex"))
        # No baseline means the token account was created by this transaction,
        # so the whole post balance is newly received.
        delta = _amount(post) - (_amount(pre) if pre else 0)
        if delta > 0:
            received += delta

    return received


def _fetch_transaction(sig, attempts: int = 15, delay: float = 0.4):
    """Poll until the transaction is visible on-chain; return it as a dict."""
    for _ in range(attempts):
        resp = solana_client.get_transaction(
            sig,
            encoding="jsonParsed",
            commitment="confirmed",
        )
        value = getattr(resp, "value", None)
        if value:
            raw = value.to_json() if hasattr(value, "to_json") else None
            return json.loads(raw) if raw else value
        time.sleep(delay)
    return None


# AETH is priced from a live quote, so a small drift between quoting and
# settlement is expected. USDC is exact and gets no tolerance.
AETH_TOLERANCE = 0.01


def verify_payment(
    tx_sig: str | None,
    user_wallet: str | None,
    price_usdc: float,
    payment_method: str = "USDC",
    component: str = "generic",
) -> bool | dict:
    """
    Verify that `tx_sig` moved enough of the expected token into PAYMENT_WALLET.

    Returns True when the component is fully paid, a dict describing the
    shortfall when it is only partly paid, and False when the transaction is
    not a payment to us at all.
    """
    if not tx_sig or not user_wallet:
        return False

    payment_method = (payment_method or "USDC").upper()
    if payment_method not in ("USDC", "AETH"):
        return False

    # AETH only becomes a payment method once a mint is configured. Without
    # this, the client-controlled X-PAYMENT-METHOD header reached
    # get_mint_decimals(None) and surfaced as a 500 on demand.
    if payment_method == "AETH" and not AETH_MINT:
        return False

    try:
        sig = Signature.from_string(tx_sig)
    except (ValueError, TypeError):
        print(f"Rejected malformed transaction signature: {tx_sig[:32]!r}")
        return False

    tx = _fetch_transaction(sig)
    if tx is None:
        return False

    meta = tx.get("meta")
    if not meta or meta.get("err") is not None:
        return False

    message = (tx.get("transaction") or {}).get("message") or {}

    # The payer must be the wallet claiming the purchase. This ran only when
    # accountKeys came back as dicts; with the string form the check was
    # skipped entirely, so anyone could submit a stranger's signature.
    signers = _extract_signers(message)
    if not signers or user_wallet not in signers:
        return False

    if payment_method == "USDC":
        decimals = USDC_DECIMALS
        target_mint = USDC_MINT
        expected_amount = int(round(float(price_usdc) * (10 ** decimals)))
    else:
        decimals = get_mint_decimals(AETH_MINT)
        target_mint = AETH_MINT
        expected_amount = int(round(calculate_required_aeth(price_usdc) * (10 ** decimals)))

    if expected_amount <= 0:
        return False

    received = extract_received_amount(tx, target_mint, PAYMENT_WALLET)
    if received <= 0:
        return False

    # Claim the signature before crediting it. The insert is the replay check,
    # so concurrent requests cannot both be credited, and a partial payment can
    # no longer be resubmitted until it accumulates past the price.
    if not consume_signature(tx_sig, user_wallet, component, received, payment_method):
        raise HTTPException(status_code=409, detail="Transaction signature already used")

    existing = get_partial(user_wallet, component, payment_method)
    total = received + (existing["paid"] if existing else 0)

    threshold = expected_amount
    if payment_method == "AETH":
        threshold = int(expected_amount * (1 - AETH_TOLERANCE))

    if total >= threshold:
        clear_partial(user_wallet, component, payment_method)
        return True

    add_partial(user_wallet, component, payment_method, received, expected_amount)

    scale = 10 ** decimals
    return {
        "status": "partial",
        "paid": total / scale,
        "required": expected_amount / scale,
        "remaining": (expected_amount - total) / scale,
        "currency": payment_method,
    }

# Output formats the workers actually produce. Anything else fell through to a
# silent TXT fallback, so a caller could not tell a typo from a real result.
ExportFormat = Literal["pdf", "txt", "md", "html", "docx"]

# Every text field was previously unbounded. A single request could carry an
# arbitrarily large body straight into an LLM call, which is both a memory
# problem and an uncapped spend.
MAX_PROMPT_CHARS = 20_000
MAX_CODE_CHARS = 100_000

# Monte Carlo allocates runs × steps floats. Unbounded, a request for
# runs=1_000_000 steps=10_000 asks for roughly 75 GB and takes the workers with
# it — a denial of service costing the price of one component.
MAX_RISK_RUNS = 10_000
MAX_RISK_STEPS = 2_000
# Deliberately below MAX_RISK_RUNS * MAX_RISK_STEPS, so the combined limit
# actually binds: either dimension may be taken to its maximum, but not both.
MAX_RISK_CELLS = 5_000_000

SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
ASSET_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
ETHEREUM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class PromptIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    format: ExportFormat | None = "pdf"


class ContractIntelInput(BaseModel):
    contract_address: str = Field(..., min_length=32, max_length=64)
    network: Literal["solana", "ethereum"]
    format: ExportFormat | None = "pdf"

    @model_validator(mode="after")
    def _check_address(self):
        """
        Addresses are interpolated into third-party URLs downstream, so they
        are validated against the shape their chain actually uses rather than
        passed through as free text.

        This runs as a model validator rather than a field validator because a
        field validator only sees fields declared before it, and network is
        declared after contract_address — so the check silently did nothing.
        """
        self.contract_address = self.contract_address.strip()

        pattern = (
            ETHEREUM_ADDRESS_RE if self.network == "ethereum" else SOLANA_ADDRESS_RE
        )
        if not pattern.match(self.contract_address):
            raise ValueError(f"Not a valid {self.network} address")
        return self


class RiskEngineInput(BaseModel):
    runs: int = Field(..., ge=1, le=MAX_RISK_RUNS)
    steps: int = Field(..., ge=1, le=MAX_RISK_STEPS)
    start_price: float = Field(..., gt=0, le=1e12)
    mu: float = Field(..., ge=-10, le=10)
    sigma: float = Field(..., ge=0, le=10)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    format: ExportFormat | None = "pdf"

    @model_validator(mode="after")
    def _cap_total_work(self):
        if self.runs * self.steps > MAX_RISK_CELLS:
            raise ValueError(
                f"runs × steps must not exceed {MAX_RISK_CELLS:,} "
                f"(got {self.runs * self.steps:,})"
            )
        return self

@app.post("/api/risk-engine")
def risk_engine_api(
    payload: RiskEngineInput,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    payment_method = x_payment_method or "USDC"
    user_wallet = request.headers.get("X-USER-WALLET")

    payment_check = verify_payment(
        x_payment,
        user_wallet,
        float(RISK_ENGINE_PRICE_USDC),
        payment_method,
        component="risk-engine",
    )

    if payment_check is False:
        return payment_required("risk-engine", "Payment required to use Agent Risk & Simulation Engine", RISK_ENGINE_PRICE_USDC)

    if isinstance(payment_check, dict):
        return JSONResponse(
            status_code=402,
            content={
                "status": 402,
                "message": "Partial payment received",
                "paid": payment_check["paid"],
                "remaining": payment_check["remaining"],
                "currency": payment_method,
            },
        )

    if payload.runs < 100:
        raise HTTPException(status_code=400, detail="runs must be >= 100")
    if payload.steps < 10:
        raise HTTPException(status_code=400, detail="steps must be >= 10")
    if payload.start_price <= 0:
        raise HTTPException(status_code=400, detail="start_price must be > 0")
    if payload.sigma < 0:
        raise HTTPException(status_code=400, detail="sigma must be >= 0")

    asset_id = "X402-RISK-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

    try:
        from celery_worker import process_risk_engine

        task = process_risk_engine.delay(
            asset_id,
            payload.runs,
            payload.steps,
            payload.mu,
            payload.sigma,
            payload.start_price,
            payload.seed,
            (payload.format or "pdf"),
            user_wallet,
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

    try:
        ledger_price = float(RISK_ENGINE_PRICE_USDC)

        add_entry(
            asset_id=asset_id,
            wallet=user_wallet,
            tx_sig=x_payment,
            component="risk-engine",
            price=ledger_price,
            currency=payment_method,
            status="pending",
            filename=None
        )
    except Exception as e:
        print("Ledger log failure (risk engine):", e)

    return JSONResponse(
        status_code=202,
        content={
            "message": "Risk simulation queued",
            "asset_id": asset_id,
            "task_id": task.id
        }
    )

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
    
@app.get("/api/status")
def api_status():
    try:
        # Lightweight, reliable call
        resp = solana_client.get_latest_blockhash()

        if resp and resp.value:
            return {"ok": True}

        return JSONResponse(status_code=503, content={"ok": False})

    except Exception:
        return JSONResponse(status_code=503, content={"ok": False})

@app.get("/shop", response_class=HTMLResponse)
def shop(request: Request):
    components = [
        {
            "id": 1,
            "slug": "prompt-optimizer",
            "name": "AI Prompt Optimizer",
            "description": "Turn rough notes or unstructured ideas into polished, high-quality prompts with clear goals, context, and formatting.",
            "price": f"{PROMPT_OPTIMIZER_PRICE_USDC} {PAYMENT_CURRENCY}",
            "type": "pay-per-use",
            "coming_soon": False,
        },
        {
            "id": 2,
            "slug": "code-explainer",
            "name": "LLM-Powered Code Explainer",
            "description": "Get readable explanations of any code snippet, including logic breakdowns, complexity insights, and suggested improvements.",
            "price": f"{CODE_EXPLAINER_PRICE_USDC} {PAYMENT_CURRENCY}",
            "type": "pay-per-use",
            "coming_soon": False,
        },
        {
            "id": 3,
            "slug": "prompt-tester",
            "name": "Smart Prompt Tester (PersonaSim)",
            "description": "Test your prompt against multiple AI personas—Developer, Skeptic, Hacker—to uncover blind spots, weaknesses, and ways to strengthen it.",
            "price": f"{PROMPT_TESTER_PRICE_USDC} {PAYMENT_CURRENCY}",
            "type": "pay-per-use",
            "coming_soon": False,
        },
        {
            "id": 4,
            "slug": "contract-intel",
            "name": "Contract Intelligence Analyzer",
            "description": "Input Ethereum or Solana contract address and get metadata, function counts, and known issues.",
            "price": "1.00 USDC",
            "type": "pay-per-use",
            "coming_soon": False,
        },
        {
            "id": 5,
            "slug": "agents",
            "name": "Prebuilt Agent Store",
            "description": "Access prebuilt automation agents like trading bots, Discord/Telegram helpers, wallet watchers and monitoring scripts, all delivered ready to deploy.",
            "price": "4.99 USDC",
            "type": "download",
            "coming_soon": False,
        },
        {
            "id": 6,
            "slug": "risk-engine",
            "name": "Agent Risk & Simulation Engine",
            "description": "Run Monte Carlo-style simulations with configurable runs/steps/mu/sigma and export the report.",
            "price": f"{RISK_ENGINE_PRICE_USDC} {PAYMENT_CURRENCY}",
            "type": "pay-per-use",
            "coming_soon": False,
        },
    ]

    return templates.TemplateResponse("shop.html", {"request": request, "components": components})


@app.get("/agents", response_class=HTMLResponse)
def agents_page(request: Request):
    return templates.TemplateResponse("agents.html", {"request": request})

@app.get("/learn", response_class=HTMLResponse)
def learn_page(request: Request):
    return templates.TemplateResponse("learn.html", {"request": request})

@app.get("/legal", response_class=HTMLResponse)
def legal_page(request: Request):
    return templates.TemplateResponse("legal.html", {"request": request})

@app.get("/roadmap", response_class=HTMLResponse)
def roadmap_page(request: Request):
    return templates.TemplateResponse("roadmap.html", {"request": request})
    
@app.get("/sdk", response_class=HTMLResponse)
def sdk_page(request: Request):
    return templates.TemplateResponse("sdk.html", {"request": request})

@app.get("/api/job-status/{task_id}")
def job_status(task_id: str):
    """
    Returns {"state": "...", "result": {...}} when ready.
    result -> {"download_url": "/download/...", "filename": "...", "format": "pdf|txt"}
    """
    from celery_worker import celery
    res = AsyncResult(task_id, app=celery)
    state = res.state
    out = {"state": state}

    if state == "SUCCESS":
        out["result"] = res.result

    if state == "FAILURE":
        out["error"] = str(res.info)

    return out

LEDGER_PAGE_SIZE = 10


@app.get("/ledger", response_class=HTMLResponse)
def ledger_page(request: Request):
    """
    Connected wallets see their own paginated history; everyone else sees the
    most recent public entries. The template renders a pager and links to
    /ledger?page=N, so both `page` and `total_pages` must always be supplied —
    omitting them raised UndefinedError and made this page a guaranteed 500.
    """
    wallet = request.headers.get("X-USER-WALLET")

    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    if wallet:
        total = get_wallet_entry_count(wallet)
        total_pages = max(1, (total + LEDGER_PAGE_SIZE - 1) // LEDGER_PAGE_SIZE)
        page = min(page, total_pages)
        entries = get_by_wallet_paginated(
            wallet,
            limit=LEDGER_PAGE_SIZE,
            offset=(page - 1) * LEDGER_PAGE_SIZE,
        )
    else:
        entries = get_recent(limit=50)
        total_pages = 1
        page = 1

    return templates.TemplateResponse("ledger.html", {
        "request": request,
        "entries": entries,
        "wallet": wallet or "Not connected",
        "page": page,
        "total_pages": total_pages,
    })

@app.get("/api/price/aeth")
def api_price_aeth(usdc_price: float, refresh: bool = False):
    """
    Returns how many AETH are required to pay the given USDC price.
    """
    try:
        required = calculate_required_aeth(usdc_price, force_refresh=refresh)
        return {
            "usdc_price": usdc_price,
            "required_aeth": required
        }
    except AethPricingError:
        raise HTTPException(status_code=502, detail="AETH pricing temporarily unavailable")
    except Exception:
        raise HTTPException(status_code=500, detail="Unexpected error")

@app.get("/api/ledger")
def ledger_api():
    return [row_to_dict(r) for r in get_recent(limit=50)]

@app.get("/api/download_agent/{agent_id}")
def download_agent(
    agent_id: str,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    try:
        payment_method = (x_payment_method or "USDC").upper()
        user_wallet = request.headers.get("X-USER-WALLET")

        payment_check = verify_payment(
            x_payment,
            user_wallet,
            float(AGENT_PRICE_USDC),
            payment_method,
            component=f"agent:{agent_id}",
        )

        if payment_check is False:
            response = payment_required(
                f"agent:{agent_id}",
                "Payment required for agent download",
                AGENT_PRICE_USDC,
            )
            return response

        if isinstance(payment_check, dict):
            return JSONResponse(
                status_code=402,
                content={
                    "status": 402,
                    "message": "Partial payment received",
                    "paid": payment_check["paid"],
                    "remaining": payment_check["remaining"],
                    "currency": "AETH",
                    "agent_id": agent_id,
                },
            )

        agent_paths = {
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

        if agent_id not in agent_paths:
            raise HTTPException(status_code=404, detail="Invalid agent ID")

        src_folder = agent_paths[agent_id]
        if not os.path.exists(src_folder):
            raise HTTPException(status_code=404, detail="Agent source folder not found")

        # A fixed path per agent meant two concurrent downloads raced: one
        # deleted the archive while the other was still streaming it.
        zip_base = f"/tmp/{agent_id}-{secrets.token_hex(8)}"
        shutil.make_archive(zip_base, "zip", src_folder)
        zip_path = f"{zip_base}.zip"

        return FileResponse(
            zip_path,
            filename=f"{agent_id}.zip",
            media_type="application/zip",
        )

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/agents")
def list_agents():
    agents = [
        {
            "id": "solana-sniper",
            "title": "Solana Sniper Bot",
            "description": "Snipes new Pump.fun tokens instantly with adjustable timing, filters, and blacklist protection.",
            "price": AGENT_PRICE_USDC,
        },
        {
            "id": "wallet-watcher",
            "title": "Wallet Watcher (Whale Tracker)",
            "description": "Tracks any wallet in real-time and alerts on buys, sells, transfers, approvals, and liquidity changes.",
            "price": AGENT_PRICE_USDC,
        },
        {
            "id": "discord-helper",
            "title": "Discord AI Helper Bot",
            "description": "A customizable AI bot for Discord — moderation, auto-replies, chat, commands, and wallet verification.",
            "price": AGENT_PRICE_USDC,
        },
        {
            "id": "pumpfun-launcher",
            "title": "Pump.fun Launch Assistant",
            "description": "Monitors new Pump.fun launches, liquidity events, and early momentum signals.",
            "price": AGENT_PRICE_USDC,
        },
        {
            "id": "solana-trading-assistant",
            "title": "Solana Trading Assistant",
            "description": "Analyzes Solana tokens, identifies trends, volume shifts, and supports trading decisions.",
            "price": AGENT_PRICE_USDC,
        },
        {
            "id": "market-tracker",
            "title": "Market Tracker Agent (Template)",
            "description": "Tracks market regimes using risk, volatility, liquidity, correlation, and psychology signals.",
            "price": AGENT_PRICE_USDC,
        },
        {
            "id": "prediction-market",
            "title": "Prediction Market Agent (Template)",
            "description": "Analyzes prediction markets, implied probabilities, sentiment, and mispricing opportunities",
            "price": AGENT_PRICE_USDC,
        },
        {
            "id": "alpha-scanner",
            "title": "Alpha Scanner Agent (Template)",
            "description": "Scans social, on-chain, and market signals to detect emerging narratives and ranked alpha opportunities.",
            "price": AGENT_PRICE_USDC,
        },
        {
            "id": "project-planner",
            "title": "Project Planner Agent (Template)",
            "description": "A modular project coordination framework for managing tasks, notes, milestones, reminders, cleanup, and structured project summaries.",
            "price": AGENT_PRICE_USDC,
        },
    ]
    return {"agents": agents}  
    
@app.get("/my-assets/{wallet}", response_class=HTMLResponse)
def my_assets_page(request: Request, wallet: str):
    try:
        entries = get_by_wallet(wallet)
    except Exception as e:
        print("Ledger fetch error:", e)
        entries = []

    try:
        return templates.TemplateResponse("my_assets.html", {
            "request": request,
            "entries": entries or [],
            "wallet": wallet
        })
    except Exception as e:
        print("Template render error:", e)
        return HTMLResponse(
            content=f"<h2 style='color:red;text-align:center;'>Error loading assets for {wallet}</h2>",
            status_code=200
        )

@app.post("/api/contract-intel")
def contract_intel_api(
    payload: ContractIntelInput,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    payment_method = x_payment_method or "USDC"

    user_wallet = request.headers.get("X-USER-WALLET")

    payment_check = verify_payment(
        x_payment,
        user_wallet,
        float(CONTRACT_INTEL_PRICE_USDC),
        payment_method,
        component="contract-intel",
    )

    if payment_check is False:
        return payment_required("contract-intel", "Payment required to use Contract Intelligence Analyzer", CONTRACT_INTEL_PRICE_USDC)

    if isinstance(payment_check, dict):
        return JSONResponse(
            status_code=402,
            content={
                "status": 402,
                "message": "Partial payment received",
                "paid": payment_check["paid"],
                "remaining": payment_check["remaining"],
                "currency": "AETH",
            },
        )

    contract_address = payload.contract_address.strip()
    network = payload.network.strip().lower()

    if network not in ["solana", "ethereum"]:
        return JSONResponse(status_code=400, content={"error": "Invalid network"})

    asset_id = "X402-CONTRACT-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

    try:
        from celery_worker import process_contract_intel
        
        task = process_contract_intel.delay(
            asset_id,
            contract_address,
            network,
            (payload.format or "pdf"),
            request.headers.get("X-USER-WALLET"),
        )
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Celery dispatch failed")

    try:
        ledger_price = float(CONTRACT_INTEL_PRICE_USDC)

        add_entry(
            asset_id=asset_id,
            wallet=user_wallet,
            tx_sig=x_payment,
            component="contract-intel",
            price=ledger_price,
            currency=payment_method,
            status="pending",
            filename=None
        )
    except Exception as e:
        print("Ledger log failure (contract intel):", e)

    return JSONResponse(
        status_code=202,
        content={
            "message": "Contract scan queued",
            "asset_id": asset_id,
            "task_id": task.id
        }
    )

@app.get("/buy_agent/{agent_id}")
def buy_agent(agent_id: str):
    return JSONResponse(
        status_code=402,
        content={
            "status": 402,
            "message": "Payment required for agent download",
            "agent_id": agent_id,
            "price": AGENT_PRICE_USDC,
            "currency": PAYMENT_CURRENCY,
            "network": PAYMENT_NETWORK,
            "wallet": PAYMENT_WALLET,
            "how_to_pay": "Send payment and retry with an X-TX-SIG header.",
        }
    )
    
@app.get("/api/my-assets/{wallet}")
def api_my_assets(wallet: str, request: Request):
    page = int(request.query_params.get("page", 1))
    per_page = 5
    offset = (page - 1) * per_page

    entries = get_by_wallet_paginated(wallet, limit=per_page, offset=offset)
    dict_entries = [row_to_dict(e) for e in entries]

    total = get_wallet_entry_count(wallet)
    total_pages = max(1, (total + per_page - 1) // per_page)

    return {
        "entries": dict_entries,
        "page": page,
        "total_pages": total_pages
    }

@app.get("/buy/{component_id}")
def buy_component(component_id: int):
    return JSONResponse(
        content={"status": "Payment Required", "component_id": component_id},
        status_code=402,
    )

def cleanup_generated_folder():
    """Delete old generated files older than 24 hours."""
    folder = "generated"
    if not os.path.exists(folder):
        return
    now = time.time()
    for f in os.listdir(folder):
        path = os.path.join(folder, f)
        if os.path.isfile(path) and now - os.path.getmtime(path) > 86400:  # 24h
            os.remove(path)

@app.post("/api/prompt-optimizer")
def prompt_optimizer(
    payload: PromptIn,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    payment_method = x_payment_method or "USDC"

    user_wallet = request.headers.get("X-USER-WALLET")

    payment_check = verify_payment(
        x_payment,
        user_wallet,
        float(PROMPT_OPTIMIZER_PRICE_USDC),
        payment_method,
        component="prompt-optimizer",
    )

    if payment_check is False:
        return payment_required("prompt-optimizer", "Payment required to use AI Prompt Optimizer", PROMPT_OPTIMIZER_PRICE_USDC)

    if isinstance(payment_check, dict):
        return JSONResponse(
            status_code=402,
            content={
                "status": 402,
                "message": "Partial payment received",
                "paid": payment_check["paid"],
                "remaining": payment_check["remaining"],
                "currency": "AETH",
            },
        )


    user_text = (payload.text or "").strip()
    if not user_text:
        return JSONResponse(status_code=400, content={"error": "Empty prompt"})

    asset_id = "X402-PROMPT-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

    try:
        from celery_worker import process_prompt

        task = process_prompt.delay(
            asset_id,
            user_text,
            (payload.format or "pdf"),
            request.headers.get("X-USER-WALLET")
        )
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Celery dispatch failed")

    try:
        ledger_price = float(PROMPT_OPTIMIZER_PRICE_USDC)

        add_entry(
            asset_id=asset_id,
            wallet=user_wallet,
            tx_sig=x_payment,
            component="prompt-optimizer",
            price=ledger_price,
            currency=payment_method,
            status="pending",
            filename=None
        )
    except Exception as e:
        print("Ledger log failure (prompt optimizer):", e)

    return JSONResponse(
        status_code=202,
        content={
            "message": "Prompt queued for processing",
            "asset_id": asset_id,
            "task_id": task.id
        }
    )
    
class CodeInput(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_CODE_CHARS)
    wallet: str | None = Field(default=None, max_length=64)
    chain: str | None = Field(default=None, max_length=32)
    format: ExportFormat | None = "pdf"

@app.post("/api/code-explainer")
def code_explainer(
    payload: CodeInput,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    payment_method = x_payment_method or "USDC"

    user_wallet = request.headers.get("X-USER-WALLET")

    payment_check = verify_payment(
        x_payment,
        user_wallet,
        float(CODE_EXPLAINER_PRICE_USDC),
        payment_method,
        component="code-explainer",
    )

    if payment_check is False:
        return payment_required("code-explainer", "Payment required to use LLM-Powered Code Explainer", CODE_EXPLAINER_PRICE_USDC)

    if isinstance(payment_check, dict):
        return JSONResponse(
            status_code=402,
            content={
                "status": 402,
                "message": "Partial payment received",
                "paid": payment_check["paid"],
                "remaining": payment_check["remaining"],
                "currency": "AETH",
            },
        )

    code_text = (payload.text or "").strip()
    if not code_text:
        return JSONResponse(status_code=400, content={"error": "Empty code input"})

    asset_id = "X402-CODE-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

    try:
        from celery_worker import process_code

        features = detect_code_features(code_text)
        
        task = process_code.delay(
            asset_id,
            code_text,
            (payload.format or "pdf"),
            request.headers.get("X-USER-WALLET"),
            features,
        )
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Celery dispatch failed")

    try:
        ledger_price = float(CODE_EXPLAINER_PRICE_USDC)

        add_entry(
            asset_id=asset_id,
            wallet=user_wallet,
            tx_sig=x_payment,
            component="code-explainer",
            price=ledger_price,
            currency=payment_method,
            status="pending",
            filename=None
        )
    except Exception as e:
        print("Ledger log failure (code explainer):", e)

    return JSONResponse(
        status_code=202,
        content={
            "message": "Code analysis queued",
            "asset_id": asset_id,
            "task_id": task.id
        }
    )
    
class PromptTestIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    format: ExportFormat | None = "pdf"

@app.post("/api/prompt-tester")
def prompt_tester(
    payload: PromptTestIn,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    payment_method = x_payment_method or "USDC"

    user_wallet = request.headers.get("X-USER-WALLET")

    payment_check = verify_payment(
        x_payment,
        user_wallet,
        float(PROMPT_TESTER_PRICE_USDC),
        payment_method,
        component="prompt-tester",
    )

    if payment_check is False:
        return payment_required("prompt-tester", "Payment required to use Smart Prompt Tester", PROMPT_TESTER_PRICE_USDC)

    if isinstance(payment_check, dict):
        return JSONResponse(
            status_code=402,
            content={
                "status": 402,
                "message": "Partial payment received",
                "paid": payment_check["paid"],
                "remaining": payment_check["remaining"],
                "currency": "AETH",
            },
        )

    user_prompt = (payload.text or "").strip()
    if not user_prompt:
        return JSONResponse(status_code=400, content={"error": "Empty prompt"})

    asset_id = "X402-TESTER-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

    try:
        from celery_worker import process_tester
        
        task = process_tester.delay(
            asset_id,
            user_prompt,
            (payload.format or "pdf"),
            request.headers.get("X-USER-WALLET")
        )
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Celery dispatch failed")

    try:
        ledger_price = float(PROMPT_TESTER_PRICE_USDC)

        add_entry(
            asset_id=asset_id,
            wallet=user_wallet,
            tx_sig=x_payment,
            component="prompt-tester",
            price=ledger_price,
            currency=payment_method,
            status="pending",
            filename=None
        )
    except Exception as e:
        print("Ledger log failure (prompt tester):", e)

    return JSONResponse(
        status_code=202,
        content={
            "message": "Prompt tester queued",
            "asset_id": asset_id,
            "task_id": task.id
        }
    )

@app.get("/download/{filename}")
def download_file(filename: str):

    # The filename is appended to a public bucket URL and echoed into a
    # Content-Disposition header, so it is constrained to the shape our own
    # generator produces. Without this, "..%2F..%2F" walked outside the bucket
    # prefix and a quote in the name broke out of the header value.
    if not filename or not ASSET_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    public_base = os.getenv("R2_PUBLIC_BASE")

    if not public_base:
        raise HTTPException(status_code=500, detail="R2 configuration missing")

    clean_name = filename.lstrip("/")
    file_url = f"{public_base}/{clean_name}"

    try:
        r = requests.get(file_url, stream=True, timeout=30)
        r.raise_for_status()
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    headers = {
        "Content-Disposition": f'attachment; filename="{os.path.basename(filename)}"'
    }

    return StreamingResponse(
        r.iter_content(chunk_size=8192),
        media_type=guess_media_type(filename),
        headers=headers,
    )

@app.post("/api/prompt-optimize")
def prompt_optimize_alias(
    payload: PromptIn,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    return prompt_optimizer(
        payload,
        request,
        x_payment,
        x_payment_method,
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "detail": str(exc)
        }
    )

