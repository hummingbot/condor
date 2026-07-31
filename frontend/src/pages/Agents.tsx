import { lazy, Suspense, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import type { AgentsTab } from "@/components/chat/AgentsTabSwitch";
import { FallbackSpinner } from "@/components/ui/FallbackSpinner";
import { AgentChatTab } from "@/pages/tabs/AgentChatTab";

// The fleet report is one click away and rarely the first thing wanted, so it
// loads on demand. The chat is what the nav item promises — it ships eagerly,
// because a spinner between the click and the composer is the whole complaint.
const AgentFleetTab = lazy(() =>
  import("@/pages/tabs/AgentFleetTab").then((m) => ({ default: m.AgentFleetTab })),
);

type TabKey = AgentsTab;

/**
 * Agents is a place to talk to them, with the fleet report one click away.
 *
 * The chat tab owns the full viewport and scrolls its own transcript, so the
 * shell drops `main`'s padding for it (see `AppShell`); the fleet tab is the
 * ordinary padded page it has always been.
 *
 * The host owns `?tab=` and nothing else: the control that flips it rides in
 * each tab's own chrome (`AgentsTabSwitch`), so no row is spent on it here.
 */
export function Agents() {
  const [searchParams, setSearchParams] = useSearchParams();
  const currentTab = (searchParams.get("tab") as TabKey) || "chat";
  const visitedRef = useRef<Set<TabKey>>(new Set([currentTab]));
  visitedRef.current.add(currentTab);

  const setTab = (tab: TabKey) => {
    if (tab === "chat") {
      setSearchParams({}, { replace: true });
    } else {
      setSearchParams({ tab }, { replace: true });
    }
  };

  const isChat = currentTab === "chat";

  return (
    <div className={isChat ? "h-full min-h-0" : "space-y-6"}>
      {/* Tab content — keep visited tabs mounted but hidden, so switching to
          Fleet and back does not re-mount the thread. */}
      <div
        className={isChat ? "h-full min-h-0" : ""}
        style={{ display: isChat ? undefined : "none" }}
      >
        <AgentChatTab onTabChange={setTab} />
      </div>
      {visitedRef.current.has("fleet") && (
        <div style={{ display: currentTab === "fleet" ? undefined : "none" }}>
          <Suspense fallback={<FallbackSpinner />}>
            <AgentFleetTab onTabChange={setTab} />
          </Suspense>
        </div>
      )}
    </div>
  );
}
