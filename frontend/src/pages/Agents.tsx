import { LayoutGrid, MessageSquare } from "lucide-react";
import { lazy, Suspense, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import { FallbackSpinner } from "@/components/ui/FallbackSpinner";
import { AgentChatTab } from "@/pages/tabs/AgentChatTab";

// The fleet report is one click away and rarely the first thing wanted, so it
// loads on demand. The chat is what the nav item promises — it ships eagerly,
// because a spinner between the click and the composer is the whole complaint.
const AgentFleetTab = lazy(() =>
  import("@/pages/tabs/AgentFleetTab").then((m) => ({ default: m.AgentFleetTab })),
);

const TABS = [
  { key: "chat", label: "Chat", icon: MessageSquare },
  { key: "fleet", label: "Fleet", icon: LayoutGrid },
] as const;

type TabKey = (typeof TABS)[number]["key"];

/**
 * Agents is a place to talk to them, with the fleet report one click away.
 *
 * The chat tab owns the full viewport and scrolls its own transcript, so the
 * shell drops `main`'s padding for it (see `AppShell`); the fleet tab is the
 * ordinary padded page it has always been.
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
    <div className={isChat ? "flex h-full min-h-0 flex-col" : "space-y-6"}>
      {/* Tab bar */}
      <div
        className={`flex w-fit shrink-0 items-center gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1 ${
          isChat ? "mx-4 mt-3 mb-2" : ""
        }`}
      >
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              currentTab === key
                ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content — keep visited tabs mounted but hidden, so switching to
          Fleet and back does not re-mount the thread. */}
      <div
        className={isChat ? "min-h-0 flex-1" : ""}
        style={{ display: isChat ? undefined : "none" }}
      >
        <AgentChatTab />
      </div>
      {visitedRef.current.has("fleet") && (
        <div style={{ display: currentTab === "fleet" ? undefined : "none" }}>
          <Suspense fallback={<FallbackSpinner />}>
            <AgentFleetTab />
          </Suspense>
        </div>
      )}
    </div>
  );
}
