---

### License Notice  
This template is licensed for **personal use only**.  
Redistribution, resale, repackaging, or inclusion in any paid product or service is **strictly prohibited**.  
See `LICENSE.txt` for full terms.

---

# Pump.fun Launch Assistant

---

# 0. Introduction

Pump.fun is one of the fastest‑moving launchpads in crypto. Hundreds of tokens launch each hour, and identifying high‑potential mints early requires fast information flow, good filtering, and sane risk controls.

This **Pump.fun Launch Assistant** is a **production‑quality template** designed to give users everything they need to build:

- A monitoring bot  
- A signal generator  
- A research assistant  
- A custom trading system  
- A wallet intelligence pipeline  

…without writing any discovery, filtering, or RPC code.

This documentation explains **exactly how the assistant works**, how to customize it, how to integrate real trading *if desired*, and how to safely run it, even if you’re a beginner.

This is a **full technical + beginner onboarding guide**, optimized for developers, traders, and automation builders.

---

# 1. Quick Start Guide (Beginner‑Friendly)

## Step 1, Install dependencies

Open a terminal inside the folder:

```
pip install -r requirements.txt
```

That's it.

## Step 2, Edit config.json (optional)

You can run the assistant with default settings, but beginners often adjust:

- `min_liquidity_sol`
- `min_bonding_curve_percent`
- `min_trades_5m`

All settings are fully explained later.

## Step 3, Run the assistant

```
python main.py
```

Immediately, you’ll see live Pump.fun token data flowing through your terminal.

No keys. No API setup. No coding.

---

# 2. Feature Overview (High-Level Summary)

### ✔ Real-time Pump.fun feed  
Fetches the newest launched tokens with their data:

- mint address  
- liquidity  
- BC %  
- market cap  
- trading activity  
- renounce status  
- liquidity lock  
- mint authority disabled  

### ✔ Advanced filtering  
Avoids:

- ultra‑early rugs  
- tokens with no liquidity  
- dead launches  
- known malicious creators  
- tokens missing renounce or lock  
- tokens with active mint authority  

### ✔ Webhook notification support  
Send alerts to:

- Discord  
- Slack  
- Telegram (via bot webhook adapters)  

### ✔ Execution hook  
A safe function where users can add:

- Jupiter swaps  
- Raydium swaps  
- Alerts  
- Logging  
- Custom analytics  

### ✔ Config-driven  
Everything is adjustable from **config.json**.

### ✔ Perfect for beginners  
Doesn’t require any knowledge to run.

### ✔ Perfect for developers  
Offers clean extension points to build a full automated system.

---

# 3. Architecture Diagram (Visual Overview)

```
 ┌───────────────────────┐
 │     Pump.fun API      │
 └───────────┬───────────┘
             ▼
     ┌─────────────────┐
     │ PumpFunClient   │
     │  (rpc.py)       │
     └─────────────────┘
             │ Normalized Tokens
             ▼
     ┌─────────────────┐
     │   helpers.py    │
     │ Filtering Logic │
     └─────────────────┘
             │ Passed Tokens
             ▼
     ┌─────────────────┐
     │    main.py      │
     │ Event Loop      │
     └─────────────────┘
             │
             ├────▶ Webhooks
             │
             └────▶ Execution Hook (custom logic)
```

---

# 4. Configuration Guide (Every Setting Explained)

The assistant is controlled through `config.json`.

Below is the upgraded explanation for **every parameter**.

---

## rpc

```
"rpc": {
  "url": "https://api.mainnet-beta.solana.com",
  "timeout_seconds": 10,
  "poll_interval_seconds": 4
}
```

- **url**: optional Solana RPC provider  
  (used for deeper checks or future extensions)  

- **poll_interval_seconds**  
  - 2-3s = sniper speed  
  - 4-6s = medium  
  - 7-12s = low frequency  

---

## pumpfun

```
"pumpfun": {
  "api_url": "https://api.pump.fun/v2/tokens/recent",
  "min_liquidity_sol": 1.5,
  "min_bonding_curve_percent": 10,
  "max_market_cap_usd": 600000,
  "track_liquidity_events": true,
  "track_creator_activity": true
}
```

- **min_liquidity_sol**  
  Minimum liquidity to consider the token valid.

- **min_bonding_curve_percent**  
  Reject tokens that are too early on the bonding curve.

- **max_market_cap_usd**  
  Filters out overextended tokens.

