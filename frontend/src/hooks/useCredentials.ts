import { useQuery } from "@tanstack/react-query";

import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

export function useCredentials() {
  const { server } = useServer();

  const { data, isLoading } = useQuery({
    queryKey: ["settings-credentials", server],
    queryFn: () => api.getCredentials(server!),
    enabled: !!server,
    staleTime: 30000,
  });

  const credentials = data?.credentials ?? [];
  const hasKeys = credentials.length > 0;

  return { hasKeys, isLoading, credentials };
}
