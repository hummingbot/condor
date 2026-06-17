import {
  ChevronDown,
  ChevronUp,
  Circle,
  Clock,
  Square,
  TrendingUp,
  X,
} from "lucide-react";
import { memo, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { useResizeDrag } from "@/hooks/useResizeDrag";
import { type ExecutorInfo } from "@/lib/api";
import {
  formatUsd,
  formatVolume,
  formatPnl,
  pnlColor,
  formatAge,
  formatPrice,
  formatPct,
  isExecutorActive,
} from "@/lib/formatters";

// ── Sort types ──

export type SortKey =
  | "id"
  | "type"
  | "connector"
  | "trading_pair"
  | "side"
  | "pnl"
  | "net_pnl_pct"
  | "volume"
  | "cum_fees_quote"
  | "status"
  | "close_type"
  | "timestamp";

export type SortDir = "asc" | "desc";

function compareExecutors(a: ExecutorInfo, b: ExecutorInfo, key: SortKey, dir: SortDir): number {
  let cmp = 0;
  switch (key) {
    case "id":
    case "type":
    case "connector":
    case "trading_pair":
    case "side":
    case "status":
    case "close_type":
      cmp = (a[key] || "").localeCompare(b[key] || "");
      break;
    case "pnl":
    case "net_pnl_pct":
    case "volume":
    case "cum_fees_quote":
    case "timestamp":
      cmp = (a[key] || 0) - (b[key] || 0);
      break;
  }
  return dir === "asc" ? cmp : -cmp;
}

// ── Status Dot ──

export function StatusDot({ status }: { status: string }) {
  const color =
    isExecutorActive(status)
      ? "text-[var(--color-green)]"
      : status === "failed" || status === "error"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-text-muted)]";
  return <Circle className={`h-2 w-2 fill-current ${color}`} />;
}

// ── Sortable Header ──

export function SortHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  onSort,
  align = "left",
}: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  onSort: (key: SortKey) => void;
  align?: "left" | "right" | "center";
}) {
  const active = currentKey === sortKey;
  const alignCls =
    align === "right" ? "text-right justify-end" : align === "center" ? "text-center justify-center" : "text-left";

  return (
    <th
      className={`px-4 py-3 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] cursor-pointer select-none hover:text-[var(--color-text)] transition-colors ${alignCls}`}
      onClick={() => onSort(sortKey)}
    >
      <div className={`flex items-center gap-1 ${align === "right" ? "justify-end" : align === "center" ? "justify-center" : ""}`}>
        {label}
        {active ? (
          currentDir === "asc" ? (
            <ChevronUp className="h-3 w-3" />
          ) : (
            <ChevronDown className="h-3 w-3" />
          )
        ) : (
          <span className="w-3" />
        )}
      </div>
    </th>
  );
}

// ── Executor Row (memoized) ──

type RowFormatter = (val: number, quote: string) => string;

