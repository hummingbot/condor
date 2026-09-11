import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { normalizeAgentSlug } from "@/lib/agentSlug";
import { api, type ConversationMeta } from "@/lib/api";

/**
 * The bubble's second way to a conversation: the newest one this agent already
 * has on the server (CORR-257).
 *
 * Adoption (CORR-255) covers the conversation that is *live* — a tab open in
 * the workspace. This covers the one that is merely *durable*: a reload drops
 * every live slot, and the shell deliberately prewarms only at `/`, so the
 * bubble left open on `/agents/X` comes back to an empty hero with the whole
 * conversation sitting one API call away. Its first message used to mint
 * conversation number two, against the bubble's own "one conversation per
 * bound agent" invariant and against the session budget FEAT-059 rations.
 *
 * Three rules make it stay the cheap surface:
 *
 * - `enabled` is the caller's whole gate — the panel being open, on an agent's
 *   page, with nothing live to adopt. A user who never opens the bubble must
 *   issue no request and spawn nothing (FEAT-059: the bubble never prewarms).
 * - The read is the rail's own query key and fetcher, so when both are mounted
 *   react-query serves one fetch to both rather than adding a second.
 * - One handover per conversation. A resume that dies on arrival would
 *   otherwise be retried on the next frame, forever — one spawn each time.
 */
export function useBubbleResume(
  enabled: boolean,
  slug: string,
  onResume: (meta: ConversationMeta) => void,
) {
  const { data } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.listConversations(),
    enabled,
  });

  // The callback closes over the bubble's render; kept in a ref so the effect
  // fires on "there is something to resume", not on every render. Written from
  // an effect of its own, declared first so it commits before the one below
  // reads it — assigning during render is a side effect, and the lint gate
  // says so.
  const onResumeRef = useRef(onResume);
  useEffect(() => {
    onResumeRef.current = onResume;
  });
  // Which conversation has already been handed over, per slug.
  const resumed = useRef<Record<string, string>>({});

  useEffect(() => {
    if (!enabled || !data) return;
    // First match wins: `/api/v1/conversations` is ordered newest first — the
    // same ordering `prewarmLatest` trusts when it takes `list[0]`. The
    // record's slug is normalized because a conversation written before the
    // two spellings were reconciled can still say `"condor"`.
    const meta = data.find((c) => normalizeAgentSlug(c.agent_slug) === slug);
    if (!meta || resumed.current[slug] === meta.id) return;
    resumed.current[slug] = meta.id;
    onResumeRef.current(meta);
  }, [enabled, data, slug]);
}
