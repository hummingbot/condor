import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Camera,
  ChevronDown,
  Clock,
  LayoutList,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  SessionActivity,
  SessionExecutors,
  SessionOverview,
  SessionSnapshots,
} from "@/components/agent/AgentSessionContent";
import { type AgentPerformance, type SessionInfo, api } from "@/lib/api";
import { type ParsedJournal, parseJournal } from "@/lib/parse-agent";

// ── Session Selector ──

export function SessionSelector({
  sessions,
  selectedSessionNum,
  onSelect,
}: {
  sessions: SessionInfo[];
  selectedSessionNum: number;
  onSelect: (num: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const selected = sessions.find((s) => s.number === selectedSessionNum);
  const sortedSessions = sessions;

  return (
    <div ref={ref} className="relative sm:w-72">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5 text-left text-sm transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
      >
        <div className="flex items-center gap-2.5 overflow-hidden">
          <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md bg-[var(--color-primary)]/10 text-xs font-bold text-[var(--color-primary)]">
            {selected?.number ?? "–"}
          </div>
          <div className="min-w-0">
            <div className="truncate font-medium text-[var(--color-text)]">
              Session {selected?.number}
            </div>
            <div className="truncate text-xs text-[var(--color-text-muted)]">
              {selected?.created_at ?? "Unknown"} · {selected?.snapshot_count ?? 0} tick{selected?.snapshot_count !== 1 ? "s" : ""}
            </div>
          </div>
        </div>
        <ChevronDown className={`h-4 w-4 flex-shrink-0 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute left-0 z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] shadow-lg">
          {sortedSessions.map((s) => {
            const isActive = s.number === selectedSessionNum;
            return (
              <button
                key={s.number}
                type="button"
                onClick={() => { onSelect(s.number); setOpen(false); }}
                className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-xs font-bold ${
                  isActive ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)]" : "bg-[var(--color-border)]/50 text-[var(--color-text-muted)]"
                }`}>
                  {s.number}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">Session {s.number}</div>
                  <div className="truncate text-xs text-[var(--color-text-muted)]">
                    {s.created_at ?? "Unknown"} · {s.snapshot_count} tick{s.snapshot_count !== 1 ? "s" : ""}
                  </div>
                </div>
                {isActive && <div className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[var(--color-primary)]" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Session Metrics Bar ──

const SESSION_SUB_TABS = [
  { id: "overview", label: "Overview", icon: LayoutList },
  { id: "activity", label: "Activity", icon: Activity },
  { id: "snapshots", label: "Snapshots", icon: Camera },
] as const;

type SessionSubTabId = (typeof SESSION_SUB_TABS)[number]["id"];

export function SessionMetricsBar({
  journal,
  perf,
}: {
  journal: ParsedJournal;
  perf?: AgentPerformance | null;
}) {
  const { summary, metrics, ticks } = journal;
  const lastMetric = metrics.length > 0 ? metrics[metrics.length - 1] : null;
  const pnl = perf ? perf.total_pnl : summary.pnl;
  const openCount = perf ? perf.open_count : summary.openExecutors;
  const volume = perf?.volume;
  const stats = [
    { label: "Ticks", value: String(summary.lastTick || ticks.length), color: "text-[var(--color-text)]" },
    { label: "PnL", value: `$${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}`, color: pnl >= 0 ? "text-emerald-400" : "text-red-400" },
    { label: "Open", value: String(openCount), color: "text-[var(--color-text)]" },
    volume !== undefined
      ? { label: "Volume", value: `$${volume.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: "text-[var(--color-text)]" }
      : { label: "Exposure", value: lastMetric ? `$${lastMetric.exposure.toFixed(2)}` : "$0.00", color: "text-[var(--color-text)]" },
  ];

  return (
    <>
      {stats.map((s, i) => (
        <div key={s.label} className="flex items-center gap-2">
          {i > 0 && <span className="text-[var(--color-border)]">|</span>}
          <span className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">{s.label}:</span>
          <span className={`text-sm font-semibold font-mono ${s.color}`}>{s.value}</span>
        </div>
      ))}
    </>
  );
}

// ── Sessions Tab ──

export function SessionsTab({
  slug,
  sessions,
  serverName,
  controllerIds,
}: {
  slug: string;
  sessions: SessionInfo[];
  serverName: string;
  controllerIds?: string[];
}) {
  const [selectedSessionNum, setSelectedSessionNum] = useState<number>(
    sessions.length > 0 ? sessions[0].number : 0
  );
  const [activeSubTab, setActiveSubTab] = useState<SessionSubTabId>("overview");

  const selectedSession = sessions.find((s) => s.number === selectedSessionNum);

  const { data: journalData } = useQuery({
    queryKey: ["agent", slug, "session", selectedSessionNum, "journal"],
    queryFn: () => api.getSessionJournal(slug, selectedSessionNum),
    enabled: selectedSessionNum > 0,
  });

  const parsedJournal = useMemo<ParsedJournal | null>(() => {
    if (!journalData?.content) return null;
    return parseJournal(journalData.content);
  }, [journalData?.content]);

  const { data: sessionPerfData } = useQuery({
    queryKey: ["agent-session-executors", slug, selectedSessionNum],
    queryFn: () => api.getAgentSessionExecutors(slug, selectedSessionNum),
    enabled: selectedSessionNum > 0,
    refetchInterval: 10000,
  });
  const sessionPerf = sessionPerfData?.performance ?? null;

  if (sessions.length === 0) {
    return (
      <div className="flex h-48 flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] text-[var(--color-text-muted)]">
        <Clock className="mb-2 h-8 w-8 opacity-30" />
        <p className="text-sm">No sessions yet. Start the agent to create one.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Session selector + inline metrics */}
      <div className="flex flex-wrap items-center gap-4">
        <SessionSelector
          sessions={sessions}
          selectedSessionNum={selectedSessionNum}
          onSelect={(num) => { setSelectedSessionNum(num); setActiveSubTab("overview"); }}
        />
        {parsedJournal && <SessionMetricsBar journal={parsedJournal} perf={sessionPerf} />}
      </div>

      {/* Sub-tab bar */}
      <div className="flex gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
        {SESSION_SUB_TABS.map(({ id, label, icon: Icon }) => (
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

      {/* Sub-tab content */}
      {!parsedJournal ? (
        <div className="flex h-32 items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
        </div>
      ) : (
        <>
          {activeSubTab === "overview" && selectedSession && (
            <div className="space-y-4">
              <SessionExecutors
                slug={slug}
                sessionNum={selectedSession.number}
                serverName={serverName}
                controllerIds={controllerIds}
              />
              <SessionOverview journal={parsedJournal} perf={sessionPerf} />
            </div>
          )}
          {activeSubTab === "overview" && !selectedSession && (
            <SessionOverview journal={parsedJournal} perf={sessionPerf} />
          )}
          {activeSubTab === "activity" && <SessionActivity journal={parsedJournal} />}
          {activeSubTab === "snapshots" && selectedSession && (
            <SessionSnapshots slug={slug} sessionNum={selectedSession.number} />
          )}
        </>
      )}
    </div>
  );
}
