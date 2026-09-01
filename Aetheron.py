from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    FileResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import requests

from pydantic import BaseModel, Field, model_validator
from typing import Literal

from dotenv import load_dotenv
from solana.rpc.api import Client
from datetime import datetime, timezone
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
    get_by_asset_id,
)
from aeth_price import calculate_required_aeth, AethPricingError

import site_stream

import storage
import agent_setup
import ledger_utils
import legacy_holders
import pricing
import asset_naming
import site_data
import burn_ledger
import aeth_quotes
import grants

from celery.result import AsyncResult
from solders.signature import Signature

import json
import secrets
import string
import logging
import os
import time
import shutil
import traceback
import functools
import logging
import re

load_dotenv()


def _require_env(name: str, why: str) -> str:
    """Fetch a mandatory setting, failing at boot with an actionable message."""
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set, {why}. "
            f"Set it in your .env (see .env.example) or in the Railway variables."
        )
    return value


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")

# Unset until the relaunched token exists. Every AETH code path is gated on this
# being truthy, so setting it in the Railway variables activates AETH payments
# with no redeploy and no code change.
AETH_MINT = (os.getenv("AETH_MINT_ADDRESS") or "").strip() or None

SOLANA_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
solana_client = Client(SOLANA_RPC)


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
# A full page is a large generation, several times the output of a report, so
# it is priced above the components rather than alongside them.
SITE_BUILDER_PRICE_USDC = os.getenv("PRICE_SITE_BUILDER", "2.50")
SITE_REVISION_PRICE_USDC = os.getenv("PRICE_SITE_REVISION", "0.99")
PAYMENT_NETWORK = "Solana"
PAYMENT_CURRENCY = "USDC"

USDC_DECIMALS = 6

logger = logging.getLogger("aetheron")

app = FastAPI(
    title="Aetheron",
    description="AI Component Shop powered by X402",
    version="0.1",
    # /docs is the written documentation page. FastAPI's generated explorer
    # moves aside rather than being switched off, since it stays useful.
    docs_url="/api-explorer",
    redoc_url=None,
)
templates = Jinja2Templates(directory="templates")

init_ledger()
storage.init_storage()
ledger_utils.init_examples()
legacy_holders.init_legacy_holders()
burn_ledger.init_burns()
aeth_quotes.init_quotes()
grants.init_grants()

templates.env.filters["fmt_ts"] = lambda ts: datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")

# Exposed as globals so no template ever restates a payment address as a literal.
# These are the values users copy and send funds to, so they must come from the
# same source the backend verifies against, never from a second, drifting copy.
def asset_url(path: str) -> str:
    """
    A static URL carrying the file's modification time.

    Replacing an image keeps its URL, so browsers hold the old bytes and a
    change looks like it never landed. Stamping the mtime changes the URL
    whenever the file does, and not otherwise.
    """
    rel = path.lstrip("/")
    try:
        stamp = int(os.path.getmtime(rel))
    except OSError:
        return "/" + rel
    return f"/{rel}?v={stamp}"


def absolute_url(request, path: str = "/") -> str:
    """
    A fully qualified URL for social card metadata.

    Scrapers reject relative og:image paths, and hardcoding a host would break
    the moment the domain changes, so this is built from the request. Railway
    terminates TLS at its proxy, so the app sees http even though the world
    sees https: the scheme is corrected unless this is genuinely local.
    """
    base = str(request.base_url).rstrip("/")
    if not base.startswith(("http://127.0.0.1", "http://localhost")):
        base = base.replace("http://", "https://", 1)
    return base + path


templates.env.globals["asset_url"] = asset_url
templates.env.globals["absolute_url"] = absolute_url

templates.env.globals["payment_wallet"] = PAYMENT_WALLET
templates.env.globals["payment_network"] = PAYMENT_NETWORK
templates.env.globals["aeth_enabled"] = bool(AETH_MINT)
templates.env.globals["aeth_mint"] = AETH_MINT or ""

# Read per render rather than at import, so a process that stays up across New
# Year does not keep serving a footer with last year in it.
templates.env.globals["current_year"] = lambda: datetime.now(timezone.utc).year


def payment_required(component: str, message: str, price_usdc,
                     wallet: str | None = None,
                     method: str = "USDC") -> JSONResponse:
    """
    Build the X402 challenge.

    The bundled web UI gets the amount and destination from its template, but
    every other client, the SDK above all, learns them only from this body.

    The quoted amount runs through pricing.effective_usd, the same function the
    settlement check uses, so a discounted buyer is never quoted one number and
    measured against another.

    When the buyer is paying in AETH the challenge carries the AETH amount too,
    and locks it. It used to carry only dollars, which left the browser to work
    the conversion out for itself, and a browser doing that arithmetic against a
    cached rate for a different price showed one number on the button and
    another in the dialog. Worse, settlement honours the locked quote, so the
    smaller of the two would have been rejected as short after the buyer had
    already sent it. There is one number now and the server decides it.
    """
    quoted = pricing.effective_usd(price_usdc, wallet, "USDC")
    discounted = quoted != float(price_usdc)

    body = {
        "status": 402,
        "message": message,
        "component": component,
        "required": quoted,
        "list_price": float(price_usdc),
        "discount": "legacy holder, 50%" if discounted else None,
        "currency": PAYMENT_CURRENCY,
        "network": PAYMENT_NETWORK,
        "wallet": PAYMENT_WALLET,
        # AETH appears only once a mint is configured, so the field stays
        # truthful before the token exists.
        "accepted_methods": ["USDC"] + (["AETH"] if AETH_MINT else []),
    }

    if str(method).upper() == "AETH" and AETH_MINT:
        try:
            in_aeth = pricing.effective_usd(price_usdc, wallet, "AETH")
            required_aeth = calculate_required_aeth(in_aeth)

            body["required_aeth"] = required_aeth
            body["required_usd_in_aeth"] = in_aeth
            body["currency"] = "AETH"

            # Locked against this exact amount, because that is what settlement
            # will measure the transfer against. Locking the component's list
            # price instead is how a part charge came to expect a full one.
            if wallet:
                decimals = get_mint_decimals(AETH_MINT)
                aeth_quotes.record(wallet, component,
                                   int(round(required_aeth * (10 ** decimals))),
                                   in_aeth)
        except Exception:
            # Without a rate there is no AETH figure to give, and quoting one
            # anyway is worse than letting them pay in USDC.
            logger.warning("Could not quote %s in AETH", component, exc_info=True)

    return JSONResponse(status_code=402, content=body)

# A price fetched now is a price nobody has to wait for later. Both run in a
# thread and are allowed to fail: the ordinary paths still work without them.
if AETH_MINT:
    try:
        import aeth_price as _aeth_price
        _aeth_price.warm()
    except Exception:
        pass

try:
    legacy_holders.warm()
except Exception:
    pass

# The Telegram bot runs in a thread here rather than as its own service. It is
# idle almost all the time and needs the same database and settings, so a
# second deployment would be a second thing to configure and watch for no gain.
#
# With no TELEGRAM_BOT_TOKEN set this does nothing at all, which is what every
# instance without one should do.
try:
    import tg_bot
    tg_bot.start()
except Exception:
    logger.warning("The Telegram bot did not start", exc_info=True)

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

