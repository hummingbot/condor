import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  FileText,
  Wrench,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { PairLabel } from "@/components/executor/PairLabel";
import { AgentPnlChart, metricsToDataPoints } from "@/components/agent/AgentPnlChart";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { snapshotQueryOptions, useSnapshotBubbles } from "@/hooks/useSnapshotBubbles";
import { type AgentExecutorRow, type AgentPerformance, type ExecutorInfo, api } from "@/lib/api";
import { groupExecutorsByMarket } from "@/lib/executor-overlays";
import { type ParsedJournal, type ParsedSnapshot, parseSnapshot } from "@/lib/parse-agent";
import { formatCompactUsd, formatCurrencyPnl, pnlTextClass, toolCallState } from "@/lib/formatters";
import { useRates } from "@/hooks/useRates";
import { DetailPanel, ExecutorTable, type SortDir, type SortKey } from "@/components/executor/ExecutorTable";

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

/** "CloseType.EARLY_STOP" → "early stop" */
function prettyCloseType(raw: string): string {
  return raw.replace(/^CloseType\./, "").replace(/_/g, " ").toLowerCase();
}

/** Total closes and a readable breakdown from the raw close-type counts. */
function closeSummary(perf?: AgentPerformance | null): { total: number; label: string } {
  const counts = perf?.close_type_counts ?? {};
  const entries = Object.entries(counts).filter(([, n]) => n > 0);
  return {
    total: entries.reduce((sum, [, n]) => sum + n, 0),
    label: entries.map(([k, n]) => `${prettyCloseType(k)} ×${n}`).join(", "),
  };
}

// ── Session KPIs ──
//
// Driven by `performance`, never by the executor rows. A session trading through
// bots has no rows to derive anything from — its executors live inside the bot
// instance's own database — so deriving the strip from rows made a session that
// traded $1.2k and lost $1.46 render as one that did nothing at all.

function Kpi({ label, value, sub, className = "" }: { label: string; value: string; sub?: string; className?: string }) {
  return (
    <div>
      <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">{label}</span>
      <span className={`font-mono text-sm font-semibold ${className || "text-[var(--color-text)]"}`}>{value}</span>
      {sub && <span className="block text-[9px] text-[var(--color-text-muted)]/70">{sub}</span>}
    </div>
  );
}

