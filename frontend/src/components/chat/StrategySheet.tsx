import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { StrategyWorkbench } from "@/components/agent/StrategyWorkbench";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { api } from "@/lib/api";

/**
 * A strategy, opened beside the conversation that made you want to open it.
 *
 * The agent panel's rule used to be that a strategy is the one thing that
 * navigates — "the page that owns starting it with real money" — and every
 * other section of an agent opens in this pane. In practice that made the
 * commonest gesture on the screen the most destructive one: the agent lists
 * its strategies, you click the one it just named, and the chat, the session
 * tabs and the whole workspace are replaced by a page whose four headline
 * numbers are the four you were already looking at.
 *
 * The premise was wrong in both directions. Starting a loop is guarded by its
 * own dialog and its own confirmation, not by how much of the window it is
 * displayed in — a page is not a safety mechanism. And the panel's *other*
 * rule, written one comment above it, is the one that should have won: anything
 * worth doing to an agent should be worth doing here, next to the conversation.
 *
 * So the same {@link StrategyWorkbench} the page renders opens here instead,
 * `dense`. The page is not deleted and not demoted: it keeps its URL for links
 * and bookmarks, and the sheet's full-screen control is a **door** to it rather
 * than a bigger overlay (the idiom `WorkspaceSheet.onFullscreen` exists for,
 * and the routine library already uses). The pane is the default; the page is
 * one click away and still the whole of the window when you want it.
 *
 * `paneProfile="tune"` — an even split, not a report's two thirds. This is a
 * surface you steer while reading what the agent says about it, and the chat
 * beside it is being used in the same minute.
 */
export function StrategySheet({
  slug,
  sslug,
  onClose,
}: {
  slug: string;
  sslug: string;
  /** Back to whatever held the pane before — in practice the agent panel. */
  onClose: () => void;
}) {
  const navigate = useNavigate();
  // The workbench polls this key anyway; reading it here for the title is a
  // cache hit, and the fallback is the slug rather than a spinner in the bar.
  const { data: strategy } = useQuery({
    queryKey: ["strategy", slug, sslug],
    queryFn: () => api.getStrategy(slug, sslug),
    enabled: !!slug && !!sslug,
  });

  return (
    <WorkspaceSheet
      title={strategy?.name || sslug}
      subtitle={slug}
      paneProfile="tune"
      // Full screen is the page, not a bigger sheet: the strategy has a URL,
      // and a reader who wants the whole window wants the thing they can link
      // to and come back to.
      onFullscreen={() => navigate(`/agents/${slug}/strategies/${sslug}`)}
      onClose={onClose}
    >
      <StrategyWorkbench slug={slug} sslug={sslug} dense onDeleted={onClose} />
    </WorkspaceSheet>
  );
}