@functools.lru_cache(maxsize=32)
def get_mint_decimals(mint: str) -> int:
    """
    How many decimal places a mint uses.

    Cached for the life of the process, because it is fixed at mint creation
    and cannot change afterwards. It was being read off the chain on every AETH
    quote, which put a round trip in front of somebody waiting to pay.
    """
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
    wallets an attacker controlled, or simply buying the token on a DEX -
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

    # Only now that the caller is proven to be a signer of this transaction is
    # it safe to price against their wallet. Doing it earlier would let anybody
    # claim a stranger's discount by naming their address.
    #
    # The method is passed in so the AETH fee tier lands here as well. It used
    # to apply only to the USDC branch, which quoted a discounted wallet the
    # full AETH amount and then accepted the overpayment without comment.
    price_usdc = pricing.effective_usd(price_usdc, user_wallet, payment_method)

    if payment_method == "USDC":
        decimals = USDC_DECIMALS
        target_mint = USDC_MINT
        expected_amount = int(round(float(price_usdc) * (10 ** decimals)))
    else:
        decimals = get_mint_decimals(AETH_MINT)
        target_mint = AETH_MINT

        # Honour what this buyer was told, not a number worked out behind them.
        # Recomputing here against a fresh rate rejected correct payments: AETH
        # is on a bonding curve, so the price moves between being quoted and
        # the transfer landing, and a payment made at the quoted amount came
        # back short. The buyer had paid and got nothing.
        locked = aeth_quotes.live(user_wallet, component)
        if locked:
            expected_amount = locked
        else:
            expected_amount = int(round(
                calculate_required_aeth(price_usdc) * (10 ** decimals)))

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
        if payment_method == "AETH":
            aeth_quotes.clear(user_wallet, component)
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
# it, a denial of service costing the price of one component.
MAX_RISK_RUNS = 10_000
MAX_RISK_STEPS = 2_000
# Deliberately below MAX_RISK_RUNS * MAX_RISK_STEPS, so the combined limit
# actually binds: either dimension may be taken to its maximum, but not both.
MAX_RISK_CELLS = 5_000_000

SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
ASSET_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
ETHEREUM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


# What the prompt will be pointed at. Constrained rather than free text so an
# unknown value cannot reach the optimiser's instructions.
PromptTarget = Literal["chat", "coding", "agent", "image", "extraction"]


class SiteEdit(BaseModel):
    """
    One change, aimed at one part of the page.

    `selector` comes from clicking the element in the preview, so a change lands
    where it was pointed at rather than wherever the model decides the words
    apply. `label` is what the person saw when they clicked it, kept so the
    prompt can say which element in plain terms as well as in CSS.
    """
    selector: str | None = Field(default=None, max_length=400)
    label: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=2, max_length=600)


class SiteReviseIn(BaseModel):
    """
    A change to a page that already exists.

    The project says which page, the change says what to do to it. The token
    fields are all optional and only overwrite what they are given, so adding a
    Telegram link later does not blank the description.
    """
    project_id: str = Field(min_length=4, max_length=64)
    # One or the other. `edits` is what the studio sends, a list of changes each
    # aimed at an element; `notes` is the plain description the modal used.
    notes: str | None = Field(default=None, max_length=2000)
    edits: list[SiteEdit] | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=80)
    symbol: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=600)
    image: str | None = Field(default=None, max_length=400)
    twitter: str | None = Field(default=None, max_length=200)
    telegram: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=200)


class SiteIn(BaseModel):
    """
    Either an address, or a description of a token that does not exist yet.

    The second is the ordinary case. People need the page before they launch,
    so demanding a mint made this useless to exactly the people it is for.
    """
    mint: str | None = Field(default=None, min_length=32, max_length=44)
    name: str | None = Field(default=None, max_length=80)
    symbol: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=600)
    image: str | None = Field(default=None, max_length=400)
    twitter: str | None = Field(default=None, max_length=200)
    telegram: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1200)


class PromptIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    format: ExportFormat | None = "pdf"
    target: PromptTarget | None = None


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
        declared after contract_address, so the check silently did nothing.
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
        return payment_required("risk-engine", "Payment required to use Agent Risk & Simulation Engine", RISK_ENGINE_PRICE_USDC, user_wallet, payment_method)

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
            user_wallet, payment_method,
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
    """
    Full system snapshot. Every figure here is measured when asked for.

    The `ok` field is kept at the top level because the header indicator on
    every page polls this and only reads that key.
    """
    import health
    import ledger_utils

    try:
        data = health.snapshot(
            solana_client=solana_client,
            ledger_utils=ledger_utils,
            redis_url=REDIS_URL,
        )
    except Exception:
        traceback.print_exc()
        return JSONResponse(status_code=503, content={"ok": False, "overall": "down"})

    # What each component costs and what it runs on, read from the same
    # configuration the checkout uses. The status page rendered these from a
    # hardcoded list, so changing a price in the environment moved what we
    # charge without moving what we advertise, and the model name stayed at
    # whatever it was when the list was written.
    import llm

    data["components"] = [
        {"name": "Prompt Optimizer", "price": PROMPT_OPTIMIZER_PRICE_USDC,
         "needs": ["ai", "workers"], "depends_on": [llm.MODEL]},
        {"name": "Code Explainer", "price": CODE_EXPLAINER_PRICE_USDC,
         "needs": ["ai", "workers"], "depends_on": [llm.MODEL]},
        {"name": "Prompt Tester", "price": PROMPT_TESTER_PRICE_USDC,
         "needs": ["ai", "workers"], "depends_on": [llm.MODEL]},
        {"name": "Risk Engine", "price": RISK_ENGINE_PRICE_USDC,
         "needs": ["ai", "workers"], "depends_on": [llm.MODEL, "matplotlib"]},
        {"name": "Contract Intelligence", "price": CONTRACT_INTEL_PRICE_USDC,
         "needs": ["ai", "workers", "solana"], "depends_on": [llm.MODEL, "chain data"]},
        {"name": "Launch Site Builder", "price": SITE_BUILDER_PRICE_USDC,
         "needs": ["ai", "workers"], "depends_on": [llm.MODEL, "pump.fun metadata"]},
        {"name": "Agent templates", "price": AGENT_PRICE_USDC,
         "needs": ["solana"], "depends_on": ["payment verification"]},
    ]
    data["currency"] = PAYMENT_CURRENCY

    return JSONResponse(status_code=200 if data["ok"] else 503, content=data)


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    return templates.TemplateResponse("status.html", {"request": request})

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
            "description": "Test your prompt against multiple AI personas, Developer, Skeptic, Hacker-to uncover blind spots, weaknesses, and ways to strengthen it.",
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
        {
            "id": 7,
            "slug": "site-builder",
            "name": "Launch Site Builder",
            "description": "A finished landing page for your token, before or after you launch. Describe it, or paste a contract address and it pulls the real name, ticker, image, supply and socials itself. One self-contained HTML file, ready to host.",
            "price": f"{SITE_BUILDER_PRICE_USDC} {PAYMENT_CURRENCY}",
            "type": "pay-per-use",
            "coming_soon": False,
        },
    ]

    return templates.TemplateResponse("shop.html", {
        "request": request,
        "components": components,
        "site_builder_price": SITE_BUILDER_PRICE_USDC,
        "site_revision_price": SITE_REVISION_PRICE_USDC,
    })


@app.get("/agents", response_class=HTMLResponse)
def agents_page(request: Request):
    return templates.TemplateResponse("agents.html", {"request": request})

