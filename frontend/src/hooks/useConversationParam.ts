import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import type { SlotInfo } from "@/hooks/useChatSocket";
import { api } from "@/lib/api";

/**
 * `?conversation=<id>` — a link that opens one conversation (FEAT-111).
 *
 * The workspace's Runs rail lists an agent's chats beside its loop runs, and a
 * chat row has to *go* somewhere: the chat is the surface for a conversation,
 * and rebuilding a wide surface inside a narrow one is what FEAT-103 argued
 * against. Navigation is the only channel a different page has, so the
 * conversation travels in the URL — the same shape `?agent=` and `?ask=`
 * already use to hand the chat a request.
 *
 * It lives in the shell rather than in the chat workspace because the shell is
 * where the socket and the slots live: a link that landed on `/` would
 * otherwise have to wait for the tab to mount, and there is no reason for two
 * components to own one parameter.
 *
 * Three rules, all of them the ones `?agent=` settled:
 *
 * - **Consumed once and stripped.** A reload must not resume a second time, and
 *   the parameter is a handover, not a state. It is deleted with `replace`, so
 *   Back does not walk through the same handover again.
 * - **The meta comes from the rail's own query key**, so when the chat is
 *   mounted this shares a fetch rather than adding one. `resumeConversation`
 *   can work from the id alone, but without the record the slot opens on the
 *   default brain rather than the one that was answering.
 * - **A conversation that is not there is still consumed.** Otherwise a deleted
 *   id retries forever, one spawn per frame.
 */
export function useConversationParam(
  resumeConversation: (id: string, meta?: Partial<SlotInfo>) => void,
) {
  const [searchParams, setSearchParams] = useSearchParams();
  const wanted = searchParams.get("conversation");

  const { data, status } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.listConversations(),
    enabled: !!wanted,
  });

  // The callback and the writer close over a render; kept in refs so the effect
  // below fires on "there is a conversation to open", not on every render.
  const resumeRef = useRef(resumeConversation);
  const paramsRef = useRef({ searchParams, setSearchParams });
  useEffect(() => {
    resumeRef.current = resumeConversation;
    paramsRef.current = { searchParams, setSearchParams };
  });

  const consumed = useRef<string | null>(null);
  useEffect(() => {
    if (!wanted || consumed.current === wanted) return;
    // Wait for the list to settle either way — but only once. An unreachable
    // API must not leave the link permanently unopened.
    if (status === "pending") return;
    consumed.current = wanted;

    const meta = data?.find((c) => c.id === wanted);
    resumeRef.current(
      wanted,
      meta
        ? {
            agent_key: meta.agent_key,
            server_name: meta.server_name || undefined,
            agent_slug: meta.agent_slug,
          }
        : undefined,
    );

    const rest = new URLSearchParams(paramsRef.current.searchParams);
    rest.delete("conversation");
    paramsRef.current.setSearchParams(rest, { replace: true });
  }, [wanted, status, data]);
}
