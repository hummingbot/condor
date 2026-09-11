import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Loader2,
  RefreshCw,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { useMemo, useState } from "react";

import { ReportFrame } from "@/components/routines/ReportFrame";
import { useArchivedReport } from "@/hooks/useArchivedReport";
import { CURRENCY_SYMBOLS, type DisplayCurrency } from "@/hooks/useDisplayCurrency";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import type {
  ArchivedBotPerformance,
  ArchivedControllerRollup,
  ArchivedExecutor,
} from "@/lib/api";
import { formatCurrencyPnl, formatCurrencyVolume, pnlTextClass } from "@/lib/formatters";

/**
 * The symbol every figure on this page is drawn behind.
 *
 * A converted run is in dollars. An unconverted one is *not*, and this page
 * used to render it behind a hardcoded `$` anyway — contradicting the
 * `ConversionNote` directly below, which says the figures are in their own
 * quote currency. So an unconverted run keeps its quote's own symbol, and a
 * quote the dashboard has no symbol for is labelled by its code rather than
 * mislabelled as dollars. A run that does not even name its quote has nothing
 * better to offer than `$`.
 */
function runSymbol(perf: ArchivedBotPerformance): string {
  if (perf.converted || !perf.quote_currency) return "$";
  return CURRENCY_SYMBOLS[perf.quote_currency as DisplayCurrency] ?? `${perf.quote_currency} `;
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

function PnlByPairBar({
  pair,
  pnl,
  maxAbs,
  symbol,
}: {
  pair: string;
  pnl: number;
  maxAbs: number;
  symbol: string;
}) {
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
        {formatCurrencyPnl(pnl, symbol)}
      </span>
    </div>
  );
}

// ── Paginated Executor Table ──

const EXECUTORS_PAGE_SIZE = 50;

type SortField = "pnl" | "volume" | "timestamp";
type SortDir = "asc" | "desc";

