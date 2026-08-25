---

### License Notice  
This template is licensed for **personal use only**.  
Redistribution, resale, repackaging, or inclusion in any paid product or service is **strictly prohibited**.  
See `LICENSE.txt` for full terms.

---

# Solana Trading Assistant  
*A full-featured, real-time Solana market analysis agent designed for real-world usage.*

This agent analyzes Solana tokens in real time using:

- **Birdeye API** (price, liquidity, volume, candles)
- **Solana RPC** (token supply, decimals, account info)
- **Multi-timeframe trend evaluation**
- **Liquidity + volatility scoring**
- **Volume acceleration detection**
- **Weighted scoring model**
- **Webhook support for alerts**
- **Structured JSON outputs**
- **Config-driven design**

This is a **real, production-ready agent**.  
No placeholders. No simulation. Everything is implemented.

---

# 0. Overview

The Solana Trading Assistant continuously evaluates selected token mints and produces structured “health” summaries based on:

- Price trend across multiple timeframes
- Liquidity depth in USD
- 24h volume and recent volume acceleration
- Volatility of recent candles
- Basic liquidity/volume thresholds
- A weighted scoring model you can tune via config

It is built as a **template**, meaning:

- The architecture is already done
- The data fetching is already wired
- The analysis logic is implemented
- You only need to configure it and extend it if you want more

Perfect for:

- Traders wanting structured market intel
- Devs building backends for dashboards and bots
- Teams wanting a reliable Solana market analysis component
- People planning to eventually add auto-trading logic on top

---

# 1. Quick Start Guide (Beginner-Friendly)

### Step 1 — Install requirements

```bash
pip install -r requirements.txt
```

This installs:

- `requests` — for HTTP/Birdeye/webhooks
- `PyNaCl` — for Solana-related cryptography/extensions (if you expand RPC usage later)

---

### Step 2 — Add your Birdeye API key

Open `config.json` and set:

```json
"birdeye": {
  "api_key": "YOUR_BIRDEYE_API_KEY",
  "base_url": "https://public-api.birdeye.so",
  "timeout_seconds": 10
}
```

You can get a free API key at: https://birdeye.so

---

### Step 3 — Add tokens to watch

In `config.json`:

```json
"analysis": {
  "tokens_to_watch": [
    "So11111111111111111111111111111111111111112"
  ],
  "poll_interval_seconds": 30,
  "timeframes": ["5m", "15m", "1h"],

  "thresholds": {
    "min_liquidity_usd": 20000,
    "min_24h_volume_usd": 50000,
    "max_volatility_percent": 35
  },

  "scores": {
    "weight_price_trend": 0.35,
    "weight_volume_trend": 0.35,
    "weight_liquidity_stability": 0.20,
    "weight_volatility": 0.10
  }
}
```

You can add multiple mints — the agent will loop through all of them.

---

### Step 4 — Run the assistant

```bash
python main.py
```

You’ll see periodic analysis prints in your terminal, and if webhooks are enabled, embeds will be sent to your endpoint.

---

# 2. Feature Overview (High-Level)

### ✔ Live token data from Birdeye
- Price (USD)
- Liquidity (USD)
- 24h Volume (USD)
- Candles (OHLCV)

### ✔ Multi-timeframe market analysis
- 5m, 15m, 1h trend evaluation (configurable)
- Volume acceleration in recent candles
- Volatility estimation from candle ranges

### ✔ Health scoring model
- Weighted contribution of price trend, volume trend, liquidity stability, volatility
- Configurable weights & thresholds

### ✔ Production-friendly design
- Config-based behavior (`config.json`)
- Clean logging (console + file)
- Webhook notifications (e.g., Discord)
- JSON-friendly analysis object

### ✔ Extension-ready
- Add your own indicators
- Pipe into databases
- Use as backend for dashboards
- Add execution logic on top (Jupiter, Raydium, etc.)

---

# 3. Folder Structure

```text
solana-trading-assistant/
 ├── main.py                # Orchestration & main loop
 ├── config.json            # All user settings
 ├── requirements.txt       # Dependencies
 ├── README.md              # This documentation
 └── utils/
      ├── __init__.py
      ├── helpers.py        # Config, logging, analysis, webhooks
      └── rpc.py            # Birdeye + Solana RPC clients
```

The structure is identical in style to your other agents so users instantly understand how to work with it.

---

# 4. Architecture Diagram

