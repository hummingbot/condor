import { Archive, Bot, FlaskConical, Loader2, TerminalSquare } from "lucide-react";
import { lazy, Suspense, useRef } from "react";
import { useSearchParams } from "react-router-dom";

const ActiveBotsTab = lazy(() =>
  import("@/pages/tabs/ActiveBotsTab").then((m) => ({ default: m.ActiveBotsTab })),
);
const ArchivedBotsTab = lazy(() =>
  import("@/pages/tabs/ArchivedBotsTab").then((m) => ({ default: m.ArchivedBotsTab })),
);
const BacktestingTab = lazy(() =>
  import("@/pages/tabs/BacktestingTab").then((m) => ({ default: m.BacktestingTab })),
);
const EditorTab = lazy(() =>
  import("@/pages/tabs/EditorTab").then((m) => ({ default: m.EditorTab })),
);

const TABS = [
  { key: "active", label: "Active", icon: Bot },
  { key: "archived", label: "Archived", icon: Archive },
  { key: "backtest", label: "Backtest", icon: FlaskConical },
  { key: "editor", label: "Editor", icon: TerminalSquare },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function FallbackSpinner() {
  return (
    <div className="flex items-center justify-center py-20">
      <Loader2 className="h-6 w-6 animate-spin text-[var(--color-text-muted)]" />
    </div>
  );
}

export function Bots() {
  const [searchParams, setSearchParams] = useSearchParams();
  const currentTab = (searchParams.get("tab") as TabKey) || "active";
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
        {visitedRef.current.has("archived") && (
          <div style={{ display: currentTab === "archived" ? undefined : "none" }}>
            <ArchivedBotsTab />
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
