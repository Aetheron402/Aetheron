import { AetheronSDK } from "../src/index";
import { Connection, clusterApiUrl, Keypair } from "@solana/web3.js";
import type { SignerWalletAdapter } from "@solana/wallet-adapter-base";

// Mock wallet for demo purposes.
// This is NOT a real wallet and cannot sign transactions.
const kp = Keypair.generate();

const mockWallet = {
  publicKey: kp.publicKey
} as SignerWalletAdapter;

const connection = new Connection(clusterApiUrl("mainnet-beta"));

async function demo() {
  const sdk = new AetheronSDK(mockWallet, connection);

  try {
    // First attempt — expected to throw 402
    const res = await sdk.promptOptimizer(
      { text: "Generate a Solana meme with Grok", format: "pdf" },
      { paymentMethod: "USDC" }
    );

    console.log("Queued:", res);

  } catch (e) {
    if (sdk.isPaymentRequired(e)) {
      const info = sdk.getPaymentInfo(e);
      console.log("Payment required");
      console.log(
        "Send:",
        info?.remaining ?? info?.required,
        info?.currency
      );

      // In a real app, user sends payment manually.
      // For demo purposes, we just show where txSig goes.
      const fakeTxSig = "PASTE_REAL_TX_SIGNATURE_HERE";

      console.log("Retrying with txSig (example only)");
      // In a real app, this is where the retry would happen:
      // await sdk.promptOptimizer(payload, { paymentMethod: "USDC", txSig });
    } else {
      throw e;
    }
  }
}

demo();
