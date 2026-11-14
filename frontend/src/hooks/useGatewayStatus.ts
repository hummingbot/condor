import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * Is Gateway up on this server — and is that known yet?
 *
 * One query key for every reader (`["gateway-status", server]`, the key
 * `GatewaySettings` and `ApiKeysSettings` already use), so the top bar, the DEX
 * pages and Settings share a single cached answer and a single poll.
 *
 * `down` is only true on a definite answer: a status still loading is not a
 * Gateway that is stopped, and treating it as one would flash "stopped"
 * banners on every DEX page load.
 */
export function useGatewayStatus(server: string | null | undefined) {
  const { data, isError, isLoading } = useQuery({
    queryKey: ["gateway-status", server],
    queryFn: () => api.getGatewayStatus(server!),
    enabled: !!server,
    refetchInterval: 30_000,
    retry: false,
  });
  const known = !!server && !isLoading && (!!data || isError);
  const running = data?.running === true;
  return { known, running, down: known && !running, unreachable: isError };
}