export function SessionKpis({
  perf,
  summary,
  hasReport,
  onOpenReport,
}: {
  perf?: AgentPerformance | null;
  summary?: { status: string; lastTick: number; lastAction: string };
  hasReport?: boolean;
  onOpenReport?: () => void;
}) {
  const total = perf?.total_pnl ?? 0;
  const closes = closeSummary(perf);
  const trades = perf?.trade_count ?? 0;
  const volume = perf?.volume ?? 0;
  const feesKnown = perf?.fees_known !== false;

  // A round-trip count of zero next to real volume is the strict close-type
  // filter refusing to read a directional controller's risk stop as a trade.
  // Show the closes that did happen rather than a bare "0" the numbers contradict.
  const tradeValue =
    trades > 0 ? String(trades) : closes.total > 0 ? String(closes.total) : volume > 0 ? "—" : "0";
  const tradeSub = trades === 0 && closes.total > 0 ? closes.label : undefined;

  const status = summary?.status || "";
  const statusClass =
    status === "ACTIVE" || status === "running"
      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
      : status === "paused"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-400"
        : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]";

  return (
    <div className="space-y-2">
      {(perf?.unresolved_bases?.length ?? 0) > 0 && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-2.5 text-xs text-amber-300">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            <strong>Incomplete.</strong> No live or archived instance for{" "}
            <span className="font-mono">{perf?.unresolved_bases?.join(", ")}</span>. The figures below are a
            floor — whatever those bots did is not in them.
          </span>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-x-5 gap-y-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
        {summary && (
          <div>
            <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Status</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${statusClass}`}>
              {status || "idle"}
            </span>
          </div>
        )}
        <Kpi
          label="Total PnL"
          value={formatCurrencyPnl(total)}
          className={pnlTextClass(total)}
        />
        <Kpi label="Realized" value={formatCurrencyPnl(perf?.realized_pnl ?? 0)} />
        <Kpi label="Unrealized" value={formatCurrencyPnl(perf?.unrealized_pnl ?? 0)} />
        <Kpi label="Volume" value={formatCompactUsd(volume)} />
        <Kpi
          label="Fees"
          value={feesKnown ? formatCompactUsd(perf?.fees ?? 0) : "—"}
          sub={feesKnown ? undefined : "not reported"}
        />
        <Kpi label="Closes" value={tradeValue} sub={tradeSub} />
        <Kpi label="Open" value={String(perf?.open_count ?? 0)} />
        {summary && summary.lastTick > 0 && <Kpi label="Ticks" value={`#${summary.lastTick}`} />}
        {/* The session's own live report. It has always existed — rebuilt every
            tick under a stable id — but was only reachable through the routines
            report grid, where it appeared as a routine nobody had created. */}
        {hasReport && (
          <button
            onClick={onOpenReport}
            className="ml-auto flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 py-1 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
          >
            <FileText className="h-3 w-3" /> Session report
          </button>
        )}
      </div>

      {summary?.lastAction && (
        <p className="px-1 text-xs text-[var(--color-text-muted)]">
          <span className="text-[10px] uppercase tracking-wider">Last action</span> — {summary.lastAction}
        </p>
      )}
    </div>
  );
}

// ── Bots & Controllers ──
//
// The answer to "what did this session actually run". The aggregator has always
// known it; the HTTP model used to drop it, so the UI could not have shown it.

export function SessionBots({ perf }: { perf?: AgentPerformance | null }) {
  const instances = perf?.bot_instances ?? [];
  const controllers = perf?.controllers ?? [];
  const liveNames = useMemo(() => new Set(perf?.bot_names ?? []), [perf?.bot_names]);

  // Group controllers under the instance they ran on, keeping deploy order.
  const groups = useMemo(() => {
    const byBot = new Map<string, typeof controllers>();
    for (const c of controllers) {
      const key = c.bot_name || "";
      byBot.set(key, [...(byBot.get(key) ?? []), c]);
    }
    const names = instances.length > 0 ? instances : Array.from(byBot.keys());
    return names.map((name) => ({ name, controllers: byBot.get(name) ?? [] }));
  }, [controllers, instances]);

  if (groups.length === 0) return null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        <Bot className="h-3.5 w-3.5" /> Bots &amp; Controllers ({groups.length} deploy{groups.length !== 1 ? "s" : ""})
      </h3>
      <div className="space-y-3">
        {groups.map(({ name, controllers: ctrls }) => {
          const live = liveNames.has(name);
          const realized = ctrls.reduce((s, c) => s + (c.realized_pnl_quote ?? 0), 0);
          const volume = ctrls.reduce((s, c) => s + (c.volume_traded ?? 0), 0);
          return (
            <div key={name} className="rounded-md border border-[var(--color-border)]/60 bg-[var(--color-bg)]/40">
              <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border)]/40 px-3 py-2">
                <span className="font-mono text-xs text-[var(--color-text)]">{name}</span>
                {/* Derived from the live snapshot's membership, NOT from the
                    controller `status` field — that reports "running" even for
                    instances this session stopped hours ago. */}
                <span
                  className={`rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase ${
                    live
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                      : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                  }`}
                >
                  {live ? "running" : "stopped"}
                </span>
                <span className={`ml-auto font-mono text-xs ${pnlTextClass(realized)}`}>
                  {formatCurrencyPnl(realized)}
                </span>
                <span className="font-mono text-[10px] text-[var(--color-text-muted)]">{formatCompactUsd(volume)} vol</span>
              </div>
              {ctrls.length === 0 ? (
                <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
                  No performance snapshot retained for this deploy.
                </p>
              ) : (
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="text-[9px] uppercase tracking-widest text-[var(--color-text-muted)]">
                      <th className="px-3 py-1.5 font-bold">Controller</th>
                      <th className="px-3 py-1.5 text-right font-bold">Realized</th>
                      <th className="px-3 py-1.5 text-right font-bold">Unrealized</th>
                      <th className="px-3 py-1.5 text-right font-bold">Volume</th>
                      <th className="px-3 py-1.5 text-right font-bold">Closes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ctrls.map((c, i) => {
                      const cCloses = Object.entries(c.close_type_counts ?? {}).filter(([, n]) => n > 0);
                      const cTotal = cCloses.reduce((s, [, n]) => s + n, 0);
                      return (
                        <tr key={`${c.controller_id}-${i}`} className="border-t border-[var(--color-border)]/25">
                          <td className="px-3 py-1.5 font-mono text-[var(--color-text)]">
                            {c.controller_id || "—"}
                            {c.trading_pair && (
                              <span className="ml-1.5 text-[10px] text-[var(--color-text-muted)]">{c.trading_pair}</span>
                            )}
                          </td>
                          <td className={`px-3 py-1.5 text-right font-mono ${(c.realized_pnl_quote ?? 0) >= 0 ? "text-[var(--color-text-muted)]" : "text-[var(--color-red)]"}`}>
                            {formatCurrencyPnl(c.realized_pnl_quote ?? 0)}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono text-[var(--color-text-muted)]">
                            {formatCurrencyPnl(c.unrealized_pnl_quote ?? 0)}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono text-[var(--color-text-muted)]">
                            {formatCompactUsd(c.volume_traded ?? 0)}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono text-[var(--color-text-muted)]">
                            {cTotal === 0 ? "—" : cTotal}
                            {cTotal > 0 && (
                              <span className="ml-1 text-[9px] text-[var(--color-text-muted)]/70">
                                {cCloses.map(([k]) => prettyCloseType(k)).join(", ")}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Session Canvas ──
//
// The agent's own thesis, written every tick and — until now — readable only
// inside the live report. The numbers say what happened; this says what the
// agent believed while it happened.

export function SessionCanvasPanel({ slug, sslug, sessionNum }: { slug: string; sslug: string; sessionNum: number }) {
  const [expanded, setExpanded] = useState(true);
  const { data } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "canvas"],
    queryFn: () => api.getSessionCanvas(slug, sslug, sessionNum),
    refetchInterval: 30000,
  });

  const sections = useMemo(() => {
    if (!data) return [];
    return data.section_order
      .map((key) => ({ key, title: data.section_titles[key] ?? key, body: data.sections[key] ?? "" }))
      .filter((s) => s.body.trim().length > 0);
  }, [data]);

  if (sections.length === 0) return null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Narrative — the agent's own words
          </h3>
          {data && data.last_revised_tick > 0 && (
            <span className="text-[10px] text-[var(--color-text-muted)]/70">
              last revised at tick #{data.last_revised_tick} · unverified
            </span>
          )}
        </div>
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        )}
      </button>
      {expanded && (
        <div className="space-y-4 border-t border-[var(--color-border)] p-4">
          {sections.map((s) => (
            <div key={s.key}>
              <h4 className="mb-1 text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                {s.title}
              </h4>
              <div className="chat-markdown text-sm leading-relaxed text-[var(--color-text)]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.body}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Session Overview ──

export function SessionOverview(props: {
  journal: ParsedJournal;
  perf?: AgentPerformance | null;
  pnlSeries?: { timestamp: string; pnl: number }[] | null;
}) {
  const { metrics } = props.journal;
  const { pnlSeries } = props;

  // Prefer the series derived from the bots' own history: the journal snapshots
  // are only what the aggregator believed at each tick, so a session that ran
  // while it could not see its bots has a permanently flat record of zeros.
  // Fall back to the snapshots for pure executor sessions, which own no bot and
  // so have no history to derive from.
  const pnlData = useMemo(() => {
    if (pnlSeries?.length) {
      return pnlSeries
        .filter((p) => p.timestamp)
        .map((p) => ({
          time: Math.floor(new Date(p.timestamp).getTime() / 1000),
          value: p.pnl,
        }))
        .sort((a, b) => a.time - b.time);
    }
    return metricsToDataPoints(metrics);
  }, [pnlSeries, metrics]);

  if (pnlData.length <= 1) {
    return null;
  }

  return (
    <div className="space-y-4">
      <AgentPnlChart
        data={pnlData}
        height={400}
        title={pnlSeries?.length ? "Realized PnL" : "Metrics Timeline"}
      />
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

// ── Session Executors (chart-focused with WS streaming) ──

export function SessionExecutors({
  slug,
  sslug,
  sessionNum,
  serverName,
  controllerIds,
  onSnapshotClick,
  isLiveSession = false,
  botMode = false,
}: {
  slug: string;
  sslug: string;
  sessionNum: number;
  serverName: string;
  controllerIds?: string[];
  onSnapshotClick?: (tick: number) => void;
  /** True only for the session currently running: lets the WS contribute
   *  executors the session REST endpoint hasn't recorded yet. */
  isLiveSession?: boolean;
  /** True when the session traded through bots. Its per-trade rows live inside
   *  the bot instance's own database and never reach the agent_id-keyed executor
   *  table, so "no rows" means "not retained here" — not "nothing happened". */
  botMode?: boolean;
}) {
  // REST data (fallback + historical executors)
  const { data: sessionDetail } = useQuery({
    queryKey: ["strategy-session-executors", slug, sslug, sessionNum],
    queryFn: () => api.getStrategySessionExecutors(slug, sslug, sessionNum),
    refetchInterval: 10000,
  });

  const restExecutors = sessionDetail?.executors ?? [];

  // WS-backed live executors (if controller IDs provided)
  const { executors: wsExecutors } = useAgentExecutors(
    controllerIds?.length ? serverName : null,
    controllerIds || [],
  );

  // Merge: id-keyed upsert — the WS refreshes rows this session already owns and
  // never invents new ones. `wsExecutors` belongs to the *running* instances, so
  // appending it to a finished session would credit it with another session's
  // PnL, volume and fees. Only the live session takes the unmatched WS rows, and
  // only to show executors the REST endpoint hasn't recorded yet.
  const executorInfos = useMemo(() => {
    const restInfos = restExecutors.map(agentRowToExecutorInfo);
    if (wsExecutors.length === 0) return restInfos;

    const wsMap = new Map(wsExecutors.map((ex) => [ex.id, ex]));
    const merged = restInfos.map((ex) => wsMap.get(ex.id) ?? ex);
    if (!isLiveSession) return merged;

    const restIds = new Set(restInfos.map((ex) => ex.id));
    for (const ex of wsExecutors) {
      if (!restIds.has(ex.id)) merged.push(ex);
    }
    return merged;
  }, [restExecutors, wsExecutors, isLiveSession]);

  // Currency conversion
  const quoteCurrencies = useMemo(
    () => executorInfos.map((ex) => ex.trading_pair?.split("-")[1] || "USDT"),
    [executorInfos],
  );
  const { formatPnlValue, formatValue, formatValueDetailed } = useRates(quoteCurrencies);

  // Fetch snapshots for bubble markers
  const { data: snapshotsData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "snapshots"],
    queryFn: () => api.getSessionSnapshots(slug, sslug, sessionNum),
  });

  // One query per snapshot body, shared with SnapshotDetail — see useSnapshotBubbles.
  const snapshotSummaries = useMemo(() => snapshotsData?.snapshots ?? [], [snapshotsData]);
  const snapshotBubbles = useSnapshotBubbles(slug, sslug, sessionNum, snapshotSummaries);

  // Group executors by connector:pair for charts
  const chartGroups = useMemo(
    () => (serverName ? groupExecutorsByMarket(executorInfos) : []),
    [executorInfos, serverName],
  );

  // Table state
  const [sortKey, setSortKey] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const stoppingIds = useMemo(() => new Set<string>(), []);
  const [selectedExecutor, setSelectedExecutor] = useState<ExecutorInfo | null>(null);

  // Positions held (filtered by controller IDs)
  const { data: positionsData } = useQuery({
    queryKey: ["positions-held", serverName],
    queryFn: () => api.getPositionsHeld(serverName),
    enabled: !!serverName && (controllerIds?.length ?? 0) > 0,
    refetchInterval: 10000,
  });

  const positions = useMemo(() => {
    if (!positionsData?.positions || !controllerIds?.length) return [];
    const cidSet = new Set(controllerIds);
    return positionsData.positions.filter((p) => p.controller_id && cidSet.has(p.controller_id));
  }, [positionsData, controllerIds]);

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

  // No rows is two different facts, and rendering them alike is what made a
  // session that traded through three bot deploys read as one that never traded.
  if (executorInfos.length === 0) {
    return (
      <p className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3 text-xs leading-relaxed text-[var(--color-text-muted)]">
        {botMode
          ? "No open positions right now. This session trades through bots, whose per-trade rows stay inside each bot instance's own database and never reach the executor table — the realized figures above come from the controllers' performance history instead."
          : "No executors for this session."}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {/* Positions Held */}
      {positions.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h3 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Positions Held ({positions.length})
          </h3>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                  <th className="pb-2 pr-3">Pair</th>
                  <th className="pb-2 pr-3">Side</th>
                  <th className="pb-2 pr-3 text-right">Amount</th>
                  <th className="pb-2 pr-3 text-right">Entry</th>
                  <th className="pb-2 pr-3 text-right">Current</th>
                  <th className="pb-2 pr-3 text-right">Unreal. PnL</th>
                  <th className="pb-2 text-right">Leverage</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p, i) => {
                  const upnl = p.unrealized_pnl_quote ?? p.unrealized_pnl ?? 0;
                  const side = p.position_side || p.side || "—";
                  const amount = p.net_amount_base ?? p.amount ?? 0;
                  const entry = p.buy_breakeven_price ?? p.entry_price ?? 0;
                  const current = p.current_price ?? 0;
                  return (
                    <tr key={`${p.trading_pair}-${i}`} className="border-b border-[var(--color-border)]/30">
                      <td className="py-2 pr-3 font-mono text-[var(--color-text)]">
                        <PairLabel tradingPair={p.trading_pair} connector={p.connector_name} />
                      </td>
                      <td className="py-2 pr-3">
                        <span className={side.toLowerCase().includes("long") || side.toLowerCase() === "buy" ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}>
                          {side.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-[var(--color-text)]">{Math.abs(amount).toFixed(4)}</td>
                      <td className="py-2 pr-3 text-right font-mono text-[var(--color-text-muted)]">${entry.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-right font-mono text-[var(--color-text)]">${current.toFixed(2)}</td>
                      <td className={`py-2 pr-3 text-right font-mono ${pnlTextClass(upnl)}`}>
                        {formatCurrencyPnl(upnl)}
                      </td>
                      <td className="py-2 text-right font-mono text-[var(--color-text-muted)]">{p.leverage ? `${p.leverage}x` : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Chart-focused view — each trading pair gets a prominent chart */}
      {chartGroups.map(([key, group]) => {
        const pairPnl = group.reduce((sum, ex) => sum + (ex.pnl ?? 0), 0);
        return (
          <div key={key}>
            {/* Pair header (only when multiple pairs) */}
            {chartGroups.length > 1 && (
              <div className="mb-1.5 flex items-center gap-2 px-1">
                <PairLabel
                  tradingPair={group[0].trading_pair}
                  connector={group[0].connector}
                  className="text-xs font-medium text-[var(--color-text)]"
                />
                <span className="text-[10px] text-[var(--color-text-muted)]">{group[0].connector}</span>
                <span className={`ml-auto font-mono text-xs ${pnlTextClass(pairPnl)}`}>
                  {formatCurrencyPnl(pairPnl)}
                </span>
                <span className="text-[10px] text-[var(--color-text-muted)]">{group.length} exec</span>
              </div>
            )}
            <ExecutorChart
              server={serverName}
              executors={group}
              connector={group[0].connector}
              tradingPair={group[0].trading_pair}
              height={500}
              snapshots={snapshotBubbles}
              onSnapshotClick={onSnapshotClick}
            />
          </div>
        );
      })}

      {/* Executor table */}
      <ExecutorTable
        executors={executorInfos}
        sortKey={sortKey}
        sortDir={sortDir}
        onSort={handleSort}
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
        onSelectAll={selectAll}
        allSelected={allSelected}
        onRowClick={(ex) => setSelectedExecutor(ex)}
        selectedExecutorId={selectedExecutor?.id ?? null}
        onStop={() => {}}
        stoppingIds={stoppingIds}
      />

      {/* Executor Detail Panel */}
      {selectedExecutor && (
        <DetailPanel
          executor={selectedExecutor}
          server={serverName}
          onClose={() => setSelectedExecutor(null)}
          onStop={() => {}}
          stopping={false}
          rateFormatPnl={formatPnlValue}
          rateFormatValue={formatValue}
          rateFormatDetailed={formatValueDetailed}
        />
      )}
    </div>
  );
}

// ── Session Snapshots ──

export function SessionSnapshots({ slug, sslug, sessionNum, initialTick }: { slug: string; sslug: string; sessionNum: number; initialTick?: number | null }) {
  const [selectedTick, setSelectedTick] = useState<number>(initialTick ?? 0);

  const { data: snapshotsData } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "snapshots"],
    queryFn: () => api.getSessionSnapshots(slug, sslug, sessionNum),
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
          <SnapshotDetail slug={slug} sslug={sslug} sessionNum={sessionNum} tick={selectedTick} />
        ) : (
          <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select a snapshot to view details.</p>
        )}
      </div>
    </div>
  );
}

// ── Snapshot Detail ──

function SnapshotDetail({ slug, sslug, sessionNum, tick }: { slug: string; sslug: string; sessionNum: number; tick: number }) {
  // Same options the marker previews use, so a tick already previewed renders
  // straight from cache — no second request, no spinner.
  const { data, isLoading } = useQuery({
    ...snapshotQueryOptions(slug, sslug, sessionNum, tick),
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
  const state = toolCallState(tc.status);
  const dotColor =
    state === "ok"
      ? "bg-[var(--color-green)]"
      : state === "error"
        ? "bg-[var(--color-red)]"
        : "bg-[var(--color-text-muted)]";

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
