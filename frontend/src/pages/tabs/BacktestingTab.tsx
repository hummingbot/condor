import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Calendar,
  CheckCircle2,
  ChevronDown,
  Circle,
  FlaskConical,
  HardDrive,
  Info,
  Maximize2,
  Minimize2,
  Pin,
  PinOff,
  Play,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { IChartApi } from "lightweight-charts";

import { useServer } from "@/hooks/useServer";
import { useTheme } from "@/hooks/useTheme";
import { api } from "@/lib/api";

// -- Helpers --

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

function tsToShortDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
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

// -- Chart config --

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

function executorLineStyle(
  ex: ExecutorData,
  LineStyle: { Solid: number; Dashed: number; Dotted: number },
): { color: string; lineWidth: number; lineStyle: number } {
  const ct = ex.closeType?.toUpperCase() ?? "";

  if (ct.includes("POSITION_HOLD")) {
    return {
      color: ex.side === "BUY" ? "#42a5f5" : "#ab47bc",
      lineWidth: 1,
      lineStyle: LineStyle.Dotted,
    };
  }
  if (ct.includes("EARLY_STOP")) {
    return { color: "#e0e0e0", lineWidth: 1, lineStyle: LineStyle.Dashed };
  }
  if (ct.includes("STOP_LOSS")) {
    return { color: "#ff6d00", lineWidth: 2, lineStyle: LineStyle.Solid };
  }
  const color = ex.netPnlQuote >= 0 ? "#26a69a" : "#ef5350";
  return { color, lineWidth: 2, lineStyle: LineStyle.Solid };
}

// -- Backtest Chart --