@app.get("/learn", response_class=HTMLResponse)
def learn_page(request: Request):
    return templates.TemplateResponse("learn.html", {"request": request})

@app.get("/api/prices")
def component_prices():
    """
    What each component costs, in USDC.

    Prices are settings rather than constants, so anything that needs to show
    one has to ask. The alternative is a number written down in a second place
    that quietly stops matching the number people are charged.
    """
    return {
        "prompt-optimizer": float(PROMPT_OPTIMIZER_PRICE_USDC),
        "code-explainer": float(CODE_EXPLAINER_PRICE_USDC),
        "prompt-tester": float(PROMPT_TESTER_PRICE_USDC),
        "contract-intel": float(CONTRACT_INTEL_PRICE_USDC),
        "risk-engine": float(RISK_ENGINE_PRICE_USDC),
        "agent": float(AGENT_PRICE_USDC),
        "site-builder": float(SITE_BUILDER_PRICE_USDC),
        "site-revision": float(SITE_REVISION_PRICE_USDC),
    }


@app.get("/tg/{code}", response_class=HTMLResponse)
def telegram_link_page(code: str, request: Request):
    """
    Where a Telegram link is finished.

    The chat hands out the code and this page does the rest, because typing a
    wallet address into a chat on a phone is forty characters of base58 with no
    error correction, and the reward for a typo is a signature that will not
    verify against an address nobody meant to type.

    Whether the code is good is decided here rather than after somebody has
    connected a wallet and approved a signature.
    """
    import tg_link

    try:
        valid = tg_link.pending_code(code) is not None
    except Exception:
        logger.warning("Could not check a Telegram link code", exc_info=True)
        valid = False

    return templates.TemplateResponse("tg_link.html", {
        "request": request, "code": code, "valid": valid,
    })


class TelegramLinkIn(BaseModel):
    code: str
    wallet: str
    signature: str


@app.post("/api/tg/link")
def telegram_link(payload: TelegramLinkIn):
    """
    Finish the link, and tell the chat it worked.

    Nothing from the page is trusted. The chat comes from the stored code, the
    message is rebuilt from the code and the submitted wallet, and the
    signature has to verify against that. A page that lied about any of it
    links nothing.
    """
    import tg_link

    try:
        chat_id = tg_link.complete_code(
            payload.code, payload.wallet, payload.signature)
    except tg_link.LinkError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception:
        logger.exception("Telegram link failed")
        raise HTTPException(status_code=500, detail="That could not be linked.")

    # The person is on a web page, so the chat has no idea this happened. A
    # failure to say so is not a failure to link, which is why it cannot throw.
    try:
        import tg_http
        if tg_http.token():
            tg_http.HttpTransport().send_text(
                int(chat_id),
                f"Wallet linked:\n{payload.wallet}\n\n"
                "Everything you buy stays tied to it. /components shows what "
                "I can run.")
    except Exception:
        logger.warning("Linked, but could not tell the chat", exc_info=True)

    return {"status": "linked"}


@app.get("/legal", response_class=HTMLResponse)
def legal_page(request: Request):
    return templates.TemplateResponse("legal.html", {"request": request})

@app.get("/roadmap", response_class=HTMLResponse)
def roadmap_page(request: Request):
    return templates.TemplateResponse("roadmap.html", {"request": request})


@app.get("/api/burns")
def api_burns():
    """
    AETH taken as payment, and AETH destroyed.

    Both sides are counted from records rather than held as a running total,
    and every burn listed was confirmed against the chain before it was
    recorded. The signatures are here so anyone can check the arithmetic
    themselves instead of believing the totals.
    """
    try:
        return {**burn_ledger.summary(), "recent": burn_ledger.recent()}
    except Exception:
        raise HTTPException(status_code=503, detail="Burn ledger unavailable")


@app.get("/token", response_class=HTMLResponse)
def token_page(request: Request):
    """
    The $AETH page.

    It renders the mint address from AETH_MINT_ADDRESS through the aeth_mint
    template global and from nowhere else, so an unlaunched token shows an
    empty panel rather than anything that could be mistaken for an address.
    """
    try:
        burns = {**burn_ledger.summary(), "recent": burn_ledger.recent(8)}
    except Exception:
        # The page is about the token, not the burn table. A database problem
        # should not take it down during a launch.
        burns = None
    return templates.TemplateResponse("token.html",
                                      {"request": request, "burns": burns})
    
@app.get("/docs", response_class=HTMLResponse)
def docs_page(request: Request):
    return templates.TemplateResponse("docs.html", {"request": request})


@app.get("/sdk", include_in_schema=False)
def sdk_page_redirect():
    """The SDK page folded into Docs; keep old links working."""
    return RedirectResponse(url="/docs#sdk", status_code=308)

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
    /ledger?page=N, so both `page` and `total_pages` must always be supplied -
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

    # Named, not positional. row_to_dict already knew about the currency column
    # the template did not, so handing over dicts is what keeps the two in step
    # when the schema grows another field.
    entries = [row_to_dict(e) for e in entries]

    return templates.TemplateResponse("ledger.html", {
        "request": request,
        "entries": entries,
        "wallet": wallet or "Not connected",
        "page": page,
        "total_pages": total_pages,
    })

@app.get("/api/price/aeth")
def api_price_aeth(request: Request, component: str | None = None,
                   usdc_price: float | None = None, refresh: bool = False):
    """
    How many AETH this caller owes for a component.

    Name the component and the server looks the price up, applies whatever this
    wallet is entitled to, and converts. The price is deliberately not taken
    from the caller any more: passing it in meant the browser decided what
    something cost, and a discounted wallet was quoted the full amount, paid
    it, and had the overpayment accepted in silence.

    usdc_price is still honoured without a component so older clients keep
    working, but it cannot carry a discount, because a raw number does not say
    which component it belongs to.
    """
    wallet = (request.headers.get("X-USER-WALLET") or "").strip() or None

    if component:
        try:
            base = pricing.list_price(component)
        except KeyError:
            raise HTTPException(status_code=404, detail="Unknown component")
        quote = pricing.quote(component, wallet, "AETH")
        price_usd = quote["price"]
    elif usdc_price is not None:
        base = float(usdc_price)
        quote = None
        price_usd = base
    else:
        raise HTTPException(status_code=400, detail="Name a component")

    try:
        required = calculate_required_aeth(price_usd, force_refresh=refresh)
    except AethPricingError:
        raise HTTPException(status_code=502, detail="AETH pricing temporarily unavailable")
    except Exception:
        raise HTTPException(status_code=500, detail="Unexpected error")

    # Written down so settlement can honour it. Only for a named component and
    # a known wallet, because a bare price has nothing to key a promise to.
    if component and wallet:
        try:
            decimals = get_mint_decimals(AETH_MINT) if AETH_MINT else 6
            aeth_quotes.record(wallet, component,
                               int(round(required * (10 ** decimals))), price_usd)
        except Exception:
            # Failing to store a quote must not stop one being shown.
            pass

    return {
        "component": component,
        "usdc_price": price_usd,
        "list_price": base,
        "required_aeth": required,
        "quote_holds_for": aeth_quotes.QUOTE_TTL_SECONDS if (component and wallet) else 0,
        "discounts": quote["discounts"] if quote else [],
    }

@app.get("/api/ledger")
def ledger_api():
    return [row_to_dict(r) for r in get_recent(limit=50)]

class AgentSetup(BaseModel):
    """Values a buyer supplies for their copy of the agent."""
    config: dict | None = None


