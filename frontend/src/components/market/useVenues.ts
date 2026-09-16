import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { VenueTraits } from "@/lib/connector-capabilities";

/**
 * Every venue the trade panel can offer, each with the traits the UI
 * decisions rest on (see `VenueTraits`). The server dedups (a venue in both
 * of its input lists is a Hummingbot connector), so there is no merge to get
 * wrong here.
 *
 * CreateExecutor and DexPool both ask through this hook so they share one
 * cached request per server, and so the `["venues", server]` key and its
 * 5-minute `staleTime` are declared in exactly one place — a third caller
 * cannot copy the literal with a drifted key or `staleTime` (ARCH-355).
 *
 * `isPending` stays true (and `venues` empty) until the first answer lands,
 * so callers can tell "no venues yet" apart from "no venues at all" instead
 * of flashing every venue as view-only while the query is in flight.
 */
/**
 * The `["venues", server]` key, single-sourced (ARCH-355) so a caller that
 * only needs to invalidate the entry — a credential mutation, not a render —
 * does not have to copy the literal (CORR-353).
 */
export function venuesQueryKey(server: string | null | undefined) {
  return ["venues", server] as const;
}

export function useVenues(server: string | null | undefined) {
  const { data: venues = [], isPending } = useQuery<VenueTraits[]>({
    queryKey: venuesQueryKey(server),
    queryFn: () => api.getVenues(server!),
    enabled: !!server,
    staleTime: 5 * 60 * 1000,
  });
  return { venues, isPending };
}
