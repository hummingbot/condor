import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import { SessionPositions } from "@/components/agent/session/SessionPositions";
import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { PairLabel } from "@/components/executor/PairLabel";
import { DetailPanel, ExecutorTable, type SortDir, type SortKey } from "@/components/perf/ExecutorTable";
import { useAgentExecutors } from "@/hooks/useAgentExecutors";
import { useRates } from "@/hooks/useRates";
import { useSnapshotBubbles } from "@/hooks/useSnapshotBubbles";
import { type AgentExecutorRow, type ExecutorInfo, api } from "@/lib/api";
import { groupExecutorsByMarket } from "@/lib/executor-overlays";
import { formatCurrencyPnl, pnlTextClass } from "@/lib/formatters";

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
      <SessionPositions positions={positions} />

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
      />

      {/* Executor Detail Panel */}
      {selectedExecutor && (
        <DetailPanel
          executor={selectedExecutor}
          server={serverName}
          onClose={() => setSelectedExecutor(null)}
          rateFormatPnl={formatPnlValue}
          rateFormatValue={formatValue}
          rateFormatDetailed={formatValueDetailed}
        />
      )}
    </div>
  );
}