def _deliver_agent(agent_id: str, request: Request, x_payment, x_payment_method, answers):
    """Shared by both download routes: verify payment or a claim, then build."""
    payment_method = (x_payment_method or "USDC").upper()
    user_wallet = request.headers.get("X-USER-WALLET")

    # A giveaway winner downloads without paying, but only against a signature
    # from the wallet the prize was granted to. The header alone proves nothing:
    # winners post their addresses publicly, so anybody reading the thread could
    # otherwise claim somebody else's prize by typing it in.
    claim_nonce = request.headers.get("X-CLAIM-NONCE")
    claim_signature = request.headers.get("X-CLAIM-SIGNATURE")
    if claim_nonce and claim_signature:
        try:
            grants.verify_claim(user_wallet, agent_id, claim_nonce, claim_signature)
        except grants.ClaimError as exc:
            raise HTTPException(status_code=403, detail=str(exc))

        if agent_id not in agent_setup.AGENT_PATHS:
            raise HTTPException(status_code=404, detail="Invalid agent ID")
        try:
            data = agent_setup.build_zip(agent_id, answers or {})
        except agent_setup.SetupError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Spent only once the archive exists, so a build that fails leaves the
        # prize claimable rather than burning it.
        if not grants.mark_claimed(user_wallet, agent_id):
            raise HTTPException(status_code=409, detail="That prize was already claimed")

        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{agent_id}.zip"'},
        )

    payment_check = verify_payment(
        x_payment,
        user_wallet,
        float(AGENT_PRICE_USDC),
        payment_method,
        component="agent",
    )

    if payment_check is False:
        return payment_required(
            "agent", "Payment required to download this agent", AGENT_PRICE_USDC,
            user_wallet, payment_method,
        )

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

    if agent_id not in agent_setup.AGENT_PATHS:
        raise HTTPException(status_code=404, detail="Invalid agent ID")

    try:
        data = agent_setup.build_zip(agent_id, answers or {})
    except agent_setup.SetupError as exc:
        # A bad wallet address is the buyer's typo, not a server fault, and
        # they can fix it without paying again.
        raise HTTPException(status_code=400, detail=str(exc))

    # Held in memory: an archive carrying someone's endpoints has no reason to
    # be written to this server's disk, and a shared path raced between two
    # concurrent downloads of the same agent.
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{agent_id}.zip"'},
    )


PREVIEW_COOLDOWN_SECONDS = int(os.getenv("AGENT_PREVIEW_COOLDOWN", "20"))
_preview_last_seen: dict[str, float] = {}


def _preview_allowed(request: Request) -> bool:
    """
    One preview at a time per caller.

    This route spawns a process on demand without payment, so without a limit
    a single visitor holding the button could occupy every worker slot and
    stall the paid queue behind it.
    """
    caller = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    caller = caller or (request.client.host if request.client else "unknown")

    now = time.time()
    # Opportunistic sweep, so the map does not grow for the process lifetime.
    for key, seen in list(_preview_last_seen.items()):
        if now - seen > PREVIEW_COOLDOWN_SECONDS * 10:
            _preview_last_seen.pop(key, None)

    if now - _preview_last_seen.get(caller, 0) < PREVIEW_COOLDOWN_SECONDS:
        return False
    _preview_last_seen[caller] = now
    return True


@app.post("/api/agents/{agent_id}/preview")
def start_agent_preview(agent_id: str, request: Request):
    """Run the agent for a few seconds so a buyer can watch it before paying."""
    import agent_preview

    if agent_id not in agent_setup.AGENT_PATHS:
        raise HTTPException(status_code=404, detail="Invalid agent ID")
    if not agent_preview.is_previewable(agent_id):
        raise HTTPException(status_code=400, detail="This agent has no live preview.")
    if not _preview_allowed(request):
        raise HTTPException(
            status_code=429,
            detail=f"One preview every {PREVIEW_COOLDOWN_SECONDS} seconds. Try again shortly.",
        )

    # Metered per wallet, like the report examples. The cooldown above stops
    # one caller occupying every worker slot; this stops the free tier being
    # the whole product.
    wallet = (request.headers.get("X-USER-WALLET") or "").strip()
    if not wallet:
        raise HTTPException(
            status_code=401,
            detail="Connect a wallet to watch an agent run. Each wallet gets "
                   f"{ledger_utils.PREVIEW_ALLOWANCE}.",
        )

    claim = ledger_utils.claim_view(wallet, agent_id, "preview")
    if not claim["allowed"]:
        raise HTTPException(
            status_code=429,
            detail=f"You have watched all {claim['allowance']} of your agent runs. "
                   "You can still rewatch the ones you chose.",
        )

    try:
        from celery_worker import preview_agent
        task = preview_agent.delay(agent_id, agent_preview.MAX_SECONDS)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=503, detail="Preview is unavailable right now.")

    return JSONResponse(status_code=202, content={
        "task_id": task.id,
        "agent_id": agent_id,
        "seconds": agent_preview.MAX_SECONDS,
        "remaining": claim["remaining"],
        "already_seen": claim["already_seen"],
    })


@app.get("/api/agents/preview/{task_id}")
def agent_preview_result(task_id: str):
    """Poll a preview run."""
    from celery_worker import celery as celery_app
    res = AsyncResult(task_id, app=celery_app)
    if not res.ready():
        return {"ready": False, "state": res.state}
    if res.failed():
        return {"ready": True, "ok": False, "reason": "The preview failed to run."}
    payload = res.result if isinstance(res.result, dict) else {}
    return {"ready": True, **payload}


EXAMPLE_SLUGS = {
    "prompt-optimizer", "code-explainer", "prompt-tester",
    "contract-intel", "risk-engine",
}

# Some examples are pages rather than reports, and are opened rather than read
# in a text box. Nothing uses this at the moment, and it stays because the
# mechanism is what any future page shaped example would need.
EXAMPLE_AS_PAGE: set[str] = set()


@app.get("/api/examples/{slug}")
def read_example(slug: str, request: Request):
    """
    Hand over one example report, against the wallet's allowance.

    The allowance is shared across the whole shop rather than per component,
    so a visitor picks which reports to spend it on. Re-opening one already
    read is free: the limit is on how many different ones a wallet sees, not
    on how often it looks at the ones it chose.

    Served from here rather than as a static file because a static file cannot
    be counted.
    """
    if slug not in EXAMPLE_SLUGS:
        raise HTTPException(status_code=404, detail="No example for that component.")

    wallet = (request.headers.get("X-USER-WALLET") or "").strip()
    if not wallet:
        raise HTTPException(
            status_code=401,
            detail="Connect a wallet to read an example. Each wallet gets "
                   f"{ledger_utils.EXAMPLE_ALLOWANCE}.",
        )

    claim = ledger_utils.claim_example(wallet, slug)
    if not claim["allowed"]:
        raise HTTPException(
            status_code=429,
            detail=f"You have opened all {ledger_utils.EXAMPLE_ALLOWANCE} of your "
                   "examples. You can still reopen the ones you chose.",
        )

    as_page = slug in EXAMPLE_AS_PAGE
    ext = "html" if as_page else "txt"
    path = os.path.join("static", "examples", f"{slug}.{ext}")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Example not found.")

    with open(path, encoding="utf-8", errors="replace") as handle:
        body = handle.read()

    return JSONResponse({
        "slug": slug,
        "report": body,
        "as_page": as_page,
        "remaining": claim["remaining"],
        "already_seen": claim["already_seen"],
    })


