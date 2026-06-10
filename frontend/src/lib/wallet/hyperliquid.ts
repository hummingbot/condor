// Hyperliquid venue logic (HGP-87), built on the generic EVM wallet layer in ./evm.
//
// Condor adds Hyperliquid keys exclusively through an agent-wallet ("API wallet")
// flow instead of asking the user to paste a raw private key:
//
//   1. Generate a throwaway agent keypair in the browser.
//   2. The user signs `ApproveAgent` with their own wallet, authorising that agent
//      to trade (but never withdraw) on their behalf.
//   3. The user signs `ApproveBuilderFee`, authorising a 1 bps builder fee that goes
//      to the not-for-profit Hummingbot Foundation to support Condor's maintenance.
//   4. The agent private key + the user's main address are stored as the connector
//      credential. The user's real key never leaves their wallet.
//
// All EIP-712 schemes mirror hummingbot's HyperliquidAuth.sign_user_signed_action.

import { concat, keccak256, numberToBytes } from "viem";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";

import {
  ARBITRUM_ONE,
  type Eip1193Provider,
  ensureChain,
  nextNonce,
  normalizeSignature,
  signTypedDataV4,
} from "./evm";

// ── Condor builder code ──
// The 1 bps builder fee on trades goes to the not-for-profit Hummingbot Foundation to support
// Condor's maintenance. These constants build the approveBuilderFee the user signs in their wallet
// (only the client can do this). The fee actually billed is hardcoded in the hummingbot connector
// (FOUNDATION_BUILDER_FEE_TENTHS_BPS), which caps it — editing these here can't raise it.
// BUILDER_ADDRESS must match the connector's builder; keep BUILDER_MAX_FEE_RATE >= the connector's
// fee. (Not fetched: hummingbot-api doesn't expose these constants, and both change ~never.)
export const BUILDER_ADDRESS = "0x10ba451e6439efc6a17dc20d21121aa838100705";
export const BUILDER_FEE_BPS = 1; // UI label only ("approve the Condor builder code (1 bps)")
export const BUILDER_MAX_FEE_RATE = "0.01%"; // 1 bps = 0.01%; the max the user authorizes on-chain

// BUILDER_MAX_FEE_RATE expressed in tenths of a basis point (Hyperliquid's maxBuilderFee unit):
// "0.01%" -> 10. Used to detect an existing approval so a returning user isn't asked to sign again.
const BUILDER_MAX_FEE_RATE_TENTHS_BPS = Math.round(parseFloat(BUILDER_MAX_FEE_RATE) * 1000);

// ── Hummingbot referral code ──
// Hyperliquid gives referred accounts a 4% discount on taker/maker fees. Linking is separate
// from the builder code above: the builder code attributes order flow to Condor, the referral
// code is the user's own fee discount. It can only be set once per account.
export const REFERRAL_CODE = "HUMMINGBOT";
export const REFERRAL_FEE_DISCOUNT = "4%";
// Hyperliquid sign-up link carrying the referral code (new accounts get REFERRAL_FEE_DISCOUNT off fees).
export const HYPERLIQUID_SIGNUP_URL = `https://app.hyperliquid.xyz/join/${REFERRAL_CODE}`;

// ── Hyperliquid mainnet signing constants ──
const HL_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange";
const HL_INFO_URL = "https://api.hyperliquid.xyz/info";
const HL_CHAIN = "Mainnet";
const HL_SIGNATURE_CHAIN_ID = ARBITRUM_ONE.chainId; // 0xa4b1 — Hyperliquid mainnet signs on Arbitrum One
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

// Hyperliquid caps agent ("API wallet") names at 16 chars. The field is pre-filled with the default
// base plus today's date (e.g. "condor-20260609") and the user can edit it freely. Same-day reconnects
// reuse the name and replace the prior agent, which is fine — the latest agent key is the credential.
export const MAX_AGENT_NAME = 16;

