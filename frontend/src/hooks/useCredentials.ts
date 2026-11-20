import { useQuery } from "@tanstack/react-query";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { credentialsQuery, gatewayWalletsQuery } from "@/lib/queryClient";

export function useCredentials() {
  const { server } = useServer();

  const { data, isLoading: credsLoading, isError: credsError } = useQuery({
    ...credentialsQuery(server),
    queryFn: () => api.getCredentials(server!),
    enabled: !!server,
  });

  // A Gateway wallet (Solana/Ethereum) unlocks the portfolio just like a CEX key does.
  // Errors (e.g. Gateway not running) simply count as "no wallets".
  const { data: walletsData, isLoading: walletsLoading } = useQuery({
    ...gatewayWalletsQuery(server),
    queryFn: () => api.getGatewayWallets(server!),
    enabled: !!server,
    retry: false,
  });

  const credentials = data?.credentials ?? [];
  const hasWallets = (walletsData?.wallets ?? []).some(
    (g) => (g.walletAddresses ?? []).length > 0,
  );
  const hasKeys = credentials.length > 0 || hasWallets;
  const isLoading = credsLoading || walletsLoading;

  // "The credentials request failed and nothing is cached" is not "this user
  // has no keys": the route raises when the Hummingbot API is unreachable, and
  // reading that as an empty list covered every page with "Connect an Exchange"
  // for a user who has keys (FU-02). With a cached answer the last known list
  // still stands, so only the unknown case is reported.
  const isError = credsError && data === undefined;

  return { hasKeys, isLoading, isError, credentials };
}