@app.get("/api/examples")
def example_allowance(request: Request):
    """What this wallet has already opened, and how many it has left."""
    wallet = (request.headers.get("X-USER-WALLET") or "").strip()
    if not wallet:
        return {"connected": False, "allowance": ledger_utils.EXAMPLE_ALLOWANCE,
                "seen": [], "remaining": ledger_utils.EXAMPLE_ALLOWANCE}
    seen = ledger_utils.examples_seen(wallet)
    return {
        "connected": True,
        "allowance": ledger_utils.EXAMPLE_ALLOWANCE,
        "seen": seen,
        "remaining": max(0, ledger_utils.EXAMPLE_ALLOWANCE - len(seen)),
    }


@app.get("/api/my-prizes")
def api_my_prizes(request: Request):
    """Which agents this wallet can download for free, if any."""
    wallet = (request.headers.get("X-USER-WALLET") or "").strip() or None
    return {"wallet": wallet, "agents": grants.unclaimed(wallet)}


@app.post("/api/claim/{agent_id}/challenge")
def api_claim_challenge(agent_id: str, request: Request):
    """
    Issue a one time message for the winner's wallet to sign.

    Refused unless this wallet actually has an unclaimed prize for this agent,
    so the endpoint cannot be used to fish for who won what.
    """
    wallet = (request.headers.get("X-USER-WALLET") or "").strip() or None
    try:
        return grants.challenge(wallet, agent_id)
    except grants.ClaimError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.get("/api/agents/{agent_id}/setup")
def agent_setup_fields(agent_id: str):
    """
    What this agent needs before it can run. Public, so the shop can show the
    form before payment rather than after.
    """
    if agent_id not in agent_setup.AGENT_PATHS:
        raise HTTPException(status_code=404, detail="Invalid agent ID")
    return {
        "agent_id": agent_id,
        "fields": agent_setup.fields_for(agent_id),
        "local_only": [
            {"path": path, "why": why}
            for path, why in agent_setup.local_only_for(agent_id)
        ],
    }