- **track_liquidity_events** (future expansion)  
- **track_creator_activity** (future expansion)

---

## filters

```
"filters": {
  "block_renounced_only": false,
  "require_locked_liquidity": false,
  "min_trades_5m": 5,
  "block_mint_authority": true
}
```

- **min_trades_5m**  
  Ensures a minimum level of real activity.

- **block_mint_authority**  
  Critical rug-check filter.

---

## blacklist

Self-explanatory filters for scam creators, mints, or programs.

---

## notifications

Webhook system (Discord/Slack/etc.):

```
"notifications": {
  "enabled": false,
  "webhook_url": ""
}
```

---

## logging

Controls verbosity and output file.

---

# 5. Strategy Examples (Trader-Focused Section)

The assistant supports many styles:

---

## Example Strategy A, Early Curve Sniping

Best for users willing to take higher risk.

Recommended config:

- `min_bonding_curve_percent: 5`  
- `min_liquidity_sol: 1.0`  
- `min_trades_5m: 3`

---

## Example Strategy B, Safer Curve Entries

Focuses on tokens past the critical risk zone.

Use:

- `min_bonding_curve_percent: 12-15`  
- `require_locked_liquidity: true`  

---

## Example Strategy C, Volume Confirmation

Focus on tokens gaining traction.

- `min_trades_5m: 10-20`  
- `min_liquidity_sol: 2.0+`  

---

## Example Strategy D, Creator Reputation Scoring

Block bad creators:

```
"creator_addresses": ["BadCreator1...", "BadCreator2..."]
```

---

# 6. Execution Hook (Developer Section)

Inside **main.py**:

```python
def execute_signal(self, token):
    self.logger.info(f"Execution hook triggered for {token['mint']}.")
```

This is where advanced users add **trading, alerts, or analytics**.

---

## Example (commented-out Jupiter swap template):

```python
# Example Jupiter swap integration (disabled by default)
#
# import requests
#
# def execute_signal(self, token):
#     amount = int(0.1 * 1_000_000_000)  # 0.1 SOL
#     route = requests.get(
#         "https://quote-api.jup.ag/v6/quote",
#         params={
#             "inputMint": "So11111111111111111111111111111111111111112",
#             "outputMint": token["mint"],
#             "amount": amount,
#             "slippageBps": 300
#         }
#     ).json()
#
#     self.logger.info(f"Jupiter route data: {route}")
```

Safe. Optional. Beginner-friendly.

---

# 7. System Concepts (Glossary)

### **Bonding Curve**  
Defines how price changes as the curve progresses.

### **Liquidity**  
SOL pooled backing the token.

### **Mint Authority Disabled**  
Whether creator can mint more supply.

### **Locked Liquidity**  
Whether LP can be pulled.

### **Trades 5m**  
Short-term activity.

### **Market Cap**  
Curve-based valuation.

---

# 8. Troubleshooting Guide (Flowchart)

```
           START
             │
             ▼
     Is the script running?
        │          │
        │          └──▶ Fix Python environment
        ▼
Are tokens appearing?
        │
   YES  │  NO
        ▼
  Do filters feel too strict?
        │
   YES  └──▶ Lower filters (min liquidity / BC%)
   NO         ▼
              RPC OK?
              │
         YES  │  NO
              ▼
           Fix RPC / Internet
```

---

# 9. Configuration Wizard

### Beginner Mode (safe):
```
min_liquidity_sol: 1.5
min_bonding_curve_percent: 10
min_trades_5m: 5
block_mint_authority: true
```

### Normal Mode:
```
min_liquidity_sol: 1.2
min_bonding_curve_percent: 8
min_trades_5m: 3
```

### Aggressive Mode:
```
min_bonding_curve_percent: 3
min_trades_5m: 1
```

### Volume Mode:
```
min_trades_5m: 15
```

---

# 10. Integration Examples

## Webhook Example
```python
send_webhook(url, formatted, self.logger)
```

## Discord Bot Forwarding
Through a simple relay script.

## CSV Logging
```
with open("log.csv", "a") as f:
    f.write(f"{token['mint']},{token['liquidity_sol']}
")
```

---

# 11. Safety Notes

- The bot does **NOT** trade by default.  
- Trading must be added manually.  
- Always test trading logic with a burner wallet.  
- Trading carries real financial risk.

---

# 12. License & Disclaimer

This is a **template**, not a trading system.  
You are responsible for any real trading code you add.

---

# End of Document