/** Local creation date YYYYMMDD (8 chars). */
function creationStamp(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`;
}

/** Pre-fill for the agent-name field: the default base with today's date, e.g. "condor-20260609". */
export function defaultAgentName(): string {
  return `${DEFAULT_AGENT_NAME}-${creationStamp()}`.slice(0, MAX_AGENT_NAME);
}

/** Clean a user-edited agent name to Hyperliquid's limits (alphanumeric + dash, max 16 chars). */
export function sanitizeAgentName(input: string): string {
  const cleaned = (input || "").trim().replace(/[^a-zA-Z0-9-]/g, "").slice(0, MAX_AGENT_NAME);
  return cleaned || defaultAgentName();
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

/**
 * The user's current on-chain approved max builder fee for Condor's builder, in tenths of a basis
 * point (0 if never approved). The maxBuilderFee info query is unauthenticated.
 */
export async function getHyperliquidBuilderApproval(userAddress: string): Promise<number> {
  const res = await fetch(HL_INFO_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "maxBuilderFee", user: userAddress, builder: BUILDER_ADDRESS }),
  });
  if (!res.ok) throw new Error(`Hyperliquid builder-fee lookup failed (HTTP ${res.status}).`);
  return Number(await res.json());
}

/**
 * True if the user already approved at least the builder fee Condor needs. The approval persists
 * on-chain per (user, builder), so a returning user need not sign approveBuilderFee again.
 */
export async function hasApprovedBuilderFee(userAddress: string): Promise<boolean> {
  return (await getHyperliquidBuilderApproval(userAddress)) >= BUILDER_MAX_FEE_RATE_TENTHS_BPS;
}

/** Turn a raw Hyperliquid error string into actionable guidance for known cases. */
export function friendlyHyperliquidError(detail: string): string {
  const d = (detail || "").trim();
  if (/must deposit before performing actions/i.test(d)) {
    return (
      "This wallet isn't funded on Hyperliquid yet. Deposit USDC to Hyperliquid from this wallet, " +
      "then reconnect — Hyperliquid requires a deposit before it will authorize an agent wallet or builder code."
    );
  }
  if (/extra agents? are not allowed|too many agents/i.test(d)) {
    return "This wallet has reached Hyperliquid's API/agent-wallet limit. Remove an unused API wallet in the Hyperliquid app, then reconnect.";
  }
  if (/referr(er|al).*already|already.*referr(er|al)|cannot set referrer/i.test(d)) {
    return "This wallet already has a referral code set on Hyperliquid — it can only be set once.";
  }
  return `Hyperliquid rejected the request: ${d}`;
}

async function signAndSubmit(
  provider: Eip1193Provider,
  userAddress: string,
  typedData: Record<string, unknown>,
  action: Record<string, unknown>,
): Promise<void> {
  const signature = await signTypedDataV4(provider, userAddress, typedData);

  const res = await fetch(HL_EXCHANGE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, nonce: action.nonce, signature }),
  });
  const data = (await res.json().catch(() => ({}))) as { status?: string; response?: unknown };
  if (!res.ok || data.status !== "ok") {
    const detail =
      typeof data.response === "string" ? data.response : data.status || `HTTP ${res.status}`;
    throw new Error(friendlyHyperliquidError(detail));
  }
}

export type ConnectStep = "switch-chain" | "approve-agent" | "approve-builder";

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
  // The agent-name field is pre-filled with a dated default (e.g. "condor-20260609") and editable;
  // use it as-is, just cleaned to Hyperliquid's 16-char / charset limits.
  const agentName = sanitizeAgentName(opts.agentName ?? "");

  // Hyperliquid mainnet signing uses the Arbitrum One domain; make sure the wallet is on it.
  onStep?.("switch-chain");
  await ensureChain(provider, ARBITRUM_ONE);

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

  // 2. Approve the builder fee (1 bps), which supports Condor's maintenance via the not-for-profit
  //    Hummingbot Foundation — but skip it if the user already approved at least that much. The
  //    approveBuilderFee approval persists on-chain per (user, builder), so a returning user (or one
  //    who approved elsewhere) need not sign again. If the lookup can't confirm a prior approval, we
  //    fall through and (re)approve so the fee is never silently left unapproved.
  let builderApproved = false;
  try {
    builderApproved = (await getHyperliquidBuilderApproval(mainAddress)) >= BUILDER_MAX_FEE_RATE_TENTHS_BPS;
  } catch {
    builderApproved = false;
  }
  if (!builderApproved) {
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
  }

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

// ── Referral code (setReferrer) ───────────────────────────────────────────────
//
// Unlike approveAgent/approveBuilderFee (user-signed actions on the Arbitrum domain), setReferrer
// is a Hyperliquid *L1 action*: msgpack(action) + nonce + a vault-address byte is keccak256-hashed,
// and that hash is signed as a "phantom agent" against the Exchange domain (chainId 1337).
//
// Injected browser wallets (MetaMask) reject eth_signTypedData_v4 whose domain.chainId differs from
// the active chain, and chain 1337 isn't switchable — so an L1 action can't be signed by the user's
// main wallet. Hyperliquid's API-wallet system exists for exactly this: we sign with the agent key
// we already generated, which signs on behalf of the master account (no chain check on local keys).

const L1_DOMAIN = {
  name: "Exchange",
  version: "1",
  chainId: 1337,
  verifyingContract: "0x0000000000000000000000000000000000000000",
} as const;

const AGENT_TYPES = {
  Agent: [
    { name: "source", type: "string" },
    { name: "connectionId", type: "bytes32" },
  ],
} as const;

// Minimal msgpack encoder for a string→string map with short keys/values — all that a setReferrer
// action ({type, code}) needs. Hyperliquid hashes the msgpack bytes, so the layout must match the
// spec exactly: fixmap/fixstr/str8 per the msgpack standard, keys emitted in insertion order (the
// hash is order-sensitive — never sort).
function pushMsgpackStr(s: string, out: number[]): void {
  const bytes = new TextEncoder().encode(s);
  if (bytes.length < 32) out.push(0xa0 | bytes.length); // fixstr
  else if (bytes.length < 256) out.push(0xd9, bytes.length); // str8
  else throw new Error("msgpack: string too long");
  for (const b of bytes) out.push(b);
}

function encodeMsgpackStringMap(obj: Record<string, string>): Uint8Array {
  const keys = Object.keys(obj);
  if (keys.length >= 16) throw new Error("msgpack: map too large");
  const out: number[] = [0x80 | keys.length]; // fixmap
  for (const k of keys) {
    pushMsgpackStr(k, out);
    pushMsgpackStr(obj[k], out);
  }
  return Uint8Array.from(out);
}

/** keccak256(msgpack(action) ‖ nonce(8B big-endian) ‖ 0x00) — the L1 "connectionId" hash (no vault). */
function l1ActionHash(action: Record<string, string>, nonce: number): `0x${string}` {
  const data = concat([
    encodeMsgpackStringMap(action),
    numberToBytes(nonce, { size: 8 }),
    new Uint8Array([0x00]), // vaultAddress: none
  ]);
  return keccak256(data);
}

/** True if `userAddress` already has a Hyperliquid referrer (setReferrer would be rejected). */
export async function hasHyperliquidReferrer(userAddress: string): Promise<boolean> {
  const res = await fetch(HL_INFO_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "referral", user: userAddress }),
  });
  if (!res.ok) throw new Error(`Hyperliquid referral lookup failed (HTTP ${res.status}).`);
  const data = (await res.json()) as { referredBy?: unknown };
  return Boolean(data.referredBy);
}

/**
 * Link the Hummingbot referral code to the connected account, signed with the agent key as an L1
 * action. Throws with actionable guidance if Hyperliquid rejects it (e.g. a referrer already set).
 */
export async function setHyperliquidReferrer(
  conn: HyperliquidConnection,
  code: string = REFERRAL_CODE,
): Promise<void> {
  const account = privateKeyToAccount(conn.agentPrivateKey as `0x${string}`);
  const nonce = nextNonce();
  const action = { type: "setReferrer", code };
  const connectionId = l1ActionHash(action, nonce);

  const sigHex = await account.signTypedData({
    domain: L1_DOMAIN,
    types: AGENT_TYPES,
    primaryType: "Agent",
    message: { source: "a", connectionId }, // "a" = mainnet
  });
  const signature = normalizeSignature(sigHex);

  const res = await fetch(HL_EXCHANGE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, nonce, signature }),
  });
  const data = (await res.json().catch(() => ({}))) as { status?: string; response?: unknown };
  if (!res.ok || data.status !== "ok") {
    const detail =
      typeof data.response === "string" ? data.response : data.status || `HTTP ${res.status}`;
    throw new Error(friendlyHyperliquidError(detail));
  }
}
