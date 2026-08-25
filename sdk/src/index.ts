import { Connection } from "@solana/web3.js";
import type { SignerWalletAdapter } from "@solana/wallet-adapter-base";

export interface AetheronConfig {
  endpoint?: string;
}

export type PaymentRequiredError = {
  status: 402;
  message?: string;
  paid?: number;
  remaining?: number;
  required?: number;
  currency?: "USDC" | "AETH";
  component?: string;
};

export class AetheronSDK {
  private api: string;
  private wallet: SignerWalletAdapter;
  private connection: Connection;

  constructor(
    wallet: SignerWalletAdapter,
    connection: Connection,
    config: AetheronConfig = {}
  ) {
    if (!wallet || !wallet.publicKey) {
      const err = new Error(
      "Wallet not connected. Aetheron SDK is designed to be used inside a browser app with a connected Solana wallet."
    );
    (err as any).code = "WALLET_REQUIRED";
    throw err;
    }

    this.wallet = wallet;
    this.connection = connection;
    this.api = config.endpoint ?? "https://api.aetheron402.com";
  }

  private async post(
    endpoint: string,
    payload: any,
    paymentMethod: "USDC" | "AETH" = "USDC",
    txSig?: string
  ): Promise<any> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-USER-WALLET": this.wallet.publicKey!.toBase58(),
      "X-PAYMENT-METHOD": paymentMethod
    };

    if (txSig) {
      headers["X-TX-SIG"] = txSig;
    }

    const res = await fetch(`${this.api}${endpoint}`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });

    if (res.status === 402) {
      throw (await res.json()) as PaymentRequiredError;
    }

    if (res.status === 409) {
      throw new Error("Transaction signature already used");
    }

    if (!res.ok) {
      let detail: any;
      try {
        detail = await res.json();
      } catch {
        detail = await res.text();
      }

      const err = new Error(
        typeof detail === "string"
          ? detail
          : detail?.message || `Request failed (${res.status})`
      );

      (err as any).status = res.status;
      (err as any).detail = detail;
      throw err;
    }

    return res.json();
  }

  isPaymentRequired(error: unknown): error is PaymentRequiredError {
    return (
      typeof error === "object" &&
      error !== null &&
      (error as any).status === 402
    );
  }

  getPaymentInfo(error: unknown): PaymentRequiredError | null {
    if (!this.isPaymentRequired(error)) return null;
    return error;
  }

  async callPaidComponent<T>(opts: {
    endpoint: string;
    payload: any;
    paymentMethod?: "USDC" | "AETH";
    txSig?: string;
  }): Promise<T> {
    return this.post(
      opts.endpoint,
      opts.payload,
      opts.paymentMethod ?? "USDC",
      opts.txSig
    );
  }

  promptOptimizer(
    payload: { text: string; format?: string },
    opts?: { paymentMethod?: "USDC" | "AETH"; txSig?: string }
  ) {
    return this.callPaidComponent({
      endpoint: "/api/prompt-optimizer",
      payload,
      ...opts
    });
  }

  codeExplainer(
    payload: { text: string; format?: string },
    opts?: { paymentMethod?: "USDC" | "AETH"; txSig?: string }
  ) {
    return this.callPaidComponent({
      endpoint: "/api/code-explainer",
      payload,
      ...opts
    });
  }

  promptTester(
    payload: { text: string; format?: string },
    opts?: { paymentMethod?: "USDC" | "AETH"; txSig?: string }
  ) {
    return this.callPaidComponent({
      endpoint: "/api/prompt-tester",
      payload,
      ...opts
    });
  }

  contractIntel(
    payload: {
      contract_address: string;
      network: "solana" | "ethereum";
      format?: string;
    },
    opts?: { paymentMethod?: "USDC" | "AETH"; txSig?: string }
  ) {
    return this.callPaidComponent({
      endpoint: "/api/contract-intel",
      payload,
      ...opts
    });
  }

  async downloadAgent(
    agentId: string,
    opts?: {
      paymentMethod?: "USDC" | "AETH";
      txSig?: string;
    }
  ): Promise<Blob> {
    const headers: Record<string, string> = {
      "X-USER-WALLET": this.wallet.publicKey!.toBase58(),
      "X-PAYMENT-METHOD": opts?.paymentMethod ?? "USDC"
    };

    if (opts?.txSig) {
      headers["X-TX-SIG"] = opts.txSig;
    }

    const res = await fetch(
      `${this.api}/api/download_agent/${agentId}`,
      {
        method: "GET",
        headers
      }
    );

    if (res.status === 402) {
      throw await res.json();
    }

    if (res.status === 409) {
      throw new Error("Transaction signature already used");
    }

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Download failed (${res.status}): ${text}`);
    }

    return res.blob();
  }
}