const ExecutorRow = memo(function ExecutorRow({
  ex,
  isSelected,
  isChecked,
  isStopping,
  onRowClick,
  onToggleSelect,
  onStop,
  fmtPnl,
  fmtVol,
  fmtDet,
}: {
  ex: ExecutorInfo;
  isSelected: boolean;
  isChecked: boolean;
  isStopping: boolean;
  onRowClick: (ex: ExecutorInfo) => void;
  onToggleSelect: (id: string) => void;
  onStop: (id: string) => void;
  fmtPnl: RowFormatter;
  fmtVol: RowFormatter;
  fmtDet: RowFormatter;
}) {
  const side = ex.side.toUpperCase();
  const pnlBorder = ex.pnl >= 0 ? "var(--color-green)" : "var(--color-red)";
  const quote = ex.trading_pair?.split("-")[1] || "USDT";
  return (
    <tr
      className={`border-b border-[var(--color-border)]/30 hover:bg-[var(--color-surface-hover)]/50 cursor-pointer transition-colors ${isSelected ? "bg-[var(--color-surface-hover)]/70" : ""}`}
      style={{ borderLeft: `3px solid ${pnlBorder}` }}
      onClick={() => onRowClick(ex)}
    >
      <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={isChecked}
          onChange={() => onToggleSelect(ex.id)}
          className="rounded border-[var(--color-border)]"
        />
      </td>
      <td className="px-4 py-2.5 text-xs font-mono text-[var(--color-text-muted)]" title={ex.id}>
        {ex.id.slice(0, 8)}
      </td>
      <td className="px-4 py-2.5">
        <span className="rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
          {ex.type}
        </span>
      </td>
      <td className="px-4 py-2.5 text-sm text-[var(--color-text-muted)]">
        {ex.connector}
      </td>
      <td className="px-4 py-2.5 text-sm font-medium">{ex.trading_pair}</td>
      <td className="px-4 py-2.5">
        <span
          className="text-xs font-semibold uppercase"
          style={{
            color: side === "BUY" || side === "1" ? "var(--color-green)" : "var(--color-red)",
          }}
        >
          {side}
        </span>
      </td>
      <td
        className="px-4 py-2.5 text-sm text-right tabular-nums font-medium"
        style={{ color: pnlColor(ex.pnl) }}
      >
        {fmtPnl(ex.pnl, quote)}
      </td>
      <td
        className="px-4 py-2.5 text-sm text-right tabular-nums"
        style={{ color: ex.net_pnl_pct ? pnlColor(ex.net_pnl_pct) : undefined }}
      >
        {formatPct(ex.net_pnl_pct)}
      </td>
      <td className="px-4 py-2.5 text-sm text-right tabular-nums text-[var(--color-text-muted)]">
        {fmtVol(ex.volume, quote)}
      </td>
      <td className="px-4 py-2.5 text-sm text-right tabular-nums text-[var(--color-text-muted)]">
        {ex.cum_fees_quote ? fmtDet(ex.cum_fees_quote, quote) : "—"}
      </td>
      <td className="px-4 py-2.5 text-sm text-[var(--color-text-muted)]">
        {ex.close_type || "—"}
      </td>
      <td className="px-4 py-2.5 text-sm text-right tabular-nums text-[var(--color-text-muted)]">
        <div className="flex items-center gap-1 justify-end">
          <Clock className="h-3 w-3" />
          {formatAge(ex.timestamp)}
        </div>
      </td>
      <td className="px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
        {isExecutorActive(ex.status) && (
          <button
            onClick={() => onStop(ex.id)}
            disabled={isStopping}
            className="p-1 rounded hover:bg-[var(--color-red)]/10 text-[var(--color-text-muted)] hover:text-[var(--color-red)] transition-colors disabled:opacity-50"
            title="Stop executor"
          >
            <Square className="h-3.5 w-3.5" />
          </button>
        )}
      </td>
    </tr>
  );
});

// ── Executor Table ──

