import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Camera,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
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
import { type AgentSummary, type SessionInfo, api } from "@/lib/api";
import { type ParsedJournal, parseJournal } from "@/lib/parse-agent";

const SUB_TABS = [
  { id: "overview", label: "Overview", icon: LayoutList },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "snapshots", label: "Snapshots", icon: Camera },
] as const;
type SubTabId = (typeof SUB_TABS)[number]["id"];

interface SessionReviewerProps {
  slug: string;
  agentName: string;
  sessions: SessionInfo[];
  initialSessionNum: number;
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
  initialSessionNum,
  serverName,
  controllerIds,
  allAgents,
  onClose,
  onSwitchAgent,
}: SessionReviewerProps) {
  const [selectedSessionNum, setSelectedSessionNum] = useState(initialSessionNum);
  const [activeSubTab, setActiveSubTab] = useState<SubTabId>("overview");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Journal data
  const { data: journalData } = useQuery({
    queryKey: ["agent", slug, "session", selectedSessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, selectedSessionNum),
    enabled: selectedSessionNum > 0,
  });

  const parsedJournal = useMemo<ParsedJournal | null>(() => {
    if (!journalData?.content) return null;
    return parseJournal(journalData.content);
  }, [journalData?.content]);

  // Session performance data
  const { data: sessionPerfData } = useQuery({
    queryKey: ["agent-session-executors", slug, selectedSessionNum],
    queryFn: () => api.getAgentSessionExecutors(slug, selectedSessionNum),
    enabled: selectedSessionNum > 0,
    refetchInterval: 10000,
  });
  const sessionPerf = sessionPerfData?.performance ?? null;

  // Sorted sessions (newest first)
  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.number - a.number),
    [sessions],
  );

  const currentIdx = sortedSessions.findIndex((s) => s.number === selectedSessionNum);
  const currentSession = sortedSessions[currentIdx];

  // Navigation helpers
  const goPrev = useCallback(() => {
    if (currentIdx < sortedSessions.length - 1) {
      setSelectedSessionNum(sortedSessions[currentIdx + 1].number);
      setActiveSubTab("overview");
    }
  }, [currentIdx, sortedSessions]);

  const goNext = useCallback(() => {
    if (currentIdx > 0) {
      setSelectedSessionNum(sortedSessions[currentIdx - 1].number);
      setActiveSubTab("overview");
    }
  }, [currentIdx, sortedSessions]);

  // Agent switching
  const agentIdx = allAgents?.findIndex((a) => a.slug === slug) ?? -1;

  const goAgentUp = useCallback(() => {
    if (!allAgents || agentIdx <= 0) return;
    const prev = allAgents[agentIdx - 1];
    onSwitchAgent?.(prev.slug);
  }, [allAgents, agentIdx, onSwitchAgent]);

  const goAgentDown = useCallback(() => {
    if (!allAgents || agentIdx >= allAgents.length - 1) return;
    const next = allAgents[agentIdx + 1];
    onSwitchAgent?.(next.slug);
  }, [allAgents, agentIdx, onSwitchAgent]);

  // Keyboard navigation
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "ArrowLeft") { goPrev(); e.preventDefault(); }
      else if (e.key === "ArrowRight") { goNext(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { goAgentUp(); e.preventDefault(); }
      else if (e.key === "ArrowDown") { goAgentDown(); e.preventDefault(); }
      else if (e.key === "Escape") { onClose(); e.preventDefault(); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [goPrev, goNext, goAgentUp, goAgentDown, onClose]);

  // PnL for current session
  const pnl = sessionPerf?.total_pnl ?? 0;
  const pnlColor = pnl >= 0 ? "text-emerald-400" : "text-red-400";

  return (
    <div className="fixed inset-0 z-50 flex bg-[var(--color-bg)]">
      {/* Left sidebar: session list */}
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

        {/* Session list */}
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {sortedSessions.map((s) => {
            const isActive = s.number === selectedSessionNum;
            if (sidebarCollapsed) {
              return (
                <button
                  key={s.number}
                  onClick={() => { setSelectedSessionNum(s.number); setActiveSubTab("overview"); }}
                  className={`flex w-full items-center justify-center py-3 transition-colors ${
                    isActive
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title={`Session ${s.number}`}
                >
                  <span className="text-xs font-bold">{s.number}</span>
                </button>
              );
            }

            return (
              <button
                key={s.number}
                onClick={() => { setSelectedSessionNum(s.number); setActiveSubTab("overview"); }}
                className={`w-full px-3 py-2.5 text-left transition-all ${
                  isActive
                    ? "bg-[var(--color-primary)]/5 border-l-2 border-l-[var(--color-primary)]"
                    : "border-l-2 border-l-transparent hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className={`text-xs font-medium ${isActive ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}>
                    Session {s.number}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-muted)]">
                    {s.snapshot_count} tick{s.snapshot_count !== 1 ? "s" : ""}
                  </span>
                </div>
                <div className="mt-0.5 text-[10px] text-[var(--color-text-muted)]/60">
                  {s.created_at}
                </div>
              </button>
            );
          })}
        </div>

        {/* Navigation hint */}
        {!sidebarCollapsed && (
          <div className="border-t border-[var(--color-border)] px-3 py-2 text-[9px] text-[var(--color-text-muted)]/40">
            <span className="flex items-center gap-1">
              <ChevronLeft className="h-2.5 w-2.5" />
              <ChevronRight className="h-2.5 w-2.5" />
              sessions
              <span className="mx-1">|</span>
              <ChevronUp className="h-2.5 w-2.5" />
              <ChevronDown className="h-2.5 w-2.5" />
              agents
              <span className="mx-1">|</span>
              esc close
            </span>
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="text-sm font-semibold text-[var(--color-text)]">
              Session {selectedSessionNum}
            </h2>
            {currentSession && (
              <span className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface-hover)] px-2 py-0.5 text-[10px] font-bold uppercase text-[var(--color-text-muted)]">
                completed
              </span>
            )}
            <span className={`text-sm font-mono font-semibold ${pnlColor}`}>
              ${pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
            </span>
          </div>

          <div className="flex items-center gap-1">
            {/* Session navigation */}
            <button
              onClick={goPrev}
              disabled={currentIdx >= sortedSessions.length - 1}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
              title="Previous session"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {sessions.length - currentIdx} of {sessions.length}
            </span>
            <button
              onClick={goNext}
              disabled={currentIdx <= 0}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] disabled:opacity-30"
              title="Next session"
            >
              <ChevronRight className="h-4 w-4" />
            </button>

            {/* Agent name */}
            <span className="ml-3 text-xs text-[var(--color-text-muted)]">{agentName}</span>

            {/* Close */}
            <button
              onClick={onClose}
              className="ml-2 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Sub-tab bar */}
        <div className="flex items-center gap-1 border-b border-[var(--color-border)]/50 px-4 py-1.5">
          {SUB_TABS.map(({ id, label, icon: Icon }) => (
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
          {!parsedJournal ? (
            <div className="flex h-32 items-center justify-center">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
            </div>
          ) : (
            <>
              {activeSubTab === "overview" && (
                <div className="space-y-4">
                  <SessionExecutors
                    slug={slug}
                    sessionNum={selectedSessionNum}
                    serverName={serverName}
                    controllerIds={controllerIds}
                  />
                  <SessionOverview journal={parsedJournal} perf={sessionPerf} />
                </div>
              )}
              {activeSubTab === "activity" && <SessionActivity journal={parsedJournal} />}
              {activeSubTab === "snapshots" && (
                <SessionSnapshots slug={slug} sessionNum={selectedSessionNum} />
              )}
            </>
          )}
        </div>

        {/* Bottom timeline strip */}
        {sortedSessions.length > 1 && (
          <div className="flex items-center gap-1 border-t border-[var(--color-border)] px-4 py-1.5 overflow-x-auto scrollbar-thin">
            {sortedSessions.map((s) => {
              const isActive = s.number === selectedSessionNum;
              return (
                <button
                  key={s.number}
                  onClick={() => { setSelectedSessionNum(s.number); setActiveSubTab("overview"); }}
                  className={`shrink-0 rounded px-2.5 py-1 text-[10px] transition-all ${
                    isActive
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)] font-medium"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  #{s.number}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
