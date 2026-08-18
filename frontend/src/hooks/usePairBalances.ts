import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * What the wallet holds of a pair's two tokens on one connector.
 *
 * `null` means "not held" (or not known yet) rather than zero, so a panel can
 * tell a wallet with no SOL apart from one whose portfolio has not loaded, and
 * grey out the percentage presets instead of sizing an order off a fake 0.
 */
export interface PairBalances {
  base: number | null;
  quote: number | null;
}

/**
 * Available base/quote balance for a pair on a connector.
 *
 * Symbols are passed in rather than split off `trading_pair`, because a DEX pair
 * is `<base_mint>-<quote_symbol>`: splitting it would look up a balance under a
 * mint address and never find one. Shares the `["portfolio", server]` query with
 * the trade pane, so opening a pool adds no extra request.
 */
export function usePairBalances(
  server: string | null,
  connector: string,
  baseSymbol: string | undefined,
  quoteSymbol: string | undefined,
): PairBalances {
  const { data: portfolio } = useQuery({
    queryKey: ["portfolio", server],
    queryFn: () => api.getPortfolio(server!),
    enabled: !!server,
    refetchInterval: 30_000,
  });

  const balances = portfolio?.connectors?.find(
    (c) => c.connector === connector,
  )?.balances;

  const find = (symbol: string | undefined) => {
    if (!symbol) return null;
    const upper = symbol.toUpperCase();
    const hit = balances?.find((b) => b.token.toUpperCase() === upper);
    return hit ? hit.available : null;
  };

  return { base: find(baseSymbol), quote: find(quoteSymbol) };
}