```text
 ┌──────────────────────────────────────┐
 │             Birdeye API             │
 │ Price • Volume • Liquidity • Candles│
 └──────────────────────┬──────────────┘
                        ▼
               ┌─────────────────┐
               │ BirdeyeClient   │
               └────────┬────────┘
                        ▼
            ┌──────────────────────────┐
            │  analyze_token()         │
            │  Trend + Volume + Score  │
            └──────────┬──────────────┘
                       ▼
         ┌──────────────────────────────────┐
         │        SolanaRPCClient           │
         │  Token supply • decimals • info  │
         └───────────┬──────────────────────┘
                     ▼
             ┌────────────────────┐
             │       main.py      │
             │ Loop + Webhooks    │
             └────────────────────┘
```

Data flows from Birdeye & RPC into the `analyze_token()` function, which constructs a structured analysis dict used for:

- Console summaries
- Webhook payloads
- Any downstream integration you add

---

# 5. Configuration Guide (Every Section)

All configuration lives inside `config.json`.

## rpc

```json
"rpc": {
  "url": "https://api.mainnet-beta.solana.com",
  "timeout_seconds": 10
}
```

Used by `SolanaRPCClient` for:

- Token decimals
- Token supply
- General account info

You can swap `url` to any Solana RPC provider (e.g., Helius, QuickNode, Triton).

---

## birdeye

```json
"birdeye": {
  "api_key": "YOUR_KEY",
  "base_url": "https://public-api.birdeye.so",
  "timeout_seconds": 10
}
```

Used by `BirdeyeClient` to fetch:

- `/public/price`
- `/public/liquidity`
- `/public/volume`
- `/public/candles`

This is the core data provider for all market metrics.

---

## analysis

```json
"analysis": {
  "tokens_to_watch": ["TOKEN_MINT_1", "TOKEN_MINT_2"],
  "poll_interval_seconds": 30,
  "timeframes": ["5m", "15m", "1h"],

  "thresholds": {
    "min_liquidity_usd": 20000,
    "min_24h_volume_usd": 50000,
    "max_volatility_percent": 35
  },

  "scores": {
    "weight_price_trend": 0.35,
    "weight_volume_trend": 0.35,
    "weight_liquidity_stability": 0.20,
    "weight_volatility": 0.10
  }
}
```

### Key fields:

- **tokens_to_watch** → list of token mints to analyze
- **poll_interval_seconds** → how often the loop runs
- **timeframes** → which Birdeye candle windows to use
- **thresholds** → minimum health criteria
- **scores** → how much each signal weighs into the final score

---

## notifications

```json
"notifications": {
  "enabled": false,
  "webhook_url": ""
}
```

If enabled, the assistant posts nicely formatted embeds to the specified webhook URL (Discord-compatible JSON payload).

---

## logging

```json
"logging": {
  "level": "INFO",
  "to_file": false,
  "file_name": "trading_assistant.log"
}
```

Supports:

- Console logging
- Optional file logging
- Adjustable verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`)

---

# 6. Internal Logic (How It Works)

High-level loop in `main.py`:

```text
main.py
 ├ Load config
 ├ Setup logger
 ├ Initialize RPC + Birdeye clients
 └ Loop forever:
      ├ For each token in tokens_to_watch:
      │    ├ Fetch market data (price, liquidity, volume, candles)
      │    ├ Run analyze_token()
      │    ├ Print a human-readable summary
      │    └ Optionally send webhook
      └ Sleep poll_interval_seconds
```

The core analysis is handled by `analyze_token()` in `helpers.py`.

It assembles an analysis object like:

```json
{
  "mint": "TokenMint...",
  "price": 0.0042,
  "liquidity_usd": 54320,
  "volume_24h_usd": 182991,
  "trend": {
    "5m": -0.52,
    "15m": 1.02,
    "1h": 3.14
  },
  "price_trend_score": 1.21,
  "volume_acceleration_percent": 14.33,
  "volatility_percent": 6.41,
  "liquidity_ok": true,
  "total_score": 7.58
}
```

This is then used by:

- `pretty_print_analysis()` → console output
- `send_webhook_notification()` → Discord-style JSON

---

# 7. Analysis Model (Medium Depth)

The assistant model focuses on **signal clarity**, not hyper-complex quant.

It evaluates:

### 1. Price Trend (per timeframe)

For each candle timeframe (e.g. 5m, 15m, 1h):

```text
pct_change = (close - open) / open * 100
```

This is stored in `trend[timeframe]` and aggregated into `price_trend_score` as an average.

---

### 2. Volume Acceleration

Uses recent 5m candle volume vs prior 5m candles:

```text
volume_accel = (recent_volume - avg_previous_volume) / avg_previous_volume * 100
```

Positive = inflow, negative = slowdown.

---

### 3. Liquidity Stability

Compares liquidity to threshold:

```text
liquidity_ok = liquidity_usd >= min_liquidity_usd
```

If stable, contributes positively to score.  
If unstable, contributes negatively.

---

### 4. Volatility

Uses candle high–low range as basic volatility estimate:

```text
volatility = (high - low) / low * 100
```

Higher volatility is penalized (depending on configuration).

---

### 5. Weighted Token Score

Each signal contributes to a final score:

```text
total_score =
  price_trend_score     * weight_price_trend
