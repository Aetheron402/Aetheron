# Aetheron SDK

Official SDK for interacting with the **Aetheron AI Component Platform**.

The Aetheron SDK is a **browser-first, production-ready client** that implements
the **X402 payment model** for accessing paid AI components and agent downloads.

It enables **explicit, non-custodial, on-chain payments** using  
**USDC or $AETH on Solana**, without hiding, abstracting, or automating the payment step.

This SDK is designed for developers who want **clarity, control, and composability**
when building applications that rely on paid AI infrastructure.

---

## What This SDK Is

- A **client-side integration layer** for Aetheron AI components
- An implementation of the **X402 (HTTP Payment Required) flow**
- A helper for **detecting, surfacing, and retrying paid requests**
- A bridge between **on-chain payments** and **AI compute**

This SDK is **not**:
- a wallet
- a payment processor
- a custodial service
- a transaction signer
- an automated billing system

The user always stays in control.

---

## Key Characteristics

- Browser-first (frontend-oriented)
- Framework-agnostic (React, Vue, Svelte, vanilla JS)
- Non-custodial
- Explicit payment handling
- Solana-native (USDC + AETH)
- Production-ready

---

## Installation

```bash
npm install aetheron-sdk
```

Or clone the repository:

```bash
git clone https://github.com/Aetheron402/aetheron-sdk
```

### Requirements

- Browser environment with `fetch`
- A connected Solana wallet  
  (Phantom, Backpack, Solflare, wallet-adapter compatible)
- Manual transaction signing by the user
- Node.js 18+ (for development/testing only)

Runtime execution is intended to happen in the browser.

---

## Core Concept: X402 Payment Flow

Aetheron uses an explicit HTTP **402 Payment Required** model.

The SDK does not automatically pay or retry.  
Instead, it exposes helpers so **your application controls the flow**.

### High-level flow

1. Your app calls a paid component
2. Backend responds with HTTP 402
3. SDK throws a structured error
4. UI shows payment instructions
5. User sends payment on-chain
6. Request is retried with a transaction signature
7. Backend verifies payment and processes the request

Payments are transparent, auditable, and user-controlled.

---

## Quickstart (Browser / React Example)

```ts
import { AetheronSDK } from "aetheron-sdk";
import { Connection, clusterApiUrl } from "@solana/web3.js";
import { useWallet } from "@solana/wallet-adapter-react";

const wallet = useWallet(); // SignerWalletAdapter
const connection = new Connection(clusterApiUrl("mainnet-beta"));

const sdk = new AetheronSDK(wallet, connection);

try {
  // First call intentionally triggers HTTP 402
  await sdk.promptOptimizer(
    { text: "Generate a Solana-themed AI mascot", format: "pdf" },
    { paymentMethod: "USDC" }
  );
} catch (e) {
  if (sdk.isPaymentRequired(e)) {
    const info = sdk.getPaymentInfo(e);

    console.log("Payment required:", info);

    // User sends payment manually via wallet UI
    const txSig = prompt("Paste your Solana transaction signature:");

    const retry = await sdk.promptOptimizer(
      { text: "Generate a Solana-themed AI mascot", format: "pdf" },
      { paymentMethod: "USDC", txSig }
    );

    console.log("Queued after payment:", retry);
  } else {
    throw e;
  }
}
```

---

## Handling Payment-Required Errors

### Detecting a 402 response

```ts
sdk.isPaymentRequired(error); // boolean
```

### Extracting payment information

```ts
const info = sdk.getPaymentInfo(error);
```

### `PaymentRequiredError` shape

```ts
type PaymentRequiredError = {
  status: 402;
  paid?: number;
  remaining?: number;
  required?: number;
  currency?: "USDC" | "AETH";
  component?: string;
};
```

The SDK never retries automatically — **your app controls the UX**.

---

## Supported Components

### Prompt Optimizer

```ts
sdk.promptOptimizer(
  { text: string, format?: "pdf" | "txt" | "docx" | "md" | "html" },
  { paymentMethod?: "USDC" | "AETH", txSig?: string }
);
```

### Code Explainer

```ts
sdk.codeExplainer(
  { text: string, format?: string },
  { paymentMethod?: "USDC" | "AETH", txSig?: string }
);
```

### Prompt Tester (PersonaSim)

```ts
sdk.promptTester(
  { text: string, format?: string },
  { paymentMethod?: "USDC" | "AETH", txSig?: string }
);
```

### Contract Intelligence Analyzer

```ts
sdk.contractIntel(
  {
    contract_address: string;
    network: "solana" | "ethereum";
    format?: string;
  },
  { paymentMethod?: "USDC" | "AETH", txSig?: string }
);
```

---

## Download Prebuilt Agents

```ts
const zip = await sdk.downloadAgent("solana-sniper", {
  paymentMethod: "USDC",
  txSig,
});
```

- Returns a `Blob`
- The SDK does not save files automatically
- File handling is left to the developer

---

## Design Principles

- Explicit on-chain payments (X402)
- No hidden balances
- No background retries
- No custody
- No private key access
- No transaction signing inside the SDK
- Browser-first and framework-agnostic

The wallet always remains fully under the user’s control.

---

## Philosophy

This SDK encodes a **payment protocol**, not just API calls.

It treats AI compute as:

- Paid infrastructure
- User-controlled
- Composable
- Verifiable

---

## Links

- Website → https://www.aetheron402.com  
- GitHub → https://github.com/Aetheron402  
- X (Twitter) → https://x.com/Aetheron402  
- Email → team@aetheron402.com
