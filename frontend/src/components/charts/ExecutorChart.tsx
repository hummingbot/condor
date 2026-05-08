import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";

import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { api, type ExecutorInfo } from "@/lib/api";
import {
  computeMultiOverlays,
  getExecutorColor,
  getOverlayTimeRange,
  type ExecutorOverlay,
} from "@/lib/executor-overlays";

export interface SnapshotBubble {
  tick: number;
  timestamp: string; // human-readable, e.g. "2024-01-15 14:30:22"
  agentResponse?: string;
  toolCallCount?: number;
}

interface ExecutorChartProps {
  server: string;
  executors: ExecutorInfo[];
  connector: string;
  tradingPair: string;
  interval?: string;
  height?: number;
  snapshots?: SnapshotBubble[];
  onSnapshotClick?: (tick: number) => void;
}

function getChartColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    bg: style.getPropertyValue("--chart-bg").trim() || "#0f1525",
    grid: style.getPropertyValue("--chart-grid").trim() || "#1c2541",
    text: style.getPropertyValue("--chart-text").trim() || "#6b7994",
    up: style.getPropertyValue("--chart-up").trim() || "#22c55e",
    down: style.getPropertyValue("--chart-down").trim() || "#ef4444",
  };
}

const isActive = (status: string) => {
  const s = status?.toLowerCase() ?? "";
  return s === "running" || s === "active_position" || s === "active";
};

