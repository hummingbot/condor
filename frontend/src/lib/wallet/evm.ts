// Generic EVM browser-wallet helpers — chain- and venue-agnostic.
//
// This is the reusable connect-wallet layer: wallet discovery (EIP-6963), account connection,
// chain switching, EIP-712 signing, and a nonce manager. Venue modules build on top of it
// (e.g. wallet/hyperliquid.ts), and new EVM keys (e.g. a plain Ethereum signer) can reuse it
// directly. Non-EVM families (e.g. Solana) belong in a sibling module, not here.

import { parseSignature } from "viem";

// ── Minimal EIP-1193 / EIP-6963 provider types ──
export interface Eip1193Provider {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
}

export interface DiscoveredWallet {
  uuid: string;
  name: string;
  icon: string;
  rdns: string;
  provider: Eip1193Provider;
}

interface Eip6963AnnounceEvent extends Event {
  detail: { info: { uuid: string; name: string; icon: string; rdns: string }; provider: Eip1193Provider };
}

/**
 * Discover injected browser wallets via EIP-6963. Falls back to a legacy
 * `window.ethereum` if no wallet announces itself.
 */
export function discoverWallets(timeoutMs = 400): Promise<DiscoveredWallet[]> {
  return new Promise((resolve) => {
    const found: Record<string, DiscoveredWallet> = {};
    const handler = (event: Event) => {
      const { detail } = event as Eip6963AnnounceEvent;
      if (!detail?.info?.uuid) return;
      found[detail.info.uuid] = { ...detail.info, provider: detail.provider };
    };
    window.addEventListener("eip6963:announceProvider", handler);
    window.dispatchEvent(new Event("eip6963:requestProvider"));
    window.setTimeout(() => {
      window.removeEventListener("eip6963:announceProvider", handler);
      const list = Object.values(found);
      const legacy = (window as unknown as { ethereum?: Eip1193Provider }).ethereum;
      if (list.length === 0 && legacy) {
        list.push({ uuid: "legacy", name: "Browser Wallet", icon: "", rdns: "injected", provider: legacy });
      }
      resolve(list);
    }, timeoutMs);
  });
}

/** Request the wallet's accounts and return the active (lowercased) address. */
export async function connectWallet(provider: Eip1193Provider): Promise<string> {
  const accounts = (await provider.request({ method: "eth_requestAccounts" })) as string[];
  if (!accounts?.length) throw new Error("No account selected in the wallet.");
  return accounts[0].toLowerCase();
}

// ── Chains ──
// Shape matches the `wallet_addEthereumChain` parameter object so a chain can be passed straight through.
export interface EvmChain {
  chainId: string; // hex, e.g. "0xa4b1"
  chainName: string;
  nativeCurrency: { name: string; symbol: string; decimals: number };
  rpcUrls: string[];
  blockExplorerUrls: string[];
}

export const ARBITRUM_ONE: EvmChain = {
  chainId: "0xa4b1", // 42161
  chainName: "Arbitrum One",
  nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: ["https://arb1.arbitrum.io/rpc"],
  blockExplorerUrls: ["https://arbiscan.io"],
};

export const ETHEREUM_MAINNET: EvmChain = {
  chainId: "0x1", // 1
  chainName: "Ethereum Mainnet",
  nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: ["https://eth.llamarpc.com"],
  blockExplorerUrls: ["https://etherscan.io"],
};

/**
 * Ensure the wallet is on `chain`, switching to it (and adding it if unknown) first. Some wallets
 * (e.g. Rabby) reject EIP-712 typed data whose domain chainId differs from the wallet's current
 * chain, so callers that sign chain-bound typed data must switch first.
 */
export async function ensureChain(provider: Eip1193Provider, chain: EvmChain): Promise<void> {
  const current = (await provider.request({ method: "eth_chainId" })) as string;
  if (current?.toLowerCase() === chain.chainId.toLowerCase()) return;
  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: chain.chainId }] });
  } catch (e) {
    // 4902 = chain not added to the wallet yet; add it (most wallets switch on add).
    if ((e as { code?: number }).code === 4902) {
      await provider.request({ method: "wallet_addEthereumChain", params: [chain] });
    } else {
      throw e;
    }
  }
}

// ── Signing ──
export interface EvmSignature {
  r: `0x${string}`;
  s: `0x${string}`;
  v: number;
}

/** Normalize a 65-byte hex signature into the `{r, s, v}` shape exchanges expect. */
export function normalizeSignature(sigHex: `0x${string}`): EvmSignature {
  const { r, s, v, yParity } = parseSignature(sigHex);
  return { r, s, v: v !== undefined ? Number(v) : yParity + 27 };
}

/** Sign EIP-712 typed data with the wallet's active account and return the normalized signature. */
export async function signTypedDataV4(
  provider: Eip1193Provider,
  address: string,
  typedData: Record<string, unknown>,
): Promise<EvmSignature> {
  const sigHex = (await provider.request({
    method: "eth_signTypedData_v4",
    params: [address, JSON.stringify(typedData)],
  })) as `0x${string}`;
  return normalizeSignature(sigHex);
}

// Strictly increasing epoch-ms nonces (mirrors hummingbot's _NonceManager).
let lastNonce = 0;
export function nextNonce(): number {
  const now = Date.now();
  lastNonce = now > lastNonce ? now : lastNonce + 1;
  return lastNonce;
}
