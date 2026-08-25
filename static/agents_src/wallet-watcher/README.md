---

### License Notice  
This template is licensed for **personal use only**.  
Redistribution, resale, repackaging, or inclusion in any paid product or service is **strictly prohibited**.  
See `LICENSE.txt` for full terms.

---

# Wallet Watcher Bot  
*A production‑grade wallet activity monitoring agent with real‑time event detection, webhook alerts, strategy modules, and an extensible architecture.*

This README is written with **richness, clarity, and depth**: including deep feature explanations, diagrams, workflows, advanced usage, onboarding guides, troubleshooting flows, and developer extension points.

---

# 0. Introduction

The **Wallet Watcher Bot** is a fast, lightweight, highly extendable wallet monitoring framework.  
After adding **your RPC** and **target wallet address**, the bot tracks:

- Token transfers  
- NFT-style movements  
- Swap/liquidity‑style behavior  
- Any meaningful account events  

This makes it ideal for:

- Wallet tracking bots  
- Discord alert integrations  
- Personal trading analytics  
- Whale watching / internal tooling  
- Real-time monitoring dashboards  
- On‑chain reporting systems  

This guide explains the bot **from zero to full customization**, friendly for beginners and powerful for developers.

---

# 1. Quick Start Guide (Beginner Mode)

### Step 1, Install Python  
https://www.python.org/downloads/

Check:
```
python --version
```

---

### Step 2, Install dependencies  
```
pip install -r requirements.txt
```

---

### Step 3, Edit config.json

```json
{
  "rpc": {
    "url": "YOUR_RPC_URL",
    "poll_interval_seconds": 8
  },
  "wallets_to_watch": ["TARGET_WALLET"],
  "notifications": {
    "enabled": false,
    "webhook_url": ""
  }
}
```

---

### Step 4, Run the watcher  
```
python main.py
```

You’ll now see live wallet events printed in your terminal.

---

# 2. Feature Overview

### ✔ Near real-time wallet activity monitoring  
### ✔ Token transfer detection  
### ✔ NFT-style activity detection  
### ✔ Swap/liquidity‑style pattern detection  
### ✔ Webhook alert support (Discord/Slack/Custom)  
### ✔ Config-driven behavior  
### ✔ Clean extension points for developers  
### ✔ Lightweight + RPC‑friendly  

---

# 3. Architecture Diagram (Visual Overview)

```
                     ┌───────────────────────────┐
                     │   RPC Endpoint / Node     │
                     └──────────────┬────────────┘
                                    ▼
                     ┌───────────────────────────┐
                     │         rpc.py            │
                     │  Chain polling + parsing  │
                     └──────────────┬────────────┘
                                    ▼
                     ┌───────────────────────────┐
                     │        helpers.py         │
                     │ Formatting, logging,      │
                     │ webhook notification      │
                     └──────────────┬────────────┘
                                    ▼
                     ┌───────────────────────────┐
                     │         main.py           │
                     │ Config load + event loop  │
                     └───────────────────────────┘
```

---

# 4. Folder Structure

```
wallet-watcher/
 ├── main.py               # Event loop + core logic
 ├── config.json           # All user settings
 ├── requirements.txt      # Dependencies
 ├── README.md             # Documentation
 └── utils/
      ├── helpers.py       # Logging, formatting, webhooks
      ├── rpc.py           # RPC client wrapper
      └── __init__.py
```

---

# 5. Configuration Guide (Full Breakdown)

The bot is controlled entirely through **config.json**.

---

## rpc

```json
"rpc": {
  "url": "https://api.mainnet-beta.solana.com",
  "poll_interval_seconds": 8,
  "timeout_seconds": 10
}
```

| Setting | Description |
|---------|-------------|
| `url` | RPC endpoint used to fetch activity |
| `poll_interval_seconds` | Frequency of checks (lower = faster, higher = safer) |
| `timeout_seconds` | Maximum wait time for RPC responses |

**Recommended settings:**

- **Fast tracking:** 2-4 seconds  
- **Balanced:** 5-8 seconds  
- **CPU/RPC friendly:** 10-15 seconds  

---

## wallets_to_watch

```json
"wallets_to_watch": ["YOUR_WALLET"]
```

Supports **multiple wallets**:

```json
"wallets_to_watch": [
  "Wallet1...",
  "Wallet2..."
]
```

---

## notifications

```json
"notifications": {
  "enabled": true,
  "webhook_url": "YOUR_WEBHOOK"
}
```

Send events to:

- Discord  
- Slack  
- Custom endpoints  

---

## logging

```json
"logging": {
  "level": "INFO",
  "to_file": false,
  "file_name": "wallet_watcher.log"
}
```

