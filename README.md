<div align="center">

<img src="docs/banner.png" alt="Aetheron" width="100%" />

<br/><br/>

[![License](https://img.shields.io/badge/license-MIT-3b82f6?style=for-the-badge)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-326_passing-3b82f6?style=for-the-badge)](tests/)
[![SDK](https://img.shields.io/badge/SDK-TypeScript_0.3.0-3b82f6?style=for-the-badge)](sdk/)
[![Python](https://img.shields.io/badge/Python-3.11-3b82f6?style=for-the-badge)](requirements.txt)

**[Website](https://aetheronprotocol.com)** · **[X](https://x.com/Aetheron402)** · **[SDK](sdk/)** · **[Agents](static/agents_src/)**

</div>

<br/>

Aetheron is a shop for AI work you pay for one call at a time.

There is no signup, no subscription and no card. You ask an endpoint to do something,
it quotes you a price, you send the money from your own wallet, and you ask again.
The whole exchange is four HTTP messages and one Solana transfer.

<br/>

<div align="center">

| | |
|:--|:--|
| **6 components** | prompts, code, personas, token risk, Monte Carlo, launch sites |
| **9 agent templates** | full Python projects, yours to keep |
| **$0.25 to $4.99** | per call, in USDC |
| **Non-custodial** | this code cannot sign anything |
| **Self-hostable** | clone, add a key, run |

</div>

---

## What's in the shop

Every component returns a written report: PDF, DOCX, HTML, Markdown or plain text.

| Component | Price | What you get |
|---|---:|---|
| **Prompt Optimizer** | `$0.25` | Loose text rewritten into a structured, agent-ready prompt |
| **Code Explainer** | `$0.50` | A file explained, rated for complexity, with refactors proposed |
| **Prompt Tester** | `$0.50` | One prompt run past several personas, with each reaction reported |
| **Risk Engine** | `$0.75` | Monte Carlo GBM simulation, charted paths and outcome distribution |
| **Contract Intelligence** | `$1.00` | Holder concentration, LP lock, admin powers and honeypot checks |
| **Launch Site Builder** | `$2.50` | A landing page for your token, before or after launch, as one self-contained file |

The first three need only an Anthropic key. The last two read Solana and Ethereum, and
fall back to whatever sources remain when a provider is missing.

### Agent templates, `$4.99` each

Nine complete Python projects. You download the source, and it is yours: they run on
your machine and depend on nothing here to keep working.

<div align="center">

| | | |
|:--|:--|:--|
| `alpha-scanner` | `discord-helper` | `market-tracker` |
| `prediction-market` | `project-planner` | `pumpfun-launcher` |
| `solana-sniper` | `solana-trading-assistant` | `wallet-watcher` |

</div>

Each ships with a README, a `config.json`, its requirements and a licence.

---

## The exchange

**1. Ask.** No payment attached.

```console
$ curl -sX POST localhost:8000/api/prompt-optimizer \
       -H 'Content-Type: application/json' -d '{"text":"..."}'
```

**2. Get quoted.** The refusal carries the terms, so you never need telling out-of-band
what a call costs.

```json
{
  "status": 402,
  "component": "prompt-optimizer",
  "required": 0.25,
  "currency": "USDC",
  "network": "Solana",
  "wallet": "<receiving address>",
  "accepted_methods": ["USDC"]
}
```

**3. Pay.** From your wallet, signed by you. Aetheron never builds, signs or submits
the transaction, and holds no key that could.

**4. Ask again**, with `X-TX-SIG` set to the signature. The server reads the
transaction off-chain, confirms the money arrived, and queues the work.

That is the entire protocol. It is plain HTTP, so `curl` is a complete client.

---

## What the verifier refuses

Checking that "a transfer happened" is not the same as checking that **you were paid**.
Aetheron only counts the increase in its own token account:

```python
received = extract_received_amount(tx, target_mint, PAYMENT_WALLET)
```

It keeps only post-balances owned by the payment wallet holding the expected mint,
matches each to its own prior balance by account index, and sums the rise. So:

| Transaction | Credited |
|---|---:|
| Moving tokens between two wallets you control | `0` |
| Buying the token on a DEX | `0` |
| Paying somebody else | `0` |
| Reusing a signature that already bought something | `409` |
| An actual transfer to the payment wallet | the full amount |

The signer must also be the wallet claiming the purchase, and claiming a payment is
an `INSERT` keyed on the signature. The write *is* the check, so two requests racing
with the same signature cannot both succeed.

Underpay and the shortfall is recorded against your wallet and that component, so a
later transfer completes it. The signature that paid it is already spent.

Each of these has a test standing over it:

```console
$ pytest -q
29 passed
```

---

## Quick start

You need Python 3.11 and Redis. Nothing else: no database to provision, no cloud
account to open.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env        # set PAYMENT_WALLET and ANTHROPIC_API_KEY
redis-server --daemonize yes

.venv/bin/uvicorn Aetheron:app --reload
.venv/bin/celery -A celery_worker.celery worker --loglevel=info
```

Open `http://127.0.0.1:8000`.

Leaving `DB_HOST` empty puts the ledger in a local SQLite file; set it and the same
code runs on Postgres. Generated reports go to Cloudflare R2 in production, and the
token price comes from DexScreener with a pump.fun fallback, and neither needs a key.

`.env.example` documents every setting. Exactly three are required: the receiving
wallet, an Anthropic key, and Redis.

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q          # 326 tests
```

---

## Layout

One FastAPI app serves both the site and the API. Celery does everything slow.
Payment is verified synchronously, before any job is queued.

```
Aetheron.py          routes, the 402 exchange, payment verification
celery_worker.py     component execution, LLM calls, on-chain intel
ledger_utils.py      ledger, replay protection, partial payments
aeth_price.py        AETH priced via DexScreener, pump.fun fallback
asset_naming.py      unguessable names for generated reports
pdf_utils.py         report typesetting
export_utils.py      DOCX, HTML, Markdown, text
r2_client.py         object storage

templates/           the site
static/agents_src/   the nine agent templates
sdk/                 TypeScript client
tests/               payment and validation tests
```

Money is integers everywhere. Prices are read as decimal strings and converted to
base units once, at the point of comparison, so nothing here holds a balance in a float.

Reports are served from a public bucket, so their filenames are cryptographically
random rather than derived from a timestamp: the name is the only thing standing
between a report and everyone else.

---

## SDK

`sdk/` is the TypeScript client. Browser-first, framework-agnostic, and it does not
hide the payment step. Nothing is auto-paid, and no transaction is signed for you.

```ts
import { AetheronSDK } from "aetheron-sdk";

const aetheron = new AetheronSDK(wallet, connection);

try {
  await aetheron.promptOptimizer({ text: "turn this into a real prompt" });
} catch (err) {
  const terms = aetheron.getPaymentInfo(err);   // null if it was a genuine error
  terms?.required;   // 0.25
  terms?.wallet;     // where to send it
}

// user signs and sends, then:
const report = await aetheron.promptOptimizer(
  { text: "turn this into a real prompt", format: "pdf" },
  { txSig }
);
```

`promptOptimizer`, `codeExplainer`, `promptTester`, `contractIntel` and `downloadAgent`
all wrap `callPaidComponent`, which accepts any endpoint, so the Risk Engine, and
anything added later, is reachable before a named method exists for it.

The endpoint defaults to the origin serving the page, so a UI hosted by Aetheron needs
no configuration. Anything else passes `{ endpoint }` explicitly rather than firing
requests at a host it merely assumed.

An MCP server is planned. The 402 exchange is already the whole interface, and nothing
about it is specific to a browser.

---

## $AETH

**Live on pump.fun.** The mint address is:

```
D3qncuGsa2iMKcaxnqZxUMeVqPztzyAr819nXfjypump
```

The same address is published at
[aetheronprotocol.com/token](https://aetheronprotocol.com/token) and on
[@Aetheron402](https://x.com/Aetheron402). All three agree. Treat any Aetheron
token announced anywhere else as fake, and compare the address in full rather
than the first and last four characters.

The mint has **no mint authority and no freeze authority**, so the supply cannot
be increased and no holder's tokens can be frozen. Both are readable on chain
and worth checking yourself rather than taking from this file.

Components can be priced in AETH as well as USDC. Setting `AETH_MINT_ADDRESS`
turns that on everywhere at once (the shop, the SDK and the `accepted_methods`
in every 402) with no redeploy and no code change.

---

## Licence

[MIT](LICENSE). Fork it, run it, charge for it. The components are the product; the
code that takes payment for them was never the secret.

<div align="center">
<br/>
<sub>Built by <a href="https://github.com/Aetheron402">Aetheron</a> · execution powered by X402</sub>
</div>