function BacktestChart({ data }: { data: BacktestData }) {
  const priceRef = useRef<HTMLDivElement>(null);
  const pnlRef = useRef<HTMLDivElement>(null);
  const posRef = useRef<HTMLDivElement>(null);
  const chartsRef = useRef<IChartApi[]>([]);
  const { theme } = useTheme();

  const hasPositionHeld = data.positionHeldTimeseries.length > 0;
  const hasPnl = data.pnlTimeseries.length > 0 || data.executors.length > 0;

  // Compute final values for badges
  const finalPnl = data.pnlTimeseries.length > 0
    ? data.pnlTimeseries[data.pnlTimeseries.length - 1].totalPnl
    : null;
  const finalPosition = data.positionHeldTimeseries.length > 0
    ? data.positionHeldTimeseries[data.positionHeldTimeseries.length - 1].netAmount
    : null;

  useEffect(() => {
    if (!priceRef.current || data.candles.length === 0) return;
    const isDark = theme === "dark";
    let ro: ResizeObserver | undefined;
    let isSyncing = false;

    (async () => {
      const mod = await import("lightweight-charts");
      if (!priceRef.current) return;

      for (const c of chartsRef.current) {
        try { c.remove(); } catch { /* ok */ }
      }
      chartsRef.current = [];

      type TS = import("lightweight-charts").UTCTimestamp;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const ts = (v: number): any => tsToSeconds(v) as TS;

      const containerWidth = priceRef.current.clientWidth;

      // Row 1: Price + Executors
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

      // Row 2: Cumulative PnL
      let pnlChart: IChartApi | undefined;
      if (hasPnl && pnlRef.current) {
        pnlChart = mod.createChart(pnlRef.current, {
          ...chartOptions(isDark),
          width: containerWidth,
          height: hasPositionHeld ? 160 : 200,
        });
        chartsRef.current.push(pnlChart);

        if (data.pnlTimeseries.length > 0) {
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

      // Row 3: Position Held
      let posChart: IChartApi | undefined;
      if (hasPositionHeld && posRef.current) {
        posChart = mod.createChart(posRef.current, {
          ...chartOptions(isDark),
          width: containerWidth,
          height: 140,
        });
        chartsRef.current.push(posChart);

        const pts = data.positionHeldTimeseries;

        const longSeries = posChart.addSeries(mod.AreaSeries, {
          lineColor: "#26a69a",
          topColor: "rgba(38,166,154,0.3)",
          bottomColor: "rgba(38,166,154,0.02)",
          lineWidth: 1,
        });
        longSeries.setData(pts.map((p) => ({ time: ts(p.time), value: p.longAmount })));

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

        const netSeries = posChart.addSeries(mod.LineSeries, {
          color: "#e0e0e0", lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        netSeries.setData(pts.map((p) => ({ time: ts(p.time), value: p.netAmount })));

        posChart.timeScale().fitContent();
      }

      // Sync time scales
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
      {/* Price chart header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
            Price &amp; Executors
          </h4>
          <span className="text-[10px] text-[var(--color-text-muted)] opacity-60">USD</span>
        </div>
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

      {/* PnL chart */}
      {hasPnl && (
        <>
          <div className="pt-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wide">Cumulative PnL</span>
              <span className="text-[10px] text-[var(--color-text-muted)] opacity-60">USD</span>
            </div>
            {finalPnl !== null && (
              <span
                className="text-[11px] font-semibold tabular-nums px-1.5 py-0.5 rounded"
                style={{
                  color: finalPnl >= 0 ? "var(--color-green)" : "var(--color-red)",
                  backgroundColor: finalPnl >= 0 ? "rgba(38,166,154,0.1)" : "rgba(239,83,80,0.1)",
                }}
              >
                {formatPnl(finalPnl)}
              </span>
            )}
          </div>
          <div ref={pnlRef} />
        </>
      )}

      {/* Position chart */}
      {hasPositionHeld && (
        <>
          <div className="pt-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wide">Position Held</span>
              <span className="text-[10px] text-[var(--color-text-muted)] opacity-60">QTY</span>
            </div>
            {finalPosition !== null && (
              <span
                className="text-[11px] font-semibold tabular-nums px-1.5 py-0.5 rounded"
                style={{
                  color: finalPosition >= 0 ? "var(--color-green)" : "var(--color-red)",
                  backgroundColor: finalPosition >= 0 ? "rgba(38,166,154,0.1)" : "rgba(239,83,80,0.1)",
                }}
              >
                {finalPosition >= 0 ? "+" : ""}{finalPosition.toFixed(4)}
              </span>
            )}
          </div>
          <div ref={posRef} />
        </>
      )}
    </div>
  );
}

// -- Main Component --

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
  const [configSearch, setConfigSearch] = useState("");
  const [activePreset, setActivePreset] = useState<string | null>("1W");
  const [pinnedTaskId, setPinnedTaskId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

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

  // Selected task detail
  const { data: selectedTask, isLoading: selectedTaskLoading } = useQuery({
    queryKey: ["backtest-task", server, selectedTaskId],
    queryFn: () => api.getBacktestTask(server!, selectedTaskId!),
    enabled: !!server && !!selectedTaskId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "pending" || status === "running") return 2000;
      return false;
    },
  });

  // Pinned task detail (for comparison)
  const { data: pinnedTask } = useQuery({
    queryKey: ["backtest-task", server, pinnedTaskId],
    queryFn: () => api.getBacktestTask(server!, pinnedTaskId!),
    enabled: !!server && !!pinnedTaskId && pinnedTaskId !== selectedTaskId,
  });

  // Auto-select first completed task
  useEffect(() => {
    if (!selectedTaskId && tasks && tasks.length > 0) {
      const completed = tasks.find((t) => t.status === "completed");
      setSelectedTaskId(completed?.task_id ?? tasks[0].task_id);
    }
  }, [tasks, selectedTaskId]);

  // Toast auto-dismiss
  useEffect(() => {
    if (toast) {
      const t = setTimeout(() => setToast(null), 3000);
      return () => clearTimeout(t);
    }
  }, [toast]);

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
      setToast("Backtest submitted successfully");
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (taskId: string) => api.deleteBacktestTask(server!, taskId),
    onSuccess: (_, taskId) => {
      if (selectedTaskId === taskId) setSelectedTaskId(null);
      if (pinnedTaskId === taskId) setPinnedTaskId(null);
      queryClient.invalidateQueries({ queryKey: ["backtest-tasks", server] });
    },
  });

  const applyPreset = useCallback((label: string, days: number) => {
    const now = Math.floor(Date.now() / 1000);
    setEndDate(toDateInputValue(now));
    setStartDate(toDateInputValue(now - days * 86400));
    setActivePreset(label);
  }, []);

  // Clear active preset when dates are manually changed
  const handleStartDateChange = useCallback((val: string) => {
    setStartDate(val);
    setActivePreset(null);
  }, []);
  const handleEndDateChange = useCallback((val: string) => {
    setEndDate(val);
    setActivePreset(null);
  }, []);

  const rawTaskResults = selectedTask?.result as Record<string, unknown> | undefined;
  const processed = rawTaskResults ? extractResults(rawTaskResults) : null;

  // Pinned task processed data for comparison
  const pinnedRawResults = pinnedTask?.result as Record<string, unknown> | undefined;
  const pinnedProcessed = pinnedRawResults ? extractResults(pinnedRawResults) : null;

  // Extract config info from a task for display
  const getTaskConfigInfo = useCallback((task: Record<string, unknown>) => {
    const config = (task.config ?? {}) as Record<string, unknown>;
    const innerConfig = (config.config ?? {}) as Record<string, unknown>;
    return {
      controllerName: String(innerConfig.controller_name ?? config.controller_name ?? ""),
      configId: String(innerConfig.id ?? config.config_id ?? ""),
      tradingPair: String(innerConfig.trading_pair ?? config.trading_pair ?? ""),
      connector: String(innerConfig.connector_name ?? config.connector_name ?? ""),
      resolution: String(config.backtesting_resolution ?? ""),
      startTime: (config.start_time as number) ?? 0,
      endTime: (config.end_time as number) ?? 0,
    };
  }, []);

  if (!server)
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;

  const selectedConfig = configsData?.configs.find((c) => c.id === configId);

  // Group configs by controller_name
  const groupedConfigs = configsData?.configs
    .filter((c) => {
      if (!configSearch) return true;
      const q = configSearch.toLowerCase();
      return (
        c.id.toLowerCase().includes(q) ||
        (c.controller_name ?? "").toLowerCase().includes(q) ||
        (c.connector_name ?? "").toLowerCase().includes(q) ||
        (c.trading_pair ?? "").toLowerCase().includes(q)
      );
    })
    .reduce<Record<string, typeof configsData.configs>>((acc, c) => {
      const group = c.controller_name ?? "Other";
      if (!acc[group]) acc[group] = [];
      acc[group].push(c);
      return acc;
    }, {});

  // Reason why button is disabled
  const disabledReason = !configId
    ? "Select a controller config first"
    : submitMutation.isPending
      ? "Backtest is being submitted..."
      : null;

  return (
    <div className="space-y-5">
      {/* Toast notification */}
      {toast && (
        <div className="fixed top-4 right-4 z-50 flex items-center gap-2 rounded-lg border border-[var(--color-green)]/30 bg-[var(--color-green)]/10 px-4 py-2.5 text-sm text-[var(--color-green)] shadow-lg backdrop-blur-sm animate-in fade-in slide-in-from-top-2">
          <CheckCircle2 className="h-4 w-4 shrink-0" />
          {toast}
        </div>
      )}

      {/* Config Panel */}
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end">
          {/* Left: Config selector (wider) */}
          <div className="lg:w-[340px] lg:shrink-0">
            <label className="mb-1.5 block text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
              Controller Config
            </label>
            <div className="relative">
              <button
                onClick={() => setConfigDropdownOpen((o) => !o)}
                className="flex w-full items-center justify-between rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm transition-colors hover:border-[var(--color-primary)]/50 focus:border-[var(--color-primary)] focus:outline-none"
              >
                <div className="min-w-0 flex-1 text-left">
                  {selectedConfig ? (
                    <div>
                      <div className="font-medium truncate">{selectedConfig.id}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)] truncate">
                        {selectedConfig.controller_name} &middot; {selectedConfig.connector_name} &middot; {selectedConfig.trading_pair}
                      </div>
                    </div>
                  ) : (
                    <span className="text-[var(--color-text-muted)]">Select a config...</span>
                  )}
                </div>
                <ChevronDown className={`h-4 w-4 shrink-0 ml-2 text-[var(--color-text-muted)] transition-transform ${configDropdownOpen ? "rotate-180" : ""}`} />
              </button>
              {configDropdownOpen && configsData && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => { setConfigDropdownOpen(false); setConfigSearch(""); }} />
                  <div className="absolute left-0 top-full z-20 mt-1 w-[420px] max-w-[90vw] rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] shadow-xl">
                    <div className="p-2 border-b border-[var(--color-border)]">
                      <input
                        type="text"
                        value={configSearch}
                        onChange={(e) => setConfigSearch(e.target.value)}
                        placeholder="Search configs..."
                        autoFocus
                        className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
                      />
                    </div>
                    <div className="max-h-72 overflow-auto">
                      {configsData.configs.length === 0 && (
                        <div className="px-3 py-4 text-xs text-center text-[var(--color-text-muted)]">
                          No configs available
                        </div>
                      )}
                      {groupedConfigs && Object.entries(groupedConfigs).map(([group, configs]) => (
                        <div key={group}>
                          <div className="sticky top-0 bg-[var(--color-bg)] px-3 py-1.5 text-[10px] font-semibold text-[var(--color-text-muted)] uppercase tracking-wider border-b border-[var(--color-border)]">
                            {group}
                          </div>
                          {configs.map((c) => (
                            <button
                              key={c.id}
                              onClick={() => {
                                setConfigId(c.id);
                                setConfigDropdownOpen(false);
                                setConfigSearch("");
                              }}
                              className={`flex w-full flex-col gap-0.5 px-3 py-2.5 text-left transition-colors hover:bg-[var(--color-surface-hover)] ${
                                c.id === configId ? "bg-[var(--color-primary)]/10" : ""
                              }`}
                            >
                              <span className={`text-sm font-medium truncate ${c.id === configId ? "text-[var(--color-primary)]" : ""}`}>
                                {c.id}
                              </span>
                              <span className="text-[11px] text-[var(--color-text-muted)] truncate">
                                {c.connector_name} &middot; {c.trading_pair}
                              </span>
                            </button>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Middle: Parameters group */}
          <div className="flex flex-1 flex-wrap items-end gap-4">
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
                    onChange={(e) => handleStartDateChange(e.target.value)}
                    className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
                  />
                  <span className="text-xs text-[var(--color-text-muted)]">to</span>
                  <input
                    type="date"
                    value={endDate}
                    onChange={(e) => handleEndDateChange(e.target.value)}
                    className="min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
                  />
                </div>
                <div className="flex rounded-md border border-[var(--color-border)] overflow-hidden w-fit">
                  {RANGE_PRESETS.map(({ label, days }) => (
                    <button
                      key={label}
                      onClick={() => applyPreset(label, days)}
                      className={`px-2.5 py-1 text-xs font-medium transition-colors ${
                        activePreset === label
                          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                      }`}
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

          {/* Right: Run button */}
          <div className="relative group lg:shrink-0">
            <button
              onClick={() => submitMutation.mutate()}
              disabled={!!disabledReason}
              className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-6 py-2.5 text-sm font-semibold text-white transition-all hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed shadow-sm shadow-[var(--color-primary)]/20"
            >
              {submitMutation.isPending ? (
                <Circle className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              Run Backtest
            </button>
            {/* Tooltip on disabled */}
            {disabledReason && (
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block">
                <div className="whitespace-nowrap rounded-md bg-[var(--color-bg)] border border-[var(--color-border)] px-2.5 py-1.5 text-xs text-[var(--color-text-muted)] shadow-lg">
                  <div className="flex items-center gap-1.5">
                    <Info className="h-3 w-3 shrink-0" />
                    {disabledReason}
                  </div>
                  <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-px">
                    <div className="border-4 border-transparent border-t-[var(--color-border)]" />
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {submitMutation.isError && (
        <div className="rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/10 px-3 py-2 text-xs text-[var(--color-red)]">
          {(submitMutation.error as Error).message}
        </div>
      )}

      {/* Tasks + Results */}
      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        {/* Task list sidebar */}
        <div className="space-y-2">
          <h3 className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
            Tasks {tasks && tasks.length > 0 && `(${tasks.length})`}
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
            const isPinned = task.task_id === pinnedTaskId;

            // Extract config info from task if available
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const taskInfo = getTaskConfigInfo(task as any);

            // Try to get quick PnL from task result for completed tasks
            let quickPnl: number | null = null;
            if (task.status === "completed") {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const r = (task as any).result;
              if (r) {
                const metrics = (r.results && typeof r.results === "object") ? r.results : r;
                quickPnl = metrics?.net_pnl_quote ?? metrics?.net_pnl ?? null;
              }
            }

            return (
              <button
                key={task.task_id}
                onClick={() => setSelectedTaskId(task.task_id)}
                className={`group flex w-full items-start gap-2 rounded-lg border px-3 py-2.5 text-left text-sm transition-colors ${
                  isSelected
                    ? "border-[var(--color-primary)]/50 bg-[var(--color-primary)]/5"
                    : "border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-primary)]/30"
                }`}
              >
                <div className="min-w-0 flex-1">
                  {/* Row 1: Status + config name */}
                  <div className="flex items-center gap-2">
                    <span
                      className="inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase leading-none shrink-0"
                      style={{ backgroundColor: style.bg, color: style.text }}
                    >
                      {task.status}
                    </span>
                    {taskInfo.configId ? (
                      <span className="text-xs font-medium truncate">{taskInfo.configId}</span>
                    ) : (
                      <code className="truncate text-xs text-[var(--color-text-muted)]">
                        {task.task_id.slice(0, 8)}
                      </code>
                    )}
                    {task.saved && (
                      <HardDrive className="h-3 w-3 text-[var(--color-primary)] shrink-0" />
                    )}
                  </div>

                  {/* Row 2: Details line */}
                  {(taskInfo.tradingPair || taskInfo.resolution || taskInfo.startTime > 0) && (
                    <div className="mt-1 flex items-center gap-1.5 text-[10px] text-[var(--color-text-muted)]">
                      {taskInfo.tradingPair && (
                        <span className="font-medium">{taskInfo.tradingPair}</span>
                      )}
                      {taskInfo.resolution && (
                        <>
                          <span>&middot;</span>
                          <span>{taskInfo.resolution}</span>
                        </>
                      )}
                      {taskInfo.startTime > 0 && taskInfo.endTime > 0 && (
                        <>
                          <span>&middot;</span>
                          <span>{tsToShortDate(taskInfo.startTime)}-{tsToShortDate(taskInfo.endTime)}</span>
                        </>
                      )}
                    </div>
                  )}

                  {/* Row 3: Quick PnL for completed */}
                  {quickPnl !== null && (
                    <div className="mt-1">
                      <span
                        className="text-xs font-semibold tabular-nums"
                        style={{ color: pnlColor(quickPnl) }}
                      >
                        {formatPnl(quickPnl)}
                      </span>
                    </div>
                  )}
                </div>

                {/* Action buttons */}
                <div className="flex flex-col gap-1 shrink-0">
                  {task.status === "completed" && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setPinnedTaskId(isPinned ? null : task.task_id);
                      }}
                      className={`rounded p-0.5 transition-all ${
                        isPinned
                          ? "text-[var(--color-primary)]"
                          : "text-[var(--color-text-muted)] opacity-0 group-hover:opacity-100 hover:text-[var(--color-primary)]"
                      }`}
                      title={isPinned ? "Unpin (remove from comparison)" : "Pin for comparison"}
                    >
                      {isPinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                    </button>
                  )}
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
                </div>
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
              <BacktestResults
                data={processed}
                taskConfig={selectedTask?.config as Record<string, unknown> | undefined}
                pinnedData={pinnedProcessed}
                pinnedConfig={pinnedTask?.config as Record<string, unknown> | undefined}
              />
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
          ) : selectedTaskLoading ? (
            <div className="flex flex-col items-center justify-center rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] py-16">
              <Circle className="h-6 w-6 animate-spin text-[var(--color-primary)] mb-3" />
              <p className="text-sm text-[var(--color-text-muted)]">Loading backtest...</p>
            </div>
          ) : (
            /* Empty state with guided steps */
            <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/50 py-20 text-[var(--color-text-muted)]">
              <FlaskConical className="h-10 w-10 mb-4 opacity-20" />
              <p className="text-base font-medium mb-6 text-[var(--color-text)]">Run your first backtest</p>
              <div className="flex flex-col gap-3 text-sm">
                <div className="flex items-center gap-3">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-primary)]/10 text-xs font-semibold text-[var(--color-primary)]">1</span>
                  <span>Select a controller config from the dropdown above</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-primary)]/10 text-xs font-semibold text-[var(--color-primary)]">2</span>
                  <span>Choose a time range and resolution</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="flex h-6 w-6 items-center justify-center rounded-full bg-[var(--color-primary)]/10 text-xs font-semibold text-[var(--color-primary)]">3</span>
                  <span>Click <strong>Run Backtest</strong> to see results</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// -- Data Types --

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
  candles: CandleData[];
  pnlTimeseries: PnlTimeseriesPoint[];
  positionHeldTimeseries: PositionHeldPoint[];
  executors: ExecutorData[];
  raw: Record<string, unknown>;
}

// -- Results Extraction --

function extractResults(taskResults: Record<string, unknown>): BacktestData | null {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw = taskResults as any;

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

  let closeTypes: Record<string, number> = {};
  const rawCT = metrics.close_types;
  if (rawCT && typeof rawCT === "object") {
    closeTypes = rawCT as Record<string, number>;
  }

  let candles: CandleData[] = [];
  const processedData = raw.processed_data;
  if (processedData) {
    const features = processedData.features ?? processedData;
    if (Array.isArray(features)) {
      candles = features.map((f: Record<string, unknown>) => ({
        time: f.timestamp as number,
        open: f.open as number,
        high: f.high as number,
        low: f.low as number,
        close: f.close as number,
      }));
    } else if (features && typeof features === "object" && features.timestamp) {
      const tsObj = features.timestamp as Record<string, number>;
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

  let executors: ExecutorData[] = [];
  const rawExecutors = raw.executors;
  if (Array.isArray(rawExecutors)) {
    executors = rawExecutors
      .filter((e: Record<string, unknown>) => e.timestamp != null)
      .map((e: Record<string, unknown>) => {
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

  if (netPnlQuote === 0 && totalExecutors === 0 && candles.length === 0 && executors.length === 0 && pnlTimeseries.length === 0) {
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

// -- Results Display --

function BacktestResults({
  data,
  taskConfig,
  pinnedData,
  pinnedConfig,
}: {
  data: BacktestData;
  taskConfig?: Record<string, unknown>;
  pinnedData?: BacktestData | null;
  pinnedConfig?: Record<string, unknown>;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const [showExecutors, setShowExecutors] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [chartExpanded, setChartExpanded] = useState(false);

  const controllerConfig = (taskConfig?.config ?? {}) as Record<string, unknown>;
  const controllerName = String(controllerConfig.controller_name ?? "");
  const configId = String(controllerConfig.id ?? "");

  // Close types total for stacked bar
  const closeTypesTotal = Object.values(data.closeTypes).reduce((s, v) => s + v, 0);

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

      {/* Hero stat cards: Net PnL & Return (larger) */}
      <div className="grid grid-cols-2 gap-3">
        <HeroStatCard
          label="Net PnL"
          value={formatPnl(data.netPnlQuote)}
          pnl={data.netPnlQuote}
        />
        <HeroStatCard
          label="Return"
          value={data.netPnlPct ? formatPct(data.netPnlPct) : "\u2014"}
          pnl={data.netPnlPct}
        />
      </div>

      {/* Secondary stat cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Executors" value={String(data.totalExecutors)} />
        <StatCard
          label="Sharpe Ratio"
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

      {/* Accuracy & fees row */}
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

      {/* Close types - stacked bar */}
      {closeTypesTotal > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3">
          <div className="text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wide mb-2">
            Close Type Distribution
          </div>
          {/* Stacked bar */}
          <div className="flex h-3 rounded-full overflow-hidden mb-2">
            {Object.entries(data.closeTypes).map(([type, count]) => {
              const ct = CLOSE_TYPE_LABELS[type];
              const pct = (count / closeTypesTotal) * 100;
              if (pct < 0.5) return null;
              return (
                <div
                  key={type}
                  className="h-full transition-all"
                  style={{
                    width: `${pct}%`,
                    backgroundColor: ct?.color ?? "#78909c",
                  }}
                  title={`${ct?.label ?? type}: ${count} (${pct.toFixed(1)}%)`}
                />
              );
            })}
          </div>
          {/* Legend */}
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(data.closeTypes).map(([type, count]) => {
              const ct = CLOSE_TYPE_LABELS[type];
              const pct = (count / closeTypesTotal) * 100;
              return (
                <div key={type} className="flex items-center gap-1.5 text-xs">
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: ct?.color ?? "#78909c" }}
                  />
                  <span className="font-medium">{ct?.label ?? type}</span>
                  <span className="text-[var(--color-text-muted)]">{count}</span>
                  <span className="text-[var(--color-text-muted)]">({pct.toFixed(0)}%)</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Comparison table */}
      {pinnedData && (
        <ComparisonTable current={data} pinned={pinnedData} pinnedConfig={pinnedConfig} currentConfig={taskConfig} />
      )}

      {/* Chart */}
      {data.candles.length > 0 && (
        <>
          <div className="relative">
            <button
              onClick={() => setChartExpanded(true)}
              className="absolute top-2 right-2 z-10 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-primary)]/50 transition-colors"
              title="Expand chart"
            >
              <Maximize2 className="h-4 w-4" />
            </button>
            <BacktestChart data={data} />
          </div>

          {chartExpanded && (
            <div className="fixed inset-0 z-50 flex flex-col bg-[var(--color-bg)]">
              <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
                <span className="text-sm font-medium">Backtest Report</span>
                <button
                  onClick={() => setChartExpanded(false)}
                  className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
                  title="Close"
                >
                  <Minimize2 className="h-4 w-4" />
                </button>
              </div>
              <div className="flex-1 overflow-auto p-4">
                <BacktestChart data={data} />
              </div>
            </div>
          )}
        </>
      )}

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

// -- Comparison Table --

function ComparisonTable({
  current,
  pinned,
  currentConfig,
  pinnedConfig,
}: {
  current: BacktestData;
  pinned: BacktestData;
  currentConfig?: Record<string, unknown>;
  pinnedConfig?: Record<string, unknown>;
}) {
  const currentName = String((currentConfig?.config as Record<string, unknown>)?.id ?? "Current");
  const pinnedName = String((pinnedConfig?.config as Record<string, unknown>)?.id ?? "Pinned");

  const rows: { label: string; currentVal: string; pinnedVal: string; currentColor?: string; pinnedColor?: string }[] = [
    {
      label: "Net PnL",
      currentVal: formatPnl(current.netPnlQuote),
      pinnedVal: formatPnl(pinned.netPnlQuote),
      currentColor: pnlColor(current.netPnlQuote),
      pinnedColor: pnlColor(pinned.netPnlQuote),
    },
    {
      label: "Return",
      currentVal: current.netPnlPct ? formatPct(current.netPnlPct) : "\u2014",
      pinnedVal: pinned.netPnlPct ? formatPct(pinned.netPnlPct) : "\u2014",
      currentColor: pnlColor(current.netPnlPct),
      pinnedColor: pnlColor(pinned.netPnlPct),
    },
    {
      label: "Sharpe",
      currentVal: current.sharpeRatio ? current.sharpeRatio.toFixed(2) : "\u2014",
      pinnedVal: pinned.sharpeRatio ? pinned.sharpeRatio.toFixed(2) : "\u2014",
    },
    {
      label: "Profit Factor",
      currentVal: current.profitFactor ? current.profitFactor.toFixed(2) : "\u2014",
      pinnedVal: pinned.profitFactor ? pinned.profitFactor.toFixed(2) : "\u2014",
    },
    {
      label: "Max Drawdown",
      currentVal: current.maxDrawdownUsd ? formatUsd(-Math.abs(current.maxDrawdownUsd)) : "\u2014",
      pinnedVal: pinned.maxDrawdownUsd ? formatUsd(-Math.abs(pinned.maxDrawdownUsd)) : "\u2014",
      currentColor: "var(--color-red)",
      pinnedColor: "var(--color-red)",
    },
    {
      label: "Executors",
      currentVal: String(current.totalExecutors),
      pinnedVal: String(pinned.totalExecutors),
    },
    {
      label: "Volume",
      currentVal: current.totalVolume ? formatUsd(current.totalVolume) : "\u2014",
      pinnedVal: pinned.totalVolume ? formatUsd(pinned.totalVolume) : "\u2014",
    },
    {
      label: "Fees",
      currentVal: current.totalFees ? formatUsd(current.totalFees) : "\u2014",
      pinnedVal: pinned.totalFees ? formatUsd(pinned.totalFees) : "\u2014",
    },
  ];

  return (
    <div className="rounded-lg border border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--color-primary)]/20">
        <Pin className="h-3.5 w-3.5 text-[var(--color-primary)]" />
        <span className="text-xs font-medium text-[var(--color-primary)] uppercase tracking-wide">Comparison</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="px-4 py-2 text-left text-xs font-medium text-[var(--color-text-muted)]">Metric</th>
              <th className="px-4 py-2 text-right text-xs font-medium text-[var(--color-text)]">
                {currentName}
                <span className="ml-1 text-[10px] text-[var(--color-text-muted)]">(selected)</span>
              </th>
              <th className="px-4 py-2 text-right text-xs font-medium text-[var(--color-text-muted)]">
                {pinnedName}
                <span className="ml-1 text-[10px]">(pinned)</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label} className="border-b border-[var(--color-border)] last:border-b-0">
                <td className="px-4 py-1.5 text-xs text-[var(--color-text-muted)]">{row.label}</td>
                <td
                  className="px-4 py-1.5 text-right text-xs font-semibold tabular-nums"
                  style={row.currentColor ? { color: row.currentColor } : undefined}
                >
                  {row.currentVal}
                </td>
                <td
                  className="px-4 py-1.5 text-right text-xs font-semibold tabular-nums"
                  style={row.pinnedColor ? { color: row.pinnedColor } : undefined}
                >
                  {row.pinnedVal}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// -- Stat Cards --

function HeroStatCard({
  label,
  value,
  pnl,
}: {
  label: string;
  value: string;
  pnl: number;
}) {
  const isPositive = pnl >= 0;
  return (
    <div
      className="rounded-lg border px-4 py-4"
      style={{
        borderColor: isPositive ? "rgba(38,166,154,0.3)" : "rgba(239,83,80,0.3)",
        backgroundColor: isPositive ? "rgba(38,166,154,0.05)" : "rgba(239,83,80,0.05)",
      }}
    >
      <div className="text-[11px] font-medium text-[var(--color-text-muted)] uppercase tracking-wide">
        {label}
      </div>
      <div
        className="mt-1 text-2xl font-bold tabular-nums"
        style={{ color: pnlColor(pnl) }}
      >
        {value}
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