@app.post("/api/download_agent/{agent_id}")
def download_agent_configured(
    agent_id: str,
    request: Request,
    payload: AgentSetup | None = None,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    """Download with the buyer's settings already written into config.json."""
    try:
        return _deliver_agent(
            agent_id, request, x_payment, x_payment_method,
            (payload.config if payload else None),
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download_agent/{agent_id}")
def download_agent(
    agent_id: str,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    """
    Download with defaults. Kept so existing clients and the SDK still work;
    settings are not accepted on a GET because they would be logged in the
    query string.
    """
    try:
        return _deliver_agent(agent_id, request, x_payment, x_payment_method, None)
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
            "description": "A customizable AI bot for Discord, moderation, auto-replies, chat, commands, and wallet verification.",
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
        return payment_required("contract-intel", "Payment required to use Contract Intelligence Analyzer", CONTRACT_INTEL_PRICE_USDC, user_wallet, payment_method)

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

# ── development bypass ──────────────────────────────────────────────────────
# Lets a component be run without paying, so a change can be checked end to end
# before it is charged for.
#
# Inert unless DEV_TOKEN is set. With it unset, which is how production runs,
# every path below returns False regardless of what a caller sends, so this
# being public gives nobody anything: the token is the secret, not the
# mechanism. Set it to a long random value if you ever set it at all.
DEV_TOKEN = (os.getenv("DEV_TOKEN") or "").strip()


def dev_unlocked(request: Request) -> bool:
    """Whether this caller holds the development token."""
    if not DEV_TOKEN:
        return False
    # The query string is accepted so one link unlocks and opens a page in a
    # single step. It is the least private of the three, since a URL ends up in
    # history and in logs, which is why the page that accepts it also drops a
    # cookie and stops needing it.
    supplied = (request.headers.get("X-DEV-TOKEN")
                or request.query_params.get("token")
                or request.cookies.get("aetheron_dev") or "")
    # Constant time, so a wrong value cannot be refined one character at a time
    # by measuring how long the comparison takes.
    return secrets.compare_digest(supplied, DEV_TOKEN)


@app.get("/dev/unlock")
def dev_unlock(request: Request, token: str = ""):
    """
    Exchange the token for a cookie, so the shop can be used normally.

    404 rather than 403 when the token is wrong or unset, so an instance that
    has no development access does not advertise that the route exists.
    """
    if not DEV_TOKEN or not secrets.compare_digest(token, DEV_TOKEN):
        raise HTTPException(status_code=404, detail="Not found")
    response = JSONResponse({"unlocked": True,
                             "note": "Components can now be run without paying."})
    response.set_cookie("aetheron_dev", DEV_TOKEN, httponly=True, samesite="lax")
    return response


@app.post("/api/site-builder")
def site_builder(
    payload: SiteIn,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    """
    Build a landing page for a token from its contract address.

    The address is checked before payment is asked for, so nobody pays to find
    out they pasted a wallet instead of a mint.
    """
    payment_method = x_payment_method or "USDC"
    user_wallet = request.headers.get("X-USER-WALLET")

    mint = (payload.mint or "").strip() or None
    details = None

    if mint:
        if not site_data.MINT_RE.match(mint):
            return JSONResponse(status_code=400, content={
                "error": "That does not look like a Solana mint address. It is the "
                         "token address, 32 to 44 base58 characters."})
    else:
        # No address, so the token has not launched and the details come from
        # the form instead.
        if not (payload.name or "").strip() or not (payload.symbol or "").strip():
            return JSONResponse(status_code=400, content={
                "error": "Give a contract address, or a name and a ticker if the "
                         "token has not launched yet."})
        details = {
            "name": payload.name, "symbol": payload.symbol,
            "description": payload.description, "image": payload.image,
            "twitter": payload.twitter, "telegram": payload.telegram,
            "website": payload.website,
        }

    # Shape is not enough. A wallet address is the same length and alphabet as a
    # mint, so pasting one passes every check that does not talk to the chain,
    # and the buyer would pay before finding out. Confirmed before the price is
    # ever quoted.
    if mint and not x_payment and not site_data.exists(mint):
        return JSONResponse(status_code=404, content={
            "error": "No token found at that address on pump.fun. Check it is the "
                     "token's mint address rather than a wallet, and note that "
                     "tokens launched elsewhere are not covered yet."})

    # A giveaway winner has one build waiting on their wallet. Spent here
    # rather than checked here, so two requests arriving together cannot both
    # take the same one, and put back below if the job never reaches the queue.
    on_the_house = (not dev_unlocked(request)
                    and not x_payment
                    and grants.spend_component(user_wallet, "site-builder"))

    # Development access skips the payment check entirely. Off unless DEV_TOKEN
    # is set, which production does not set.
    payment_check = True if (dev_unlocked(request) or on_the_house) else verify_payment(
        x_payment, user_wallet, float(SITE_BUILDER_PRICE_USDC),
        payment_method, component="site-builder",
    )

    if payment_check is False:
        return payment_required(
            "site-builder", "Payment required to build a site",
            SITE_BUILDER_PRICE_USDC, user_wallet, payment_method)

    if isinstance(payment_check, dict):
        return JSONResponse(status_code=402, content={
            "status": 402, "message": "Partial payment received",
            "paid": payment_check["paid"], "remaining": payment_check["remaining"],
            "currency": "AETH"})

    asset_id = "X402-SITE-" + ''.join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

    # Kept before the job is queued, so a build that dies partway can be run
    # again from what it was asked for rather than charged for twice.
    site_stream.remember(asset_id, {
        "mint": mint, "notes": payload.notes, "wallet": user_wallet,
        "details": details,
    })

    try:
        from celery_worker import process_site_builder
        task = process_site_builder.delay(
            asset_id, mint, payload.notes, user_wallet, details)
    except Exception:
        traceback.print_exc()
        # Nothing was built, so a prize spent on it has to come back. A winner
        # whose build never queued has not had their build.
        if on_the_house:
            grants.return_component(user_wallet, "site-builder")
        raise HTTPException(status_code=500, detail="Celery dispatch failed")

    try:
        add_entry(asset_id=asset_id, wallet=user_wallet, tx_sig=x_payment,
                  component="site-builder",
                  price=0.0 if on_the_house else float(SITE_BUILDER_PRICE_USDC),
                  currency="GIVEAWAY" if on_the_house else payment_method,
                  status="pending", filename=None)
    except Exception as e:
        print("Ledger log failure (site builder):", e)

    return {"task_id": task.id, "asset_id": asset_id, "status": "queued",
            "free": on_the_house}


@app.get("/telegram")
def telegram_page(request: Request):
    """
    What the bot and the channels are for, written before they ship.

    Here rather than in a thread because the thing people most want to know
    about a bot that takes payments is what it will not do, and that is worth a
    page somebody can read twice.
    """
    return templates.TemplateResponse("telegram.html", {"request": request})


@app.get("/api/my-grants/{wallet}")
def my_grants(wallet: str):
    """
    What this wallet can run for free.

    Read by the studio so a winner is told before they click rather than after
    a payment dialog they did not need to see.
    """
    return {"wallet": wallet,
            "components": grants.unclaimed_components(wallet)}


@app.post("/api/site-builder/retry/{asset_id}")
def site_builder_retry(asset_id: str, request: Request, mint: str | None = None):
    """
    Run a paid build again.

    A build can be lost between the payment and the file: a worker restarted
    mid generation leaves a ledger row sitting at pending and a buyer holding
    nothing. The money is already on chain and refunding it is not something
    this can do, so the answer is to make the thing that was paid for.

    Only the arguments already recorded for that asset are used, so this
    cannot be pointed at a different token, and only rows still pending are
    accepted, so a finished build cannot be re-run for free.
    """
    row = get_by_asset_id(asset_id)
    if not row:
        raise HTTPException(status_code=404, detail="No such build.")

    # Whoever paid can ask for their build again without going through anybody.
    # Needing an operator to recover it means most people never get it back.
    caller = request.headers.get("X-USER-WALLET")
    if not dev_unlocked(request) and caller != (row.get("wallet") or object()):
        raise HTTPException(status_code=403, detail="That build is not yours.")

    if (row.get("status") or "") != "pending":
        raise HTTPException(
            status_code=409,
            detail="That build already finished. Re-running it would be a "
                   "second build for one payment.")

    args = site_stream.recall(asset_id)
    if not args:
        # Builds queued before the arguments were being recorded have nothing
        # to recover from. The mint can be supplied for those, and only those:
        # the wallet still comes off the paid row, so the page can only ever
        # go to whoever paid for it.
        if not mint:
            raise HTTPException(
                status_code=404,
                detail="Nothing recorded for that build. Pass the mint to run "
                       "it again.")
        args = {"mint": mint, "notes": "", "wallet": row.get("wallet"),
                "details": None}
        site_stream.remember(asset_id, args)

    if not site_stream.count_retry(asset_id):
        raise HTTPException(
            status_code=429,
            detail="That build has been run again as many times as it can be. "
                   "Get in touch and it will be sorted by hand.")

    from celery_worker import process_site_builder
    task = process_site_builder.delay(
        asset_id, args.get("mint"), args.get("notes"), args.get("wallet"),
        args.get("details"))

    return {"task_id": task.id, "asset_id": asset_id, "status": "requeued"}


@app.get("/build")
def site_studio(request: Request):
    """
    The studio: the form on one side, the page building itself on the other.

    A page of its own rather than a dialog, because watching a site generate
    and then pointing at parts of it to change them does not fit in a box, and
    the old dialog gave no way to see what you were buying until it was bought.
    """
    unlocked = dev_unlocked(request)
    response = templates.TemplateResponse("site_studio.html", {
        "request": request,
        "site_builder_price": SITE_BUILDER_PRICE_USDC,
        "site_revision_price": SITE_REVISION_PRICE_USDC,
        "site_section_price": f"{pricing.section_price():.2f}",
        "aeth_enabled": bool(AETH_MINT),
        # The bypass is enforced server side, so the page worked without this
        # and only the buttons lied, still quoting a price for something that
        # was about to be free. Passed through so the studio can say plainly
        # that nothing is being charged.
        "dev_mode": unlocked,
    })

    # Arriving with ?token= leaves the cookie behind, so the rest of the visit
    # stays unlocked without the token riding along in every later URL.
    if unlocked and request.query_params.get("token"):
        response.set_cookie("aetheron_dev", DEV_TOKEN, httponly=True,
                            samesite="lax", max_age=60 * 60 * 12)
    return response


@app.get("/api/site-builder/stream/{asset_id}")
def site_builder_stream(asset_id: str, request: Request):
    """
    The page as it is written, replayable from the start.

    Polled with an index rather than held open, so a browser that reconnects
    picks up where it left off instead of watching half a page appear. The
    build itself runs in the worker and does not depend on anybody reading
    this: what was paid for is stored either way.
    """
    try:
        index = max(0, int(request.query_params.get("from", 0)))
    except ValueError:
        index = 0

    import site_stream
    chunks, next_index, state = site_stream.read_from(asset_id, index)

    detail = {}
    if state and state != "running":
        try:
            detail = json.loads(state)
        except (TypeError, ValueError):
            detail = {"status": "error", "error": "the build reported an unreadable state"}

    # No buffer at all means the job has not started writing yet, or the buffer
    # has expired. The caller has to be able to tell that from a finished one.
    status = detail.get("status") or ("running" if state else "waiting")

    return {"text": "".join(chunks), "next": next_index, "status": status,
            "filename": detail.get("filename"), "error": detail.get("error"),
            "project_id": detail.get("project_id")}


@app.get("/api/my-sites/{wallet}")
def api_my_sites(wallet: str):
    """
    Every site this wallet has built, so it can be downloaded again or changed.

    A generated page is a thing somebody paid for, not a one time download. The
    link expiring out of their browser history should not be the end of it.
    """
    import site_projects
    return {"projects": site_projects.for_wallet(wallet),
            "revision_price": SITE_REVISION_PRICE_USDC}


class SiteAddressIn(BaseModel):
    project_id: str = Field(min_length=4, max_length=64)
    address: str = Field(min_length=32, max_length=44)


class SiteRerollIn(BaseModel):
    project_id: str = Field(min_length=4, max_length=64)


@app.post("/api/site-builder/address")
def site_builder_address(payload: SiteAddressIn, request: Request):
    """
    Fill in the contract address on a page built before launch.

    Free, and instant, because it is a string replacement rather than a
    generation. Launch day otherwise means opening the file in a text editor
    and finding the line, which is the most likely reason somebody comes back
    and the worst possible moment to make them work for it.
    """
    import site_patch
    import site_projects
    from storage import load_asset_text

    user_wallet = request.headers.get("X-USER-WALLET")

    if not site_data.MINT_RE.match(payload.address.strip()):
        return JSONResponse(status_code=400, content={
            "error": "That does not look like a Solana mint address. It is the "
                     "token address, 32 to 44 base58 characters."})

    if not dev_unlocked(request) and not site_projects.owned_by(
            payload.project_id, user_wallet):
        return JSONResponse(status_code=403, content={
            "error": "That site belongs to a different wallet."})

    filename = site_projects.latest_file(payload.project_id)
    html = load_asset_text(filename) if filename else None
    if not html:
        return JSONResponse(status_code=409, content={
            "error": "There is no finished version of this site to change."})

    try:
        patched = site_patch.set_contract_address(html, payload.address)
    except site_patch.PatchError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    asset_id = "X402-SITE-" + ''.join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
    version = site_projects.add_version(
        payload.project_id, asset_id, "filled in the contract address")

    fname = asset_naming.asset_filename(asset_id, "html")
    url = storage.store_asset(patched.encode("utf-8"), fname)
    ledger_utils.finalize_asset(asset_id, fname)
    site_projects.finish(asset_id, fname)

    try:
        add_entry(asset_id=asset_id, wallet=user_wallet, tx_sig=None,
                  component="site-address", price=0.0, currency="USDC",
                  status="success", filename=fname)
    except Exception as e:
        print("Ledger log failure (site address):", e)

    return {"filename": fname, "download_url": url, "version": version,
            "project_id": payload.project_id, "address": payload.address.strip()}


@app.post("/api/site-builder/reroll")
def site_builder_reroll(
    payload: SiteRerollIn,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    """
    Build the same token again in a different look.

    The direction is decided by hashing the token, which is right for making two
    projects look different and wrong for the person who simply does not like
    what they got. Without this their only move is paying for edits that
    describe a whole new design, which is the dearest path and the one least
    likely to work.

    The first is free, because somebody who dislikes the first result is on the
    way to asking for their money back and the answer to that should be another
    attempt rather than an argument.
    """
    import site_projects

    payment_method = x_payment_method or "USDC"
    user_wallet = request.headers.get("X-USER-WALLET")

    project = site_projects.get(payload.project_id)
    if not project:
        return JSONResponse(status_code=404, content={
            "error": "That site could not be found."})

    if not dev_unlocked(request) and not site_projects.owned_by(
            payload.project_id, user_wallet):
        return JSONResponse(status_code=403, content={
            "error": "That site belongs to a different wallet."})

    used = site_projects.rerolls_used(payload.project_id)
    free = used < 1

    if not free:
        price = pricing.revision_quote(
            [{"selector": None, "description": "redesign the whole page"}])["price"]
        payment_check = True if dev_unlocked(request) else verify_payment(
            x_payment, user_wallet, price, payment_method,
            component="site-revision")

        if payment_check is False:
            return payment_required(
                "site-revision", "Payment required for another design",
                f"{price:.2f}", user_wallet, payment_method)
        if isinstance(payment_check, dict):
            return JSONResponse(status_code=402, content={
                "status": 402, "message": "Partial payment received",
                "paid": payment_check["paid"],
                "remaining": payment_check["remaining"], "currency": "AETH"})

    asset_id = "X402-SITE-" + ''.join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
    offset = site_projects.next_direction(payload.project_id)
    site_projects.add_version(payload.project_id, asset_id, "a different design")

    try:
        from celery_worker import process_site_builder
        task = process_site_builder.delay(
            asset_id, project.get("mint"), None, user_wallet,
            project.get("details") or None, offset, payload.project_id)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Celery dispatch failed")

    return {"task_id": task.id, "asset_id": asset_id,
            "project_id": payload.project_id, "free": free,
            "rerolls_used": used + 1}


class SiteSectionIn(BaseModel):
    project_id: str = Field(min_length=4, max_length=64)
    section: str = Field(min_length=2, max_length=24)
    instruction: str = Field(min_length=3, max_length=1200)


@app.get("/api/site-builder/bundle/{project_id}")
def site_builder_bundle(project_id: str, request: Request):
    """
    The page as a zip, with a page of instructions on how to put it online.

    A buyer gets one HTML file, which is right technically and leaves a good
    number of them holding something they do not know what to do with. Built
    here rather than by the model, since it is the same instructions every time.
    """
    import site_bundle
    import site_projects
    from storage import load_asset_text

    user_wallet = request.headers.get("X-USER-WALLET")

    if not dev_unlocked(request) and not site_projects.owned_by(
            project_id, user_wallet):
        raise HTTPException(status_code=403, detail="That site is not yours.")

    filename = site_projects.latest_file(project_id)
    html = load_asset_text(filename) if filename else None
    if not html:
        raise HTTPException(status_code=404, detail="Nothing finished to package.")

    project = site_projects.get(project_id) or {}
    name = (project.get("symbol") or project.get("name") or "site").lower()
    name = re.sub(r"[^a-z0-9-]+", "-", name).strip("-") or "site"

    data = site_bundle.build(html, launched=site_bundle.is_launched(html))
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}-site.zip"'},
    )


@app.post("/api/site-builder/section")
def site_builder_section(
    payload: SiteSectionIn,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    """
    Rewrite one section, leaving the rest of the page alone.

    Priced between an edit and a build, because that is what it is: more work
    than changing a line, far less than writing a document.
    """
    import site_projects

    payment_method = x_payment_method or "USDC"
    user_wallet = request.headers.get("X-USER-WALLET")

    allowed = {"hero", "contract", "about", "how-to-buy", "market", "links", "footer"}
    section = payload.section.strip().lower()
    if section not in allowed:
        return JSONResponse(status_code=400, content={
            "error": f"There is no {section} section. It is one of: "
                     + ", ".join(sorted(allowed))})

    if not dev_unlocked(request) and not site_projects.owned_by(
            payload.project_id, user_wallet):
        return JSONResponse(status_code=403, content={
            "error": "That site belongs to a different wallet."})

    if not site_projects.latest_file(payload.project_id):
        return JSONResponse(status_code=409, content={
            "error": "This site has no finished version to rewrite part of."})

    price = pricing.section_price()
    payment_check = True if dev_unlocked(request) else verify_payment(
        x_payment, user_wallet, price, payment_method, component="site-revision")

    if payment_check is False:
        return payment_required(
            "site-revision", f"Payment required to rewrite the {section} section",
            f"{price:.2f}", user_wallet, payment_method)
    if isinstance(payment_check, dict):
        return JSONResponse(status_code=402, content={
            "status": 402, "message": "Partial payment received",
            "paid": payment_check["paid"],
            "remaining": payment_check["remaining"], "currency": "AETH"})

    asset_id = "X402-SITE-" + ''.join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
    site_projects.add_version(
        payload.project_id, asset_id, f"rewrote the {section} section")

    try:
        from celery_worker import process_site_section
        task = process_site_section.delay(
            asset_id, payload.project_id, section, payload.instruction, user_wallet)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Celery dispatch failed")

    try:
        add_entry(asset_id=asset_id, wallet=user_wallet, tx_sig=x_payment,
                  component="site-section", price=price, currency=payment_method,
                  status="pending", filename=None)
    except Exception as e:
        print("Ledger log failure (site section):", e)

    return {"task_id": task.id, "asset_id": asset_id,
            "project_id": payload.project_id, "section": section, "paid": price}


@app.post("/api/site-builder/quote")
def site_builder_quote(payload: SiteReviseIn):
    """
    What a batch of changes would cost, before anybody commits to it.

    Here so the studio can show a running total without working the price out
    itself. Two implementations of a price disagree eventually, and the half
    that is wrong is either the number somebody was shown or the number they
    were charged.

    Costs nothing to call and moves nothing, so it needs no payment and no
    ownership check: it prices a description, and knowing what a change would
    cost gives away nothing about whose page it is.
    """
    edits = [e.model_dump() for e in (payload.edits or [])
             if (e.description or "").strip()]
    if not edits and (payload.notes or "").strip():
        edits = [{"description": payload.notes, "selector": None}]

    quote = pricing.revision_quote(edits)
    quote["tier_labels"] = [pricing.describe_tier(t) for t in quote["tiers"]]
    return quote


@app.post("/api/site-builder/revise")
def site_builder_revise(
    payload: SiteReviseIn,
    request: Request,
    x_payment: str | None = Header(default=None, alias="X-TX-SIG"),
    x_payment_method: str | None = Header(default=None, alias="X-PAYMENT-METHOD"),
):
    """
    Change a page that has already been built, for less than building one.

    Ownership is checked before the price is quoted, so nobody pays to be told
    the site is not theirs, and nobody pays the smaller revision price to edit
    somebody else's page.
    """
    import site_projects

    payment_method = x_payment_method or "USDC"
    user_wallet = request.headers.get("X-USER-WALLET")

    project = site_projects.get(payload.project_id)
    if not project:
        return JSONResponse(status_code=404, content={
            "error": "That site could not be found."})

    # Checked before payment. A revision costs less than a build, so without
    # this the cheapest way to edit any page on the platform would be to edit
    # one belonging to somebody else.
    if not dev_unlocked(request) and not site_projects.owned_by(
            payload.project_id, user_wallet):
        return JSONResponse(status_code=403, content={
            "error": "That site belongs to a different wallet. Connect the wallet "
                     "that built it to make changes."})

    if not site_projects.latest_file(payload.project_id):
        return JSONResponse(status_code=409, content={
            "error": "This site has no finished version yet, so there is nothing "
                     "to change. Wait for the build to land, or build it again."})

    edits = [e for e in (payload.edits or []) if (e.description or "").strip()]
    if not edits and not (payload.notes or "").strip():
        return JSONResponse(status_code=400, content={
            "error": "Say what you want changed."})

    # Priced by what the changes are rather than by how many. One revision is
    # one generation call whatever it is asked to do, so counting changes would
    # charge five times for work done once, and would put the same price on
    # renaming a heading as on adding a section.
    quote = pricing.revision_quote(
        [e.model_dump() for e in edits] or [{"description": payload.notes}])
    price = quote["price"]
    units = max(1, len(edits))

    payment_check = True if dev_unlocked(request) else verify_payment(
        x_payment, user_wallet, price,
        payment_method, component="site-revision",
    )

    if payment_check is False:
        return payment_required(
            "site-revision",
            f"Payment required for {units} change{'s' if units > 1 else ''}",
            f"{price:.2f}", user_wallet, payment_method)

    if isinstance(payment_check, dict):
        return JSONResponse(status_code=402, content={
            "status": 402, "message": "Partial payment received",
            "paid": payment_check["paid"], "remaining": payment_check["remaining"],
            "currency": "AETH"})

    asset_id = "X402-SITE-" + ''.join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))

    details = {
        "name": payload.name, "symbol": payload.symbol,
        "description": payload.description, "image": payload.image,
        "twitter": payload.twitter, "telegram": payload.telegram,
        "website": payload.website,
    }
    details = {k: v for k, v in details.items() if (v or "").strip()}

    # One instruction per change, each naming the element it was aimed at.
    if edits:
        change_text = "\n".join(
            (f"In the element matching `{e.selector}`"
             + (f" (the {e.label})" if e.label else "") + f": {e.description.strip()}")
            if e.selector else f"{e.description.strip()}"
            for e in edits)
    else:
        change_text = (payload.notes or "").strip()

    site_projects.add_version(payload.project_id, asset_id, change_text)

    try:
        from celery_worker import process_site_revision
        task = process_site_revision.delay(
            asset_id, payload.project_id, change_text, user_wallet,
            details or None)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Celery dispatch failed")

    try:
        add_entry(asset_id=asset_id, wallet=user_wallet, tx_sig=x_payment,
                  component="site-revision", price=price,
                  currency=payment_method, status="pending", filename=None)
    except Exception as e:
        print("Ledger log failure (site revision):", e)

    return {"task_id": task.id, "asset_id": asset_id,
            "project_id": payload.project_id, "status": "queued",
            "changes": units, "paid": price}


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
        return payment_required("prompt-optimizer", "Payment required to use AI Prompt Optimizer", PROMPT_OPTIMIZER_PRICE_USDC, user_wallet, payment_method)

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
            request.headers.get("X-USER-WALLET"),
            payload.target,
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
        return payment_required("code-explainer", "Payment required to use LLM-Powered Code Explainer", CODE_EXPLAINER_PRICE_USDC, user_wallet, payment_method)

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
        return payment_required("prompt-tester", "Payment required to use Smart Prompt Tester", PROMPT_TESTER_PRICE_USDC, user_wallet, payment_method)

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
    """
    Serve a generated report from whichever backend stored it.

    The filename is echoed into a Content-Disposition header and, on the R2
    path, appended to a bucket URL, so it is constrained to the shape our own
    generator produces before it is used for either.
    """
    if not filename or not ASSET_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    stored = storage.fetch_asset(filename)
    if stored is not None:
        data, content_type = stored
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                # The site builder returns a live HTML document. It is already
                # sent as an attachment so it downloads rather than executing on
                # this origin, and this stops a browser second guessing the type
                # and rendering it anyway.
                "X-Content-Type-Options": "nosniff",
            },
        )

    # R2 is configured, so the file lives in the bucket rather than the database.
    public_base = os.getenv("R2_PUBLIC_BASE")
    if not public_base:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        r = requests.get(f"{public_base.rstrip('/')}/{filename}", stream=True, timeout=30)
        r.raise_for_status()
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")

    return StreamingResponse(
        r.iter_content(chunk_size=8192),
        media_type=guess_media_type(filename),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """
    A rejected request, logged so it can be diagnosed without guessing.

    The default answer is a list of objects, which is fine for a machine and
    useless to a person watching a browser console. This logs which field was
    refused and how long the value was, never the value itself, because these
    bodies carry wallet addresses and whatever somebody typed about their
    project.

    The response body keeps FastAPI's shape, since clients already read it, and
    gains a `message` that can be shown to somebody as it is.
    """
    fields = []
    for error in exc.errors():
        where = ".".join(str(p) for p in error.get("loc", ()) if p != "body")
        given = error.get("input")
        size = len(given) if isinstance(given, str) else None
        fields.append(f"{where or 'body'}({error.get('type')}"
                      + (f", {size} chars" if size is not None else "") + ")")

    print(f"422 on {request.url.path}: " + ", ".join(fields))

    readable = "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', ()) if p != 'body') or 'input'}: "
        f"{e.get('msg', 'is not valid')}"
        for e in exc.errors())

    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "message": readable},
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

