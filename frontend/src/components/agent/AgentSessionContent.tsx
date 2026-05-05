import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Wrench,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { AgentPnlChart, metricsToDataPoints } from "@/components/agent/AgentPnlChart";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { type AgentExecutorRow, type AgentPerformance, type ExecutorInfo, api } from "@/lib/api";
import { type ParsedJournal, type ParsedSnapshot, parseSnapshot } from "@/lib/parse-agent";
import { DetailPanel, ExecutorTable, type SortDir, type SortKey } from "@/pages/Executors";

// ── Helper ──

function agentRowToExecutorInfo(row: AgentExecutorRow): ExecutorInfo {
  return {
    id: row.id,
    type: row.type,
    connector: row.connector || "unknown",
    trading_pair: row.pair,
    side: row.side,
    status: row.status,
    close_type: row.close_type,
    pnl: row.pnl,
    volume: row.volume,
    timestamp: row.timestamp,
    controller_id: row.controller_id,
    cum_fees_quote: row.fees,
    net_pnl_pct: 0,
    entry_price: row.entry_price,
    current_price: row.current_price,
    close_timestamp: row.close_timestamp,
    custom_info: row.custom_info ?? {},
    config: row.config ?? {},
  };
}

// ── Session Overview ──

export function SessionOverview({
  journal,
  perf,
}: {
  journal: ParsedJournal;
  perf?: AgentPerformance | null;
}) {
  const { summary, executors, metrics } = journal;
  const pnl = perf ? perf.total_pnl : summary.pnl;

  // PnL chart data from metrics timeline
  const pnlData = useMemo(() => metricsToDataPoints(metrics), [metrics]);

  return (
    <div className="space-y-4">
      {/* Summary Card */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Summary</h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Status</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${
              summary.status === "ACTIVE" || summary.status === "running"
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                : summary.status === "paused"
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-400"
                  : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
            }`}>
              {summary.status || "idle"}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Tick</span>
            <span className="font-mono text-sm text-[var(--color-text)]">#{summary.lastTick}</span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">PnL</span>
            <span className={`font-mono text-sm ${pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              ${pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
            </span>
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">Last Action</span>
            <span className="text-sm text-[var(--color-text)]">{summary.lastAction || "—"}</span>
          </div>
        </div>
      </div>

      {/* Executor Table */}
      {executors.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Executors</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                  <th className="pb-2 pr-3">ID</th>
                  <th className="pb-2 pr-3">Type</th>
                  <th className="pb-2 pr-3">Pair</th>
                  <th className="pb-2 pr-3">Side</th>
                  <th className="pb-2 pr-3 text-right">Amount</th>
                  <th className="pb-2 pr-3">Status</th>
                  <th className="pb-2 pr-3 text-right">PnL</th>
                  <th className="pb-2 text-right">Volume</th>
                </tr>
              </thead>
              <tbody>
                {executors.map((ex, i) => (
                  <tr key={`${ex.id}-${i}`} className="border-b border-[var(--color-border)]/30">
                    <td className="py-2 pr-3 font-mono text-[var(--color-text)]">{ex.id.slice(0, 8)}</td>
                    <td className="py-2 pr-3 text-[var(--color-text-muted)]">{ex.type}</td>
                    <td className="py-2 pr-3 font-mono text-[var(--color-text)]">{ex.pair}</td>
                    <td className="py-2 pr-3">
                      <span className={ex.side.toLowerCase() === "buy" ? "text-emerald-400" : "text-red-400"}>
                        {ex.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2 pr-3 text-right font-mono text-[var(--color-text)]">${ex.amount.toFixed(2)}</td>
                    <td className="py-2 pr-3">
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${
                        ex.status === "open"
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                          : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                      }`}>
                        {ex.status}
                      </span>
                    </td>
                    <td className={`py-2 pr-3 text-right font-mono ${ex.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {ex.pnl >= 0 ? "+" : ""}{ex.pnl.toFixed(2)}
                    </td>
                    <td className="py-2 text-right font-mono text-[var(--color-text-muted)]">{ex.volume.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* PnL Chart (replaces metrics timeline text) */}
      {pnlData.length > 1 && (
        <AgentPnlChart data={pnlData} height={400} title="Metrics Timeline" />
      )}

      {/* Metrics table (compact, below chart) */}
      {metrics.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Metrics Detail</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                  <th className="px-2 py-1">Time</th>
                  <th className="px-2 py-1 text-right">PnL</th>
                  <th className="px-2 py-1 text-right">Volume</th>
                  <th className="px-2 py-1 text-right">Open</th>
                  <th className="px-2 py-1 text-right">Exposure</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((m, i) => (
                  <tr key={i} className="border-t border-[var(--color-border)]/30 font-mono">
                    <td className="px-2 py-1.5 text-[var(--color-text-muted)]">{m.timestamp}</td>
                    <td className={`px-2 py-1.5 text-right ${m.pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      ${m.pnl >= 0 ? "+" : ""}{m.pnl.toFixed(2)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">
                      ${m.volume.toLocaleString("en-US", { maximumFractionDigits: 0 })}
                    </td>
                    <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">{m.open}</td>
                    <td className="px-2 py-1.5 text-right text-[var(--color-text-muted)]">${m.exposure.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {executors.length === 0 && metrics.length === 0 && (
        <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No executors or metrics yet.</p>
      )}
    </div>
  );
}

// ── Session Activity ──

export function SessionActivity({ journal }: { journal: ParsedJournal }) {
  const { decisions } = journal;

  if (decisions.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No decisions yet.</p>;
  }

  return (
    <div className="space-y-2">
      {decisions.map((d, i) => (
        <div key={i} className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <div className="flex items-start gap-3">
            {d.tick > 0 ? (
              <span className="mt-0.5 shrink-0 rounded-md bg-[var(--color-surface-hover)] px-2 py-0.5 font-mono text-xs font-bold text-[var(--color-text-muted)]">
                #{d.tick}
              </span>
            ) : (
              <span className="mt-0.5 shrink-0 rounded-md bg-red-500/10 px-2 py-0.5 font-mono text-xs font-bold text-red-400">
                ERR
              </span>
            )}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-[var(--color-text-muted)]">{d.time}</span>
                <span className="text-sm font-medium text-[var(--color-text)]">{d.action}</span>
                {d.riskNote && (
                  <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-400">
                    {d.riskNote}
                  </span>
                )}
              </div>
              {d.reasoning && (
                <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-muted)]">{d.reasoning}</p>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Session Executors (with WS streaming) ──

export function SessionExecutors({
  slug,
  sessionNum,
  serverName,
  controllerIds,
}: {
  slug: string;
  sessionNum: number;
  serverName: string;
  controllerIds?: string[];
}) {
  // REST data (fallback + historical executors)
  const { data: sessionDetail } = useQuery({
    queryKey: ["agent-session-executors", slug, sessionNum],
    queryFn: () => api.getAgentSessionExecutors(slug, sessionNum),
    refetchInterval: 10000,
  });

  const restExecutors = sessionDetail?.executors ?? [];

  // WS-backed live executors (if controller IDs provided)
  const { executors: wsExecutors } = useAgentExecutors(
    controllerIds?.length ? serverName : null,
    controllerIds || [],
  );

  // Merge: prefer WS data for matching IDs, keep REST for historical
  const executorInfos = useMemo(() => {
    const restInfos = restExecutors.map(agentRowToExecutorInfo);
    if (wsExecutors.length === 0) return restInfos;

    // Build a map of WS executors by ID for fast lookup
    const wsMap = new Map(wsExecutors.map((ex) => [ex.id, ex]));
    // Update REST entries with live WS data where available
    const merged = restInfos.map((ex) => wsMap.get(ex.id) ?? ex);
    // Add any WS-only executors not in REST
    const restIds = new Set(restInfos.map((ex) => ex.id));
    for (const ex of wsExecutors) {
      if (!restIds.has(ex.id)) merged.push(ex);
    }
    return merged;
  }, [restExecutors, wsExecutors]);

  // Group executors by connector:pair for charts
  const chartGroups = useMemo(() => {
    if (!serverName || executorInfos.length === 0) return [];
    const groups = new Map<string, ExecutorInfo[]>();
    for (const ex of executorInfos) {
      if (!ex.trading_pair) continue;
      const key = `${ex.connector}:${ex.trading_pair}`;
      const arr = groups.get(key);
      if (arr) arr.push(ex);
      else groups.set(key, [ex]);
    }
    return Array.from(groups.entries());
  }, [executorInfos, serverName]);

  // Table state
  const [sortKey, setSortKey] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedExecutor, setSelectedExecutor] = useState<ExecutorInfo | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const stoppingIds = useMemo(() => new Set<string>(), []);

  const handleSort = useCallback((key: SortKey) => {
    setSortDir((prev) => (sortKey === key ? (prev === "asc" ? "desc" : "asc") : "desc"));
    setSortKey(key);
  }, [sortKey]);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    setSelectedIds((prev) =>
      prev.size === executorInfos.length ? new Set() : new Set(executorInfos.map((e) => e.id)),
    );
  }, [executorInfos]);

  const allSelected = selectedIds.size === executorInfos.length && executorInfos.length > 0;

  if (!sessionDetail) {
    return (
      <div className="flex h-32 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (executorInfos.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No executors for this session.</p>;
  }

  return (
    <div className="space-y-4">
      {/* Executor charts by trading pair */}
      {chartGroups.map(([key, group]) => (
        <ExecutorChart
          key={key}
          server={serverName}
          executors={group}
          connector={group[0].connector}
          tradingPair={group[0].trading_pair}
          height={400}
        />
      ))}

      {/* Executor table + detail panel */}
      <div className="flex">
        <div className={`min-w-0 ${selectedExecutor ? "flex-1" : "w-full"}`}>
          <ExecutorTable
            executors={executorInfos}
            sortKey={sortKey}
            sortDir={sortDir}
            onSort={handleSort}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelect}
            onSelectAll={selectAll}
            allSelected={allSelected}
            onRowClick={setSelectedExecutor}
            selectedExecutorId={selectedExecutor?.id ?? null}
            onStop={() => {}}
            stoppingIds={stoppingIds}
          />
        </div>
        {selectedExecutor && serverName && (
          <DetailPanel
            executor={selectedExecutor}
            server={serverName}
            onClose={() => setSelectedExecutor(null)}
            onStop={() => {}}
            stopping={false}
          />
        )}
      </div>
    </div>
  );
}

// ── Session Snapshots ──

export function SessionSnapshots({ slug, sessionNum }: { slug: string; sessionNum: number }) {
  const [selectedTick, setSelectedTick] = useState<number>(0);

  const { data: snapshotsData } = useQuery({
    queryKey: ["agent", slug, "session", sessionNum, "snapshots"],
    queryFn: () => api.getSessionSnapshots(slug, sessionNum),
  });

  const snapshots = snapshotsData?.snapshots || [];

  if (snapshots.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">No snapshots yet.</p>;
  }

  return (
    <div className="flex flex-col gap-4 lg:flex-row">
      {/* Snapshot list */}
      <div className="w-full shrink-0 lg:w-72">
        <div className="max-h-[600px] space-y-1 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2">
          {snapshots.map((snap) => (
            <button
              key={snap.tick}
              onClick={() => setSelectedTick(snap.tick)}
              className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-left transition-colors ${
                selectedTick === snap.tick
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs font-bold">#{snap.tick}</span>
                <span className="text-[10px]">{snap.timestamp}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Snapshot detail */}
      <div className="min-w-0 flex-1">
        {selectedTick > 0 ? (
          <SnapshotDetail slug={slug} sessionNum={sessionNum} tick={selectedTick} />
        ) : (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select a snapshot to view details.</p>
        )}
      </div>
    </div>
  );
}

// ── Snapshot Detail ──

function SnapshotDetail({ slug, sessionNum, tick }: { slug: string; sessionNum: number; tick: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["agent", slug, "session", sessionNum, "snapshot", tick],
    queryFn: () => api.getSnapshot(slug, sessionNum, tick),
    enabled: tick > 0,
  });

  const parsed = useMemo<ParsedSnapshot | null>(() => {
    if (!data?.content) return null;
    return parseSnapshot(data.content);
  }, [data?.content]);

  if (isLoading) {
    return (
      <div className="flex h-48 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (!parsed) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select a snapshot to view details.</p>;
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-lg font-bold text-[var(--color-text)]">#{parsed.tick}</span>
        <span className="text-sm text-[var(--color-text-muted)]">{parsed.timestamp}</span>
      </div>

      {/* System Prompt */}
      {parsed.systemPrompt && (
        <SystemPromptCard prompt={parsed.systemPrompt} charCount={parsed.systemPromptLength} />
      )}

      {/* Agent Response */}
      {parsed.agentResponse && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h4 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Agent Response</h4>
          <div className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--color-text)]">
            {parsed.agentResponse}
          </div>
        </div>
      )}

      {/* Tool Calls */}
      {parsed.toolCalls.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h4 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            <Wrench className="h-3 w-3" /> Tool Calls ({parsed.toolCalls.length})
          </h4>
          <div className="flex flex-wrap gap-2">
            {parsed.toolCalls.map((tc) => (
              <ToolCallChip key={tc.number} tc={tc} />
            ))}
          </div>
        </div>
      )}

      {/* Risk + Executor side by side */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {parsed.riskState && (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Risk State</h4>
            <div className="space-y-1 font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {parsed.riskState.split("\n").map((line, i) => {
                const isBlocked = line.includes("BLOCKED");
                const isActiveLine = line.includes("ACTIVE");
                return (
                  <div key={i} className={isBlocked ? "text-red-400" : isActiveLine ? "text-emerald-400" : ""}>
                    {line.replace(/^- /, "")}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {parsed.executorState && (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Executor State</h4>
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {parsed.executorState}
            </pre>
          </div>
        )}
      </div>

      {/* Stats Footer */}
      {parsed.stats.duration > 0 && (
        <div className="flex flex-wrap gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 font-mono text-xs text-[var(--color-text-muted)]">
          <span>Duration: <strong className="text-[var(--color-text)]">{parsed.stats.duration.toFixed(1)}s</strong></span>
        </div>
      )}
    </div>
  );
}

// ── Tool Call Chip ──

export function ToolCallChip({ tc }: { tc: import("@/lib/parse-agent").ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = tc.input || tc.output;
  const isOk = tc.status === "success" || tc.status === "completed";
  const isErr = tc.status === "error";
  const dotColor = isOk ? "bg-emerald-400" : isErr ? "bg-red-400" : "bg-[var(--color-text-muted)]";

  const shortName = tc.name.replace(/^mcp__\w+__/, "");

  if (!hasDetails) {
    return (
      <div className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)] px-2.5 py-1.5">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
        <span className="font-mono text-[11px] text-[var(--color-text)]">{shortName}</span>
      </div>
    );
  }

  return (
    <div className="w-full rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
          <span className="font-mono text-[11px] text-[var(--color-text)]">{shortName}</span>
        </div>
        {expanded ? <ChevronDown className="h-3 w-3 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3 w-3 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-[var(--color-border)]/30 p-3">
          {tc.input && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Input</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                {tc.input}
              </pre>
            </div>
          )}
          {tc.output && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Output</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                {tc.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── System Prompt Card ──

export function SystemPromptCard({ prompt, charCount }: { prompt: string; charCount: number }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between p-4 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">System Prompt</h4>
          <span className="text-[10px] text-[var(--color-text-muted)]">({charCount.toLocaleString()} chars)</span>
        </div>
        {expanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && (
        <pre className="max-h-96 overflow-auto border-t border-[var(--color-border)] p-4 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          {prompt}
        </pre>
      )}
    </div>
  );
}