Log levels:

- `DEBUG` = most information  
- `INFO` = recommended default  
- `WARNING` / `ERROR` = reduced output  

---

# 6. Event Types (Full Classification)

The bot classifies all detected activity into **three main categories**:

---

## 1. Token Transfer Detection  
Triggered when:

- SPL / ERC-style tokens move  
- Wallet sends or receives tokens  
- Balance changes appear on-chain  

Useful for:

- Whale tracking  
- Trading automation triggers  
- Alert bots  

---

## 2. NFT-Style Movements  
Triggered by:

- NFT mint  
- NFT transfer  
- Ownership changes  
- Activity signals resembling NFT interactions  

Useful for:

- NFT trading bots  
- Collection activity monitors  

---

## 3. Swap / Liquidity Activity Signals  
Pattern-based detection of:

- Swaps  
- Liquidity adds/removes  
- Program-level interactions  

Useful for:

- Trading insights  
- Wallet strategy tracking  
- Automated alert systems  

---

# 7. How the System Works Internally

```
main.py
 ├─ load_config()
 ├─ setup_logging()
 ├─ initialize RPC client
 └─ main loop:
       ├─ fetch recent events via RPC
       ├─ interpret + classify (token, NFT, swap)
       ├─ log results
       ├─ send webhook (optional)
       └─ sleep poll_interval_seconds
```

---

# 8. Strategy Examples (User Guide Section)

These examples help beginners configure the watcher for different goals.

---

## Strategy A, Whale Watching  
Ideal config:

- `poll_interval_seconds: 3-5`  
- Webhooks enabled  
- Track large token movements  
- Add filters for high‑value tokens  

---

## Strategy B, NFT Wallet Tracker  
- Lower frequency needed (8-12 sec)  
- Focus on NFT‑style events  
- Use Discord for alerting  

---

## Strategy C, Personal Trading Monitor  
- Watch your own wallet  
- Track swaps  
- Get pinged when trades settle  

---

## Strategy D, Quiet Monitoring (RPC Friendly)  
- `poll_interval_seconds: 12-18`  
- Logging level WARNING  
- No webhooks  

---

# 9. Extending the Agent (Developer Mode)

### Add new event categories  
Modify `rpc.py` → detection logic.

### Add filtering logic  
Add rules in `helpers.py`.

### Add reaction logic  
Inside `main.py`, inside the loop:

```python
if event["type"] == "swap":
    do_something(event)
```

### Connect to external systems  
Examples:

- Database storage  
- Dashboards  
- Telegram alerts  
- Trading bots  
- AI analysis pipelines  

---

# 10. Webhook Formats (Copy-Paste)

### Discord JSON Payload

```json
{
  "content": "**New Wallet Event Detected**",
  "embeds": [{
    "title": "Token Transfer",
    "description": "Wallet sent 5.2 SOL",
    "color": 1127128
  }]
}
```

---

# 11. Troubleshooting Guide (Flowchart)

```
START
  │
  ├─ Bot shows no events?
  │       ├─ RPC URL valid?
  │       ├─ Wallet active recently?
  │       └─ Increase poll interval?
  │
  ├─ Webhook not firing?
  │       ├─ notifications.enabled = true?
  │       ├─ Webhook URL valid?
  │       └─ Internet connection stable?
  │
  ├─ Too many RPC errors?
  │       ├─ Increase poll interval
  │       └─ Switch to a more stable RPC
  │
  └─ Logs too noisy?
          ├─ Set level = WARNING
          └─ Disable DEBUG prints
```

---

# 12. Beginner Mistakes to Avoid

❌ Using a slow/free RPC  
❌ Forgetting to add your wallet address  
❌ Expecting events when the wallet is inactive  
❌ Setting poll interval below 2 seconds  
❌ Forgetting to enable webhooks  

---

# 13. Recommended Settings

### Safe beginner mode:
```
poll_interval_seconds: 8
logging: INFO
notifications: off
```

### Fast mode (active wallets):
```
poll_interval_seconds: 3-5
logging: INFO
notifications: on
```

### Low‑noise mode:
```
poll_interval_seconds: 12
logging: WARNING
```

---

# 14. Roadmap (Expanded)

### Coming soon  
- Multi-chain version (EVM, Sui, Aptos)  
- Token filtering  
- Program-specific detectors  
- Real swap decoding  
- Dashboard exporter module  

### Long-term  
- AI wallet behavior prediction  
- Advanced transaction decoding  
- Multi-wallet analytics suite  

---

# 15. Disclaimer

This is a **wallet monitoring framework**.  
Use your own RPC infrastructure for production deployments.  
Always follow blockchain provider terms.

---

# End of Document