function tsToSeconds(ts: number): number {
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

/** Parse snapshot timestamp string to unix seconds */
function parseSnapshotTs(ts: string): number {
  // Handle formats like "2024-01-15 14:30:22" or ISO
  const d = new Date(ts.replace(" ", "T"));
  if (isNaN(d.getTime())) return 0;
  return Math.floor(d.getTime() / 1000);
}

export function ExecutorChart({
  server,
  executors,
  connector,
  tradingPair,
  interval = "1m",
  height = 350,
  snapshots,
  onSnapshotClick,
}: ExecutorChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const snapshotTooltipRef = useRef<HTMLDivElement>(null);
  const bubblesRef = useRef<HTMLDivElement>(null);
  const chartModuleRef = useRef<typeof import("lightweight-charts") | null>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);
  const seriesRef = useRef<import("lightweight-charts").ISeriesApi<"Candlestick"> | null>(null);
  const segmentSeriesRef = useRef<import("lightweight-charts").ISeriesApi<"Line">[]>([]);
  const overlaysRef = useRef<ExecutorOverlay[]>([]);
  const initializedRef = useRef(false);
  const [chartReady, setChartReady] = useState(false);

  // Compute overlays
  const overlays = useMemo(() => computeMultiOverlays(executors), [executors]);
  overlaysRef.current = overlays;
  const timeRange = useMemo(() => getOverlayTimeRange(overlays), [overlays]);

  // Determine if any executor is active (for WS subscription)
  const hasActive = executors.some((ex) => isActive(ex.status));

  // WS for non-candle updates (candle streams managed by candleStore)
  const channels = useMemo(() => [] as string[], []);
  useCondorWebSocket(channels, server);

  // Pad time range for candle fetch
  const paddingSeconds = 1800;
  const startTime = Math.floor(timeRange.start - paddingSeconds);
  const endTime = Math.ceil(timeRange.end + paddingSeconds);

  const { data: candles, isLoading, isError } = useQuery({
    queryKey: ["candles", server, connector, tradingPair, interval],
    queryFn: () => api.getCandles(server, connector, tradingPair, interval, 5000, startTime, endTime),
    enabled: !!server && !!connector && !!tradingPair,
    retry: 1,
  });

  // Initialize chart
  useEffect(() => {
    let cancelled = false;
    import("lightweight-charts").then((mod) => {
      if (cancelled || !containerRef.current) return;
      chartModuleRef.current = mod;

      const colors = getChartColors();
      const chart = mod.createChart(containerRef.current, {
        autoSize: true,
        layout: {
          background: { type: mod.ColorType.Solid, color: colors.bg },
          textColor: colors.text,
        },
        grid: {
          vertLines: { color: colors.grid },
          horzLines: { color: colors.grid },
        },
        crosshair: { mode: mod.CrosshairMode.Normal },
        timeScale: { timeVisible: true, secondsVisible: false },
        rightPriceScale: { borderVisible: false },
        localization: {
          priceFormatter: (price: number) => {
            if (Math.abs(price) >= 1000) return price.toFixed(2);
            if (Math.abs(price) >= 1) return price.toFixed(4);
            return price.toPrecision(6);
          },
        },
      });
      chartRef.current = chart;

      const series = chart.addSeries(mod.CandlestickSeries, {
        upColor: colors.up,
        downColor: colors.down,
        wickUpColor: colors.up,
        wickDownColor: colors.down,
        borderVisible: false,
      });
      seriesRef.current = series;

      // Crosshair tooltip handler
      chart.subscribeCrosshairMove((param) => {
        const tooltip = tooltipRef.current;
        if (!tooltip || !containerRef.current) return;

        if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
          tooltip.style.display = "none";
          return;
        }

        const crosshairTime = typeof param.time === "number" ? param.time : 0;
        if (!crosshairTime) {
          tooltip.style.display = "none";
          return;
        }

        // Find the closest overlay to the crosshair
        let bestOverlay: ExecutorOverlay | null = null;
        let bestDist = Infinity;
        const cursorY = param.point?.y ?? 0;

        for (const overlay of overlaysRef.current) {
          // Check grid box
          const box = overlay.gridBox;
          if (box) {
            const t1 = tsToSeconds(box.startTime);
            const t2 = tsToSeconds(box.endTime);
            if (crosshairTime < t1 - 60 || crosshairTime > t2 + 60) continue;
            // Check if cursor Y is within the box price range
            const topY = series.priceToCoordinate(Math.max(box.startPrice, box.endPrice));
            const botY = series.priceToCoordinate(Math.min(box.startPrice, box.endPrice));
            if (topY === null || botY === null) continue;
            const minY = Math.min(topY, botY);
            const maxY = Math.max(topY, botY);
            // Distance: 0 if inside box, else distance to nearest edge
            const dist = cursorY >= minY && cursorY <= maxY ? 0 : Math.min(Math.abs(cursorY - minY), Math.abs(cursorY - maxY));
            if (dist < bestDist && dist < 30) {
              bestDist = dist;
              bestOverlay = overlay;
            }
            continue;
          }

          // Check segment
          const seg = overlay.segment;
          if (!seg) continue;
          const entryT = tsToSeconds(seg.entryTime);
          const exitT = tsToSeconds(seg.exitTime);
          if (crosshairTime < entryT - 60 || crosshairTime > exitT + 60) continue;
          const tFrac = exitT === entryT ? 0.5 : (crosshairTime - entryT) / (exitT - entryT);
          const expectedPrice = seg.entryPrice + tFrac * (seg.exitPrice - seg.entryPrice);
          const priceY = series.priceToCoordinate(expectedPrice);
          if (priceY === null) continue;
          const dist = Math.abs(cursorY - priceY);
          if (dist < bestDist && dist < 30) {
            bestDist = dist;
            bestOverlay = overlay;
          }
        }

        if (!bestOverlay) {
          tooltip.style.display = "none";
          return;
        }

        const o = bestOverlay;
        // Read theme colors for tooltip content
        const cs = getComputedStyle(document.documentElement);
        const textMuted = cs.getPropertyValue("--color-text-muted").trim() || "#6b7994";
        const textColor = cs.getPropertyValue("--color-text").trim() || "#e2e8f0";
        const borderColor = cs.getPropertyValue("--color-border").trim() || "#1c2541";

        const pnlClr = o.pnl >= 0 ? "#22c55e" : "#ef4444";
        const pnlSign = o.pnl >= 0 ? "+" : "";
        const pnlStr = Math.abs(o.pnl) >= 1000 ? `${pnlSign}$${(o.pnl / 1000).toFixed(1)}K` : `${pnlSign}$${o.pnl.toFixed(2)}`;
        const pctStr = o.pnlPct !== 0 ? `${o.pnlPct > 0 ? "+" : ""}${(o.pnlPct * 100).toFixed(2)}%` : "";
        const volStr = Math.abs(o.volume) >= 1000 ? `$${(o.volume / 1000).toFixed(1)}K` : `$${o.volume.toFixed(0)}`;
        const feesStr = o.fees ? `$${o.fees.toFixed(2)}` : "";

        const sideClr = o.side === "buy" ? "#22c55e" : "#ef4444";
        const sideBg = o.side === "buy" ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)";
        const statusBg = isActive(o.status) ? "rgba(34,197,94,0.15)" : "rgba(156,163,175,0.15)";
        const statusClr = isActive(o.status) ? "#22c55e" : textMuted;

        // Build config detail rows
        const cfg = o.config || {};
        const tripleBarrier: Record<string, unknown> = (() => {
          const raw = cfg.triple_barrier_config;
          if (!raw) return {};
          if (typeof raw === "string") { try { return JSON.parse(raw); } catch { return {}; } }
          return typeof raw === "object" ? (raw as Record<string, unknown>) : {};
        })();

        let detailRows = "";
        const fmtPrice = (p: number) => {
          if (p === 0) return "—";
          if (Math.abs(p) >= 1000) return p.toFixed(2);
          if (Math.abs(p) >= 1) return p.toFixed(4);
          return p.toPrecision(6);
        };
        const fmtUsd = (v: number) => {
          if (Math.abs(v) >= 1_000_000) return "$" + (v / 1_000_000).toFixed(2) + "M";
          if (Math.abs(v) >= 10_000) return "$" + (v / 1_000).toFixed(1) + "K";
          return "$" + v.toFixed(2);
        };

        const addRow = (label: string, value: string, color?: string) => {
          detailRows += `<div style="display:flex;justify-content:space-between;gap:12px"><span style="color:${textMuted}">${label}</span><span style="font-family:monospace;${color ? `color:${color}` : `color:${textColor}`}">${value}</span></div>`;
        };

        if (o.type === "grid" && o.gridBox) {
          addRow("Start Price", fmtPrice(o.gridBox.startPrice));
          addRow("End Price", fmtPrice(o.gridBox.endPrice));
          if (o.gridBox.limitPrice) addRow("Limit Price", fmtPrice(o.gridBox.limitPrice));
        } else if (o.segment) {
          addRow("Entry", fmtPrice(o.segment.entryPrice));
          if (o.segment.exitPrice > 0 && o.segment.exitPrice !== o.segment.entryPrice) {
            addRow(isActive(o.status) ? "Current" : "Close", fmtPrice(o.segment.exitPrice));
          }
        }

        if (cfg.leverage != null && Number(cfg.leverage) > 1) addRow("Leverage", `${cfg.leverage}x`);
        if (cfg.total_amount_quote != null) addRow("Amount", fmtUsd(Number(cfg.total_amount_quote)));
        else if (cfg.amount != null && Number(cfg.amount) > 0) addRow("Amount", String(cfg.amount));

        const tp = Number(tripleBarrier.take_profit || cfg.take_profit);
        if (tp > 0 && tp !== -1) addRow("Take Profit", `${(tp * 100).toFixed(2)}%`, "#22c55e");
        const sl = Number(cfg.stop_loss);
        if (sl > 0 && sl !== -1) addRow("Stop Loss", `${(sl * 100).toFixed(2)}%`, "#ef4444");

        tooltip.innerHTML = `
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
            <span style="font-weight:700;font-size:12px;font-family:monospace;color:${textColor}">${o.executorId.slice(0, 10)}\u2026</span>
            <span style="background:${sideBg};color:${sideClr};font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;text-transform:uppercase">${o.side}</span>
            <span style="background:${statusBg};color:${statusClr};font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px">${o.status}</span>
          </div>
          <div style="display:flex;align-items:center;gap:4px;margin-bottom:2px">
            <span style="background:${borderColor};padding:1px 5px;border-radius:3px;font-size:10px;border:1px solid ${borderColor};color:${textColor}">${o.type.toUpperCase()}</span>
            ${o.closeType ? `<span style="font-size:10px;color:${textMuted}">${o.closeType}</span>` : ""}
          </div>
          <div style="border-top:1px solid ${borderColor};margin:6px 0;padding-top:6px;display:grid;grid-template-columns:1fr 1fr;gap:4px 16px">
            <div><div style="color:${textMuted};font-size:9px;text-transform:uppercase;margin-bottom:1px">Net PnL</div><div style="font-weight:600;font-size:13px;color:${pnlClr};font-family:monospace">${pnlStr}</div></div>
            <div><div style="color:${textMuted};font-size:9px;text-transform:uppercase;margin-bottom:1px">PnL %</div><div style="font-weight:600;font-size:13px;color:${pnlClr};font-family:monospace">${pctStr || "—"}</div></div>
            <div><div style="color:${textMuted};font-size:9px;text-transform:uppercase;margin-bottom:1px">Volume</div><div style="font-family:monospace;font-size:11px;color:${textColor}">${volStr}</div></div>
            <div><div style="color:${textMuted};font-size:9px;text-transform:uppercase;margin-bottom:1px">Fees</div><div style="font-family:monospace;font-size:11px;color:${textColor}">${feesStr || "—"}</div></div>
          </div>
          ${detailRows ? `<div style="border-top:1px solid ${borderColor};margin-top:4px;padding-top:6px;font-size:11px;display:flex;flex-direction:column;gap:3px">${detailRows}</div>` : ""}
        `;
        tooltip.style.display = "block";

        // Position tooltip on opposite side of cursor
        const containerRect = containerRef.current.getBoundingClientRect();
        const tooltipW = 280;
        const cursorInRightHalf = param.point.x > containerRect.width / 2;
        let left = cursorInRightHalf
          ? param.point.x - tooltipW - 16
          : param.point.x + 16;
        if (left < 4) left = 4;
        if (left + tooltipW > containerRect.width - 4) left = containerRect.width - tooltipW - 4;
        let top = param.point.y - 10;
        if (top < 0) top = 4;

        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${top}px`;
      });

      setChartReady(true);
    });

    return () => {
      cancelled = true;
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        seriesRef.current = null;
        segmentSeriesRef.current = [];
      }
      setChartReady(false);
    };
  }, []);

  // Set initial candle data
  useEffect(() => {
    if (!chartReady || !seriesRef.current || !candles?.length || !chartModuleRef.current) return;

    const mapped = candles.map((c) => {
      const ts = c.timestamp > 1e12 ? c.timestamp / 1000 : c.timestamp;
      return {
        time: ts as import("lightweight-charts").UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      };
    });
    seriesRef.current.setData(mapped);
    if (!initializedRef.current) {
      chartRef.current?.timeScale().fitContent();
      initializedRef.current = true;
    }
  }, [candles, chartReady]);

  // Reset on pair/interval change
  useEffect(() => {
    initializedRef.current = false;
  }, [tradingPair, interval]);

  // Apply overlays: segments, price lines, markers
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    const mod = chartModuleRef.current;
    if (!series || !chart || !mod || !chartReady) return;

    // Clean up old segment series
    for (const s of segmentSeriesRef.current) {
      try { chart.removeSeries(s); } catch { /* ok */ }
    }
    segmentSeriesRef.current = [];

    const isMulti = overlays.length > 1;

    overlays.forEach((overlay: ExecutorOverlay, idx: number) => {
      const color = isMulti ? getExecutorColor(idx, overlay.pnl) : undefined;

      // Grid executor → draw a box (top, bottom, limit lines)
      const box = overlay.gridBox;
      if (box) {
        const boxColor = color ?? box.color;
        const t1 = tsToSeconds(box.startTime);
        const t2 = tsToSeconds(box.endTime);
        type TS = import("lightweight-charts").UTCTimestamp;

        // Need at least 4 seconds span for the outline shape
        const span = t2 - t1;
        if (span < 4) {
          // Too short for a box — just draw a vertical segment
          const seg = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 2,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          seg.setData([
            { time: t1 as TS, value: box.startPrice },
            { time: (t1 + 1) as TS, value: box.endPrice },
          ]);
          segmentSeriesRef.current.push(seg);
          return;
        }

        try {
          // Top edge (end_price)
          const top = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 2,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          top.setData([
            { time: t1 as TS, value: box.endPrice },
            { time: t2 as TS, value: box.endPrice },
          ]);
          segmentSeriesRef.current.push(top);

          // Bottom edge (start_price) — dashed
          const bottom = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 2, lineStyle: mod.LineStyle.Dashed,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          bottom.setData([
            { time: t1 as TS, value: box.startPrice },
            { time: t2 as TS, value: box.startPrice },
          ]);
          segmentSeriesRef.current.push(bottom);

          // Left edge (vertical at start)
          const left = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          left.setData([
            { time: t1 as TS, value: box.startPrice },
            { time: (t1 + 1) as TS, value: box.endPrice },
          ]);
          segmentSeriesRef.current.push(left);

          // Right edge (vertical at end)
          const right = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          right.setData([
            { time: (t2 - 1) as TS, value: box.endPrice },
            { time: t2 as TS, value: box.startPrice },
          ]);
          segmentSeriesRef.current.push(right);

          // Limit price line (if present) — dotted red
          if (box.limitPrice) {
            const limit = chart.addSeries(mod.LineSeries, {
              color: "#ef4444", lineWidth: 1, lineStyle: mod.LineStyle.Dotted,
              priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
            });
            limit.setData([
              { time: t1 as TS, value: box.limitPrice },
              { time: t2 as TS, value: box.limitPrice },
            ]);
            segmentSeriesRef.current.push(limit);
          }
        } catch { /* grid box rendering failed — skip */ }
        return;
      }

      // Position/order/generic executor → segment line from entry to exit
      const seg = overlay.segment;
      if (!seg) return;

      const segColor = color ?? seg.color;
      const entryT = tsToSeconds(seg.entryTime);
      const exitT = tsToSeconds(seg.exitTime);

      // Order executors: solid line when active (horizontal), dashed otherwise
      const isOrderActive = overlay.type === "order" && isActive(overlay.status);
      const lineStyle = isOrderActive ? mod.LineStyle.Solid : mod.LineStyle.Dashed;

      const lineSeries = chart.addSeries(mod.LineSeries, {
        color: segColor,
        lineWidth: 2,
        lineStyle,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });

      lineSeries.setData([
        { time: entryT as import("lightweight-charts").UTCTimestamp, value: seg.entryPrice },
        { time: exitT as import("lightweight-charts").UTCTimestamp, value: seg.exitPrice },
      ]);

      segmentSeriesRef.current.push(lineSeries);
    });
  }, [overlays, chartReady]);

  // Position snapshot bubbles along the time axis
  const snapshotPositions = useRef<{ tick: number; x: number }[]>([]);

  const updateBubblePositions = useCallback(() => {
    const chart = chartRef.current;
    const bubbles = bubblesRef.current;
    if (!chart || !bubbles || !snapshots?.length) return;

    const ts = chart.timeScale();
    const positions: { tick: number; x: number }[] = [];
    const children = bubbles.children;

    for (let i = 0; i < snapshots.length; i++) {
      const snap = snapshots[i];
      const time = parseSnapshotTs(snap.timestamp);
      if (!time) continue;
      const x = ts.timeToCoordinate(time as import("lightweight-charts").UTCTimestamp);
      if (x === null) {
        if (children[i]) (children[i] as HTMLElement).style.display = "none";
        continue;
      }
      positions.push({ tick: snap.tick, x });
      if (children[i]) {
        const el = children[i] as HTMLElement;
        el.style.display = "";
        el.style.left = `${x}px`;
      }
    }
    snapshotPositions.current = positions;
  }, [snapshots]);

  // Subscribe to time scale changes for bubble repositioning
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !chartReady || !snapshots?.length) return;

    updateBubblePositions();
    const handler = () => updateBubblePositions();
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler);
    return () => { chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler); };
  }, [chartReady, snapshots, updateBubblePositions]);

  const [fullscreen, setFullscreen] = useState(false);

  const toggleFullscreen = useCallback(() => {
    setFullscreen((prev) => !prev);
  }, []);

  // Close fullscreen on Escape
  useEffect(() => {
    if (!fullscreen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [fullscreen]);

  // Force chart to recalculate dimensions when fullscreen toggles
  useEffect(() => {
    if (!chartRef.current) return;
    // Small delay so the CSS transition / layout has settled
    const timer = setTimeout(() => {
      chartRef.current?.resize(
        containerRef.current?.clientWidth ?? 0,
        containerRef.current?.clientHeight ?? 0,
      );
      chartRef.current?.timeScale().fitContent();
    }, 50);
    return () => clearTimeout(timer);
  }, [fullscreen]);

  return (
    <div
      className={
        fullscreen
          ? "fixed inset-0 z-50 flex flex-col bg-[var(--color-bg)]"
          : "rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden"
      }
    >
      {/* Header bar */}
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <p className="text-[10px] text-[var(--color-text-muted)]">
          {tradingPair} &middot; {interval}
          {hasActive && (
            <span className="ml-2 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
          )}
        </p>
        <div className="flex items-center gap-2">
          {isLoading && (
            <span className="text-[10px] text-[var(--color-text-muted)]">Loading...</span>
          )}
          {isError && (
            <span className="text-[10px] text-red-400">Failed to load candles</span>
          )}
          {!isLoading && !isError && candles && candles.length === 0 && (
            <span className="text-[10px] text-[var(--color-text-muted)]">No candle data</span>
          )}
          {overlays.length > 1 && (
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {overlays.length} executors overlaid
            </span>
          )}
          <button
            onClick={toggleFullscreen}
            className="p-0.5 rounded hover:bg-[var(--color-surface-hover)] transition-colors text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            title={fullscreen ? "Exit fullscreen (Esc)" : "Fullscreen"}
          >
            {fullscreen ? (
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="4 14 10 14 10 20" /><polyline points="20 10 14 10 14 4" />
                <line x1="14" y1="10" x2="21" y2="3" /><line x1="3" y1="21" x2="10" y2="14" />
              </svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 3 21 3 21 9" /><polyline points="9 21 3 21 3 15" />
                <line x1="21" y1="3" x2="14" y2="10" /><line x1="3" y1="21" x2="10" y2="14" />
              </svg>
            )}
          </button>
        </div>
      </div>
      {/* Snapshot bubble strip */}
      {snapshots && snapshots.length > 0 && (
        <div
          style={{ position: "relative", height: 28, borderBottom: "1px solid var(--color-border)", background: "var(--color-bg)", overflow: "hidden" }}
        >
          <div ref={bubblesRef} style={{ position: "absolute", inset: 0 }}>
            {snapshots.map((snap, i) => (
              <div
                key={snap.tick}
                data-idx={i}
                style={{
                  position: "absolute",
                  top: 4,
                  transform: "translateX(-50%)",
                  display: "none", // positioned by updateBubblePositions
                }}
                className="group cursor-pointer"
                onClick={() => onSnapshotClick?.(snap.tick)}
                onMouseEnter={(e) => {
                  const tip = snapshotTooltipRef.current;
                  const wrapper = wrapperRef.current;
                  if (!tip || !wrapper) return;

                  const cs = getComputedStyle(document.documentElement);
                  const muted = cs.getPropertyValue("--color-text-muted").trim() || "#6b7994";
                  const txt = cs.getPropertyValue("--color-text").trim() || "#e2e8f0";
                  const bdr = cs.getPropertyValue("--color-border").trim() || "#1c2541";

                  const preview = snap.agentResponse
                    ? snap.agentResponse.length > 280
                      ? snap.agentResponse.slice(0, 280) + "..."
                      : snap.agentResponse
                    : "No response recorded";
                  const toolLine = snap.toolCallCount
                    ? `<div style="margin-top:4px;font-size:10px;color:${muted}">${snap.toolCallCount} tool call${snap.toolCallCount !== 1 ? "s" : ""}</div>`
                    : "";

                  tip.innerHTML = `
                    <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
                      <span style="background:rgba(139,92,246,0.15);color:#a78bfa;font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px">TICK #${snap.tick}</span>
                      <span style="font-size:10px;color:${muted}">${snap.timestamp}</span>
                    </div>
                    <div style="font-size:11px;line-height:1.5;color:${txt};white-space:pre-wrap;word-break:break-word">${preview.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>
                    ${toolLine}
                    <div style="margin-top:6px;font-size:9px;color:${muted};border-top:1px solid ${bdr};padding-top:4px">Click to view full snapshot</div>
                  `;
                  tip.style.display = "block";

                  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                  const wrapperRect = wrapper.getBoundingClientRect();
                  const tipW = 320;
                  let left = rect.left - wrapperRect.left + rect.width / 2 - tipW / 2;
                  if (left < 4) left = 4;
                  if (left + tipW > wrapperRect.width - 4) left = wrapperRect.width - tipW - 4;
                  tip.style.left = `${left}px`;
                  tip.style.top = `${28 + 4}px`;
                }}
                onMouseLeave={() => {
                  if (snapshotTooltipRef.current) snapshotTooltipRef.current.style.display = "none";
                }}
              >
                <div
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: "50%",
                    background: "rgba(139, 92, 246, 0.15)",
                    border: "1.5px solid rgba(139, 92, 246, 0.5)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 9,
                    fontWeight: 700,
                    color: "#a78bfa",
                    transition: "all 150ms",
                  }}
                  className="group-hover:!bg-[rgba(139,92,246,0.3)] group-hover:!border-[rgba(139,92,246,0.8)] group-hover:scale-110"
                >
                  {snap.tick}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Chart area */}
      <div ref={wrapperRef} style={{ position: "relative", flex: fullscreen ? 1 : undefined }}>
        <div
          ref={containerRef}
          style={{ height: fullscreen ? "100%" : height, width: "100%" }}
        />
        {/* Executor tooltip overlay */}
        <div
          ref={tooltipRef}
          className="chart-tooltip"
          style={{
            display: "none",
            position: "absolute",
            top: 0,
            left: 0,
            zIndex: 10,
            pointerEvents: "none",
            background: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 11,
            color: "var(--color-text)",
            maxWidth: 280,
            minWidth: 200,
            lineHeight: 1.4,
            backdropFilter: "blur(8px)",
            boxShadow: "0 4px 16px rgba(0,0,0,0.2)",
          }}
        />
        {/* Snapshot tooltip overlay */}
        <div
          ref={snapshotTooltipRef}
          className="chart-tooltip"
          style={{
            display: "none",
            position: "absolute",
            top: 0,
            left: 0,
            zIndex: 20,
            pointerEvents: "none",
            background: "var(--color-surface)",
            border: "1px solid rgba(139, 92, 246, 0.3)",
            borderRadius: 8,
            padding: "10px 14px",
            fontSize: 11,
            color: "var(--color-text)",
            maxWidth: 360,
            minWidth: 240,
            maxHeight: 300,
            overflow: "hidden",
            lineHeight: 1.4,
            backdropFilter: "blur(12px)",
            boxShadow: "0 4px 20px rgba(0,0,0,0.25)",
          }}
        />
      </div>
    </div>
  );
}
