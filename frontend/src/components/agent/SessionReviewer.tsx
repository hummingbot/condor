import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Camera,
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  LayoutList,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  SessionActivity,
  SessionExecutors,
  SessionOverview,
  SessionSnapshots,
} from "@/components/agent/AgentSessionContent";
import { type AgentSummary, type ExperimentInfo, type SessionInfo, api } from "@/lib/api";
import { type ParsedJournal, type ParsedSnapshot, parseJournal, parseSnapshot } from "@/lib/parse-agent";

const SUB_TABS = [
  { id: "overview", label: "Overview", icon: LayoutList },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "snapshots", label: "Snapshots", icon: Camera },
] as const;
type SubTabId = (typeof SUB_TABS)[number]["id"];

// Unified sidebar item
interface SidebarItem {
  number: number;
  kind: "session" | "experiment";
  snapshot_count: number;
  created_at: string;
  execution_mode?: string;
  agent_key?: string;
}

interface SessionReviewerProps {
  slug: string;
  agentName: string;
  sessions: SessionInfo[];
  experiments?: ExperimentInfo[];
  initialSessionNum: number;
  initialKind?: "session" | "experiment";
  serverName: string;
  controllerIds?: string[];
  allAgents?: AgentSummary[];
  onClose: () => void;
  onSwitchAgent?: (slug: string, sessionNum?: number) => void;
}

