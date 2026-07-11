#!/usr/bin/env node
// Standalone WalletConnect bridge for the Hyperliquid agent-wallet + builder-fee
// approval flow. Spawned as a subprocess by condor/walletconnect.py -- ONE
// signature per process invocation, each with its OWN fresh WalletConnect
// pairing (fresh QR / fresh MetaMask deep link), rather than one long-lived
// session driving both signatures back to back. A fresh pairing forces the
// wallet app to foreground on every step; a second request sent to an
// already-paired-but-backgrounded session doesn't reliably prompt the wallet
// (confirmed by hand -- see docs/architecture/hyperliquid-walletconnect-spike.md).
//
// Invocation:
//   node bridge.mjs agent
//   node bridge.mjs builder <expectedMainAddress>
//
// Speaks newline-delimited JSON on stdout, one event per line:
//   {"event":"uri","uri":"wc:..."}
//   {"event":"approved","mainAddress":"0x..."}
//   {"event":"done", ...}                          -- shape depends on step, see below
//   {"event":"error","message":"..."}
//
// Python never touches private keys or the WalletConnect protocol -- all of
// that lives here. The EIP-712 payloads mirror
// frontend/src/lib/wallet/hyperliquid.ts by hand (spike-scoped; see
// docs/architecture/hyperliquid-walletconnect-spike.md for the tradeoffs and
// the follow-up to de-duplicate if this graduates past a spike).

import { SignClient } from "@walletconnect/sign-client";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";

// @walletconnect/sign-client's internal pino logger writes its own (non-JSON,
// multi-line) diagnostics straight to stdout regardless of the `logger: "silent"`
// option passed below in some versions -- so rather than relying on fully
// silencing a third-party dependency, every line WE emit is prefixed with this
// marker. The Python side only parses lines that start with it and quietly
// ignores everything else, so stray SDK log noise can never be mistaken for one
// of our protocol events.
const EVENT_PREFIX = "CONDOR_WC_EVENT ";

function emit(obj) {
  process.stdout.write(EVENT_PREFIX + JSON.stringify(obj) + "\n");
}

const PROJECT_ID = process.env.WALLETCONNECT_PROJECT_ID;
if (!PROJECT_ID) {
  emit({ event: "error", message: "WALLETCONNECT_PROJECT_ID is not set." });
  process.exit(1);
}

const STEP = process.argv[2]; // "agent" | "builder"
const EXPECTED_MAIN_ADDRESS = process.argv[3] || null; // required for "builder"
if (STEP !== "agent" && STEP !== "builder") {
  emit({ event: "error", message: `Unknown step '${STEP}'. Use agent | builder.` });
  process.exit(1);
}
if (STEP === "builder" && !EXPECTED_MAIN_ADDRESS) {
  emit({ event: "error", message: "The builder step requires the expected main address as argv[3]." });
  process.exit(1);
}

// ── Hyperliquid mainnet signing constants (mirrors wallet/hyperliquid.ts) ──
const CHAIN_ID_NUM = 42161; // Arbitrum One -- Hyperliquid mainnet signs on this domain
const CHAIN = `eip155:${CHAIN_ID_NUM}`;
const HL_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange";
const HL_CHAIN = "Mainnet";

const BUILDER_ADDRESS = "0x10ba451e6439efc6a17dc20d21121aa838100705";
const BUILDER_MAX_FEE_RATE = "0.01%"; // 1 bps -- mirrors _BUILDER_MAX_FEE_RATE_TENTHS_BPS in condor/walletconnect.py
const DEFAULT_AGENT_NAME = "condor";
const MAX_AGENT_NAME = 16;

const EIP712_DOMAIN = {
  name: "HyperliquidSignTransaction",
  version: "1",
  chainId: CHAIN_ID_NUM,
  verifyingContract: "0x0000000000000000000000000000000000000000",
};
const DOMAIN_TYPE = [
  { name: "name", type: "string" },
  { name: "version", type: "string" },
  { name: "chainId", type: "uint256" },
  { name: "verifyingContract", type: "address" },
];

function creationStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`;
}

function defaultAgentName() {
  return `${DEFAULT_AGENT_NAME}-${creationStamp()}`.slice(0, MAX_AGENT_NAME);
}

function nextNonce() {
  return Date.now();
}

/** Normalize a 65-byte hex signature into the {r, s, v} shape Hyperliquid expects. */
function normalizeSignature(sigHex) {
  const hex = sigHex.slice(2);
  if (hex.length !== 130) throw new Error(`Expected a 65-byte signature, got ${hex.length / 2} bytes.`);
  const r = `0x${hex.slice(0, 64)}`;
  const s = `0x${hex.slice(64, 128)}`;
  let v = parseInt(hex.slice(128, 130), 16);
  if (v < 27) v += 27; // normalize a 0/1 yParity to the legacy 27/28 v value
  return { r, s, v };
}

function buildApproveAgentTypedData(agentAddress, agentName, nonce) {
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

function buildApproveBuilderFeeTypedData(nonce) {
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

async function signAndSubmit(client, topic, mainAddress, typedData, action) {
  const sigHex = await client.request({
    topic,
    chainId: CHAIN,
    request: {
      method: "eth_signTypedData_v4",
      params: [mainAddress, JSON.stringify(typedData)],
    },
  });
  const signature = normalizeSignature(sigHex);

  const res = await fetch(HL_EXCHANGE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, nonce: action.nonce, signature }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status !== "ok") {
    const detail = typeof data.response === "string" ? data.response : data.status || `HTTP ${res.status}`;
    throw new Error(`Hyperliquid rejected the request: ${detail}`);
  }
}

// client.connect()'s relay handshake either resolves in a couple seconds or, on
// e.g. a bad/misconfigured project id, silently retries forever without ever
// resolving or rejecting -- confirmed by hand while building this bridge (see
// docs/architecture/hyperliquid-walletconnect-spike.md). Bound it explicitly so
// a bad config fails fast with a clear error instead of leaking an orphaned,
// permanently-hung subprocess.
const RELAY_CONNECT_TIMEOUT_MS = 15_000;
// approval() genuinely waits on a human to act on their phone -- give it minutes,
// not seconds, but still bound it so an abandoned session doesn't hang forever.
const APPROVAL_TIMEOUT_MS = 5 * 60_000;

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`Timed out waiting for ${label}.`)), ms)),
  ]);
}

async function pairAndApprove(client) {
  const { uri, approval } = await withTimeout(
    client.connect({
      requiredNamespaces: {
        eip155: {
          methods: ["eth_signTypedData_v4"],
          chains: [CHAIN],
          events: ["chainChanged", "accountsChanged"],
        },
      },
    }),
    RELAY_CONNECT_TIMEOUT_MS,
    "a WalletConnect pairing URI",
  );
  emit({ event: "uri", uri });

  const session = await withTimeout(approval(), APPROVAL_TIMEOUT_MS, "wallet approval");
  const account = session.namespaces.eip155.accounts[0]; // "eip155:42161:0xabc..."
  const mainAddress = account.split(":")[2];
  return { session, mainAddress };
}

/** Pairs, waits for approval, and (for the builder step, reconnecting after the
 * agent step) verifies the reconnecting wallet is the same one that created the
 * agent -- each step is a fresh pairing, so nothing but the address itself
 * carries over between them. */
async function pairApproveAndVerify(client) {
  const { session, mainAddress } = await pairAndApprove(client);

  if (EXPECTED_MAIN_ADDRESS && mainAddress.toLowerCase() !== EXPECTED_MAIN_ADDRESS.toLowerCase()) {
    await client.disconnect({ topic: session.topic, reason: { code: 6000, message: "wrong wallet" } }).catch(() => {});
    throw new Error(
      `Connected wallet ${mainAddress} doesn't match ${EXPECTED_MAIN_ADDRESS} from the previous step -- ` +
        "reconnect using the same wallet.",
    );
  }
  emit({ event: "approved", mainAddress });
  return { session, mainAddress };
}

async function runAgentStep(client) {
  const { session, mainAddress } = await pairApproveAndVerify(client);

  const agentPrivateKey = generatePrivateKey();
  const agentAddress = privateKeyToAccount(agentPrivateKey).address;
  const agentName = defaultAgentName();
  const nonce = nextNonce();

  await signAndSubmit(client, session.topic, mainAddress, buildApproveAgentTypedData(agentAddress, agentName, nonce), {
    type: "approveAgent",
    hyperliquidChain: HL_CHAIN,
    signatureChainId: `0x${CHAIN_ID_NUM.toString(16)}`,
    agentAddress,
    agentName,
    nonce,
  });

  emit({ event: "done", mainAddress, agentAddress, agentPrivateKey });
  await client.disconnect({ topic: session.topic, reason: { code: 6000, message: "done" } }).catch(() => {});
}

async function runBuilderStep(client) {
  const { session, mainAddress } = await pairApproveAndVerify(client);

  const nonce = nextNonce();
  await signAndSubmit(client, session.topic, mainAddress, buildApproveBuilderFeeTypedData(nonce), {
    type: "approveBuilderFee",
    hyperliquidChain: HL_CHAIN,
    signatureChainId: `0x${CHAIN_ID_NUM.toString(16)}`,
    maxFeeRate: BUILDER_MAX_FEE_RATE,
    builder: BUILDER_ADDRESS,
    nonce,
  });

  emit({ event: "done", mainAddress });
  await client.disconnect({ topic: session.topic, reason: { code: 6000, message: "done" } }).catch(() => {});
}

async function main() {
  const client = await withTimeout(
    SignClient.init({
      projectId: PROJECT_ID,
      logger: "silent",
      metadata: {
        name: "Condor",
        description: "Connect your Hyperliquid wallet to Condor",
        url: "https://hummingbot.org",
        icons: [],
      },
    }),
    RELAY_CONNECT_TIMEOUT_MS,
    "the WalletConnect relay to initialize",
  );

  if (STEP === "agent") {
    await runAgentStep(client);
  } else {
    await runBuilderStep(client);
  }
  process.exit(0);
}

main().catch((err) => {
  emit({ event: "error", message: err?.message || String(err) });
  process.exit(1);
});
