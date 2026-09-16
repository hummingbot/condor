import { useQuery } from "@tanstack/react-query";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { credentialsQuery, gatewayWalletsQuery } from "@/lib/queryClient";

export function useCredentials() {
  const { server } = useServer();

  const { data, isLoading: credsLoading } = useQuery({
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

  return { hasKeys, isLoading, credentials };
}
