/**
 * Which pool listing to browse. The upstreams take genuinely different arguments
 * — GeckoTerminal ranks a *chain*'s pools and searches by address, Gateway CLMM
 * lists one *connector*'s pools and searches free text, favourites are a local
 * list of addresses — so they are separate tabs rather than one filter row.
 */
export type PoolSource =
  | { kind: "gecko"; view: "trending" | "top" | "new" | "token" }
  | { kind: "gateway"; connector: string }
  | { kind: "favorites" }
  // The pools of vaults this account runs. Its own tab rather than a filter,
  // for the same reason the others are: the rows do not come from an upstream
  // at all. A vault's pool exists the moment its curve graduates, long before
  // any indexer has seen it, so it is built from what Condor already knows —
  // which is also why it is the one tab that lists a pool with no volume yet.
  | { kind: "vaults" };

export const GECKO_TABS = [
  { view: "trending", label: "Trending" },
  { view: "top", label: "Top" },
  { view: "new", label: "New" },
  { view: "token", label: "Search" },
] as const;

/**
 * The CLMM connectors Gateway can list pools for (and open positions in).
 *
 * Both are Solana venues, so these tabs are hidden on any other chain: a Meteora
 * tab on Base lists nothing and offers no position to open, which is worse than
 * not offering it. GeckoTerminal browsing works on every chain either way.
 */
export const GATEWAY_TABS = [
  { connector: "meteora", label: "Meteora" },
  { connector: "orca", label: "Orca" },
] as const;

export const GATEWAY_TAB_CHAIN = "solana";

export function sameSource(a: PoolSource, b: PoolSource): boolean {
  if (a.kind === "gecko" && b.kind === "gecko") return a.view === b.view;
  if (a.kind === "gateway" && b.kind === "gateway")
    return a.connector === b.connector;
  if (a.kind === "vaults" && b.kind === "vaults") return true;
  return a.kind === "favorites" && b.kind === "favorites";
}
