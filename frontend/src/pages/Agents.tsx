import { useSearchParams } from "react-router-dom";

import { FleetOverview } from "@/components/agent/workspace/FleetOverview";
import { homeView } from "@/lib/homeView";
import { AgentChatTab } from "@/pages/tabs/AgentChatTab";

/**
 * The home, which is two views of the same question (FEAT-104).
 *
 * It used to be two *pages*: a chat and a card grid of the fleet. The grid went
 * because its only unique job was showing which agents are running, and a line
 * at the top of the rail does that without a second page — an agent's
 * strategies, its brain and its routines all live on `/agents/:slug`, and its
 * background tasks in the chat's own dock.
 *
 * What comes back at `?view=fleet` is not that grid. The bar it has to clear is
 * the rail's live line, so every row carries what the line cannot: the money
 * its fleet actually made, what it last decided, and when it ticks next. If it
 * ever stops carrying those, the grid's argument applies again and this should
 * go the same way.
 *
 * `?view=chat` — and, for now, a bare `/` — is the conversation, unchanged,
 * with `?agent=` and `?ask=` (FEAT-092) working beneath it. Which of the two a
 * bare `/` means is one constant in `lib/homeView.ts`, and moving it is the
 * habit change this feature is really about.
 *
 * Both views own the full viewport and scroll their own bodies, so the shell
 * drops `main`'s padding for this route (see `AppShell`).
 */
export function Agents() {
  const [searchParams] = useSearchParams();

  if (homeView(searchParams) === "fleet") return <FleetOverview />;

  return (
    <div className="h-full min-h-0">
      <AgentChatTab />
    </div>
  );
}
