import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { ArrowLeft, Calendar, ChevronLeft, ChevronRight, Database, Loader2, TrendingDown, TrendingUp } from "lucide-react";

import { ArchivedPerformanceCharts } from "@/components/charts/ArchivedPerformanceCharts";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import type { ArchivedBotSummary, ExecutorInfo } from "@/lib/api";

function formatUsd(v: number) {
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(2)}`;
}

function formatPnl(v: number) {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${formatUsd(v)}`;
}

function pnlColor(v: number) {
  return v >= 0 ? "text-emerald-400" : "text-red-400";
}

function formatDate(epoch: number | null) {
  if (!epoch) return "—";
  const d = new Date(epoch * 1000);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatAge(startEpoch: number | null, endEpoch: number | null) {
  if (!startEpoch || !endEpoch) return "—";
  const diffSec = endEpoch - startEpoch;
  if (diffSec < 3600) return `${Math.round(diffSec / 60)}m`;
  if (diffSec < 86400) return `${(diffSec / 3600).toFixed(1)}h`;
  return `${(diffSec / 86400).toFixed(1)}d`;
}

// ── List View ──

function BotCard({ bot, onClick }: { bot: ArchivedBotSummary; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-full text-left rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 hover:border-[var(--color-primary)]/50 hover:bg-[var(--color-surface-hover)] transition-colors"
    >
      <div className="flex items-start justify-between mb-2">
        <h3 className="font-semibold text-sm truncate">{bot.bot_name}</h3>
        <span className="text-xs text-[var(--color-text-muted)] whitespace-nowrap ml-2">
          {formatAge(bot.start_time, bot.end_time)}
        </span>
      </div>

      <div className="flex items-center gap-2 mb-2 text-xs text-[var(--color-text-muted)]">
        <Calendar className="h-3 w-3" />
        <span>{formatDate(bot.start_time)} — {formatDate(bot.end_time)}</span>
      </div>

      <div className="flex items-center gap-3 text-xs">
        <span className="text-[var(--color-text-muted)]">
          {bot.total_trades} trades
        </span>
        <span className="text-[var(--color-text-muted)]">
          {bot.total_orders} orders
        </span>
      </div>

      {bot.trading_pairs.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {bot.trading_pairs.slice(0, 4).map((pair) => (
            <span
              key={pair}
              className="rounded bg-[var(--color-bg)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)]"
            >
              {pair}
            </span>
          ))}
          {bot.trading_pairs.length > 4 && (
            <span className="text-[10px] text-[var(--color-text-muted)]">
              +{bot.trading_pairs.length - 4}
            </span>
          )}
        </div>
      )}
    </button>
  );
}

function ArchivedBotsList() {
  const { server } = useServer();
  const [selectedBot, setSelectedBot] = useState<ArchivedBotSummary | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["archived-bots", server],
    queryFn: () => api.getArchivedBots(server!),
    enabled: !!server,
  });

  if (selectedBot) {
    return (
      <ArchivedBotDetail
        dbPath={selectedBot.db_path}
        startTime={selectedBot.start_time ?? undefined}
        endTime={selectedBot.end_time ?? undefined}
        onBack={() => setSelectedBot(null)}
      />
    );
  }

  if (!server) {
    return (
      <div className="flex items-center justify-center h-64 text-[var(--color-text-muted)]">
        Select a server to view archived bots
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--color-text-muted)]" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-400">
        Failed to load archived bots: {(error as Error).message}
      </div>
    );
  }

  const bots = data?.bots ?? [];

  if (bots.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-[var(--color-text-muted)]">
        <Database className="h-10 w-10 mb-3 opacity-40" />
        <p>No archived bots found</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {bots.map((bot) => (
        <BotCard
          key={bot.db_path}
          bot={bot}
          onClick={() => setSelectedBot(bot)}
        />
      ))}
    </div>
  );
}

// ── Detail View ──

function StatCard({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-muted)] mb-1">{label}</p>
      <p className={`text-sm font-semibold ${className ?? ""}`}>{value}</p>
    </div>
  );
}

