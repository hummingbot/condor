import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Check,
  ChevronDown,
  ChevronRight,
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

import {
  DetailPanel,
  ExecutorTable,
  type SortDir,
  type SortKey,
} from "@/components/executor/ExecutorTable";
import { useRates } from "@/hooks/useRates";
import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { useServer } from "@/hooks/useServer";
import { api, type ExecutorInfo } from "@/lib/api";
import {
  pnlColor,
  isExecutorActive,
  formatCurrency,
  formatCurrencyPnl,
  formatCurrencyVolume,
} from "@/lib/formatters";

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
  const PAGE_SIZE = 500;
  const [maxPages, setMaxPages] = useState<number>(4); // 4 * 500 = 2000 cap by default
  const [historyCollapsed, setHistoryCollapsed] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [pendingStopIds, setPendingStopIds] = useState<string[] | null>(null);
  const [kpiPeriod, setKpiPeriod] = useState<string>("3M");

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

  // Currency conversion
  const quoteCurrencies = useMemo(
    () => executors.map((ex) => ex.trading_pair?.split("-")[1] || "USDT"),
    [executors],
  );
  const { convert, formatPnlValue, formatValue, formatValueDetailed, currencySymbol } = useRates(quoteCurrencies);

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

  // Aggregate stats (archived only for win rate), filtered by kpiPeriod
  const activePnl = useMemo(() => activeExecutors.reduce((s, ex) => s + convert(ex.pnl, ex.trading_pair?.split("-")[1] || "USDT").value, 0), [activeExecutors, convert]);
  const activeVolume = useMemo(() => activeExecutors.reduce((s, ex) => s + convert(ex.volume, ex.trading_pair?.split("-")[1] || "USDT").value, 0), [activeExecutors, convert]);

  const periodFilteredArchived = useMemo(() => {
    const now = Date.now() / 1000;
    const cutoff =
      kpiPeriod === "1W" ? now - 7 * 86400 :
      kpiPeriod === "1M" ? now - 30 * 86400 :
      now - 90 * 86400;
    return archivedExecutors.filter((ex) => ex.timestamp >= cutoff);
  }, [archivedExecutors, kpiPeriod]);

  const archivedPnl = useMemo(() => periodFilteredArchived.reduce((s, ex) => s + convert(ex.pnl, ex.trading_pair?.split("-")[1] || "USDT").value, 0), [periodFilteredArchived, convert]);
  const archivedVolume = useMemo(() => periodFilteredArchived.reduce((s, ex) => s + convert(ex.volume, ex.trading_pair?.split("-")[1] || "USDT").value, 0), [periodFilteredArchived, convert]);
  const archivedFees = useMemo(() => periodFilteredArchived.reduce((s, ex) => s + convert(ex.cum_fees_quote, ex.trading_pair?.split("-")[1] || "USDT").value, 0), [periodFilteredArchived, convert]);
  const winRate = useMemo(() => {
    if (periodFilteredArchived.length === 0) return 0;
    return periodFilteredArchived.filter((ex) => ex.pnl > 0).length / periodFilteredArchived.length;
  }, [periodFilteredArchived]);

  // Selection helpers
  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Toggle select-all for a specific table's executors, preserving selections in the other table.
  const toggleSelectAll = useCallback((tableExecutors: ExecutorInfo[]) => {
    setSelectedIds((prev) => {
      const allSel = tableExecutors.length > 0 && tableExecutors.every((ex) => prev.has(ex.id));
      const next = new Set(prev);
      if (allSel) {
        tableExecutors.forEach((ex) => next.delete(ex.id));
      } else {
        tableExecutors.forEach((ex) => next.add(ex.id));
      }
      return next;
    });
  }, []);

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
            onClick={() => setMaxPages((p) => p + 4)}
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
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => navigate("/trade")}
            className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] hover:bg-[var(--color-primary)]/10 transition-colors"
          >
            <Plus className="h-3.5 w-3.5" />
            New Executor
          </button>
          <button
            onClick={() => exportCsv(filteredExecutors)}
            className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] hover:border-[var(--color-primary)] hover:text-[var(--color-primary)] hover:bg-[var(--color-primary)]/10 transition-colors"
            title="Export all to CSV"
          >
            <Download className="h-3.5 w-3.5" />
            Export CSV
          </button>
        </div>
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
          {/* ── Performance Summary ── */}
          {archivedExecutors.length > 0 && (
            <div className="flex items-stretch rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden divide-x divide-[var(--color-border)]">
              <div className="flex-1 px-4 py-3">
                <div className="flex items-center gap-2 mb-1">
                  <TrendingUp className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                  <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Total PnL</span>
                </div>
                <p className="text-xl font-bold tabular-nums" style={{ color: pnlColor(archivedPnl) }}>
                  {formatCurrencyPnl(archivedPnl, currencySymbol)}
                </p>
              </div>
              <div className="flex-1 px-4 py-3">
                <div className="flex items-center gap-2 mb-1">
                  <Percent className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                  <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Win Rate</span>
                </div>
                <p className="text-xl font-bold tabular-nums">
                  {winRate > 0 ? (winRate * 100).toFixed(1) + "%" : "\u2014"}
                </p>
              </div>
              <div className="flex-1 px-4 py-3">
                <div className="flex items-center gap-2 mb-1">
                  <Volume2 className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                  <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Total Volume</span>
                </div>
                <p className="text-xl font-bold tabular-nums">{formatCurrencyVolume(archivedVolume, currencySymbol)}</p>
              </div>
              <div className="flex-1 px-4 py-3">
                <div className="flex items-center gap-2 mb-1">
                  <Layers className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                  <span className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Total Fees</span>
                </div>
                <p className="text-xl font-bold tabular-nums">{archivedFees > 0 ? formatCurrency(archivedFees, currencySymbol) : "\u2014"}</p>
              </div>
              <div className="flex items-center px-3 shrink-0">
                <div className="flex gap-0.5 rounded-md border border-[var(--color-border)] p-0.5 bg-[var(--color-bg)]">
                  {(["1W", "1M", "3M"] as const).map((p) => (
                    <button
                      key={p}
                      onClick={() => setKpiPeriod(p)}
                      className={`px-2 py-1 text-[10px] rounded font-medium transition-colors ${
                        kpiPeriod === p
                          ? "bg-[var(--color-accent)] text-white shadow-sm"
                          : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                      }`}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              </div>
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
                      {formatCurrencyPnl(activePnl, currencySymbol)}
                    </span>
                  )}
                  {activeVolume > 0 && (
                    <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
                      {formatCurrencyVolume(activeVolume, currencySymbol)} vol
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
                onSelectAll={() => toggleSelectAll(activeExecutors)}
                allSelected={activeExecutors.length > 0 && activeExecutors.every((ex) => selectedIds.has(ex.id))}
                onRowClick={setSelectedExecutor}
                selectedExecutorId={selectedExecutor?.id ?? null}
                onStop={handleStopOne}
                stoppingIds={stoppingIds}
                rateFormatPnl={formatPnlValue}
                rateFormatValue={formatValue}
                rateFormatDetailed={formatValueDetailed}
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
                  {formatCurrencyPnl(archivedPnl, currencySymbol)}
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
                  onSelectAll={() => toggleSelectAll(archivedExecutors)}
                  allSelected={archivedExecutors.length > 0 && archivedExecutors.every((ex) => selectedIds.has(ex.id))}
                  onRowClick={setSelectedExecutor}
                  selectedExecutorId={selectedExecutor?.id ?? null}
                  onStop={handleStopOne}
                  stoppingIds={stoppingIds}
                  rateFormatPnl={formatPnlValue}
                  rateFormatValue={formatValue}
                  rateFormatDetailed={formatValueDetailed}
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
          rateFormatPnl={formatPnlValue}
          rateFormatValue={formatValue}
          rateFormatDetailed={formatValueDetailed}
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