function ExecutorTable({
  server,
  dbPath,
  executorCount,
  symbol,
}: {
  server: string;
  dbPath: string;
  executorCount: number;
  symbol: string;
}) {
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
      const scale = (ex: ArchivedExecutor) => (sortField === "timestamp" ? 1 : ex.usd_rate);
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
                      {formatCurrencyPnl(ex.pnl * ex.usd_rate, symbol)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-amber-400/80">
                      {formatCurrencyVolume(ex.cum_fees_quote * ex.usd_rate, symbol)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {formatCurrencyVolume(ex.volume * ex.usd_rate, symbol)}
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

// ── Controllers ──

/**
 * The controllers that ran inside the run, and the button that charts one.
 *
 * Condor never had this axis: ``controller_id`` arrives on every archived
 * executor and nothing grouped by it, so a run with five controllers read as
 * one number. The rollup is executor-derived — archived trade rows carry no
 * controller — so on a run whose headline came from its trades the two can
 * differ; the report says so, and so does the note below.
 *
 * The unattributed row (executors that ran under no controller at all) has no
 * chart of its own: its subject *is* the run's, so the run chart is its chart.
 */
function ControllerTable({
  server,
  dbPath,
  perf,
  charted,
  onChart,
}: {
  server: string;
  dbPath: string;
  perf: ArchivedBotPerformance;
  charted: string | null;
  onChart: (controllerId: string | null) => void;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["archived-controllers", server, dbPath],
    queryFn: () => api.getArchivedControllers(server, dbPath),
    enabled: !!server && !!dbPath,
    staleTime: Infinity,
  });

  const controllers: ArchivedControllerRollup[] = data?.controllers ?? [];
  const symbol = runSymbol(perf);

  // Whether the split adds up to the run's own header. It need not: a run whose
  // headline came from its trade rows is reconstructed from a different source
  // than these rows, and archived trades carry no controller to roll up by.
  const reconciles =
    perf.stats_source === "executors" ||
    ([
      [controllers.reduce((sum, c) => sum + c.pnl_usd, 0), perf.total_pnl],
      [controllers.reduce((sum, c) => sum + c.volume_usd, 0), perf.total_volume],
    ] as const).every(
      ([parts, whole]) =>
        Math.abs(parts - whole) <= Math.max(0.01, Math.abs(whole) * 0.01),
    );
  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-16">
        <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />
      </div>
    );
  }
  if (controllers.length === 0) return null;

  return (
    <div>
      <h3 className="text-xs font-medium text-[var(--color-text-muted)] mb-2 uppercase tracking-wider">
        Controllers ({controllers.length})
      </h3>
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-[var(--color-text-muted)]">
              <th className="px-3 py-2 text-left font-medium">Controller</th>
              <th className="px-3 py-2 text-left font-medium">Pairs</th>
              <th className="px-3 py-2 text-right font-medium">PnL</th>
              <th className="px-3 py-2 text-right font-medium">Volume</th>
              <th className="px-3 py-2 text-right font-medium">Fees</th>
              <th className="px-3 py-2 text-right font-medium">Executors</th>
              <th className="px-3 py-2 text-right font-medium">Chart</th>
            </tr>
          </thead>
          <tbody>
            {controllers.map((c) => {
              const unattributed = !c.controller_id;
              return (
                <tr
                  key={c.controller_id || "__none__"}
                  className={`border-b border-[var(--color-border)]/50 hover:bg-[var(--color-surface-hover)] ${
                    charted === c.controller_id && !unattributed
                      ? "bg-[var(--color-primary)]/10"
                      : ""
                  }`}
                >
                  <td className="px-3 py-1.5 font-mono">
                    {c.controller_id || (
                      <span className="text-[var(--color-text-muted)] italic">
                        no controller
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5">{c.trading_pairs.join(", ") || "—"}</td>
                  <td className={`px-3 py-1.5 text-right font-mono ${pnlTextClass(c.pnl_usd)}`}>
                    {formatCurrencyPnl(c.pnl_usd, symbol)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono">
                    {formatCurrencyVolume(c.volume_usd, symbol)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-amber-400/80">
                    {formatCurrencyVolume(c.fees_usd, symbol)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono">
                    {c.executor_count.toLocaleString()}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <button
                      onClick={() => onChart(unattributed ? null : c.controller_id)}
                      title={
                        unattributed
                          ? "These executors ran under no controller — the run chart is their chart"
                          : `Chart ${c.controller_id}`
                      }
                      className="p-1 rounded hover:bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    >
                      <BarChart3 className="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!reconciles && (
        <p className="mt-1.5 text-[10px] text-[var(--color-text-muted)]">
          Rolled up from this run's executors. The cards above come from its
          trade rows, which carry no controller — for this run the two do not
          total the same.
        </p>
      )}
    </div>
  );
}

// ── Report ──

/**
 * The chart for this run, or for one controller of it.
 *
 * There is one thing that charts an archived run in Condor — the
 * ``archived_analyzer`` routine — and this is one of its callers (FEAT-079).
 * An archived run is immutable, so the report it produces is stored against
 * that (server, db, controller) and found again on the next open: the first
 * chart costs a run of the routine, every open after it is a lookup.
 */
function ArchivedReportPanel({
  server,
  dbPath,
  controllerId,
  label,
}: {
  server: string;
  dbPath: string;
  controllerId: string | null;
  label: string;
}) {
  const { reportId, isLoading, isRunning, error, chart, regenerate } =
    useArchivedReport(server, dbPath, controllerId ?? "");

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
          Chart — {label}
        </h3>
        {reportId && !isRunning && (
          <button
            onClick={regenerate}
            className="flex items-center gap-1 text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            <RefreshCw className="h-3 w-3" /> Regenerate
          </button>
        )}
      </div>

      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
        {isRunning ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
            <p className="text-xs text-[var(--color-text-muted)]">
              Charting {label}…
            </p>
          </div>
        ) : reportId ? (
          // The height goes on the wrapper, never on the frame: ReportFrame's
          // iframe is `h-full`, which resolves to nothing against an auto-height
          // parent and collapses the report to its top few pixels.
          <div className="h-[640px]">
            <ReportFrame reportId={reportId} title={`Archived run — ${label}`} />
          </div>
        ) : isLoading ? (
          <div className="flex items-center justify-center h-48">
            <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <p className="text-xs text-[var(--color-text-muted)]">
              No chart for {label} yet.
            </p>
            <button
              onClick={chart}
              className="flex items-center gap-2 rounded px-3 py-1.5 text-xs bg-[var(--color-primary)]/20 text-[var(--color-primary)] border border-[var(--color-primary)]/40 hover:bg-[var(--color-primary)]/30"
            >
              <BarChart3 className="h-3.5 w-3.5" /> Chart
            </button>
            {error && <p className="text-[10px] text-[var(--color-red)]">{error}</p>}
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
export function ArchivedBotDetail({ dbPath, botName, onBack }: Props) {
  const { server } = useServer();

  // Query 1: Performance summary (no executors) — fast path
  const { data: perf, isLoading, error } = useQuery({
    queryKey: ["archived-performance", server, dbPath],
    queryFn: () => api.getArchivedBotPerformance(server!, dbPath, false),
    enabled: !!server,
    staleTime: Infinity,
  });

  const executorCount = perf?.executor_count ?? 0;

  // Which subject the chart panel is showing: the whole run, or one controller
  // of it. A controller is charted by *naming* it — the report is stored under
  // that subject, so switching back to the run finds the run's own chart rather
  // than re-deriving one.
  const [chartedController, setChartedController] = useState<string | null>(null);

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
  const symbol = runSymbol(perf);

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
          {formatCurrencyPnl(perf.total_pnl, symbol)}
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <StatCard
          label="Total PnL"
          value={formatCurrencyPnl(perf.total_pnl, symbol)}
          className={pnlTextClass(perf.total_pnl)}
        />
        <StatCard label="Volume" value={formatCurrencyVolume(perf.total_volume, symbol)} />
        <StatCard label="Fees" value={formatCurrencyVolume(perf.total_fees, symbol)} />
        <StatCard
          label={perf.stats_source === "executors" ? "Executors" : "Trades"}
          value={perf.trade_count.toLocaleString()}
        />
        <StatCard label="Buy / Sell" value={`${perf.buy_count} / ${perf.sell_count}`} />
        <StatCard label="Pairs" value={String(perf.trading_pairs.length)} />
      </div>

      <ConversionNote perf={perf} />

      {/* The chart: the routine's report, looked up or generated once */}
      {server && (
        <ArchivedReportPanel
          server={server}
          dbPath={dbPath}
          controllerId={chartedController}
          label={chartedController ?? "whole run"}
        />
      )}

      {/* Controllers that ran inside this run */}
      {server && (
        <ControllerTable
          server={server}
          dbPath={dbPath}
          perf={perf}
          charted={chartedController}
          onChart={setChartedController}
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
              <PnlByPairBar key={pair} pair={pair} pnl={pnl} maxAbs={maxAbsPnl} symbol={symbol} />
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
          symbol={symbol}
        />
      )}
    </div>
  );
}
