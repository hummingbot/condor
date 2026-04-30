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

interface ExecutorChartProps {
  server: string;
  executors: ExecutorInfo[];
  connector: string;
  tradingPair: string;
  interval?: string;
  height?: number;
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

function formatPnl(pnl: number): string {
  const sign = pnl >= 0 ? "+" : "";
  if (Math.abs(pnl) >= 1000) return `${sign}$${(pnl / 1000).toFixed(1)}K`;
  return `${sign}$${pnl.toFixed(2)}`;
}

export function ExecutorChart({
  server,
  executors,
  connector,
  tradingPair,
  interval = "1m",
  height = 350,
}: ExecutorChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
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
        const pnlClr = o.pnl >= 0 ? "#22c55e" : "#ef4444";
        const pnlStr = formatPnl(o.pnl);
        const pctStr = o.pnlPct !== 0 ? ` (${o.pnlPct > 0 ? "+" : ""}${(o.pnlPct * 100).toFixed(2)}%)` : "";

        let detailLine = "";
        if (o.segment) {
          detailLine = `<div style="color:#9ca3af;font-size:10px">Entry: ${o.segment.entryPrice.toPrecision(6)} → Exit: ${o.segment.exitPrice.toPrecision(6)}</div>`;
        } else if (o.gridBox) {
          detailLine = `<div style="color:#9ca3af;font-size:10px">Range: ${o.gridBox.startPrice.toPrecision(6)} – ${o.gridBox.endPrice.toPrecision(6)}</div>`;
        }

        tooltip.innerHTML = `
          <div style="font-weight:600;margin-bottom:2px">${o.executorId.slice(0, 8)}… · ${o.type.toUpperCase()} ${o.side.toUpperCase()}</div>
          <div style="color:${pnlClr}">${pnlStr}${pctStr}</div>
          ${detailLine}
          <div style="color:#9ca3af;font-size:10px">${o.status} · ${o.closeType || "—"}</div>
        `;
        tooltip.style.display = "block";

        // Position tooltip
        const containerRect = containerRef.current.getBoundingClientRect();
        let left = param.point.x + 16;
        if (left + 200 > containerRect.width) left = param.point.x - 210;
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
      {/* Chart area */}
      <div style={{ position: "relative", flex: fullscreen ? 1 : undefined }}>
        <div
          ref={containerRef}
          style={{ height: fullscreen ? "100%" : height, width: "100%" }}
        />
        {/* Tooltip overlay */}
        <div
          ref={tooltipRef}
          style={{
            display: "none",
            position: "absolute",
            top: 0,
            left: 0,
            zIndex: 10,
            pointerEvents: "none",
            background: "rgba(15, 21, 37, 0.95)",
            border: "1px solid rgba(107, 121, 148, 0.3)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 11,
            color: "#e2e8f0",
            maxWidth: 220,
            whiteSpace: "nowrap",
            lineHeight: 1.4,
            backdropFilter: "blur(8px)",
          }}
        />
      </div>
    </div>
  );
}
