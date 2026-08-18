import { useQuery } from "@tanstack/react-query";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

export function useCredentials() {
  const { server } = useServer();

  const { data, isLoading: credsLoading } = useQuery({
    queryKey: ["settings-credentials", server],
    queryFn: () => api.getCredentials(server!),
    enabled: !!server,
    staleTime: 30000,
  });

  // A Gateway wallet (Solana/Ethereum) unlocks the portfolio just like a CEX key does.
  // Errors (e.g. Gateway not running) simply count as "no wallets".
  const { data: walletsData, isLoading: walletsLoading } = useQuery({
    queryKey: ["gateway-wallets", server],
    queryFn: () => api.getGatewayWallets(server!),
    enabled: !!server,
    staleTime: 30000,
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