+ volume_accel          * weight_volume_trend
+ (liquidity_ok ? +10 : -10) * weight_liquidity_stability
+ (-volatility)         * weight_volatility
```

This gives you a **simple numeric gauge** of token health and momentum.

You can adjust all weights via config.

---

# 8. Output Examples

### Console Output Example

```text
──────────────────────────────────────────────────────────
Token: So11111111111111111111111111111111111111112
Price: 0.004253 USD
Liquidity: $54,320
24h Volume: $182,991
Trend (%): {'5m': -0.52, '15m': 1.02, '1h': 3.14}
Price Trend Score: 1.21
Volume Acceleration: 14.33%
Volatility: 6.41%
Liquidity Stable: True
TOTAL SCORE: 7.58
──────────────────────────────────────────────────────────
```

### Webhook Embed Example (Discord)

The assistant sends a JSON payload like:

```json
{
  "content": "Market Analysis for So1111...",
  "embeds": [
    {
      "title": "Token Market Summary",
      "fields": [
        {"name": "Price", "value": "0.004253 USD", "inline": true},
        {"name": "Liquidity", "value": "$54,320", "inline": true},
        {"name": "24h Volume", "value": "$182,991", "inline": true},
        {"name": "Volatility", "value": "6.41%", "inline": true},
        {"name": "Trend Score", "value": "1.21", "inline": true},
        {"name": "Total Score", "value": "7.58", "inline": true}
      ]
    }
  ]
}
```

You can customize the fields and structure in `send_webhook_notification()`.

---

# 9. Use Case Examples

### Use Case A — Trading Signal Assistant
Use the score and trend metrics to:

- Flag tokens entering a strong uptrend
- Watch for exhaustion or volatility spikes
- Trigger alerts when conditions match your strategy

### Use Case B — Dashboard Backend
Pipe the JSON analysis into:

- A database (Postgres, MongoDB, etc.)
- A time-series store (Influx, Timescale)
- A custom UI or dashboard

### Use Case C — Alerting Bot
Combine this agent with:

- Discord webhooks
- Telegram bots
- Email alerts

To get structured notifications when a token’s score crosses a threshold.

### Use Case D — Research / Backtesting
Log outputs over time and run:

- Historical performance analysis
- Strategy backtesting on trend/volume conditions
- Comparative token scoring

---

# 10. Troubleshooting

```text
Token data looks empty?
 ├ Is the mint correct?
 ├ Is the token listed / liquid?
 └ Did Birdeye return data?

Birdeye errors?
 ├ Is the API key set?
 ├ Are you rate limited?
 └ Is the base_url correct?

No webhook notifications?
 ├ notifications.enabled = true?
 ├ webhook_url set correctly?
 └ Any network/firewall restrictions?
```

If something fails, check logs — the logger is configured to surface RPC and Birdeye errors clearly.

---

# 11. Performance & Safety Tips

- Don’t set `poll_interval_seconds` too low for many tokens
- Watch Birdeye rate limits if monitoring many mints
- Use `WARNING` or `ERROR` log levels for production to reduce noise
- If extending with trading logic, **always** test with dev wallets first

---

# 12. Extending the Assistant

Here are common ways users extend this template:

- Add indicators like MA/EMA/RSI inside `analyze_token()`
- Add trend phase classification (Accumulation → Uptrend → Distribution → Downtrend)
- Write analysis to CSV / database at each loop
- Build a small web API that exposes the latest data
- Attach order execution logic calling Jupiter / Raydium APIs

The template is intentionally clean so advanced users can bend it to their needs.

---

# 13. Disclaimer

This is a **real analysis framework**, not an auto-trading bot.

- It does not execute trades by default
- It does not hold private keys for execution
- It is meant as a safe, extensible foundation

If you add trading logic, you are responsible for testing, risk management, and security.

---

# End of Document

Use this assistant as your base layer for Solana analytics, and build the next layer of trading intelligence on top of it.
