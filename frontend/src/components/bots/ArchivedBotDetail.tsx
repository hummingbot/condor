import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, Loader2, TrendingDown, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";

import { ArchivedPerformanceCharts } from "@/components/charts/ArchivedPerformanceCharts";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import type { ArchivedBotPerformance, ExecutorInfo } from "@/lib/api";
import { pnlTextClass } from "@/lib/formatters";

function formatUsd(v: number) {
  if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(2)}`;
}

function formatPnl(v: number) {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${formatUsd(v)}`;
}

/**
 * Says which currency the run actually traded in and what it was converted at.
 *
 * A BRL-quoted run rendered behind a bare "$" overstated itself by the whole
 * BRL/USD rate, so a converted figure has to show its work. Stablecoin quotes
 * are already dollars and say nothing.
 */
function ConversionNote({ perf }: { perf: ArchivedBotPerformance }) {
  const rate = perf.usd_rates[perf.quote_currency];

  if (!perf.converted) {
    return (
      <p className="text-[10px] text-amber-500/90">
        No USD rate for {perf.quote_currency || "this run's quote"} — figures are
        shown in their own quote currency, not dollars.
      </p>
    );
  }

  // A dollar-quoted run has nothing to disclose.
  if (!rate || rate === 1) return null;

  return (
    <p className="text-[10px] text-[var(--color-text-muted)]">
      Converted from {perf.quote_currency} at 1 USD ={" "}
      {(1 / rate).toLocaleString(undefined, { maximumFractionDigits: 4 })}{" "}
      {perf.quote_currency}
    </p>
  );
}

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
      <span className={`w-20 text-right font-mono ${pnlTextClass(pnl)}`}>
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
      // Money columns sort on their USD value: across markets with different
      // quotes, raw quote figures are not comparable to each other.
      const scale = (ex: ExecutorInfo) =>
        sortField === "timestamp" ? 1 : ex.usd_rate ?? 1;
      const av = (a[sortField] as number) * scale(a);
      const bv = (b[sortField] as number) * scale(b);
      return sortDir === "desc" ? bv - av : av - bv;
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
    return sortDir === "desc" ? " ↓" : " ↑";
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
                  ex.side === "BUY" ? "text-[var(--color-green)]" : ex.side === "SELL" ? "text-[var(--color-red)]" : "";
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
                    <td className={`px-3 py-1.5 text-right font-mono ${pnlTextClass(ex.pnl)}`}>
                      {formatPnl(ex.pnl * (ex.usd_rate ?? 1))}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-amber-400/80">
                      {formatUsd(ex.cum_fees_quote * (ex.usd_rate ?? 1))}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {formatUsd(ex.volume * (ex.usd_rate ?? 1))}
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

interface Props {
  /** The run's archived sqlite path, from ``BotRunInfo.archive_db_path``. */
  dbPath: string;
  /** Shown in the header while the (slow) performance fetch is still in flight. */
  botName: string;
  /** Run window in epoch seconds, for the candle range. */
  startTime?: number;
  endTime?: number;
  onBack: () => void;
}

/**
 * Deep history for one stopped bot, read out of the sqlite its container left
 * behind. Reached from a Runs row that has an ``archive_db_path``.
 *
 * The performance fetch is slow and unbounded — see ``condor/web/routes/archived.py``
 * — so the header and its back button render immediately, above the spinner,
 * rather than stranding the reader on a bare loader.
 */
export function ArchivedBotDetail({ dbPath, botName, startTime, endTime, onBack }: Props) {
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

  // Available connector+pair combos for the pair selector. Preferred from the
  // server's per-market series, which is keyed over every executor of the run —
  // deriving it from the loaded executor page hides markets that page missed
  // and undercounts the ones it caught.
  const pairOptions = useMemo(() => {
    const series = perf?.chart_series;
    if (series && Object.keys(series).length > 0) {
      return Object.entries(series)
        .map(([key, s]) => {
          const [connector, ...rest] = key.split(":");
          return { connector, pair: rest.join(":"), count: s.executor_count };
        })
        .sort((a, b) => b.count - a.count);
    }

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
  }, [perf?.chart_series, executors]);

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

  const backButton = (
    <button
      onClick={onBack}
      className="flex items-center gap-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
    >
      <ArrowLeft className="h-4 w-4" /> Back to runs
    </button>
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          {backButton}
          <h2 className="text-lg font-semibold">{botName}</h2>
        </div>
        <div className="flex flex-col items-center justify-center h-48 gap-3">
          <Loader2 className="h-6 w-6 animate-spin text-[var(--color-text-muted)]" />
          <p className="text-xs text-[var(--color-text-muted)]">Reading archived database…</p>
        </div>
      </div>
    );
  }

  if (error || !perf) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          {backButton}
          <h2 className="text-lg font-semibold">{botName}</h2>
        </div>
        <div className="flex items-center justify-center h-48 text-[var(--color-red)]">
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
          {backButton}
          <h2 className="text-lg font-semibold">{perf.bot_name || botName}</h2>
        </div>
        <div className={`flex items-center gap-1 text-lg font-bold ${pnlTextClass(perf.total_pnl)}`}>
          {perf.total_pnl >= 0 ? <TrendingUp className="h-5 w-5" /> : <TrendingDown className="h-5 w-5" />}
          {formatPnl(perf.total_pnl)}
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <StatCard label="Total PnL" value={formatPnl(perf.total_pnl)} className={pnlTextClass(perf.total_pnl)} />
        <StatCard label="Volume" value={formatUsd(perf.total_volume)} />
        <StatCard label="Fees" value={formatUsd(perf.total_fees)} />
        <StatCard
          label={perf.stats_source === "executors" ? "Executors" : "Trades"}
          value={perf.trade_count.toLocaleString()}
        />
        <StatCard label="Buy / Sell" value={`${perf.buy_count} / ${perf.sell_count}`} />
        <StatCard label="Pairs" value={String(perf.trading_pairs.length)} />
      </div>

      <ConversionNote perf={perf} />

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
          series={perf.chart_series?.[`${currentConnector}:${currentPair}`]}
          connector={currentConnector}
          tradingPair={currentPair}
          startTime={startTime}
          endTime={endTime}
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
