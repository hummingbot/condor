import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Circle,
  Clock,
  Download,
  Filter,
  Layers,
  Percent,
  Plus,
  Square,
  TrendingUp,
  Volume2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { useServer } from "@/hooks/useServer";
import { api, type ExecutorInfo } from "@/lib/api";

// ── Formatters ──

export function formatUsd(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return val.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

export function formatVolume(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(1) + "M";
  if (Math.abs(val) >= 1_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return "$" + val.toFixed(0);
}

export function formatPnl(val: number) {
  const prefix = val >= 0 ? "+" : "";
  return prefix + formatUsd(val);
}

export function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

export function formatAge(timestamp: number): string {
  if (!timestamp) return "\u2014";
  try {
    const now = Date.now();
    const diffMs = now - timestamp * 1000;
    if (diffMs < 0) return "\u2014";
    const days = Math.floor(diffMs / 86400000);
    const hours = Math.floor((diffMs % 86400000) / 3600000);
    if (days > 0) return `${days}d ${hours}h`;
    const mins = Math.floor((diffMs % 3600000) / 60000);
    if (hours > 0) return `${hours}h ${mins}m`;
    if (mins > 0) return `${mins}m`;
    return "<1m";
  } catch {
    return "\u2014";
  }
}

export function formatPrice(val: number): string {
  if (!val) return "\u2014";
  if (val >= 1000) return val.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (val >= 1) return val.toFixed(4);
  return val.toPrecision(4);
}

export function formatPct(val: number): string {
  if (!val) return "\u2014";
  return (val >= 0 ? "+" : "") + (val * 100).toFixed(2) + "%";
}

export function isExecutorActive(status: string) {
  return status === "active" || status === "running";
}

// ── Multi-select dropdown ──

function MultiSelect({
  options,
  selected,
  onChange,
  placeholder,
  label,
}: {
  options: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
  placeholder: string;
  label?: (value: string) => string;
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

  const toggle = (value: string) => {
    if (selected.includes(value)) {
      onChange(selected.filter((v) => v !== value));
    } else {
      onChange([...selected, value]);
    }
  };

  const display = label ?? ((v: string) => v);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-left text-sm transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
      >
        <span className="truncate max-w-[180px] text-[var(--color-text)]">
          {selected.length === 0
            ? placeholder
            : selected.length === 1
              ? display(selected[0])
              : `${selected.length} selected`}
        </span>
        {selected.length > 0 && (
          <span
            className="flex h-4 w-4 items-center justify-center rounded-full bg-[var(--color-primary)]/15 text-[10px] font-bold text-[var(--color-primary)]"
          >
            {selected.length}
          </span>
        )}
        <ChevronDown className={`h-3.5 w-3.5 flex-shrink-0 text-[var(--color-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="absolute left-0 z-50 mt-1 max-h-64 w-max min-w-full overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] shadow-lg">
          {selected.length > 0 && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="flex w-full items-center gap-2 border-b border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              <X className="h-3 w-3" />
              Clear all
            </button>
          )}
          {options.map((opt) => {
            const isActive = selected.includes(opt);
            return (
              <button
                key={opt}
                type="button"
                onClick={() => toggle(opt)}
                className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                    : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className={`flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-colors ${
                  isActive
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-white"
                    : "border-[var(--color-border)]"
                }`}>
                  {isActive && <Check className="h-3 w-3" />}
                </div>
                <span className="truncate">{display(opt)}</span>
              </button>
            );
          })}
          {options.length === 0 && (
            <div className="px-3 py-2 text-xs text-[var(--color-text-muted)]">No options</div>
          )}
        </div>
      )}
    </div>
  );
}

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

// ── Stat Card ──

function StatCard({
  label,
  value,
  icon: Icon,
  valueColor,
}: {
  label: string;
  value: string;
  icon: React.ComponentType<{ className?: string }>;
  valueColor?: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">
          {label}
        </span>
      </div>
      <p
        className="text-xl font-bold tabular-nums"
        style={valueColor ? { color: valueColor } : {}}
      >
        {value}
      </p>
    </div>
  );
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

// ── CSV Export ──

function exportCsv(executors: ExecutorInfo[], filename = "executors.csv") {
  const headers = [
    "ID", "Type", "Controller", "Connector", "Pair", "Side", "Status", "Close Type",
    "PnL", "PnL%", "Volume", "Fees", "Entry Price", "Current Price", "Timestamp",
  ];
  const rows = executors.map((ex) => [
    ex.id,
    ex.type,
    ex.controller_id,
    ex.connector,
    ex.trading_pair,
    ex.side,
    ex.status,
    ex.close_type,
    ex.pnl.toFixed(4),
    ex.net_pnl_pct ? (ex.net_pnl_pct * 100).toFixed(2) + "%" : "",
    ex.volume.toFixed(2),
    ex.cum_fees_quote.toFixed(4),
    ex.entry_price || "",
    ex.current_price || "",
    ex.timestamp ? new Date(ex.timestamp * 1000).toISOString() : "",
  ]);
  const csv = [headers, ...rows].map((r) => r.map((c) => `"${c}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── Controller Card ──

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
}) {
  const sorted = useMemo(
    () => [...executors].sort((a, b) => compareExecutors(a, b, sortKey, sortDir)),
    [executors, sortKey, sortDir],
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
              <SortHeader label="Status" sortKey="status" currentKey={sortKey} currentDir={sortDir} onSort={onSort} align="center" />
              <SortHeader label="Close Type" sortKey="close_type" currentKey={sortKey} currentDir={sortDir} onSort={onSort} />
              <SortHeader label="Age" sortKey="timestamp" currentKey={sortKey} currentDir={sortDir} onSort={onSort} align="right" />
              <th className="px-3 py-3 w-10" />
            </tr>
          </thead>
          <tbody>
            {sorted.map((ex) => {
              const isSelected = selectedExecutorId === ex.id;
              const isChecked = selectedIds.has(ex.id);
              const side = ex.side.toUpperCase();
              const pnlBorder = ex.pnl >= 0 ? "var(--color-green)" : "var(--color-red)";
              return (
                <tr
                  key={ex.id}
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
                    {formatPnl(ex.pnl)}
                  </td>
                  <td
                    className="px-4 py-2.5 text-sm text-right tabular-nums"
                    style={{ color: ex.net_pnl_pct ? pnlColor(ex.net_pnl_pct) : undefined }}
                  >
                    {formatPct(ex.net_pnl_pct)}
                  </td>
                  <td className="px-4 py-2.5 text-sm text-right tabular-nums text-[var(--color-text-muted)]">
                    {formatVolume(ex.volume)}
                  </td>
                  <td className="px-4 py-2.5 text-sm text-right tabular-nums text-[var(--color-text-muted)]">
                    {ex.cum_fees_quote ? formatUsd(ex.cum_fees_quote) : "\u2014"}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-1.5 justify-center">
                      <StatusDot status={ex.status} />
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-sm text-[var(--color-text-muted)]">
                    {ex.close_type || "\u2014"}
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
                        disabled={stoppingIds.has(ex.id)}
                        className="p-1 rounded hover:bg-[var(--color-red)]/10 text-[var(--color-text-muted)] hover:text-[var(--color-red)] transition-colors disabled:opacity-50"
                        title="Stop executor"
                      >
                        <Square className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
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
}: {
  executor: ExecutorInfo;
  server: string;
  onClose: () => void;
  onStop: (id: string) => void;
  stopping: boolean;
}) {
  const [panelWidth, setPanelWidth] = useState(480);
  const isDragging = useRef(false);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;

    const onMouseMove = (ev: MouseEvent) => {
      if (!isDragging.current) return;
      const newWidth = window.innerWidth - ev.clientX;
      setPanelWidth(Math.max(300, Math.min(newWidth, window.innerWidth * 0.8)));
    };
    const onMouseUp = () => {
      isDragging.current = false;
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }, []);

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
                  {formatPnl(executor.pnl)}
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
                <div className="font-medium tabular-nums">{formatVolume(executor.volume)}</div>
              </div>
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Fees</div>
                <div className="font-medium tabular-nums">
                  {executor.cum_fees_quote ? formatUsd(executor.cum_fees_quote) : "\u2014"}
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
                    <div className="font-medium tabular-nums">{formatUsd(Number(config.total_amount_quote))}</div>
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
                    <div className="font-medium tabular-nums">{formatUsd(Number(config.total_amount_quote))}</div>
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

// ── Bulk Action Bar ──

function BulkActionBar({
  count,
  onStop,
  onExport,
  onClear,
  stopping,
}: {
  count: number;
  onStop: () => void;
  onExport: () => void;
  onClear: () => void;
  stopping: boolean;
}) {
  if (count === 0) return null;
  return (
    <div className="flex items-center gap-3 rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 px-4 py-2.5">
      <span className="text-sm font-medium">{count} selected</span>
      <div className="flex-1" />
      <button
        onClick={onExport}
        className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <Download className="h-3.5 w-3.5" />
        Export CSV
      </button>
      <button
        onClick={onStop}
        disabled={stopping}
        className="flex items-center gap-1.5 rounded-md bg-[var(--color-red)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 transition-colors disabled:opacity-50"
      >
        <Square className="h-3.5 w-3.5" />
        {stopping ? "Stopping\u2026" : "Stop Selected"}
      </button>
      <button
        onClick={onClear}
        className="p-1 rounded hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

// ── Stop Confirm Dialog ──

function StopConfirmDialog({
  ids,
  onConfirm,
  onCancel,
}: {
  ids: string[];
  onConfirm: (ids: string[], keepPosition: boolean) => void;
  onCancel: () => void;
}) {
  const [keepPosition, setKeepPosition] = useState(false);
  const count = ids.length;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onConfirm(ids, keepPosition);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onCancel}>
      <div
        className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-xl p-6 w-full max-w-sm space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold">
          Stop {count === 1 ? "Executor" : `${count} Executors`}?
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          {count === 1
            ? "This will stop the executor."
            : `This will stop ${count} active executors.`}
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={keepPosition}
              onChange={(e) => setKeepPosition(e.target.checked)}
              className="h-4 w-4 rounded border-[var(--color-border)] accent-[var(--color-primary)]"
            />
            <span className="text-sm">Keep position open</span>
          </label>
          <p className="text-[10px] text-[var(--color-text-muted)] -mt-2 ml-6">
            {keepPosition
              ? "The executor will stop but the position will remain open on the exchange."
              : "The executor will stop and close any open position."}
          </p>

          <div className="flex items-center gap-2 justify-end">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="rounded-md bg-[var(--color-red)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 transition-colors"
            >
              Confirm Stop
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Main Page ──

export function Executors() {
  const { server } = useServer();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedExecutor, setSelectedExecutor] = useState<ExecutorInfo | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState({
    executor_types: [] as string[],
    trading_pair: "",
    controller_ids: [] as string[],
  });
  const [maxPages, setMaxPages] = useState<number>(40); // 40 * 50 = 2000 cap by default
  const PAGE_SIZE = 50;
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [pendingStopIds, setPendingStopIds] = useState<string[] | null>(null);

  // WebSocket for real-time updates
  const wsChannels = useMemo(
    () => (server ? [`executors:${server}`] : []),
    [server],
  );
  useCondorWebSocket(wsChannels, server);

  const {
    data,
    isLoading,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    refetch,
  } = useInfiniteQuery({
    queryKey: ["executors-infinite", server],
    enabled: !!server,
    initialPageParam: "" as string,
    queryFn: ({ pageParam }) =>
      api.getExecutorsPage(server!, {
        cursor: pageParam || undefined,
        limit: PAGE_SIZE,
      }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    refetchInterval: 10000,
    refetchOnWindowFocus: false,
  });

  // Progressive loading: auto-fetch next chunk as soon as current arrives.
  const loadedPages = data?.pages.length ?? 0;
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && loadedPages < maxPages) {
      fetchNextPage();
    }
  }, [hasNextPage, isFetchingNextPage, loadedPages, maxPages, fetchNextPage]);

  const stopMutation = useMutation({
    mutationFn: async ({ ids, keepPosition }: { ids: string[]; keepPosition: boolean }) => {
      setStoppingIds((prev) => new Set([...prev, ...ids]));
      const results = await Promise.allSettled(
        ids.map((id) => api.stopExecutor(server!, id, keepPosition)),
      );
      return results;
    },
    onSettled: (_data, _error, vars) => {
      setStoppingIds((prev) => {
        const next = new Set(prev);
        vars?.ids.forEach((id) => next.delete(id));
        return next;
      });
      setSelectedIds(new Set());
      queryClient.invalidateQueries({ queryKey: ["executors-infinite", server] });
    },
  });

  const handleStopOne = useCallback(
    (id: string) => setPendingStopIds([id]),
    [],
  );

  const handleConfirmStop = useCallback(
    (ids: string[], keepPosition: boolean) => {
      setPendingStopIds(null);
      stopMutation.mutate({ ids, keepPosition });
    },
    [stopMutation],
  );

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const executors = useMemo(
    () => (data?.pages.flatMap((p) => p?.executors ?? []) ?? []) as ExecutorInfo[],
    [data],
  );
  const reachedCap = loadedPages >= maxPages && hasNextPage;

  const executorTypes = useMemo(() => {
    const types = new Set(executors.map((ex) => ex.type));
    return Array.from(types).sort();
  }, [executors]);

  const controllerOptions = useMemo(() => {
    const ids = new Set<string>();
    for (const ex of executors) {
      if (ex.controller_id) ids.add(ex.controller_id);
    }
    return Array.from(ids).sort();
  }, [executors]);

  const filteredExecutors = useMemo(() => {
    let result = executors;
    if (filters.trading_pair) {
      const q = filters.trading_pair.toLowerCase();
      result = result.filter((ex) => ex.trading_pair.toLowerCase().includes(q));
    }
    if (filters.executor_types.length > 0) {
      result = result.filter((ex) => filters.executor_types.includes(ex.type));
    }
    if (filters.controller_ids.length > 0) {
      result = result.filter((ex) => ex.controller_id && filters.controller_ids.includes(ex.controller_id));
    }
    return result;
  }, [executors, filters.trading_pair, filters.executor_types, filters.controller_ids]);

  // Split into active and archived
  const activeExecutors = useMemo(
    () => filteredExecutors.filter((ex) => isExecutorActive(ex.status)),
    [filteredExecutors],
  );
  const archivedExecutors = useMemo(
    () => filteredExecutors.filter((ex) => !isExecutorActive(ex.status)),
    [filteredExecutors],
  );

  // Aggregate stats (archived only for win rate)
  const activePnl = useMemo(() => activeExecutors.reduce((s, ex) => s + ex.pnl, 0), [activeExecutors]);
  const activeVolume = useMemo(() => activeExecutors.reduce((s, ex) => s + ex.volume, 0), [activeExecutors]);
  const archivedPnl = useMemo(() => archivedExecutors.reduce((s, ex) => s + ex.pnl, 0), [archivedExecutors]);
  const archivedVolume = useMemo(() => archivedExecutors.reduce((s, ex) => s + ex.volume, 0), [archivedExecutors]);
  const archivedFees = useMemo(() => archivedExecutors.reduce((s, ex) => s + ex.cum_fees_quote, 0), [archivedExecutors]);
  const winRate = useMemo(() => {
    if (archivedExecutors.length === 0) return 0;
    return archivedExecutors.filter((ex) => ex.pnl > 0).length / archivedExecutors.length;
  }, [archivedExecutors]);

  // Selection helpers
  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const currentTableExecutors = useMemo(() => archivedExecutors, [archivedExecutors]);

  const allSelected = currentTableExecutors.length > 0 && currentTableExecutors.every((ex) => selectedIds.has(ex.id));

  const selectAll = useCallback(() => {
    if (allSelected) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(currentTableExecutors.map((ex) => ex.id)));
    }
  }, [allSelected, currentTableExecutors]);

  const handleBulkStop = useCallback(() => {
    const activeIds = Array.from(selectedIds).filter((id) => {
      const ex = executors.find((e) => e.id === id);
      return ex && isExecutorActive(ex.status);
    });
    if (activeIds.length > 0) {
      setPendingStopIds(activeIds);
    }
  }, [selectedIds, executors]);

  const handleBulkExport = useCallback(() => {
    const selected = executors.filter((ex) => selectedIds.has(ex.id));
    exportCsv(selected.length > 0 ? selected : filteredExecutors);
  }, [selectedIds, executors, filteredExecutors]);

  if (!server)
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;

  return (
    <div className="flex gap-0 -m-6 h-[calc(100vh)] overflow-hidden">
      {/* Main content */}
      <div className={`flex-1 overflow-auto p-6 transition-all duration-200 ${selectedExecutor ? "min-w-0" : ""}`}>
      <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h2 className="text-xl font-bold">Executors</h2>
        <div className="flex items-center gap-2">
          {/* New Executor button */}
          <button
            onClick={() => navigate("/trade")}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-colors hover:brightness-110"
          >
            <Plus className="h-3.5 w-3.5" />
            New Executor
          </button>
          {/* Export all */}
          <button
            onClick={() => exportCsv(filteredExecutors)}
            className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
            title="Export all to CSV"
          >
            <Download className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-2 flex-wrap items-center">
        <Filter className="h-4 w-4 text-[var(--color-text-muted)]" />
        <input
          type="text"
          placeholder="Filter pair..."
          value={filters.trading_pair}
          onChange={(e) =>
            setFilters((f) => ({ ...f, trading_pair: e.target.value }))
          }
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-sm transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
        />
        <MultiSelect
          options={executorTypes}
          selected={filters.executor_types}
          onChange={(v) => setFilters((f) => ({ ...f, executor_types: v }))}
          placeholder="All types"
        />
        <MultiSelect
          options={controllerOptions}
          selected={filters.controller_ids}
          onChange={(v) => setFilters((f) => ({ ...f, controller_ids: v }))}
          placeholder="All controllers"
        />
        <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
          {executors.length} loaded
          {isFetchingNextPage && " · loading…"}
          {!hasNextPage && !isFetchingNextPage && executors.length > 0 && " · done"}
          {reachedCap && " · cap reached"}
        </span>
        {reachedCap && (
          <button
            onClick={() => setMaxPages((p) => p + 40)}
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
          >
            Load more
          </button>
        )}
        <button
          onClick={() => refetch()}
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
          title="Refresh"
        >
          Refresh
        </button>
      </div>

      {isLoading ? (
        <p className="text-[var(--color-text-muted)]">Loading...</p>
      ) : error ? (
        <p className="text-[var(--color-red)]">
          {error instanceof Error ? error.message : "Error"}
        </p>
      ) : !filteredExecutors.length ? (
        <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
          <Activity className="h-10 w-10" />
          <p>No executors found</p>
        </div>
      ) : (
        <>
          {/* ── Performance Summary (always visible at top) ── */}
          {archivedExecutors.length > 0 && (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatCard
                label="Total PnL"
                value={formatPnl(archivedPnl)}
                icon={TrendingUp}
                valueColor={pnlColor(archivedPnl)}
              />
              <StatCard
                label="Win Rate"
                value={winRate > 0 ? (winRate * 100).toFixed(1) + "%" : "\u2014"}
                icon={Percent}
              />
              <StatCard label="Total Volume" value={formatVolume(archivedVolume)} icon={Volume2} />
              <StatCard label="Total Fees" value={archivedFees > 0 ? formatUsd(archivedFees) : "\u2014"} icon={Layers} />
            </div>
          )}

          {/* ── Active Executors ── */}
          {activeExecutors.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-[var(--color-green)] animate-pulse" />
                  <h3 className="text-sm font-semibold">
                    Active ({activeExecutors.length})
                  </h3>
                  {activePnl !== 0 && (
                    <span className="text-sm font-medium tabular-nums" style={{ color: pnlColor(activePnl) }}>
                      {formatPnl(activePnl)}
                    </span>
                  )}
                  {activeVolume > 0 && (
                    <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
                      {formatVolume(activeVolume)} vol
                    </span>
                  )}
                </div>
              </div>
              <ExecutorTable
                executors={activeExecutors}
                sortKey={sortKey}
                sortDir={sortDir}
                onSort={handleSort}
                selectedIds={selectedIds}
                onToggleSelect={toggleSelect}
                onSelectAll={selectAll}
                allSelected={activeExecutors.length > 0 && activeExecutors.every((ex) => selectedIds.has(ex.id))}
                onRowClick={setSelectedExecutor}
                selectedExecutorId={selectedExecutor?.id ?? null}
                onStop={handleStopOne}
                stoppingIds={stoppingIds}
              />
            </div>
          )}

          {/* Bulk action bar */}
          <BulkActionBar
            count={selectedIds.size}
            onStop={handleBulkStop}
            onExport={handleBulkExport}
            onClear={() => setSelectedIds(new Set())}
            stopping={stopMutation.isPending}
          />

          {/* ── History ── */}
          {archivedExecutors.length > 0 && (
            <div className="space-y-3">
              <button
                onClick={() => setHistoryCollapsed((v) => !v)}
                className="flex items-center gap-2 hover:text-[var(--color-text)] transition-colors"
              >
                {historyCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                <h3 className="text-sm font-semibold">
                  History ({archivedExecutors.length})
                </h3>
                <span className="text-sm font-medium tabular-nums" style={{ color: pnlColor(archivedPnl) }}>
                  {formatPnl(archivedPnl)}
                </span>
                {winRate > 0 && (
                  <span className="text-xs text-[var(--color-text-muted)]">
                    {(winRate * 100).toFixed(0)}% WR
                  </span>
                )}
              </button>

              {!historyCollapsed && (
                <ExecutorTable
                  executors={archivedExecutors}
                  sortKey={sortKey}
                  sortDir={sortDir}
                  onSort={handleSort}
                  selectedIds={selectedIds}
                  onToggleSelect={toggleSelect}
                  onSelectAll={selectAll}
                  allSelected={allSelected}
                  onRowClick={setSelectedExecutor}
                  selectedExecutorId={selectedExecutor?.id ?? null}
                  onStop={handleStopOne}
                  stoppingIds={stoppingIds}
                />
              )}
            </div>
          )}
        </>
      )}

      </div>
      </div>

      {/* Detail panel */}
      {selectedExecutor && (
        <DetailPanel
          executor={selectedExecutor}
          server={server!}
          onClose={() => setSelectedExecutor(null)}
          onStop={handleStopOne}
          stopping={stoppingIds.has(selectedExecutor.id)}
        />
      )}

      {/* Stop confirmation dialog */}
      {pendingStopIds && (
        <StopConfirmDialog
          ids={pendingStopIds}
          onConfirm={handleConfirmStop}
          onCancel={() => setPendingStopIds(null)}
        />
      )}
    </div>
  );
}