export function ExecutorTable({
  executors,
  sortKey,
  sortDir,
  onSort,
  selectedIds,
  onToggleSelect,
  onSelectAll,
  allSelected,
  onRowClick,
  selectedExecutorId,
  onStop,
  stoppingIds,
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
}: {
  executors: ExecutorInfo[];
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  selectedIds: Set<string>;
  onToggleSelect: (id: string) => void;
  onSelectAll: () => void;
  allSelected: boolean;
  onRowClick: (ex: ExecutorInfo) => void;
  selectedExecutorId: string | null;
  onStop: (id: string) => void;
  stoppingIds: Set<string>;
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
}) {
  const sorted = useMemo(
    () => [...executors].sort((a, b) => compareExecutors(a, b, sortKey, sortDir)),
    [executors, sortKey, sortDir],
  );

  const fmtPnl = useMemo<RowFormatter>(
    () => rateFormatPnl ?? ((val: number) => formatPnl(val)),
    [rateFormatPnl],
  );
  const fmtVol = useMemo<RowFormatter>(
    () => rateFormatValue ?? ((val: number) => formatVolume(val)),
    [rateFormatValue],
  );
  const fmtDet = useMemo<RowFormatter>(
    () => rateFormatDetailed ?? ((val: number) => formatUsd(val)),
    [rateFormatDetailed],
  );

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
              <th className="px-3 py-3 w-8">
                <input
                  type="checkbox"
                  checked={allSelected && executors.length > 0}
                  onChange={onSelectAll}
                  className="rounded border-[var(--color-border)]"
                />
              </th>
              <SortHeader label="ID" sortKey="id" currentKey={sortKey} currentDir={sortDir} onSort={onSort} />
              <SortHeader label="Type" sortKey="type" currentKey={sortKey} currentDir={sortDir} onSort={onSort} />
              <SortHeader label="Connector" sortKey="connector" currentKey={sortKey} currentDir={sortDir} onSort={onSort} />
              <SortHeader label="Pair" sortKey="trading_pair" currentKey={sortKey} currentDir={sortDir} onSort={onSort} />
              <SortHeader label="Side" sortKey="side" currentKey={sortKey} currentDir={sortDir} onSort={onSort} />
              <SortHeader label="PnL" sortKey="pnl" currentKey={sortKey} currentDir={sortDir} onSort={onSort} align="right" />
              <SortHeader label="PnL%" sortKey="net_pnl_pct" currentKey={sortKey} currentDir={sortDir} onSort={onSort} align="right" />
              <SortHeader label="Volume" sortKey="volume" currentKey={sortKey} currentDir={sortDir} onSort={onSort} align="right" />
              <SortHeader label="Fees" sortKey="cum_fees_quote" currentKey={sortKey} currentDir={sortDir} onSort={onSort} align="right" />
              <SortHeader label="Close Type" sortKey="close_type" currentKey={sortKey} currentDir={sortDir} onSort={onSort} />
              <SortHeader label="Age" sortKey="timestamp" currentKey={sortKey} currentDir={sortDir} onSort={onSort} align="right" />
              <th className="px-3 py-3 w-10" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((ex) => (
              <ExecutorRow
                key={ex.id}
                ex={ex}
                isSelected={selectedExecutorId === ex.id}
                isChecked={selectedIds.has(ex.id)}
                isStopping={stoppingIds.has(ex.id)}
                onRowClick={onRowClick}
                onToggleSelect={onToggleSelect}
                onStop={onStop}
                fmtPnl={fmtPnl}
                fmtVol={fmtVol}
                fmtDet={fmtDet}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Detail Panel ──

export function DetailPanel({
  executor,
  server,
  onClose,
  onStop,
  stopping,
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
}: {
  executor: ExecutorInfo;
  server: string;
  onClose: () => void;
  onStop: (id: string) => void;
  stopping: boolean;
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
}) {
  const navigate = useNavigate();
  const [panelWidth, setPanelWidth] = useState(480);

  const { onMouseDown } = useResizeDrag({
    axis: "x",
    value: panelWidth,
    onChange: setPanelWidth,
    min: 300,
    max: () => window.innerWidth * 0.8,
    compute: (coord) => window.innerWidth - coord,
    cursor: "col-resize",
    lockUserSelect: true,
  });

  const sideLabel = executor.side.toUpperCase();
  const sideColor = sideLabel === "BUY" || sideLabel === "1" ? "var(--color-green)" : "var(--color-red)";
  const sideBg = sideLabel === "BUY" || sideLabel === "1" ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)";
  const configEntries = Object.entries(executor.config || {});
  const customEntries = Object.entries(executor.custom_info || {});

  const config = executor.config || {};
  const isPosition = executor.type === "position";
  const isGrid = executor.type === "grid";

  // Parse triple_barrier_config (may be a JSON string or object)
  const tripleBarrier: Record<string, unknown> = (() => {
    const raw = config.triple_barrier_config;
    if (!raw) return {};
    if (typeof raw === "string") {
      try { return JSON.parse(raw); } catch { return {}; }
    }
    return typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  })();

  const quote = executor.trading_pair?.split("-")[1] || "USDT";
  const fmtPnl = rateFormatPnl ? (v: number) => rateFormatPnl(v, quote) : formatPnl;
  const fmtVal = rateFormatValue ? (v: number) => rateFormatValue(v, quote) : formatUsd;
  const fmtDet = rateFormatDetailed ? (v: number) => rateFormatDetailed(v, quote) : formatUsd;

  return (
      <div
        className="h-full bg-[var(--color-bg)] border-l border-[var(--color-border)] overflow-y-auto shadow-xl shrink-0 relative"
        style={{ width: panelWidth }}
      >
        <div
          className="absolute top-0 left-0 w-1.5 h-full cursor-col-resize hover:bg-[var(--color-primary)]/30 transition-colors z-10"
          onMouseDown={onMouseDown}
        />

        <div className="sticky top-0 bg-[var(--color-bg)] border-b border-[var(--color-border)] px-5 py-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold truncate pr-4 font-mono" title={executor.id}>
            {executor.id.slice(0, 12)}\u2026
          </h2>
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate(`/trade?connector=${encodeURIComponent(executor.connector)}&pair=${encodeURIComponent(executor.trading_pair)}`)}
              className="flex items-center gap-1.5 rounded-md bg-[var(--color-surface)] border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              <TrendingUp className="h-3 w-3" />
              Trade
            </button>
            {isExecutorActive(executor.status) && (
              <button
                onClick={() => onStop(executor.id)}
                disabled={stopping}
                className="flex items-center gap-1.5 rounded-md bg-[var(--color-red)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 transition-colors disabled:opacity-50"
              >
                <Square className="h-3 w-3" />
                {stopping ? "Stopping\u2026" : "Stop"}
              </button>
            )}
            <button
              onClick={onClose}
              className="p-1 rounded hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="p-5 space-y-5">
          {/* Status & Meta */}
          <div className="flex items-center gap-3 flex-wrap text-sm">
            <div className="flex items-center gap-1.5">
              <StatusDot status={executor.status} />
              <span className="capitalize">{executor.status}</span>
            </div>
            <span className="rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
              {executor.type}
            </span>
            <span className="text-[var(--color-text-muted)]">{executor.connector}</span>
            <span>{executor.trading_pair}</span>
            <span
              className="rounded px-1.5 py-0.5 text-xs font-semibold uppercase"
              style={{ color: sideColor, background: sideBg }}
            >
              {sideLabel}
            </span>
            {executor.close_type && (
              <span className="rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
                {executor.close_type}
              </span>
            )}
          </div>

          {/* Controller ID */}
          {executor.controller_id && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-1">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Controller
              </h3>
              <div className="text-sm font-medium font-mono">{executor.controller_id}</div>
            </div>
          )}

          {/* Executor Chart */}
          {server && executor.connector && executor.trading_pair && (
            <ExecutorChart
              server={server}
              executors={[executor]}
              connector={executor.connector}
              tradingPair={executor.trading_pair}
              height={300}
            />
          )}

          {/* Price Info */}
          {(executor.entry_price > 0 || executor.current_price > 0) && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-3">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Price Info
              </h3>
              <div className="grid grid-cols-2 gap-3 text-sm">
                {executor.entry_price > 0 && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Entry</div>
                    <div className="font-medium tabular-nums">{formatPrice(executor.entry_price)}</div>
                  </div>
                )}
                {executor.current_price > 0 && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Current</div>
                    <div className="font-medium tabular-nums">{formatPrice(executor.current_price)}</div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* PnL Breakdown */}
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-3">
            <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              PnL & Volume
            </h3>
            <div className="grid grid-cols-4 gap-3 text-sm">
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Net PnL</div>
                <div className="font-medium tabular-nums" style={{ color: pnlColor(executor.pnl) }}>
                  {fmtPnl(executor.pnl)}
                </div>
              </div>
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">PnL %</div>
                <div
                  className="font-medium tabular-nums"
                  style={{ color: executor.net_pnl_pct ? pnlColor(executor.net_pnl_pct) : undefined }}
                >
                  {formatPct(executor.net_pnl_pct)}
                </div>
              </div>
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Volume</div>
                <div className="font-medium tabular-nums">{fmtVal(executor.volume)}</div>
              </div>
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Fees</div>
                <div className="font-medium tabular-nums">
                  {executor.cum_fees_quote ? fmtDet(executor.cum_fees_quote) : "\u2014"}
                </div>
              </div>
            </div>
          </div>

          {/* Position-specific details */}
          {isPosition && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-3">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Position Details
              </h3>
              <div className="grid grid-cols-2 gap-3 text-sm">
                {config.stop_loss != null && Number(config.stop_loss) !== -1 && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Stop Loss</div>
                    <div className="font-medium tabular-nums text-[var(--color-red)]">
                      {(Number(config.stop_loss) * 100).toFixed(2)}%
                    </div>
                  </div>
                )}
                {config.take_profit != null && Number(config.take_profit) !== -1 && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Take Profit</div>
                    <div className="font-medium tabular-nums text-[var(--color-green)]">
                      {(Number(config.take_profit) * 100).toFixed(2)}%
                    </div>
                  </div>
                )}
                {config.leverage != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Leverage</div>
                    <div className="font-medium tabular-nums">{String(config.leverage)}x</div>
                  </div>
                )}
                {config.total_amount_quote != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Amount</div>
                    <div className="font-medium tabular-nums">{fmtDet(Number(config.total_amount_quote))}</div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Grid-specific details */}
          {isGrid && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-3">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Grid Details
              </h3>
              <div className="grid grid-cols-2 gap-3 text-sm">
                {config.start_price != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Start Price</div>
                    <div className="font-medium tabular-nums">{formatPrice(Number(config.start_price))}</div>
                  </div>
                )}
                {config.end_price != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">End Price</div>
                    <div className="font-medium tabular-nums">{formatPrice(Number(config.end_price))}</div>
                  </div>
                )}
                {config.limit_price != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Limit Price</div>
                    <div className="font-medium tabular-nums">{formatPrice(Number(config.limit_price))}</div>
                  </div>
                )}
                {config.leverage != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Leverage</div>
                    <div className="font-medium tabular-nums">{String(config.leverage)}x</div>
                  </div>
                )}
                {config.total_amount_quote != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Amount</div>
                    <div className="font-medium tabular-nums">{fmtDet(Number(config.total_amount_quote))}</div>
                  </div>
                )}
                {tripleBarrier.take_profit != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Take Profit</div>
                    <div className="font-medium tabular-nums text-[var(--color-green)]">
                      {(Number(tripleBarrier.take_profit) * 100).toFixed(2)}%
                    </div>
                  </div>
                )}
                {config.keep_position != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Keep Position</div>
                    <div className="font-medium">{String(config.keep_position) === "true" ? "Yes" : "No"}</div>
                  </div>
                )}
                {tripleBarrier.open_order_type != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Open Order Type</div>
                    <div className="font-medium text-xs">{String(tripleBarrier.open_order_type)}</div>
                  </div>
                )}
                {tripleBarrier.take_profit_order_type != null && (
                  <div>
                    <div className="text-[var(--color-text-muted)] text-xs mb-0.5">TP Order Type</div>
                    <div className="font-medium text-xs">{String(tripleBarrier.take_profit_order_type)}</div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Timestamps */}
          {executor.timestamp > 0 && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-1">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Timing
              </h3>
              <div className="text-sm">
                <div className="flex justify-between py-0.5">
                  <span className="text-[var(--color-text-muted)]">Created</span>
                  <span className="font-medium tabular-nums">
                    {new Date(executor.timestamp * 1000).toLocaleString()} ({formatAge(executor.timestamp)} ago)
                  </span>
                </div>
                {executor.close_timestamp > 0 && (
                  <div className="flex justify-between py-0.5">
                    <span className="text-[var(--color-text-muted)]">Closed</span>
                    <span className="font-medium tabular-nums">
                      {new Date(executor.close_timestamp * 1000).toLocaleString()}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Custom Info */}
          {customEntries.length > 0 && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-2">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Custom Info
              </h3>
              <div className="space-y-1 text-xs">
                {customEntries.map(([key, val]) => (
                  <div key={key} className="flex justify-between gap-3 py-0.5">
                    <span className="text-[var(--color-text-muted)] shrink-0">{key}</span>
                    <span className="tabular-nums text-right truncate" title={String(val ?? "")}>
                      {typeof val === "object" && val !== null
                        ? JSON.stringify(val)
                        : String(val ?? "")}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Config */}
          {configEntries.length > 0 && (
            <details className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
              <summary className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] cursor-pointer select-none hover:text-[var(--color-text)]">
                Raw Config ({configEntries.length} fields)
              </summary>
              <div className="px-4 pb-3 space-y-1 text-xs">
                {configEntries.map(([key, val]) => {
                  // Parse JSON strings into objects for display
                  let parsed = val;
                  if (typeof val === "string") {
                    try { const p = JSON.parse(val); if (typeof p === "object" && p !== null) parsed = p; } catch {}
                  }
                  const isNested = typeof parsed === "object" && parsed !== null;

                  if (isNested) {
                    return (
                      <details key={key} className="py-0.5">
                        <summary className="flex justify-between gap-3 cursor-pointer hover:text-[var(--color-text)]">
                          <span className="text-[var(--color-text-muted)] shrink-0">{key}</span>
                          <span className="text-[var(--color-text-muted)]">{Object.keys(parsed as object).length} fields</span>
                        </summary>
                        <div className="ml-3 pl-3 border-l border-[var(--color-border)]/50 mt-1 space-y-0.5">
                          {Object.entries(parsed as Record<string, unknown>).map(([k, v]) => (
                            <div key={k} className="flex justify-between gap-3 py-0.5">
                              <span className="text-[var(--color-text-muted)] shrink-0">{k}</span>
                              <span className="tabular-nums text-right truncate" title={String(v ?? "")}>
                                {v == null ? "null" : String(v)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </details>
                    );
                  }

                  return (
                    <div key={key} className="flex justify-between gap-3 py-0.5">
                      <span className="text-[var(--color-text-muted)] shrink-0">{key}</span>
                      <span className="tabular-nums text-right truncate" title={String(val ?? "")}>
                        {String(val ?? "")}
                      </span>
                    </div>
                  );
                })}
              </div>
            </details>
          )}
        </div>
      </div>
  );
}