export function SessionReviewer({
  slug,
  agentName,
  sessions,
  experiments = [],
  initialSessionNum,
  initialKind = "session",
  serverName,
  controllerIds,
  allAgents: _allAgents,
  onClose,
  onSwitchAgent: _onSwitchAgent,
}: SessionReviewerProps) {
  const [selectedNum, setSelectedNum] = useState(initialSessionNum);
  const [selectedKind, setSelectedKind] = useState<"session" | "experiment">(initialKind);
  const [activeSubTab, setActiveSubTab] = useState<SubTabId>("overview");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Build unified sidebar items
  const sidebarItems = useMemo<SidebarItem[]>(() => {
    const items: SidebarItem[] = [
      ...sessions.map((s) => ({
        number: s.number,
        kind: "session" as const,
        snapshot_count: s.snapshot_count,
        created_at: s.created_at,
      })),
      ...experiments.map((e) => ({
        number: e.number,
        kind: "experiment" as const,
        snapshot_count: e.snapshot_count,
        created_at: e.created_at,
        execution_mode: e.execution_mode,
        agent_key: e.agent_key,
      })),
    ];
    // Sort: sessions first (newest first), then experiments (newest first)
    items.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "session" ? -1 : 1;
      return b.number - a.number;
    });
    return items;
  }, [sessions, experiments]);

  const isExperiment = selectedKind === "experiment";

  // Journal data (for sessions)
  const { data: journalData } = useQuery({
    queryKey: ["agent", slug, "session", selectedNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, selectedNum),
    enabled: !isExperiment && selectedNum > 0,
  });

  const parsedJournal = useMemo<ParsedJournal | null>(() => {
    if (!journalData?.content) return null;
    return parseJournal(journalData.content);
  }, [journalData?.content]);

  // Experiment snapshot data
  const { data: experimentData } = useQuery({
    queryKey: ["agent", slug, "experiment", selectedNum],
    queryFn: () => api.getExperiment(slug, selectedNum),
    enabled: isExperiment && selectedNum > 0,
  });

  const parsedSnapshot = useMemo<ParsedSnapshot | null>(() => {
    if (!experimentData?.content) return null;
    return parseSnapshot(experimentData.content);
  }, [experimentData?.content]);

  // Session performance data
  const { data: sessionPerfData } = useQuery({
    queryKey: ["agent-session-executors", slug, selectedNum],
    queryFn: () => api.getAgentSessionExecutors(slug, selectedNum),
    enabled: !isExperiment && selectedNum > 0,
    refetchInterval: 10000,
  });
  const sessionPerf = sessionPerfData?.performance ?? null;

  const currentIdx = sidebarItems.findIndex(
    (s) => s.number === selectedNum && s.kind === selectedKind,
  );

  // Navigation helpers
  const selectItem = useCallback((item: SidebarItem) => {
    setSelectedNum(item.number);
    setSelectedKind(item.kind);
    setActiveSubTab("overview");
  }, []);

  // Snapshot click from chart → navigate to snapshots tab with that tick
  const [pendingSnapshotTick, setPendingSnapshotTick] = useState<number | null>(null);

  const handleSnapshotClick = useCallback((tick: number) => {
    setPendingSnapshotTick(tick);
    setActiveSubTab("snapshots");
  }, []);

  // Keyboard: Escape only
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "Escape") { onClose(); e.preventDefault(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // PnL for current session
  const pnl = sessionPerf?.total_pnl ?? 0;
  const pnlColor = pnl >= 0 ? "text-emerald-400" : "text-red-400";

  // Visible sub-tabs depend on kind
  const visibleSubTabs = isExperiment
    ? SUB_TABS.filter((t) => t.id === "overview")
    : SUB_TABS;

  return (
    <div className="fixed inset-0 z-50 flex bg-[var(--color-bg)]">
      {/* Left sidebar */}
      <div
        className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
          sidebarCollapsed ? "w-12" : "w-64"
        }`}
      >
        {/* Sidebar header */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2.5">
          {!sidebarCollapsed && (
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
              Sessions
            </span>
          )}
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            {sidebarCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
          </button>
        </div>

        {/* Sidebar items */}
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {sidebarItems.map((item) => {
            const isActive = item.number === selectedNum && item.kind === selectedKind;
            const isExp = item.kind === "experiment";

            if (sidebarCollapsed) {
              return (
                <button
                  key={`${item.kind}-${item.number}`}
                  onClick={() => selectItem(item)}
                  className={`flex w-full items-center justify-center py-3 transition-colors ${
                    isActive
                      ? isExp
                        ? "bg-amber-500/10 text-amber-400"
                        : "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title={`${isExp ? "Experiment" : "Session"} ${item.number}`}
                >
                  {isExp ? (
                    <FlaskConical className="h-3.5 w-3.5 text-amber-400" />
                  ) : (
                    <span className="text-xs font-bold">{item.number}</span>
                  )}
                </button>
              );
            }

            return (
              <button
                key={`${item.kind}-${item.number}`}
                onClick={() => selectItem(item)}
                className={`w-full px-3 py-2.5 text-left transition-all ${
                  isActive
                    ? isExp
                      ? "bg-amber-500/5 border-l-2 border-l-amber-400"
                      : "bg-[var(--color-primary)]/5 border-l-2 border-l-[var(--color-primary)]"
                    : "border-l-2 border-l-transparent hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className={`text-xs font-medium ${isActive ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}>
                    {isExp ? (
                      <span className="flex items-center gap-1">
                        <FlaskConical className="h-3 w-3 text-amber-400" />
                        Exp {item.number}
                      </span>
                    ) : (
                      `Session ${item.number}`
                    )}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-muted)]">
                    {item.snapshot_count} tick{item.snapshot_count !== 1 ? "s" : ""}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  {isExp && item.execution_mode && (
                    <span className="rounded bg-amber-500/10 px-1 py-0.5 text-[8px] font-bold uppercase text-amber-400">
                      {item.execution_mode === "dry_run" ? "dry" : item.execution_mode === "run_once" ? "once" : item.execution_mode}
                    </span>
                  )}
                  <span className="text-[10px] text-[var(--color-text-muted)]/60">
                    {item.created_at}
                  </span>
                </div>
              </button>
            );
          })}
        </div>

        {/* Navigation hint */}
        {!sidebarCollapsed && (
          <div className="border-t border-[var(--color-border)] px-3 py-2 text-[9px] text-[var(--color-text-muted)]/40">
            esc close
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="text-sm font-semibold text-[var(--color-text)]">
              {isExperiment ? (
                <span className="flex items-center gap-1.5">
                  <FlaskConical className="h-4 w-4 text-amber-400" />
                  Experiment {selectedNum}
                </span>
              ) : (
                `Session ${selectedNum}`
              )}
            </h2>
            {isExperiment && (
              <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-400">
                experiment
              </span>
            )}
            {!isExperiment && (
              <span className={`text-sm font-mono font-semibold ${pnlColor}`}>
                ${pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs text-[var(--color-text-muted)]">{agentName}</span>
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {sidebarItems.length - currentIdx} / {sidebarItems.length}
            </span>

            {/* Close */}
            <button
              onClick={onClose}
              className="ml-1 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Sub-tab bar */}
        <div className="flex items-center gap-1 border-b border-[var(--color-border)]/50 px-4 py-1.5">
          {visibleSubTabs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveSubTab(id)}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
                activeSubTab === id
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {isExperiment ? (
            // Experiment snapshot view
            !parsedSnapshot ? (
              <div className="flex h-32 items-center justify-center">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
              </div>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-mono text-lg font-bold text-[var(--color-text)]">
                    Experiment #{selectedNum}
                  </span>
                  <span className="text-sm text-[var(--color-text-muted)]">{parsedSnapshot.timestamp}</span>
                </div>
                {parsedSnapshot.agentResponse && (
                  <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Agent Response</h3>
                    <div className="whitespace-pre-wrap text-sm text-[var(--color-text)]">{parsedSnapshot.agentResponse}</div>
                  </div>
                )}
                {parsedSnapshot.toolCalls.length > 0 && (
                  <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                      Tool Calls ({parsedSnapshot.toolCalls.length})
                    </h3>
                    <div className="space-y-2">
                      {parsedSnapshot.toolCalls.map((tc, i) => (
                        <div key={i} className="rounded-md bg-[var(--color-bg)]/50 px-3 py-2 text-xs">
                          <span className="font-medium text-[var(--color-primary)]">{tc.name}</span>
                          <span className="ml-2 text-[var(--color-text-muted)]">({tc.status})</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {parsedSnapshot.riskState && (
                  <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">Risk State</h3>
                    <div className="whitespace-pre-wrap text-xs text-[var(--color-text-muted)]">{parsedSnapshot.riskState}</div>
                  </div>
                )}
              </div>
            )
          ) : (
            // Session view
            !parsedJournal ? (
              <div className="flex h-32 items-center justify-center">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
              </div>
            ) : (
              <>
                {activeSubTab === "overview" && (
                  <div className="space-y-4">
                    <SessionExecutors
                      slug={slug}
                      sessionNum={selectedNum}
                      serverName={serverName}
                      controllerIds={controllerIds}
                      onSnapshotClick={handleSnapshotClick}
                    />
                    <SessionOverview journal={parsedJournal} perf={sessionPerf} />
                  </div>
                )}
                {activeSubTab === "activity" && <SessionActivity journal={parsedJournal} />}
                {activeSubTab === "snapshots" && (
                  <SessionSnapshots slug={slug} sessionNum={selectedNum} initialTick={pendingSnapshotTick} />
                )}
              </>
            )
          )}
        </div>

      </div>
    </div>
  );
}
