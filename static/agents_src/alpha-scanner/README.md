### License Notice

This template is licensed for **personal use only**.  
Redistribution, resale, repackaging, or inclusion in any paid product or
service is **strictly prohibited**.  
See `LICENSE.txt` for full terms.

------------------------------------------------------------------------

# Alpha Scanner Agent Template

------------------------------------------------------------------------

# 0. Introduction

Crypto markets are driven less by single data points and more by **emerging narratives** — themes that form when social attention, on-chain behavior, and market structure begin to align.

Most tools surface raw metrics:
- tweet counts  
- wallet movements  
- volume spikes  

Individually, these signals are noisy and easy to misinterpret.

The **Alpha Scanner Agent Template** is a **production-grade reasoning framework** designed to detect, validate, and rank *narratives* by combining multiple independent signal domains into a single, inspectable decision pipeline.

This template is **not** a trading bot.  
It is **not** an analytics dashboard.  
It is a **logic-first alpha detection engine**.

It provides everything needed to build:

- Narrative detection systems  
- Multi-signal confirmation engines  
- Alpha scouting agents  
- Research pipelines for emerging themes  
- Risk-aware signal aggregation systems  

…without hardcoding APIs, execution, or market assumptions.

This document is a **full onboarding and reference guide**.

------------------------------------------------------------------------

# 1. Quick Start Guide (Beginner-Friendly)

## Step 1 — Install Python

Python 3.10 or newer is required.

macOS (recommended):

    brew install python

Verify:

    python3 --version

------------------------------------------------------------------------

## Step 2 — Install dependencies

From inside the project directory:

    pip install -r requirements.txt

All dependencies are lightweight and standard.

------------------------------------------------------------------------

## Step 3 — Run the agent

    python3 main.py

When running, the agent will:

- start in **continuous scan mode**
- clearly log each scan stage
- use **placeholder signals only**
- explain exactly where real logic should be added

No APIs. No wallets. Safe by default.

Stop execution with `Ctrl+C`.

------------------------------------------------------------------------

# 2. What This Template Is (And Is Not)

## What it is

- A reusable narrative reasoning engine  
- A reference architecture for alpha scouting  
- A deterministic, inspectable agent framework  
- A safe foundation for real-world integrations  

## What it is not

- A plug-and-play alpha bot  
- A data provider  
- A signal-selling product  
- A profit guarantee  

------------------------------------------------------------------------

# 3. Core Design Philosophy

### Narratives over events

A **narrative** is a persistent theme supported by multiple signals across time.

Narratives:
- evolve gradually  
- gain or lose momentum  
- require confirmation  
- can be filtered and ranked  

The agent exists to track these dynamics, not chase individual spikes.

------------------------------------------------------------------------

### Logic before data

All reasoning logic is implemented **before** any real data is introduced.

This ensures:
- predictable behavior  
- easy auditing  
- safe extension  
- no hidden assumptions  

------------------------------------------------------------------------

### Separation of concerns

Each layer does exactly one thing:

- signals observe  
- narratives group  
- fusion validates  
- ranking orders  
- confidence estimates reliability  
- risk suppresses noise  

------------------------------------------------------------------------

# 4. Architecture Overview

    ┌────────────────────────────┐
    │   Data Sources (User)      │
    │  social / onchain / market │
    └──────────────┬─────────────┘
                   ▼
           ┌───────────────────┐
           │ Signal Generators │
           │  signals/*.py    │
           └───────────────────┘
                   │ Normalized Signals
                   ▼
           ┌───────────────────┐
           │ Narrative Engine  │
           │ narratives.py    │
           └───────────────────┘
                   │
                   ▼
           ┌───────────────────┐
           │ Signal Fusion     │
           │ fusion.py        │
           └───────────────────┘
                   │
                   ▼
           ┌───────────────────┐
           │ Ranking + Score   │
           │ ranking.py       │
           └───────────────────┘
                   │
                   ▼
           ┌───────────────────┐
           │ Confidence Layer  │
           │ confidence.py    │
           └───────────────────┘
                   │
                   ▼
           ┌───────────────────┐
           │ Risk Filters      │
           │ risk/*.py        │
           └───────────────────┘
                   │
                   ▼
           ┌───────────────────┐
           │ Opportunities     │
           │ output            │
           └───────────────────┘

------------------------------------------------------------------------

# 5. Repository Structure

```
alpha-scanner/
├── core/              # reasoning engine
├── signals/           # signal generators (logic-only)
├── risk/              # noise & regime filters
├── schemas/           # explicit data contracts
├── utils/             # shared helpers
├── config.json        # tunable behavior
├── main.py            # runtime entrypoint
└── requirements.txt
```

------------------------------------------------------------------------

# 6. Configuration Guide (`config.json`)

All behavior is controlled via configuration.

You can tune:
- scan frequency  
- enabled signal domains  
- fusion thresholds  
- ranking weights  
- confidence limits  
- risk suppression rules  

No core logic changes required.

------------------------------------------------------------------------

# 7. Implementing Real Signal Logic

The template ships with **placeholder signal generators**.

To go live, users replace logic in:

## `signals/social.py`
Examples:
- X / forum scraping  
- discourse velocity  
- influencer clustering  

## `signals/onchain.py`
Examples:
- wallet clustering  
- net inflows  
- contract interaction spikes  

## `signals/market.py`
Examples:
- volume anomalies  
- liquidity shifts  
- volatility regimes  

As long as valid `Signal` objects are emitted, the rest of the system remains unchanged.

------------------------------------------------------------------------

# 8. Narrative Lifecycle

Narratives:

- are created on first signal  
- accumulate evidence over time  
- decay when inactive  
- can be suppressed if recycled  
- persist across scan cycles  

State management is explicit and inspectable.

------------------------------------------------------------------------

# 9. Logging & Transparency

The agent logs:

- every scan cycle  
- every signal source  
- narrative updates  
- fusion decisions  
- ranking outcomes  

Logs are designed to **teach the system**, not hide it.

------------------------------------------------------------------------

# 10. Safety Notes

- This template does not connect to real data  
- No execution logic exists  
- All integrations are user-controlled  
- You are responsible for any real-world usage  

------------------------------------------------------------------------

# 11. Intended Audience

- Builders creating alpha scouts  
- Researchers studying narrative formation  
- Developers integrating multiple data domains  
- Teams building internal research agents  

Not intended as a finished product.

------------------------------------------------------------------------

# 12. License & Disclaimer

This repository is a **template**, not an alpha product.  
No financial advice is provided.  
You assume all responsibility for any real integrations.

------------------------------------------------------------------------

# End of Document
