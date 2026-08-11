import type { ExecutorType } from "@/components/executor/types";

/**
 * What a connector can do in the trade panel.
 *
 * DEX-ness is **not** guessed from the name here. The backend already carries four
 * mutually inconsistent connector predicates (prefix lists, substring lists, live
 * gateway lookups) that disagree on cases like `binance-smart-chain`; a fifth one
 * in the frontend would silently drift the same way, and the symptom would be "the
 * chart is blank and Grid is offered on a DEX". Instead the server tells us which
 * connectors are gateway networks (`GET /market/gateway-networks`, already
 * intersected with the set Condor can chart) and this module classifies by
 * membership in that list.
 *
 * The executor-type and strategy maps below *are* frontend literals, deliberately:
 * they are a UI product decision about which tabs and options to render, not a
 * server fact, and they are changed by whoever changes the tabs.
 */

export type ConnectorKind = "cex" | "dex";

export interface ConnectorCapabilities {
  kind: ConnectorKind;
  /** Executor types this venue supports. */
  executorTypes: ExecutorType[];
  /** Allowed `execution_strategy` values (subset of OrderConfigPanel's options). */
  orderStrategies: string[];
  /** Whether `/market/order-book` and `/market/tickers` answer — depth + markets tabs. */
  hasOrderBook: boolean;
  /** Whether `/market/trading-rules` answers — pair list + price precision. */
  hasTradingRules: boolean;
}

const CEX_CAPABILITIES: ConnectorCapabilities = {
  kind: "cex",
  executorTypes: ["order", "position", "grid", "dca"],
  orderStrategies: ["MARKET", "LIMIT", "LIMIT_MAKER", "LIMIT_CHASER"],
  hasOrderBook: true,
  hasTradingRules: true,
};

// A gateway swap has no resting order book to post to, so MARKET is the only
// execution strategy that means anything. `lp` is the CLMM liquidity position —
// the second, and only other, executor a gateway network supports.
const DEX_CAPABILITIES: ConnectorCapabilities = {
  kind: "dex",
  executorTypes: ["order", "lp"],
  orderStrategies: ["MARKET"],
  hasOrderBook: false,
  hasTradingRules: false,
};

/**
 * Capabilities of `connector`, given the gateway networks this server exposes.
 *
 * An unknown connector — or any connector at all before the gateway-networks
 * query resolves — is treated as a CEX, which is the status quo behavior for
 * every venue the panel handled before DEX support existed.
 */
export function connectorCapabilities(
  connector: string,
  gatewayNetworks: string[],
): ConnectorCapabilities {
  return gatewayNetworks.includes(connector) ? DEX_CAPABILITIES : CEX_CAPABILITIES;
}
