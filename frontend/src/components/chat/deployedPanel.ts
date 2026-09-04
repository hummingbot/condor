/**
 * The conversation's ledger: who reads it, and the tile that opens it.
 *
 * Split from the panel that draws it for the reason `accountPanels` and
 * `contextPanels` are: the two halves of this feature live in different places.
 * The table is a pane occupant (`DockDeployed`), and the word that opens it
 * belongs to the one strip shared with everything else on the right edge
 * (`WorkspaceRail`) — so the page composes both, and the state they share can
 * sit inside neither.
 */
import { useQuery } from "@tanstack/react-query";
import { Package } from "lucide-react";

import type { RailItem } from "@/components/chat/WorkspaceRail";
import { api } from "@/lib/api";

/**
 * The conversation's ledger, shared by the panel and the rail tile that badges
 * it.
 *
 * One query key for both, so the count on the rail and the rows in the panel
 * are the same fetch rather than two — and so opening the panel is a cache hit.
 * It is polled because a badge that only appears on reload is not a badge; the
 * poll is affordable because the route makes **no** Hummingbot API call for a
 * conversation that recorded nothing, which is nearly all of them.
 */
export function useConversationDeployments(conversationId: string) {
  return useQuery({
    queryKey: ["conversation-deployments", conversationId],
    queryFn: () => api.getConversationDeployments(conversationId),
    enabled: !!conversationId,
    refetchInterval: 30000,
  });
}

/**
 * The rail tile that opens this panel, and the badge that makes it worth
 * shipping.
 *
 * A panel behind a click is a panel nobody discovers, so the tile carries the
 * count whenever this conversation has deployed something and carries nothing
 * when it has not — the same device the rail's Tasks and Routines tiles already
 * use for what is running. That is the difference between a panel and a
 * notification, and it is the whole of the Decision's answer to A's failure
 * mode.
 *
 * A descriptor rather than JSX in the page, for the reason `accountPanels` and
 * `deployments.ts` are modules: the rule is reachable from a test, and there is
 * still exactly one rail button.
 */
export function deployedRailItem({
  conversationId,
  count,
  active,
  onToggle,
}: {
  conversationId: string;
  count: number;
  active: boolean;
  onToggle: () => void;
}): RailItem {
  return {
    id: "deployed",
    label: "Deployed",
    // The glyph `DeploymentLedger` heads its own table with, so the tile and
    // what it opens read as one thing.
    Icon: Package,
    hint: "The bots and controllers this conversation created",
    active,
    // Nothing has been said yet, so there is no conversation to have deployed
    // anything — the same reason the desk's tiles go dead without a server.
    disabled: !conversationId,
    disabledHint: "Start the conversation — it has deployed nothing yet",
    count,
    onToggle,
  };
}
