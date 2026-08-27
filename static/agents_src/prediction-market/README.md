### License Notice

This template is licensed for **personal use only**.\
Redistribution, resale, repackaging, or inclusion in any paid product or
service is **strictly prohibited**.\
See `LICENSE.txt` for full terms.

------------------------------------------------------------------------

# Prediction Market Agent Template

------------------------------------------------------------------------

# 0. Introduction

Prediction markets allow participants to express conviction about future
outcomes by taking positions on events rather than price movements. They
are powerful tools for forecasting, hedging, and probabilistic
decision-making --- but most platforms still require manual interaction
and lack reusable execution infrastructure.

The **Prediction Market Agent Template** is a **production-quality
execution framework** designed to automate participation in prediction
markets in a controlled, modular, and extensible way.

This template is not a trading bot.\
It is an **execution agent foundation**.

It provides everything needed to build:

-   Automated outcome-based strategies\
-   Rule-driven betting agents\
-   Risk-managed exposure systems\
-   Event-driven lifecycle automation\
-   Custom integrations with any prediction market

...without rewriting execution logic, sizing logic, or position
management.

This document serves as a **full onboarding guide** for both beginners
and advanced builders.

------------------------------------------------------------------------

# 1. Quick Start Guide (Beginner-Friendly)

## Step 1 --- Install dependencies

From inside the project directory:

    pip install -r requirements.txt

All dependencies are standard and safe.

------------------------------------------------------------------------

## Step 2 --- Review `config.json`

The agent is fully configurable.\
You can run it immediately with defaults, or adjust:

-   run interval\
-   bankroll reference\
-   exposure limits\
-   strategy thresholds\
-   lifecycle timing

No Python changes required.

------------------------------------------------------------------------

## Step 3 --- Run the agent

    python main.py

When running, the agent will:

-   clearly log startup information\
-   load live markets from Polymarket, no API key required\
-   run the full decision flow against real prices\
-   fill every order in memory and say so on each one

No API keys. No real trading. Safe by default.

------------------------------------------------------------------------

# 2. What This Template Is (And Is Not)

## What it is

-   A reusable execution engine\
-   A reference architecture for prediction market automation\
-   A configurable, deterministic agent framework\
-   A safe foundation for real-money systems

## What it is not

-   A plug-and-play betting bot\
-   A market analytics platform\
-   A prediction engine\
-   A guarantee of profit

------------------------------------------------------------------------

# 3. Feature Overview

### Venue-agnostic by design

The agent does not assume any specific prediction market.\
All market-specific logic lives in the adapter layer.

------------------------------------------------------------------------

### Deterministic execution flow

Every execution cycle follows the same steps:

1.  Fetch markets\
2.  Manage existing positions\
3.  Evaluate strategy rules\
4.  Calculate position size\
5.  Enforce risk limits\
6.  Execute orders

No hidden behavior. No side effects.

------------------------------------------------------------------------

### Built-in risk management

Includes:

-   bankroll-aware sizing\
-   global exposure limits\
-   centralized risk enforcement

Risk logic is separate from strategy logic.

------------------------------------------------------------------------

### Lifecycle-based exits

Positions are automatically exited when:

-   the market closes\
-   a maximum hold time is exceeded

This prevents unmanaged or forgotten exposure.

------------------------------------------------------------------------

### Config-driven behavior

Most behavior is controlled via `config.json`.\
Users do not need to edit code for common changes.

------------------------------------------------------------------------

### Safe by default

-   Mock adapter included\
-   Explicit `[MOCK]` logging\
-   No real execution unless added intentionally

------------------------------------------------------------------------

