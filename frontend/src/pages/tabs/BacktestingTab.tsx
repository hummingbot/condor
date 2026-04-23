import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Calendar,
  ChevronDown,
  Circle,
  FlaskConical,
  HardDrive,
  Play,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { IChartApi } from "lightweight-charts";

import { useServer } from "@/hooks/useServer";
import { useTheme } from "@/hooks/useTheme";
import { api } from "@/lib/api";

// ── Helpers ──

/** Normalize a timestamp to seconds (lightweight-charts expects epoch seconds) */
function tsToSeconds(ts: number): number {
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

function formatUsd(val: number) {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return val.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

function formatPnl(val: number) {
  return (val >= 0 ? "+" : "") + formatUsd(val);
}

function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

function formatPct(val: number) {
  return (val >= 0 ? "+" : "") + (val * 100).toFixed(2) + "%";
}

function tsToDateTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function dateToTs(dateStr: string): number {
  return Math.floor(new Date(dateStr).getTime() / 1000);
}

function toDateInputValue(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(0, 10);
}

const RANGE_PRESETS: { label: string; days: number }[] = [
  { label: "1D", days: 1 },
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
  { label: "3M", days: 90 },
];

const RESOLUTIONS = ["1m", "5m", "15m", "1h"] as const;

const STATUS_STYLES: Record<string, { bg: string; text: string }> = {
  pending: { bg: "var(--color-yellow)", text: "#000" },
  running: { bg: "var(--color-primary)", text: "#000" },
  completed: { bg: "var(--color-green)", text: "#000" },
  failed: { bg: "var(--color-red)", text: "#fff" },
};

const CLOSE_TYPE_LABELS: Record<string, { label: string; color: string }> = {
  TAKE_PROFIT: { label: "TP", color: "#26a69a" },
  STOP_LOSS: { label: "SL", color: "#ef5350" },
  TIME_LIMIT: { label: "TL", color: "#78909c" },
  TRAILING_STOP: { label: "TS", color: "#ab47bc" },
  EARLY_STOP: { label: "ES", color: "#e0e0e0" },
  POSITION_HOLD: { label: "PH", color: "#42a5f5" },
};

// ── Chart: Lightweight Charts base config ──

function chartOptions(isDark: boolean) {
  return {
    layout: {
      background: { color: "transparent" },
      textColor: isDark ? "#6b7994" : "#64748b",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: isDark ? "#1c254133" : "#e2e8f033" },
      horzLines: { color: isDark ? "#1c254133" : "#e2e8f033" },
    },
    rightPriceScale: { borderColor: "transparent" },
    timeScale: { borderColor: "transparent", timeVisible: true, secondsVisible: false },
    crosshair: {
      horzLine: { color: isDark ? "#6b7994" : "#94a3b8", style: 2 as const, width: 1 as const, labelBackgroundColor: isDark ? "#1c2541" : "#e2e8f0" },
      vertLine: { color: isDark ? "#6b7994" : "#94a3b8", style: 2 as const, width: 1 as const, labelBackgroundColor: isDark ? "#1c2541" : "#e2e8f0" },
    },
  };
}

// ── Executor line style by close type (matches Python _add_executor_markers convention) ──

function executorLineStyle(
  ex: ExecutorData,
  LineStyle: { Solid: number; Dashed: number; Dotted: number },
): { color: string; lineWidth: number; lineStyle: number } {
  const ct = ex.closeType?.toUpperCase() ?? "";

  if (ct.includes("POSITION_HOLD")) {
    // Hold: dotted, blue for buy / purple for sell
    return {
      color: ex.side === "BUY" ? "#42a5f5" : "#ab47bc",
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
    };
  }
  if (ct.includes("EARLY_STOP")) {
    // Early stop: dashed, gray
    return { color: "#e0e0e0", lineWidth: 1, lineStyle: LineStyle.Dashed };
  }
  if (ct.includes("STOP_LOSS")) {
    // Stop loss: solid, orange
    return { color: "#ff6d00", lineWidth: 2, lineStyle: LineStyle.Solid };
  }
  // TP / Time Limit / Other: solid, green if profit / red if loss
  const color = ex.netPnlQuote >= 0 ? "#26a69a" : "#ef5350";
  return { color, lineWidth: 2, lineStyle: LineStyle.Solid };
}

// ── Unified Backtest Chart (synced subplots) ──

function BacktestChart({ data }: { data: BacktestData }) {
  const priceRef = useRef<HTMLDivElement>(null);
  const pnlRef = useRef<HTMLDivElement>(null);
  const posRef = useRef<HTMLDivElement>(null);
  const chartsRef = useRef<IChartApi[]>([]);
  const { theme } = useTheme();

  const hasPositionHeld = data.positionHeldTimeseries.length > 0;
  const hasPnl = data.pnlTimeseries.length > 0 || data.executors.length > 0;

  useEffect(() => {
    if (!priceRef.current || data.candles.length === 0) return;
    const isDark = theme === "dark";
    let ro: ResizeObserver | undefined;
    // Track if syncing to prevent infinite loops
    let isSyncing = false;

    (async () => {
      const mod = await import("lightweight-charts");
      if (!priceRef.current) return;

      // Cleanup previous charts
      for (const c of chartsRef.current) {
        try { c.remove(); } catch { /* ok */ }
      }
      chartsRef.current = [];

      type TS = import("lightweight-charts").UTCTimestamp;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const ts = (v: number): any => tsToSeconds(v) as TS;

      const containerWidth = priceRef.current.clientWidth;

      // ── Row 1: Price + Executors ──
      const priceChart = mod.createChart(priceRef.current, {
        ...chartOptions(isDark),
        width: containerWidth,
        height: hasPositionHeld ? 350 : hasPnl ? 380 : 450,
      });
      chartsRef.current.push(priceChart);

      const candleSeries = priceChart.addSeries(mod.CandlestickSeries, {
        upColor: "#26a69a",
        downColor: "#ef5350",
        borderUpColor: "#26a69a",
        borderDownColor: "#ef5350",
        wickUpColor: "#26a69a",
        wickDownColor: "#ef5350",
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      candleSeries.setData(data.candles.map((c) => ({
        time: ts(c.time),
        open: c.open, high: c.high, low: c.low, close: c.close,
      })));

      // Executor overlays
      for (const ex of data.executors) {
        if (!ex.timestamp || !ex.closeTimestamp) continue;
        const entryT = ts(ex.timestamp);
        const exitT = ts(ex.closeTimestamp);
        const wasFilled = ex.filledAmountQuote > 0;

        if (wasFilled && ex.entryPrice > 0) {
          const exitP = ex.closePrice > 0 ? ex.closePrice : ex.entryPrice;
          const style = executorLineStyle(ex, mod.LineStyle);
          const seg = priceChart.addSeries(mod.LineSeries, {
            color: style.color,
            lineWidth: style.lineWidth as 1 | 2 | 3 | 4,
            lineStyle: style.lineStyle,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
          seg.setData([
            { time: entryT, value: ex.entryPrice },
            { time: exitT, value: exitP },
          ]);
        } else if (ex.entryPrice > 0) {
          // Unfilled: dashed white horizontal
          const seg = priceChart.addSeries(mod.LineSeries, {
            color: "rgba(255,255,255,0.4)",
            lineWidth: 1,
            lineStyle: mod.LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          });
          seg.setData([
            { time: entryT, value: ex.entryPrice },
            { time: exitT, value: ex.entryPrice },
          ]);
        }
      }

      priceChart.timeScale().fitContent();

      // ── Row 2: Cumulative PnL ──
      let pnlChart: IChartApi | undefined;
      if (hasPnl && pnlRef.current) {
        pnlChart = mod.createChart(pnlRef.current, {
          ...chartOptions(isDark),
          width: containerWidth,
          height: hasPositionHeld ? 160 : 200,
        });
        chartsRef.current.push(pnlChart);

        if (data.pnlTimeseries.length > 0) {
          // Full tick-level PnL timeseries
          const totalSeries = pnlChart.addSeries(mod.AreaSeries, {
            lineColor: "#ffd54f",
            topColor: "rgba(255,213,79,0.25)",
            bottomColor: "rgba(255,213,79,0.02)",
            lineWidth: 2,
          });
          totalSeries.setData(data.pnlTimeseries.map((p) => ({ time: ts(p.time), value: p.totalPnl })));

          if (data.pnlTimeseries.some((p) => p.executorRealizedPnl !== 0)) {
            const s = pnlChart.addSeries(mod.LineSeries, {
              color: "#26a69a", lineWidth: 1, lineStyle: mod.LineStyle.Dashed,
              priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
            });
            s.setData(data.pnlTimeseries.map((p) => ({ time: ts(p.time), value: p.executorRealizedPnl })));
          }
          if (data.pnlTimeseries.some((p) => p.positionRealizedPnl !== 0)) {
            const s = pnlChart.addSeries(mod.LineSeries, {
              color: "#42a5f5", lineWidth: 1, lineStyle: mod.LineStyle.Dashed,
              priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
            });
            s.setData(data.pnlTimeseries.map((p) => ({ time: ts(p.time), value: p.positionRealizedPnl })));
          }
          if (data.pnlTimeseries.some((p) => p.positionUnrealizedPnl !== 0)) {
            const s = pnlChart.addSeries(mod.LineSeries, {
              color: "#ab47bc", lineWidth: 1, lineStyle: mod.LineStyle.Dashed,
              priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
            });
            s.setData(data.pnlTimeseries.map((p) => ({ time: ts(p.time), value: p.positionUnrealizedPnl })));
          }
        } else {
          // Fallback: cumulative from executor closures
          const closed = data.executors
            .filter((e) => e.closeTimestamp && e.filledAmountQuote > 0 && !e.closeType?.toUpperCase().includes("POSITION_HOLD"))
            .sort((a, b) => a.closeTimestamp - b.closeTimestamp);
          if (closed.length > 0) {
            const series = pnlChart.addSeries(mod.AreaSeries, {
              lineColor: "#ffd54f",
              topColor: "rgba(255,213,79,0.25)",
              bottomColor: "rgba(255,213,79,0.02)",
              lineWidth: 2,
            });
            let cum = 0;
            series.setData(closed.map((ex) => {
              cum += ex.netPnlQuote;
              return { time: ts(ex.closeTimestamp), value: cum };
            }));
          }
        }
        pnlChart.timeScale().fitContent();
      }

      // ── Row 3: Position Held (only if data exists) ──
      let posChart: IChartApi | undefined;
      if (hasPositionHeld && posRef.current) {
        posChart = mod.createChart(posRef.current, {
          ...chartOptions(isDark),
          width: containerWidth,
          height: 140,
        });
        chartsRef.current.push(posChart);

        const pts = data.positionHeldTimeseries;

        // Long held (green area)
        const longSeries = posChart.addSeries(mod.AreaSeries, {
          lineColor: "#26a69a",
          topColor: "rgba(38,166,154,0.3)",
          bottomColor: "rgba(38,166,154,0.02)",
          lineWidth: 1,
        });
        longSeries.setData(pts.map((p) => ({ time: ts(p.time), value: p.longAmount })));

        // Short held (red area, negative)
        if (pts.some((p) => p.shortAmount > 0)) {
          const shortSeries = posChart.addSeries(mod.AreaSeries, {
            lineColor: "#ef5350",
            topColor: "rgba(239,83,80,0.02)",
            bottomColor: "rgba(239,83,80,0.3)",
            lineWidth: 1,
            invertFilledArea: true,
          });
          shortSeries.setData(pts.map((p) => ({ time: ts(p.time), value: -p.shortAmount })));
        }

        // Net position line
        const netSeries = posChart.addSeries(mod.LineSeries, {
          color: "#e0e0e0", lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        netSeries.setData(pts.map((p) => ({ time: ts(p.time), value: p.netAmount })));

        posChart.timeScale().fitContent();
      }

      // ── Sync time scales across all charts ──
      const allCharts = chartsRef.current;
      for (let i = 0; i < allCharts.length; i++) {
        allCharts[i].timeScale().subscribeVisibleLogicalRangeChange((range) => {
          if (isSyncing || !range) return;
          isSyncing = true;
          for (let j = 0; j < allCharts.length; j++) {
            if (j !== i) {
              allCharts[j].timeScale().setVisibleLogicalRange(range);
            }
          }
          isSyncing = false;
        });
      }

      // Crosshair sync — clearCrosshairPosition when mouse leaves a chart
      for (let i = 0; i < allCharts.length; i++) {
        allCharts[i].subscribeCrosshairMove((param) => {
          if (isSyncing) return;
          isSyncing = true;
          for (let j = 0; j < allCharts.length; j++) {
            if (j !== i) {
              if (!param.time) {
                allCharts[j].clearCrosshairPosition();
              }
            }
          }
          isSyncing = false;
        });
      }

      // Resize observer
      ro = new ResizeObserver((entries) => {
        const w = entries[0]?.contentRect?.width;
        if (w) {
          for (const c of chartsRef.current) {
            c.applyOptions({ width: w });
          }
        }
      });
      ro.observe(priceRef.current!);
    })();

    return () => {
      ro?.disconnect();
      for (const c of chartsRef.current) {
        try { c.remove(); } catch { /* ok */ }
      }
      chartsRef.current = [];
    };
  }, [data, theme, hasPnl, hasPositionHeld]);

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-0">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
          Price &amp; Executors
        </h4>
        {data.pnlTimeseries.length > 0 && (
          <div className="flex gap-3 text-[10px]">
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-[#ffd54f]" /> Total PnL</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-[#26a69a]" /> Executor</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-[#42a5f5]" /> Position</span>
            <span className="flex items-center gap-1"><span className="inline-block w-3 h-0.5 bg-[#ab47bc]" /> Unrealized</span>
          </div>
        )}
      </div>
      <div ref={priceRef} />
      {hasPnl && (
        <>
          <div className="pt-1">
            <span className="text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wide">Cumulative PnL</span>
          </div>
          <div ref={pnlRef} />
        </>
      )}
      {hasPositionHeld && (
        <>
          <div className="pt-1">
            <span className="text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wide">Position Held</span>
          </div>
          <div ref={posRef} />
        </>
      )}
    </div>
  );
}

// ── Main Component ──

export function BacktestingTab() {
  const { server } = useServer();
  const queryClient = useQueryClient();

  // Form state
  const [configId, setConfigId] = useState("");
  const [resolution, setResolution] = useState<string>("1m");
  const [tradeCost, setTradeCost] = useState("0.0002");
  const [startDate, setStartDate] = useState(() =>
    toDateInputValue(Math.floor(Date.now() / 1000) - 7 * 86400),
  );
  const [endDate, setEndDate] = useState(() =>
    toDateInputValue(Math.floor(Date.now() / 1000)),
  );
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [configDropdownOpen, setConfigDropdownOpen] = useState(false);

  // Available configs
  const { data: configsData } = useQuery({
    queryKey: ["available-configs", server],
    queryFn: () => api.getAvailableConfigs(server!),
    enabled: !!server,
  });

  // Task list
  const {
    data: tasks,
    isLoading: tasksLoading,
  } = useQuery({
    queryKey: ["backtest-tasks", server],
    queryFn: () => api.listBacktestTasks(server!),
    enabled: !!server,
    refetchInterval: 5000,
  });

  // Selected task detail (poll while pending/running)
  const { data: selectedTask } = useQuery({
    queryKey: ["backtest-task", server, selectedTaskId],
    queryFn: () => api.getBacktestTask(server!, selectedTaskId!),
    enabled: !!server && !!selectedTaskId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "pending" || status === "running") return 2000;
      return false;
    },
  });

  // Auto-select first completed task if none selected
  useEffect(() => {
    if (!selectedTaskId && tasks && tasks.length > 0) {
      const completed = tasks.find((t) => t.status === "completed");
      setSelectedTaskId(completed?.task_id ?? tasks[0].task_id);
    }
  }, [tasks, selectedTaskId]);

  // Submit mutation
  const submitMutation = useMutation({
    mutationFn: () =>
      api.submitBacktest(server!, {
        config_id: configId,
        start_time: dateToTs(startDate),
        end_time: dateToTs(endDate),
        backtesting_resolution: resolution,
        trade_cost: parseFloat(tradeCost),
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["backtest-tasks", server] });
      if (data.task_id) setSelectedTaskId(data.task_id);
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => api.deleteBacktestTask(server!, taskId),
    onSuccess: (_, taskId) => {
      if (selectedTaskId === taskId) setSelectedTaskId(null);
      queryClient.invalidateQueries({ queryKey: ["backtest-tasks", server] });
    },
  });

  const applyPreset = useCallback((days: number) => {
    const now = Math.floor(Date.now() / 1000);
    setEndDate(toDateInputValue(now));
    setStartDate(toDateInputValue(now - days * 86400));
  }, []);

  // The backend-api uses "result" (singular) for the backtesting data
  // which contains: { executors, results (metrics), processed_data, pnl_timeseries, ... }
  const rawTaskResults = selectedTask?.result as Record<string, unknown> | undefined;
  const processed = rawTaskResults ? extractResults(rawTaskResults) : null;

  if (!server)
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;

  const selectedConfig = configsData?.configs.find((c) => c.id === configId);

  return (
    <div className="space-y-6">
      {/* ── Config Panel ── */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {/* Config selector */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
              Controller Config
            </label>
            <div className="relative">
              <button
                onClick={() => setConfigDropdownOpen((o) => !o)}
                className="flex w-full items-center justify-between rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
              >
                <span className={`truncate ${configId ? "" : "text-[var(--color-text-muted)]"}`}>
                  {selectedConfig
                    ? `${selectedConfig.id}`
                    : "Select a config..."}
                </span>
                <ChevronDown className={`h-4 w-4 shrink-0 text-[var(--color-text-muted)] transition-transform ${configDropdownOpen ? "rotate-180" : ""}`} />
              </button>
              {configDropdownOpen && configsData && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setConfigDropdownOpen(false)} />
                  <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-60 overflow-auto rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] shadow-lg">
                    {configsData.configs.length === 0 && (
                      <div className="px-3 py-2 text-xs text-[var(--color-text-muted)]">
                        No configs available
                      </div>
                    )}
                    {configsData.configs.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => {
                          setConfigId(c.id);
                          setConfigDropdownOpen(false);
                        }}
                        className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-[var(--color-surface-hover)] ${
                          c.id === configId ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]" : ""
                        }`}
                      >
                        <span className="font-medium">{c.id}</span>
                        <span className="text-xs text-[var(--color-text-muted)]">
                          {c.controller_name} &middot; {c.connector_name} &middot; {c.trading_pair}
                        </span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Time range */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
              Time Range
            </label>
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <Calendar className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                <input
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
                />
                <span className="text-xs text-[var(--color-text-muted)]">to</span>
                <input
                  type="date"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
                />
              </div>
              <div className="flex rounded-md border border-[var(--color-border)] overflow-hidden w-fit">
                {RANGE_PRESETS.map(({ label, days }) => (
                  <button
                    key={label}
                    onClick={() => applyPreset(days)}
                    className="px-2.5 py-1 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Resolution */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
              Resolution
            </label>
            <div className="flex rounded-md border border-[var(--color-border)] overflow-hidden w-fit">
              {RESOLUTIONS.map((r) => (
                <button
                  key={r}
                  onClick={() => setResolution(r)}
                  className={`px-3 py-2 text-xs font-medium transition-colors ${
                    resolution === r
                      ? "bg-[var(--color-primary)] text-white"
                      : "bg-[var(--color-bg)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>

          {/* Trade Cost */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
              Trade Cost
            </label>
            <div className="flex items-center gap-1">
              <input
                type="number"
                step="0.0001"
                min="0"
                value={tradeCost}
                onChange={(e) => setTradeCost(e.target.value)}
                className="w-24 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-sm tabular-nums focus:border-[var(--color-primary)] focus:outline-none"
              />
              <span className="text-xs text-[var(--color-text-muted)]">
                ({(parseFloat(tradeCost || "0") * 100).toFixed(2)}%)
              </span>
            </div>
          </div>
        </div>

        {/* Submit button */}
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={() => submitMutation.mutate()}
            disabled={!configId || submitMutation.isPending}
            className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-5 py-2.5 text-sm font-semibold text-white transition-all hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitMutation.isPending ? (
              <Circle className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Run Backtest
          </button>
          {selectedConfig && (
            <span className="text-xs text-[var(--color-text-muted)]">
              {selectedConfig.controller_name} &middot; {selectedConfig.connector_name} &middot; {selectedConfig.trading_pair}
            </span>
          )}
        </div>
      </div>

      {submitMutation.isError && (
        <div className="rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {(submitMutation.error as Error).message}
        </div>
      )}

      {/* ── Tasks + Results ── */}
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        {/* Task list sidebar */}
        <div className="space-y-2">
          <h3 className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
            Tasks
          </h3>

          {tasksLoading && (
            <div className="flex items-center gap-2 py-4 text-xs text-[var(--color-text-muted)]">
              <Circle className="h-3.5 w-3.5 animate-spin" /> Loading...
            </div>
          )}

          {!tasksLoading && (!tasks || tasks.length === 0) && (
            <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-6 text-center text-xs text-[var(--color-text-muted)]">
              No backtest tasks yet
            </div>
          )}

          {tasks?.map((task) => {
            const style = STATUS_STYLES[task.status] ?? STATUS_STYLES.pending;
            const isSelected = task.task_id === selectedTaskId;

            return (
              <button
                key={task.task_id}
                onClick={() => setSelectedTaskId(task.task_id)}
                className={`group flex w-full items-center gap-2 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors ${
                  isSelected
                    ? "border-[var(--color-primary)]/50 bg-[var(--color-primary)]/5"
                    : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-primary)]/30"
                }`}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase leading-none"
                      style={{ backgroundColor: style.bg, color: style.text }}
                    >
                      {task.status}
                    </span>
                    <code className="truncate text-xs text-[var(--color-text-muted)]">
                      {task.task_id.slice(0, 8)}
                    </code>
                    {task.saved && (
                      <HardDrive className="h-3 w-3 text-[var(--color-primary)] shrink-0" />
                    )}
                  </div>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteMutation.mutate(task.task_id);
                  }}
                  className="rounded p-0.5 text-[var(--color-text-muted)] opacity-0 transition-all hover:text-[var(--color-red)] group-hover:opacity-100"
                  title="Delete task"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </button>
            );
          })}
        </div>

        {/* Results panel */}
        <div className="space-y-4">
          {selectedTask?.status === "pending" || selectedTask?.status === "running" ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-16">
              <Circle className="h-6 w-6 animate-spin text-[var(--color-primary)] mb-3" />
              <p className="text-sm text-[var(--color-text-muted)]">
                Backtest {selectedTask.status}...
              </p>
            </div>
          ) : selectedTask?.status === "failed" ? (
            <div className="rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/5 p-4">
              <div className="flex items-center gap-2 text-sm font-medium text-[var(--color-red)]">
                <X className="h-4 w-4" />
                Backtest Failed
              </div>
              {selectedTask.error && (
                <p className="mt-2 text-xs text-[var(--color-text-muted)]">{selectedTask.error}</p>
              )}
            </div>
          ) : selectedTask?.status === "completed" ? (
            processed ? (
              <BacktestResults data={processed} taskConfig={selectedTask?.config as Record<string, unknown> | undefined} />
            ) : rawTaskResults ? (
              <div className="space-y-4">
                <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
                  <h4 className="mb-3 text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
                    Backtest Completed
                  </h4>
                  <p className="text-sm text-[var(--color-text-muted)] mb-3">
                    Results received but could not be parsed into charts. Raw data:
                  </p>
                  <pre className="max-h-96 overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-3 text-xs">
                    {JSON.stringify(rawTaskResults, null, 2)}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-16 text-[var(--color-text-muted)]">
                <FlaskConical className="h-8 w-8 mb-3 opacity-30" />
                <p className="text-sm">Backtest completed but no results data returned</p>
              </div>
            )
          ) : (
            <div className="flex flex-col items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-16 text-[var(--color-text-muted)]">
              <FlaskConical className="h-8 w-8 mb-3 opacity-30" />
              <p className="text-sm">Select a config and run a backtest</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Data Types ──

interface CandleData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface ExecutorData {
  id: string;
  timestamp: number;
  closeTimestamp: number;
  side: string;
  closeType: string;
  netPnlQuote: number;
  filledAmountQuote: number;
  entryPrice: number;
  closePrice: number;
}

interface PnlTimeseriesPoint {
  time: number;
  totalPnl: number;
  executorRealizedPnl: number;
  positionRealizedPnl: number;
  positionUnrealizedPnl: number;
}

interface PositionHeldPoint {
  time: number;
  longAmount: number;
  shortAmount: number;
  netAmount: number;
  unrealizedPnl: number;
}

interface BacktestData {
  // Summary metrics
  netPnlQuote: number;
  netPnlPct: number;
  maxDrawdownUsd: number;
  maxDrawdownPct: number;
  totalVolume: number;
  sharpeRatio: number;
  profitFactor: number;
  totalExecutors: number;
  accuracyLong: number;
  accuracyShort: number;
  totalFees: number;
  closeTypes: Record<string, number>;
  // Chart data
  candles: CandleData[];
  pnlTimeseries: PnlTimeseriesPoint[];
  positionHeldTimeseries: PositionHeldPoint[];
  executors: ExecutorData[];
  // Raw for debug
  raw: Record<string, unknown>;
}

// ── Results Extraction ──

function extractResults(taskResults: Record<string, unknown>): BacktestData | null {
  // The hummingbot API returns nested structure:
  //   { processed_data: { features: [...] }, results: { net_pnl_quote, ... }, executors: [...], pnl_timeseries: [...] }
  // But it could also be flat if the backend wraps it differently.

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw = taskResults as any;

  // Find the metrics dict - could be at results.results (nested) or results directly (flat)
  const metrics: Record<string, unknown> =
    (raw.results && typeof raw.results === "object" && !Array.isArray(raw.results))
      ? raw.results
      : raw;

  const num = (obj: Record<string, unknown>, ...keys: string[]): number => {
    for (const k of keys) {
      const v = obj[k];
      if (typeof v === "number") return v;
    }
    return 0;
  };

  const netPnlQuote = num(metrics, "net_pnl_quote", "net_pnl", "total_pnl", "pnl");
  const netPnlPct = num(metrics, "net_pnl", "net_pnl_pct", "return_pct");
  const maxDrawdownUsd = num(metrics, "max_drawdown_usd", "max_drawdown");
  const maxDrawdownPct = num(metrics, "max_drawdown_pct");
  const totalVolume = num(metrics, "total_volume");
  const sharpeRatio = num(metrics, "sharpe_ratio", "sharpe");
  const profitFactor = num(metrics, "profit_factor");
  const totalExecutors = num(metrics, "total_executors", "total_trades", "trade_count");
  const accuracyLong = num(metrics, "accuracy_long");
  const accuracyShort = num(metrics, "accuracy_short");
  const totalFees = num(metrics, "total_fees_quote", "total_fees");

  // Close types
  let closeTypes: Record<string, number> = {};
  const rawCT = metrics.close_types;
  if (rawCT && typeof rawCT === "object") {
    closeTypes = rawCT as Record<string, number>;
  }

  // Extract candle data from processed_data
  // Backend returns df.to_dict() which gives { column: { "0": val, "1": val, ... } }
  let candles: CandleData[] = [];
  const processedData = raw.processed_data;
  if (processedData) {
    const features = processedData.features ?? processedData;
    if (Array.isArray(features)) {
      // Records format
      candles = features.map((f: Record<string, unknown>) => ({
        time: f.timestamp as number,
        open: f.open as number,
        high: f.high as number,
        low: f.low as number,
        close: f.close as number,
      }));
    } else if (features && typeof features === "object" && features.timestamp) {
      const tsObj = features.timestamp as Record<string, number>;
      // Check if it's columnar array format or dict-of-dicts (df.to_dict() default)
      if (Array.isArray(tsObj)) {
        const timestamps = tsObj as unknown as number[];
        const opens = features.open as unknown as number[];
        const highs = features.high as unknown as number[];
        const lows = features.low as unknown as number[];
        const closes = features.close as unknown as number[];
        candles = timestamps.map((t: number, i: number) => ({
          time: t,
          open: opens[i],
          high: highs[i],
          low: lows[i],
          close: closes[i],
        }));
      } else {
        // df.to_dict() format: { column: { "0": val, "1": val, ... } }
        const keys = Object.keys(tsObj).sort((a, b) => Number(a) - Number(b));
        const opensObj = (features.open ?? {}) as Record<string, number>;
        const highsObj = (features.high ?? {}) as Record<string, number>;
        const lowsObj = (features.low ?? {}) as Record<string, number>;
        const closesObj = (features.close ?? {}) as Record<string, number>;
        candles = keys.map((k) => ({
          time: tsObj[k],
          open: opensObj[k],
          high: highsObj[k],
          low: lowsObj[k],
          close: closesObj[k],
        }));
      }
    }
  }

  // Extract PnL timeseries
  let pnlTimeseries: PnlTimeseriesPoint[] = [];
  const rawPnlTs = raw.pnl_timeseries;
  if (Array.isArray(rawPnlTs) && rawPnlTs.length > 0) {
    pnlTimeseries = rawPnlTs.map((p: Record<string, unknown>) => ({
      time: p.timestamp as number,
      totalPnl: (p.total_pnl ?? 0) as number,
      executorRealizedPnl: (p.executor_realized_pnl ?? 0) as number,
      positionRealizedPnl: (p.position_realized_pnl ?? 0) as number,
      positionUnrealizedPnl: (p.position_unrealized_pnl ?? 0) as number,
    }));
  }

  // Extract executors
  let executors: ExecutorData[] = [];
  const rawExecutors = raw.executors;
  if (Array.isArray(rawExecutors)) {
    executors = rawExecutors
      .filter((e: Record<string, unknown>) => e.timestamp != null)
      .map((e: Record<string, unknown>) => {
        // Handle both serialized ExecutorInfo dicts and simpler formats
        const config = (e.config ?? {}) as Record<string, unknown>;
        const customInfo = (e.custom_info ?? {}) as Record<string, unknown>;
        return {
          id: String(e.id ?? e.executor_id ?? ""),
          timestamp: (e.timestamp ?? 0) as number,
          closeTimestamp: (e.close_timestamp ?? 0) as number,
          side: normalizeSide(e.side),
          closeType: String(e.close_type ?? ""),
          netPnlQuote: (e.net_pnl_quote ?? 0) as number,
          filledAmountQuote: (e.filled_amount_quote ?? 0) as number,
          entryPrice: (customInfo.current_position_average_price ?? config.entry_price ?? e.entry_price ?? 0) as number,
          closePrice: (customInfo.close_price ?? e.close_price ?? 0) as number,
        };
      });
  }

  // Extract position held timeseries
  let positionHeldTimeseries: PositionHeldPoint[] = [];
  const rawPosTs = raw.position_held_timeseries;
  if (Array.isArray(rawPosTs) && rawPosTs.length > 0) {
    positionHeldTimeseries = rawPosTs.map((p: Record<string, unknown>) => ({
      time: (p.timestamp ?? 0) as number,
      longAmount: (p.long_amount ?? 0) as number,
      shortAmount: (p.short_amount ?? 0) as number,
      netAmount: (p.net_amount ?? 0) as number,
      unrealizedPnl: (p.unrealized_pnl ?? 0) as number,
    }));
  }

  // If we found no metrics and no data at all, return null
  if (netPnlQuote === 0 && totalExecutors === 0 && candles.length === 0 && executors.length === 0 && pnlTimeseries.length === 0) {
    // Check if there's really nothing useful
    const hasAnything = Object.keys(metrics).length > 0 || Object.keys(raw).length > 1;
    if (!hasAnything) return null;
  }

  return {
    netPnlQuote,
    netPnlPct,
    maxDrawdownUsd,
    maxDrawdownPct,
    totalVolume,
    sharpeRatio,
    profitFactor,
    totalExecutors,
    accuracyLong,
    accuracyShort,
    totalFees,
    closeTypes,
    candles,
    pnlTimeseries,
    positionHeldTimeseries,
    executors,
    raw: taskResults,
  };
}

function normalizeSide(side: unknown): string {
  if (typeof side === "string") {
    if (side === "1" || side.toUpperCase() === "BUY" || side === "TradeType.BUY") return "BUY";
    if (side === "2" || side.toUpperCase() === "SELL" || side === "TradeType.SELL") return "SELL";
    return side;
  }
  if (typeof side === "number") return side === 1 ? "BUY" : "SELL";
  return String(side ?? "");
}

// ── Results Display ──

function BacktestResults({ data, taskConfig }: { data: BacktestData; taskConfig?: Record<string, unknown> }) {
  const [showRaw, setShowRaw] = useState(false);
  const [showExecutors, setShowExecutors] = useState(false);
  const [showConfig, setShowConfig] = useState(false);

  // Extract controller info from task config
  // Task config shape: { start_time, end_time, backtesting_resolution, trade_cost, config: { controller_name, id, ... } }
  const controllerConfig = (taskConfig?.config ?? {}) as Record<string, unknown>;
  const controllerName = String(controllerConfig.controller_name ?? "");
  const configId = String(controllerConfig.id ?? "");

  return (
    <div className="space-y-4">
      {/* Collapsible config header */}
      {taskConfig && (
        <div className="rounded-lg border border-[var(--color-border)] overflow-hidden">
          <button
            onClick={() => setShowConfig((v) => !v)}
            className="flex w-full items-center justify-between bg-[var(--color-surface)] px-4 py-2.5 hover:bg-[var(--color-surface-hover)] transition-colors"
          >
            <div className="flex items-center gap-2 min-w-0">
              <FlaskConical className="h-3.5 w-3.5 text-[var(--color-primary)] shrink-0" />
              <span className="text-sm font-medium truncate">
                {controllerName || "Config"}
              </span>
              {configId && (
                <code className="text-xs text-[var(--color-text-muted)] truncate">
                  {configId}
                </code>
              )}
            </div>
            <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)] transition-transform ${showConfig ? "rotate-180" : ""}`} />
          </button>
          {showConfig && (
            <div className="border-t border-[var(--color-border)] bg-[var(--color-bg)] p-4">
              <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-3 lg:grid-cols-4">
                {Object.entries(controllerConfig)
                  .filter(([k]) => k !== "id" && k !== "controller_name" && k !== "type")
                  .map(([key, value]) => (
                    <div key={key} className="flex items-baseline gap-1.5 min-w-0">
                      <span className="text-[var(--color-text-muted)] shrink-0">{key.replace(/_/g, " ")}:</span>
                      <span className="font-medium tabular-nums truncate">{String(value)}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Stat cards - row 1: core metrics */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Net PnL" value={formatPnl(data.netPnlQuote)} color={pnlColor(data.netPnlQuote)} />
        <StatCard
          label="Return"
          value={data.netPnlPct ? formatPct(data.netPnlPct) : "\u2014"}
          color={pnlColor(data.netPnlPct)}
        />
        <StatCard label="Executors" value={String(data.totalExecutors)} />
        <StatCard
          label="Sharpe"
          value={data.sharpeRatio ? data.sharpeRatio.toFixed(2) : "\u2014"}
          color={data.sharpeRatio > 0 ? "var(--color-green)" : "var(--color-red)"}
        />
        <StatCard
          label="Profit Factor"
          value={data.profitFactor ? data.profitFactor.toFixed(2) : "\u2014"}
          color={data.profitFactor > 1 ? "var(--color-green)" : "var(--color-red)"}
        />
        <StatCard
          label="Max Drawdown"
          value={data.maxDrawdownUsd ? formatUsd(-Math.abs(data.maxDrawdownUsd)) : data.maxDrawdownPct ? formatPct(-Math.abs(data.maxDrawdownPct)) : "\u2014"}
          color="var(--color-red)"
        />
      </div>

      {/* Row 2: accuracy & fees */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Accuracy Long"
          value={data.accuracyLong ? (data.accuracyLong * 100).toFixed(1) + "%" : "\u2014"}
          color={data.accuracyLong >= 0.5 ? "var(--color-green)" : "var(--color-red)"}
        />
        <StatCard
          label="Accuracy Short"
          value={data.accuracyShort ? (data.accuracyShort * 100).toFixed(1) + "%" : "\u2014"}
          color={data.accuracyShort >= 0.5 ? "var(--color-green)" : "var(--color-red)"}
        />
        <StatCard
          label="Total Volume"
          value={data.totalVolume ? formatUsd(data.totalVolume) : "\u2014"}
        />
        <StatCard
          label="Total Fees"
          value={data.totalFees ? formatUsd(data.totalFees) : "\u2014"}
          color="var(--color-red)"
        />
      </div>

      {/* Close types breakdown */}
      {Object.keys(data.closeTypes).length > 0 && (
        <div className="flex flex-wrap gap-2">
          {Object.entries(data.closeTypes).map(([type, count]) => {
            const ct = CLOSE_TYPE_LABELS[type];
            return (
              <div
                key={type}
                className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5"
              >
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: ct?.color ?? "#78909c" }}
                />
                <span className="text-xs font-medium">{ct?.label ?? type}</span>
                <span className="text-xs text-[var(--color-text-muted)]">{count}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Unified chart: Price+Executors / PnL / Position Held */}
      {data.candles.length > 0 && <BacktestChart data={data} />}

      {/* Executors table */}
      {data.executors.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] overflow-hidden">
          <button
            onClick={() => setShowExecutors((v) => !v)}
            className="flex w-full items-center justify-between bg-[var(--color-surface)] px-4 py-2.5 border-b border-[var(--color-border)] hover:bg-[var(--color-surface-hover)] transition-colors"
          >
            <h4 className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
              Executors ({data.executors.length})
            </h4>
            <ChevronDown className={`h-3.5 w-3.5 text-[var(--color-text-muted)] transition-transform ${showExecutors ? "rotate-180" : ""}`} />
          </button>
          {showExecutors && (
            <div className="max-h-80 overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-[var(--color-surface)]">
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="whitespace-nowrap px-3 py-2 text-left text-xs font-medium text-[var(--color-text-muted)]">Time</th>
                    <th className="whitespace-nowrap px-3 py-2 text-left text-xs font-medium text-[var(--color-text-muted)]">Side</th>
                    <th className="whitespace-nowrap px-3 py-2 text-left text-xs font-medium text-[var(--color-text-muted)]">Close</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right text-xs font-medium text-[var(--color-text-muted)]">Entry</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right text-xs font-medium text-[var(--color-text-muted)]">Exit</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right text-xs font-medium text-[var(--color-text-muted)]">Amount</th>
                    <th className="whitespace-nowrap px-3 py-2 text-right text-xs font-medium text-[var(--color-text-muted)]">PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {data.executors.map((ex, i) => {
                    const ct = CLOSE_TYPE_LABELS[ex.closeType];
                    return (
                      <tr
                        key={ex.id || i}
                        className="border-b border-[var(--color-border)] last:border-b-0 hover:bg-[var(--color-surface-hover)] transition-colors"
                      >
                        <td className="whitespace-nowrap px-3 py-1.5 text-xs text-[var(--color-text-muted)] tabular-nums">
                          {ex.timestamp ? tsToDateTime(ex.timestamp) : "\u2014"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-1.5">
                          <span
                            className="text-xs font-semibold"
                            style={{ color: ex.side === "BUY" ? "var(--color-green)" : "var(--color-red)" }}
                          >
                            {ex.side}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-3 py-1.5">
                          <span
                            className="inline-flex rounded px-1 py-0.5 text-[10px] font-semibold"
                            style={{ backgroundColor: (ct?.color ?? "#78909c") + "22", color: ct?.color ?? "#78909c" }}
                          >
                            {ct?.label ?? ex.closeType}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums text-xs">
                          {ex.entryPrice ? ex.entryPrice.toPrecision(6) : "\u2014"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums text-xs">
                          {ex.closePrice ? ex.closePrice.toPrecision(6) : "\u2014"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums text-xs">
                          {ex.filledAmountQuote ? formatUsd(ex.filledAmountQuote) : "\u2014"}
                        </td>
                        <td
                          className="whitespace-nowrap px-3 py-1.5 text-right tabular-nums text-xs font-medium"
                          style={{ color: pnlColor(ex.netPnlQuote) }}
                        >
                          {formatPnl(ex.netPnlQuote)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Raw results toggle */}
      <div>
        <button
          onClick={() => setShowRaw((v) => !v)}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
        >
          {showRaw ? "Hide" : "Show"} raw results
        </button>
        {showRaw && (
          <pre className="mt-2 max-h-64 overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-3 text-xs">
            {JSON.stringify(data.raw, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2.5">
      <div className="text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
        {label}
      </div>
      <div
        className="mt-0.5 text-lg font-semibold tabular-nums"
        style={color ? { color } : undefined}
      >
        {value}
      </div>
    </div>
  );
}
