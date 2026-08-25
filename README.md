<div align="center">

<img src="docs/banner.svg" alt="Aetheron" width="100%" />

<br/>

### $AETH

**Not yet launched.**

When it is, the address will be published here and on
[aetheron402.com](https://www.aetheron402.com), and nowhere else. This repository is
the only place, besides the site itself, where that address is published by the
project. Any token presenting itself as Aetheron before then, or with a different
address after, is not ours.

<br/>

<a href="https://readme-typing-svg.demolab.com">
  <img src="https://readme-typing-svg.demolab.com?font=JetBrains+Mono&weight=600&size=21&duration=2600&pause=900&color=22D3EE&center=true&vCenter=true&width=820&height=44&lines=Ask+for+the+work.+Get+402+back.;Pay+on+chain.+Ask+again.;The+server+checks+the+transfer%2C+not+your+word.;No+key+ever+leaves+your+wallet." alt="Aetheron" />
</a>

<br/><br/>

[![SDK](https://img.shields.io/badge/SDK-0.3.0-22d3ee?style=flat-square)](#sdk)
[![Components](https://img.shields.io/badge/components-5-22d3ee?style=flat-square)](#the-components)
[![Agents](https://img.shields.io/badge/agent%20templates-9-22d3ee?style=flat-square)](#the-agents)
![Tests](https://img.shields.io/badge/tests-29%20passing-22d3ee?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-22d3ee?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-3776ab?style=flat-square&logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-SDK-3178c6?style=flat-square&logo=typescript&logoColor=white)
![Self-hostable](https://img.shields.io/badge/self--hostable-yes-22d3ee?style=flat-square)

<br/>

<img src="https://skillicons.dev/icons?i=py,fastapi,ts,tailwind,redis,postgres,sqlite,nodejs" alt="stack" />

</div>

<br/>

**Paid AI components, priced per call, settled on Solana.** Ask an endpoint to do
the work and it answers `402 Payment Required` with the amount, the currency and the
wallet. Send the transfer yourself, from your own wallet, and ask again with the
signature. The server reads the transaction off the chain, checks that its own
balance actually went up, and only then does the work.

No account. No card. No key ever leaves your wallet — this codebase cannot sign a
transaction, because it never holds anything to sign with.

---

## How it works

### The 402 carries everything you need to pay it

An unpaid request is not an error page. It is the terms:

```console
$ curl -s -X POST localhost:8000/api/prompt-optimizer \
       -H 'Content-Type: application/json' -d '{"text":"..."}'
```

```json
{
  "status": 402,
  "component": "prompt-optimizer",
  "required": 0.25,
  "currency": "USDC",
  "network": "Solana",
  "wallet": "<the receiving address>",
  "accepted_methods": ["USDC"]
}
```

Amount, denomination, chain and destination, in the response that refused you. A
client never has to be told out-of-band what a call costs, and the page you are
looking at never has to restate the address — it reads the same value the verifier
checks against, so the address you are shown and the address that is accepted cannot
drift apart.

### Payment means *our* balance went up

The verifier does not ask whether tokens moved. It asks whether they moved **to us**:

```python
received = extract_received_amount(tx, target_mint, PAYMENT_WALLET)
```

It walks the transaction's post balances, keeps only accounts owned by the payment
wallet holding the expected mint, matches each against its own prior balance by
account index, and sums the increase. A transfer between two wallets you control
credits nothing. Buying the token on a DEX credits nothing. Paying somebody else
credits nothing. The signer must also be the wallet claiming the purchase.

This is exercised directly, with the transaction shapes that would abuse it:

```console
$ pytest -q
29 passed
```

### A signature buys one thing, once

Claiming a payment is an insert whose primary key is the transaction signature:

```sql
INSERT INTO consumed_signatures (tx_signature, wallet, component, amount, ...)
```

The write *is* the check. Two requests racing with the same signature cannot both
be credited, because the second insert fails rather than the second read coming back
empty. Underpay and the shortfall is recorded against your wallet and that component,
so a later transfer tops it up — but the signature that paid it is already spent and
cannot be presented again.

### It runs without us

Postgres, Cloudflare R2 and the market-data keys are what production uses, not what
the code requires. Leave `DB_HOST` unset and the ledger is a local SQLite file. The
token price comes from DexScreener with a pump.fun bonding-curve fallback, neither of
which needs an API key. Clone it, add an OpenAI key and a wallet address, and the
whole marketplace runs on your machine.

---

## The components

Five, each priced per call. Every one returns a report as PDF, DOCX, HTML, Markdown
or plain text.

| Component | Price | What it does |
|---|---|---|
| **Prompt Optimizer** | $0.25 | Rewrites loose text into a structured, agent-ready prompt |
| **Code Explainer** | $0.50 | Explains a file, rates its complexity, proposes refactors |
| **Prompt Tester** | $0.50 | Runs one prompt past several personas and reports how each reacts |
| **Risk Engine** | $0.75 | Monte Carlo GBM simulation with charted paths and a distribution |
| **Contract Intelligence** | $1.00 | Holder concentration, LP lock, admin powers and honeypot checks on a Solana or Ethereum token |

The first three need nothing but an OpenAI key. Contract Intelligence and the Risk
Engine read the chain, and degrade to what they can still source when a given data
provider is absent.

## The agents

Nine downloadable Python projects, $4.99 each, licensed for you to run and modify.
They are yours after purchase and depend on nothing here to keep working.

| | | |
|---|---|---|
| `alpha-scanner` | `discord-helper` | `market-tracker` |
| `prediction-market` | `project-planner` | `pumpfun-launcher` |
| `solana-sniper` | `solana-trading-assistant` | `wallet-watcher` |

Each ships with its own README, `config.json`, requirements and licence.

---

## SDK

`sdk/` is the TypeScript client, browser-first and framework-agnostic. It implements
the 402 exchange without hiding it: nothing is auto-paid, and no transaction is
built, signed or submitted on your behalf.

```ts
import { AetheronSDK } from "aetheron-sdk";

const aetheron = new AetheronSDK(wallet, connection);

try {
  await aetheron.promptOptimizer({ text: "turn this into a real prompt" });
} catch (err) {
  const terms = aetheron.getPaymentInfo(err);   // null if it was a real error
  if (terms) {
    terms.required;   // 0.25
    terms.currency;   // "USDC"
    terms.wallet;     // where to send it
  }
}

// The user signs and sends the transfer. Then ask again, with the signature.
const report = await aetheron.promptOptimizer(
  { text: "turn this into a real prompt", format: "pdf" },
  { txSig }
);
```

`promptOptimizer`, `codeExplainer`, `promptTester`, `contractIntel` and
`downloadAgent` each wrap `callPaidComponent`, which takes any endpoint — so the
Risk Engine, and anything added later, is reachable before a named method exists for
it. `isPaymentRequired` and `getPaymentInfo` exist so a 402 can be told apart from a
genuine failure without inspecting status codes by hand.

The endpoint defaults to the origin serving the page, so a UI hosted by Aetheron
needs no configuration; anything else passes `{ endpoint }` explicitly rather than
having requests sent to a host it merely assumed.

An MCP server is planned, so an agent can buy and run a component the same way — the
402 exchange is already the whole interface, and nothing about it is browser-specific.

---

## Architecture

One FastAPI application serving both the site and the API, with Celery workers doing
everything slow. Payment verification is synchronous and happens before a job is ever
queued.

| Module | Responsibility | Reaches out |
|---|---|---|
| `Aetheron.py` | Routes, the 402 exchange, payment verification | network |
| `celery_worker.py` | Component execution, LLM calls, on-chain intel | network |
| `ledger_utils.py` | Ledger, replay protection, partial payments | database |
| `aeth_price.py` | AETH priced via DexScreener, pump.fun fallback | network |
| `asset_naming.py` | Unguessable, collision-free names for generated files | |
| `pdf_utils.py` | Report typesetting | |
| `export_utils.py` | DOCX, HTML, Markdown and text output | |
| `r2_client.py` | Object storage | network |

Amounts are integers everywhere. Prices are read as decimal strings and converted to
base units once, at the point of comparison; nothing in this repository holds money
in a float.

Generated reports are served from a public bucket, so their filenames are
cryptographically random rather than derived from a timestamp — the name is the only
thing standing between a report and anyone else.

---

## Run your own

Requires Python 3.11 and Redis.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # set PAYMENT_WALLET and OPENAI_API_KEY
redis-server --daemonize yes

.venv/bin/uvicorn Aetheron:app --reload
.venv/bin/celery -A celery_worker.celery worker --loglevel=info
```

That is the whole setup. `DB_HOST` left empty gives you a SQLite ledger, so there is
no database to provision.

```bash
pip install -r requirements-dev.txt
pytest -q                     # 29 tests, no network
```

`.env.example` documents every setting and marks which are genuinely required. Only
three are: the receiving wallet, an OpenAI key, and Redis.

---

## Licence

[MIT](LICENSE). Fork it, run it, sell it. The components are the product; the code
that charges for them is not a secret worth keeping.
