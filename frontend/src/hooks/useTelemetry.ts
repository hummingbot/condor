import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { TelemetryEffectiveLevel, TelemetrySettingsResponse } from "@/lib/api";

export const TELEMETRY_KEY = ["telemetry-settings"] as const;

/**
 * The install's telemetry state, shared by the two places that render it: the
 * consent banner in the shell and the card in Settings → Privacy. One query key,
 * so opening Settings right after answering the banner costs no second request
 * and cannot show a staler answer than the one just given.
 */
export function useTelemetry() {
  return useQuery({
    queryKey: TELEMETRY_KEY,
    queryFn: api.getTelemetrySettings,
    // Nothing else in the app changes this, and it survives restarts on disk.
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function useSetTelemetryLevel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (level: TelemetryEffectiveLevel) => api.setTelemetryLevel(level),
    onSuccess: (res) => {
      // Write the answer through rather than refetching: the banner unmounts on
      // `consent !== "unknown"`, and a refetch round-trip would leave it on
      // screen long enough to look like the click did nothing. The state comes
      // from the response, not from a guess — turning reporting off answers
      // `denied`, not `granted`.
      qc.setQueryData(TELEMETRY_KEY, (old: TelemetrySettingsResponse | undefined) =>
        old ? { ...old, level: res.level, consent: res.consent } : old,
      );
    },
  });
}

/**
 * Should we ask? Only an install that has never answered, only the admin who is
 * allowed to answer, and never when the environment has pinned the level — in
 * that case there is no answer left to give.
 */
export function shouldAskConsent(data: TelemetrySettingsResponse | undefined): boolean {
  return !!data && data.consent === "unknown" && data.can_change && !data.env_overridden;
}