function PnlByPairBar({ pair, pnl, maxAbs }: { pair: string; pnl: number; maxAbs: number }) {
  const pct = maxAbs > 0 ? (Math.abs(pnl) / maxAbs) * 100 : 0;
  const isPositive = pnl >= 0;

  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-24 truncate text-[var(--color-text-muted)]">{pair}</span>
      <div className="flex-1 h-4 bg-[var(--color-bg)] rounded overflow-hidden relative">
        <div
          className={`h-full rounded ${isPositive ? "bg-emerald-500/60" : "bg-red-500/60"}`}
          style={{ width: `${Math.max(pct, 2)}%` }}
        />
      </div>
      <span className={`w-20 text-right font-mono ${pnlColor(pnl)}`}>
        {formatPnl(pnl)}
      </span>
    </div>
  );
}

// ── Paginated Executor Table ──

const EXECUTORS_PAGE_SIZE = 50;

type SortField = "pnl" | "volume" | "timestamp";
type SortDir = "asc" | "desc";

function ExecutorTable({ server, dbPath, executorCount }: { server: string; dbPath: string; executorCount: number }) {
  const [page, setPage] = useState(0);
  const [sortField, setSortField] = useState<SortField>("pnl");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const offset = page * EXECUTORS_PAGE_SIZE;

  const { data, isLoading } = useQuery({
    queryKey: ["archived-executors", server, dbPath, offset, EXECUTORS_PAGE_SIZE],
    queryFn: () => api.getArchivedExecutors(server, dbPath, offset, EXECUTORS_PAGE_SIZE),
    enabled: !!server && executorCount > 0,
    staleTime: Infinity,
  });

  const executors = data?.executors ?? [];
  const total = data?.total ?? executorCount;
  const totalPages = Math.ceil(total / EXECUTORS_PAGE_SIZE);

  // Client-side sort within the current page
  const sorted = useMemo(() => {
    const arr = [...executors];
    arr.sort((a, b) => {
      const av = a[sortField];
      const bv = b[sortField];
      return sortDir === "desc" ? (bv as number) - (av as number) : (av as number) - (bv as number);
    });
    return arr;
  }, [executors, sortField, sortDir]);

  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortField(field);
      setSortDir("desc");
    }
  };

  const sortIndicator = (field: SortField) => {
    if (sortField !== field) return "";
    return sortDir === "desc" ? " \u2193" : " \u2191";
  };

  if (executorCount === 0) return null;

  return (
    <div>
      <h3 className="text-xs font-medium text-[var(--color-text-muted)] mb-2 uppercase tracking-wider">
        Executors ({total})
      </h3>
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-auto">
        {isLoading ? (
          <div className="flex items-center justify-center h-24">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-[var(--color-text-muted)]">
                <th className="px-3 py-2 text-left font-medium">ID</th>
                <th className="px-3 py-2 text-left font-medium">Type</th>
                <th className="px-3 py-2 text-left font-medium">Pair</th>
                <th className="px-3 py-2 text-left font-medium">Side</th>
                <th className="px-3 py-2 text-left font-medium">Close Type</th>
                <th className="px-3 py-2 text-right font-medium">Entry</th>
                <th className="px-3 py-2 text-right font-medium">Exit</th>
                <th
                  className="px-3 py-2 text-right font-medium cursor-pointer hover:text-[var(--color-text)]"
                  onClick={() => toggleSort("pnl")}
                >
                  PnL{sortIndicator("pnl")}
                </th>
                <th className="px-3 py-2 text-right font-medium">Fees</th>
                <th
                  className="px-3 py-2 text-right font-medium cursor-pointer hover:text-[var(--color-text)]"
                  onClick={() => toggleSort("volume")}
                >
                  Volume{sortIndicator("volume")}
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((ex, i) => {
                const sideColor =
                  ex.side === "BUY" ? "text-emerald-400" : ex.side === "SELL" ? "text-red-400" : "";
                return (
                  <tr
                    key={`${ex.id}-${i}`}
                    className="border-b border-[var(--color-border)]/50 hover:bg-[var(--color-surface-hover)]"
                  >
                    <td className="px-3 py-1.5 font-mono">{ex.id.slice(0, 8)}</td>
                    <td className="px-3 py-1.5">{ex.type || "—"}</td>
                    <td className="px-3 py-1.5">{ex.trading_pair || "—"}</td>
                    <td className={`px-3 py-1.5 ${sideColor}`}>{ex.side || "—"}</td>
                    <td className="px-3 py-1.5">{ex.close_type || "—"}</td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {ex.entry_price > 0 ? ex.entry_price.toPrecision(6) : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {ex.current_price > 0 ? ex.current_price.toPrecision(6) : "—"}
                    </td>
                    <td className={`px-3 py-1.5 text-right font-mono ${pnlColor(ex.pnl)}`}>
                      {formatPnl(ex.pnl)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-amber-400/80">
                      {formatUsd(ex.cum_fees_quote)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {formatUsd(ex.volume)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {/* Pagination controls */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-3 py-2 border-t border-[var(--color-border)]">
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {offset + 1}–{Math.min(offset + EXECUTORS_PAGE_SIZE, total)} of {total}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="p-1 rounded hover:bg-[var(--color-surface-hover)] disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <span className="text-[10px] text-[var(--color-text-muted)] px-2">
                {page + 1} / {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="p-1 rounded hover:bg-[var(--color-surface-hover)] disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Detail View ──

function ArchivedBotDetail({ dbPath, startTime: botStartTime, endTime: botEndTime, onBack }: { dbPath: string; startTime?: number; endTime?: number; onBack: () => void }) {
  const { server } = useServer();

  // Query 1: Performance summary (no executors) — fast path
  const { data: perf, isLoading, error } = useQuery({
    queryKey: ["archived-performance", server, dbPath],
    queryFn: () => api.getArchivedBotPerformance(server!, dbPath, false),
    enabled: !!server,
    staleTime: Infinity,
  });

  // Query 2: First page of executors — loads in background for charts + table
  const { data: execData } = useQuery({
    queryKey: ["archived-executors", server, dbPath, 0, EXECUTORS_PAGE_SIZE],
    queryFn: () => api.getArchivedExecutors(server!, dbPath, 0, EXECUTORS_PAGE_SIZE),
    enabled: !!server && !!perf,
    staleTime: Infinity,
  });

  const executors: ExecutorInfo[] = execData?.executors ?? [];
  const executorCount = execData?.total ?? perf?.executor_count ?? 0;

  // Derive available connector+pair combos from executors for pair selector
  const pairOptions = useMemo(() => {
    if (!executors.length) return [];
    const counts = new Map<string, { connector: string; pair: string; count: number }>();
    for (const ex of executors) {
      if (!ex.connector || !ex.trading_pair) continue;
      const key = `${ex.connector}:${ex.trading_pair}`;
      const existing = counts.get(key);
      if (existing) {
        existing.count++;
      } else {
        counts.set(key, { connector: ex.connector, pair: ex.trading_pair, count: 1 });
      }
    }
    return Array.from(counts.values()).sort((a, b) => b.count - a.count);
  }, [executors]);

  const [selectedPairKey, setSelectedPairKey] = useState<string | null>(null);

  // Current connector+pair for charts
  const currentConnector = selectedPairKey
    ? selectedPairKey.split(":")[0]
    : perf?.primary_connector ?? "";
  const currentPair = selectedPairKey
    ? selectedPairKey.split(":").slice(1).join(":")
    : perf?.primary_trading_pair ?? "";

  // Filter executors by selected pair for the candle chart
  const filteredExecutors: ExecutorInfo[] = useMemo(() => {
    if (!executors.length) return [];
    if (!currentConnector && !currentPair) return executors;
    return executors.filter(
      (ex) =>
        (!currentConnector || ex.connector === currentConnector) &&
        (!currentPair || ex.trading_pair === currentPair),
    );
  }, [executors, currentConnector, currentPair]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--color-text-muted)]" />
      </div>
    );
  }

  if (error || !perf) {
    return (
      <div className="space-y-4">
        <button
          onClick={onBack}
          className="flex items-center gap-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        >
          <ArrowLeft className="h-4 w-4" /> Back to list
        </button>
        <div className="flex items-center justify-center h-48 text-red-400">
          Failed to load performance data
        </div>
      </div>
    );
  }

  const pnlPairs = Object.entries(perf.pnl_by_pair).sort(
    (a, b) => Math.abs(b[1]) - Math.abs(a[1]),
  );
  const maxAbsPnl = pnlPairs.length > 0 ? Math.abs(pnlPairs[0][1]) : 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="flex items-center gap-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
          >
            <ArrowLeft className="h-4 w-4" /> Back
          </button>
          <h2 className="text-lg font-semibold">{perf.bot_name}</h2>
        </div>
        <div className={`flex items-center gap-1 text-lg font-bold ${pnlColor(perf.total_pnl)}`}>
          {perf.total_pnl >= 0 ? <TrendingUp className="h-5 w-5" /> : <TrendingDown className="h-5 w-5" />}
          {formatPnl(perf.total_pnl)}
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <StatCard label="Total PnL" value={formatPnl(perf.total_pnl)} className={pnlColor(perf.total_pnl)} />
        <StatCard label="Volume" value={formatUsd(perf.total_volume)} />
        <StatCard label="Fees" value={formatUsd(perf.total_fees)} />
        <StatCard label="Trades" value={String(perf.trade_count)} />
        <StatCard label="Buy / Sell" value={`${perf.buy_count} / ${perf.sell_count}`} />
        <StatCard label="Pairs" value={String(perf.trading_pairs.length)} />
      </div>

      {/* Pair selector (if multiple pairs from executors) */}
      {pairOptions.length > 1 && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-[var(--color-text-muted)]">Chart pair:</span>
          <div className="flex flex-wrap gap-1">
            {pairOptions.map((opt) => {
              const key = `${opt.connector}:${opt.pair}`;
              const isSelected =
                key === selectedPairKey ||
                (!selectedPairKey &&
                  opt.connector === perf.primary_connector &&
                  opt.pair === perf.primary_trading_pair);
              return (
                <button
                  key={key}
                  onClick={() => setSelectedPairKey(key)}
                  className={`rounded px-2 py-0.5 text-[11px] transition-colors ${
                    isSelected
                      ? "bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40"
                      : "bg-[var(--color-bg)] text-[var(--color-text-muted)] border border-[var(--color-border)] hover:border-[var(--color-text-muted)]"
                  }`}
                >
                  {opt.pair} ({opt.count})
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Performance charts: candles (by default), PnL, position */}
      {server && (currentConnector || currentPair) && (
        <ArchivedPerformanceCharts
          server={server}
          executors={filteredExecutors}
          cumulativePnl={perf.cumulative_pnl}
          connector={currentConnector}
          tradingPair={currentPair}
          startTime={botStartTime}
          endTime={botEndTime}
        />
      )}

      {/* PnL by Pair */}
      {pnlPairs.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-[var(--color-text-muted)] mb-2 uppercase tracking-wider">
            PnL by Trading Pair
          </h3>
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 space-y-2">
            {pnlPairs.map(([pair, pnl]) => (
              <PnlByPairBar key={pair} pair={pair} pnl={pnl} maxAbs={maxAbsPnl} />
            ))}
          </div>
        </div>
      )}

      {/* Paginated Executor Table */}
      {server && (
        <ExecutorTable
          server={server}
          dbPath={dbPath}
          executorCount={executorCount}
        />
      )}
    </div>
  );
}

// ── Main Page ──

export function ArchivedBotsTab() {
  return <ArchivedBotsList />;
}