# 4. Architecture Overview

    ┌────────────────────────────┐
    │   Prediction Market API    │
    │   (user implementation)    │
    └──────────────┬─────────────┘
                   ▼
           ┌───────────────────┐
           │   Adapter Layer   │
           │  adapters/*.py    │
           └───────────────────┘
                   │ Normalized Markets
                   ▼
           ┌───────────────────┐
           │ Execution Engine  │
           │   engine.py       │
           └───────────────────┘
                   │
         ┌─────────┼───────────┐
         ▼         ▼           ▼
     Strategy     Sizing       Risk
    (strategy)   (sizing)   (exposure)
         │         │           │
         └─────────┴───────────┘
                   ▼
           ┌───────────────────┐
           │ Lifecycle Manager │
           │  lifecycle.py    │
           └───────────────────┘

------------------------------------------------------------------------

# 5. Configuration Guide (`config.json`)

Example configuration:

``` json
{
  "bankroll": 1000.0,
  "max_exposure": 300.0,
  "strategy": {
    "type": "probability_threshold",
    "min_probability": 0.30
  },
  "sizing": {
    "type": "fixed_fraction",
    "fraction": 0.05,
    "max_size": 100.0
  },
  "risk_limits": {
    "max_exposure": 300.0
  },
  "lifecycle": {
    "max_hold_time_seconds": 21600
  },
  "run_interval": 10
}
```

------------------------------------------------------------------------

## bankroll

Reference capital used for sizing calculations.

## max_exposure

Maximum total exposure allowed across all positions.

## strategy

Defines **when** the agent decides to act.

`min_probability` is a floor. With the example value of `0.30` the agent only
enters an outcome the market already gives a 30% chance or better, and it takes
the strongest outcome that clears the floor rather than whichever the API
listed first. Raise it to trade favourites only, lower it to include long-shots.

The bundled strategy holds no independent estimate of fair value, so it cannot
tell you a price is wrong. It is the scaffold: replace `evaluate` in
`core/strategy.py` with your own view, and the engine, sizing, risk and
lifecycle around it keep working unchanged.

## sizing

Defines **how much** capital is allocated per decision.

## lifecycle

Defines **how long** positions may remain open.

## run_interval

Execution frequency in seconds.

------------------------------------------------------------------------

# 6. Adapter System (Most Important Section)

Adapters live in:

    prediction_market_agent/adapters/

## `base.py`

Defines the adapter interface.\
This file should not be modified.

## `polymarket.py`

The default adapter. Reads are real:

-   live markets, outcomes, prices and close times from Polymarket's public
    Gamma API\
-   thin markets filtered out, since a price nobody can trade at is not a
    probability worth acting on\
-   open positions marked against the current live price

Orders are not real. They fill in memory at the live price and every fill is
logged as a paper fill. Placing a real order means holding a funded wallet and
signing on your behalf, which this template does not do. To trade for real,
implement `place_order` and `close_position` against Polymarket's CLOB with
your own signer; nothing else has to change.

Set `"adapter": "example"` in config.json to use the offline adapter instead.

## `example.py`

An offline adapter that:

-   returns invented markets\
-   simulates execution\
-   stores positions in memory

Useful for testing a strategy against a board you control, or with no network.

This file exists purely as a reference.

------------------------------------------------------------------------

## Going live (what users actually change)

To trade for real, users only need to:

1.  Copy `example.py`
2.  Rename it (e.g. `my_adapter.py`)
3.  Implement real API calls
4.  Replace the adapter instantiation in `main.py`

No engine changes required.

------------------------------------------------------------------------

# 7. Execution Flow (Detailed)

On each run:

-   markets are fetched from the adapter\
-   existing positions are evaluated for exit\
-   strategies evaluate market conditions\
-   position sizes are calculated\
-   risk rules are enforced\
-   orders are executed

All steps are logged.

------------------------------------------------------------------------

# 8. Logging & Transparency

The agent logs:

-   startup instructions\
-   execution ticks\
-   strategy decisions\
-   sizing calculations\
-   risk rejections\
-   lifecycle exits\
-   paper fill notices, on every order

Logs are designed to teach users how the agent works.

------------------------------------------------------------------------

# 9. Safety Notes

-   This template does not trade by default\
-   Real trading requires custom adapters\
-   Always test with small sizes\
-   You are responsible for any real trading logic

------------------------------------------------------------------------

# 10. Intended Audience

-   Developers building prediction market bots\
-   Traders automating outcome-based strategies\
-   Researchers testing probabilistic execution\
-   Builders integrating multiple markets

Not intended as a ready-made betting product.

------------------------------------------------------------------------

# 11. License & Disclaimer

This repository is a **template**, not a trading system.\
No financial advice is provided.\
You assume all risk for any real trading code you implement.

------------------------------------------------------------------------

# End of Document
