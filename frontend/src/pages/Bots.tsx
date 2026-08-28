import { Bot, FlaskConical, History, TerminalSquare } from "lucide-react";
import { lazy, Suspense, useRef } from "react";
import { useSearchParams } from "react-router-dom";

import { FallbackSpinner } from "@/components/ui/FallbackSpinner";

const ActiveBotsTab = lazy(() =>
  import("@/pages/tabs/ActiveBotsTab").then((m) => ({ default: m.ActiveBotsTab })),
);
const BotRunsTab = lazy(() =>
  import("@/pages/tabs/BotRunsTab").then((m) => ({ default: m.BotRunsTab })),
);
const BacktestingTab = lazy(() =>
  import("@/pages/tabs/BacktestingTab").then((m) => ({ default: m.BacktestingTab })),
);
const EditorTab = lazy(() =>
  import("@/pages/tabs/EditorTab").then((m) => ({ default: m.EditorTab })),
);

const TABS = [
  { key: "active", label: "Active", icon: Bot },
  { key: "runs", label: "Runs", icon: History },
  { key: "editor", label: "Editor", icon: TerminalSquare },
  { key: "backtest", label: "Backtest", icon: FlaskConical },
] as const;

type TabKey = (typeof TABS)[number]["key"];

/**
 * Runs absorbed the old Archived tab: both listed the same stopped bots, one
 * off `bot_runs` in Postgres and one off a per-database walk of every archived
 * sqlite. A run that left a database behind now carries `archive_db_path` and
 * drills into that history from its row. Old `?tab=archived` links land on Runs.
 */
const RETIRED_TABS: Record<string, TabKey> = { archived: "runs" };

export function Bots() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab") ?? "";
  const currentTab =
    RETIRED_TABS[requestedTab] ?? ((requestedTab as TabKey) || "active");
  const visitedRef = useRef<Set<TabKey>>(new Set([currentTab]));
  visitedRef.current.add(currentTab);

  const setTab = (tab: TabKey) => {
    if (tab === "active") {
      setSearchParams({}, { replace: true });
    } else {
      setSearchParams({ tab }, { replace: true });
    }
  };

  return (
    <div className="space-y-6">
      {/* Tab bar */}
      <div className="flex items-center gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1 w-fit">
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

      {/* Tab content — keep visited tabs mounted but hidden */}
      <Suspense fallback={<FallbackSpinner />}>
        {visitedRef.current.has("active") && (
          <div style={{ display: currentTab === "active" ? undefined : "none" }}>
            <ActiveBotsTab />
          </div>
        )}
        {visitedRef.current.has("runs") && (
          <div style={{ display: currentTab === "runs" ? undefined : "none" }}>
            <BotRunsTab />
          </div>
        )}
        {visitedRef.current.has("backtest") && (
          <div style={{ display: currentTab === "backtest" ? undefined : "none" }}>
            <BacktestingTab />
          </div>
        )}
        {visitedRef.current.has("editor") && (
          <div style={{ display: currentTab === "editor" ? undefined : "none" }}>
            <EditorTab />
          </div>
        )}
      </Suspense>
    </div>
  );
}
