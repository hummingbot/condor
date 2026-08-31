import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * A venue's trading rules — the pair list and the price/amount increments.
 *
 * Shares its query key with the market browser's own rules query, so the trade
 * page and the browser hit one cached request per venue.
 *
 * @param enabled Pass false for venues with no rules endpoint, where the
 *   request would only 502.
 */
export function useTradingRules(server: string, connector: string, enabled = true) {
  const { data } = useQuery({
    queryKey: ["trading-rules", server, connector],
    queryFn: () => api.getTradingRules(server, connector),
    enabled: !!server && !!connector && enabled,
    staleTime: 5 * 60 * 1000,
  });
  return data;
}
