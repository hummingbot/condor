// Hyperliquid wallet-connect helpers (HGP-87).
//
// Condor adds Hyperliquid keys exclusively through an agent-wallet ("API wallet")
// flow instead of asking the user to paste a raw private key:
//
//   1. Generate a throwaway agent keypair in the browser.
//   2. The user signs `ApproveAgent` with their own wallet, authorising that agent
//      to trade (but never withdraw) on their behalf.
//   3. The user signs `ApproveBuilderFee`, authorising Condor's Foundation builder
//      code at 1 bps so orders are attributed.
//   4. The agent private key + the user's main address are stored as the connector
//      credential. The user's real key never leaves their wallet.
//
// All EIP-712 schemes mirror hummingbot's HyperliquidAuth.sign_user_signed_action.

import { parseSignature } from "viem";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";

// ── Condor builder code (same address as the hummingbot connector default) ──
export const BUILDER_ADDRESS = "0x10ba451e6439efc6a17dc20d21121aa838100705";
export const BUILDER_FEE_BPS = 1;
// maxFeeRate is a percentage string: 1 bps = 0.01%. The connector injects f = 10
// (tenths-of-bps) per order, so the approved max must be >= the charged fee.
export const BUILDER_MAX_FEE_RATE = "0.01%";

// ── Hyperliquid mainnet signing constants ──
const HL_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange";
const HL_CHAIN = "Mainnet";
const HL_SIGNATURE_CHAIN_ID = "0xa4b1"; // Arbitrum One
const HL_DOMAIN_CHAIN_ID = 42161;
const DEFAULT_AGENT_NAME = "condor";

const EIP712_DOMAIN = {
  name: "HyperliquidSignTransaction",
  version: "1",
  chainId: HL_DOMAIN_CHAIN_ID,
  verifyingContract: "0x0000000000000000000000000000000000000000",
} as const;

const DOMAIN_TYPE = [
  { name: "name", type: "string" },
  { name: "version", type: "string" },
  { name: "chainId", type: "uint256" },
  { name: "verifyingContract", type: "address" },
];

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

// Strictly increasing epoch-ms nonces (mirrors the connector's _NonceManager).
let lastNonce = 0;
function nextNonce(): number {
  const now = Date.now();
  lastNonce = now > lastNonce ? now : lastNonce + 1;
  return lastNonce;
}

function buildApproveAgentTypedData(agentAddress: string, agentName: string, nonce: number) {
  return {
    domain: EIP712_DOMAIN,
    types: {
      EIP712Domain: DOMAIN_TYPE,
      "HyperliquidTransaction:ApproveAgent": [
        { name: "hyperliquidChain", type: "string" },
        { name: "agentAddress", type: "address" },
        { name: "agentName", type: "string" },
        { name: "nonce", type: "uint64" },
      ],
    },
    primaryType: "HyperliquidTransaction:ApproveAgent",
    message: { hyperliquidChain: HL_CHAIN, agentAddress, agentName, nonce },
  };
}

function buildApproveBuilderFeeTypedData(nonce: number) {
  return {
    domain: EIP712_DOMAIN,
    types: {
      EIP712Domain: DOMAIN_TYPE,
      "HyperliquidTransaction:ApproveBuilderFee": [
        { name: "hyperliquidChain", type: "string" },
        { name: "maxFeeRate", type: "string" },
        { name: "builder", type: "address" },
        { name: "nonce", type: "uint64" },
      ],
    },
    primaryType: "HyperliquidTransaction:ApproveBuilderFee",
    message: { hyperliquidChain: HL_CHAIN, maxFeeRate: BUILDER_MAX_FEE_RATE, builder: BUILDER_ADDRESS, nonce },
  };
}

async function signAndSubmit(
  provider: Eip1193Provider,
  userAddress: string,
  typedData: Record<string, unknown>,
  action: Record<string, unknown>,
): Promise<void> {
  const sigHex = (await provider.request({
    method: "eth_signTypedData_v4",
    params: [userAddress, JSON.stringify(typedData)],
  })) as `0x${string}`;

  const { r, s, v, yParity } = parseSignature(sigHex);
  const signature = { r, s, v: v !== undefined ? Number(v) : yParity + 27 };

  const res = await fetch(HL_EXCHANGE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, nonce: action.nonce, signature }),
  });
  const data = (await res.json().catch(() => ({}))) as { status?: string; response?: unknown };
  if (!res.ok || data.status !== "ok") {
    const detail =
      typeof data.response === "string" ? data.response : data.status || `HTTP ${res.status}`;
    throw new Error(`Hyperliquid rejected the request: ${detail}`);
  }
}

export type ConnectStep = "approve-agent" | "approve-builder";

export interface HyperliquidConnection {
  mainAddress: string;
  agentAddress: string;
  agentPrivateKey: string;
}

/**
 * Run the full connect flow: generate an agent wallet, have the user approve it
 * and the builder code, and return the agent credential to store. Throws if any
 * signature is rejected or any Hyperliquid call fails.
 */
export async function connectHyperliquid(opts: {
  provider: Eip1193Provider;
  mainAddress: string;
  agentName?: string;
  onStep?: (step: ConnectStep) => void;
}): Promise<HyperliquidConnection> {
  const { provider, mainAddress, onStep } = opts;
  const agentName = (opts.agentName?.trim() || DEFAULT_AGENT_NAME).slice(0, 64);

  const agentPrivateKey = generatePrivateKey();
  const agentAddress = privateKeyToAccount(agentPrivateKey).address;

  // 1. Authorise the agent wallet (trade-only, no withdrawals).
  onStep?.("approve-agent");
  const aaNonce = nextNonce();
  await signAndSubmit(
    provider,
    mainAddress,
    buildApproveAgentTypedData(agentAddress, agentName, aaNonce),
    {
      type: "approveAgent",
      hyperliquidChain: HL_CHAIN,
      signatureChainId: HL_SIGNATURE_CHAIN_ID,
      agentAddress,
      agentName,
      nonce: aaNonce,
    },
  );

  // 2. Approve Condor's builder code so orders are attributed at 1 bps.
  onStep?.("approve-builder");
  const bfNonce = nextNonce();
  await signAndSubmit(provider, mainAddress, buildApproveBuilderFeeTypedData(bfNonce), {
    type: "approveBuilderFee",
    hyperliquidChain: HL_CHAIN,
    signatureChainId: HL_SIGNATURE_CHAIN_ID,
    maxFeeRate: BUILDER_MAX_FEE_RATE,
    builder: BUILDER_ADDRESS,
    nonce: bfNonce,
  });

  return { mainAddress, agentAddress, agentPrivateKey };
}

/**
 * Credential payloads for hummingbot-api, keyed by connector name. The same agent
 * key authorises both the perpetual and spot connectors.
 */
export function buildHyperliquidCredentials(conn: HyperliquidConnection): Record<string, Record<string, string>> {
  return {
    hyperliquid_perpetual: {
      hyperliquid_perpetual_mode: "api_wallet",
      use_vault: "false",
      hyperliquid_perpetual_address: conn.mainAddress,
      hyperliquid_perpetual_secret_key: conn.agentPrivateKey,
    },
    hyperliquid: {
      hyperliquid_mode: "api_wallet",
      use_vault: "false",
      hyperliquid_address: conn.mainAddress,
      hyperliquid_secret_key: conn.agentPrivateKey,
    },
  };
}
