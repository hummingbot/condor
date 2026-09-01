import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";

import { PairLabel } from "@/components/executor/PairLabel";
import { useRates } from "@/hooks/useRates";
import { api, type ExecutorInfo } from "@/lib/api";
import {
  computeMultiOverlays,
  getExecutorColor,
  getOverlayTimeRange,
  getPoolAddress,
  renderOverlayTooltipHtml,
  type ExecutorOverlay,
} from "@/lib/executor-overlays";
import { tsToSeconds } from "@/lib/formatters";
import { geckoIntervalForSpan } from "@/lib/gecko-candles";
import { candlesQuery } from "@/lib/queryClient";
import { getThemeColors } from "@/lib/theme-colors";

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

const isActive = (status: string) => {
  const s = status?.toLowerCase() ?? "";
  return s === "running" || s === "active_position" || s === "active";
};

/** Vertical line definition for grid box edges drawn directly on the canvas */
interface GridVerticalLine {
  time: number;
  topPrice: number;
  bottomPrice: number;
  color: string;
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
  interval: requestedInterval,
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
  const gridVerticalLinesRef = useRef<GridVerticalLine[]>([]);
  const overlaysRef = useRef<ExecutorOverlay[]>([]);
  const initializedRef = useRef(false);
  const [chartReady, setChartReady] = useState(false);

  // The hover card spells its amounts in the reader's display currency, the
  // same as the trade chart's (ARCH-207) — the quote asset comes from the pair
  // this chart is already drawing, so nothing new is threaded in from callers.
  // The refs keep the imperative lightweight-charts crosshair callback reading
  // the current formatters without being re-subscribed on every rate tick.
  const quoteCurrency = tradingPair.split("-")[1] || "USDT";
  const quoteCurrencies = useMemo(() => [quoteCurrency], [quoteCurrency]);
  const { formatPnlValue, formatValue } = useRates(quoteCurrencies);
  const convertValueRef = useRef<(val: number) => string>(() => "");
  const convertPnlRef = useRef<(val: number) => string>(() => "");
  convertValueRef.current = (val: number) => formatValue(val, quoteCurrency);
  convertPnlRef.current = (val: number) => formatPnlValue(val, quoteCurrency);

  // Compute overlays
  const overlays = useMemo(() => computeMultiOverlays(executors), [executors]);
  overlaysRef.current = overlays;
  const timeRange = useMemo(() => getOverlayTimeRange(overlays), [overlays]);

  // Determine if any executor is active (for WS subscription)
  const hasActive = executors.some((ex) => isActive(ex.status));

  // No WS here: this chart is REST-only. Its candle data comes from the query
  // below, and the parent view owns the socket for live executor updates.

  // Pad time range for candle fetch. The window is part of the cache key: the
  // same market charted over another range (another session) must not reuse it.
  const paddingSeconds = 1800;
  // DEX/LP executors record the pool they traded in; passing it sends the backend
  // to GeckoTerminal, since these connectors have no CEX candle feed (which is
  // what surfaced as "Failed to load candles").
  const poolAddress = useMemo(() => getPoolAddress(executors), [executors]);
  // A pool chart's interval is a budget decision, not a taste one: GeckoTerminal
  // caps a response at 1000 candles and trims the *oldest*, so a week-long LP
  // position drawn at 1m loses its own entry and shows the last ~16h. Size the
  // candle to the executor's window instead, so one request covers it whole. CEX
  // candles have no such cap — they keep the 1m default.
  const interval = useMemo(() => {
    if (requestedInterval) return requestedInterval;
    if (!poolAddress) return "1m";
    return geckoIntervalForSpan(
      timeRange.end + paddingSeconds - (timeRange.start - paddingSeconds),
    );
  }, [requestedInterval, poolAddress, timeRange]);
  const { startTime, endTime, queryKey } = candlesQuery(
    server,
    connector,
    tradingPair,
    interval,
    timeRange.start - paddingSeconds,
    timeRange.end + paddingSeconds,
    poolAddress,
  );

  const { data: candles, isLoading, isError } = useQuery({
    queryKey,
    queryFn: () =>
      api.getCandles(server, connector, tradingPair, interval, 5000, startTime, endTime, poolAddress),
    enabled: !!server && !!connector && !!tradingPair,
    retry: 1,
  });

  // Initialize chart
  useEffect(() => {
    let cancelled = false;
    import("lightweight-charts").then((mod) => {
      if (cancelled || !containerRef.current) return;
      chartModuleRef.current = mod;

      const colors = getThemeColors();
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

        tooltip.innerHTML = renderOverlayTooltipHtml(bestOverlay, {
          formatValue: convertValueRef.current,
          formatPnl: convertPnlRef.current,
        });
        tooltip.style.display = "block";

        // Position tooltip on opposite side of cursor (fixed/viewport coords)
        const containerRect = containerRef.current.getBoundingClientRect();
        const tooltipW = 280;
        const tooltipH = tooltip.offsetHeight || 200;
        const cursorInRightHalf = param.point.x > containerRect.width / 2;
        let left = cursorInRightHalf
          ? containerRect.left + param.point.x - tooltipW - 16
          : containerRect.left + param.point.x + 16;
        if (left < 4) left = 4;
        if (left + tooltipW > window.innerWidth - 4) left = window.innerWidth - tooltipW - 4;
        let top = containerRect.top + param.point.y - 10;
        if (top + tooltipH > window.innerHeight - 4) {
          top = window.innerHeight - tooltipH - 4;
        }
        if (top < 4) top = 4;

        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${top}px`;
      });

      // Draw vertical lines for grid boxes on a canvas overlay
      const drawVerticalLines = () => {
        const lines = gridVerticalLinesRef.current;
        const chartEl = containerRef.current;
        if (!chartEl) return;

        // If no lines, clear the overlay canvas if it exists
        if (!lines.length) {
          const existing = chartEl.querySelector<HTMLCanvasElement>(".grid-vlines-canvas");
          if (existing) {
            const ctx2 = existing.getContext("2d");
            if (ctx2) ctx2.clearRect(0, 0, existing.width, existing.height);
          }
          return;
        }
        // lightweight-charts renders into a canvas inside the container
        const sourceCanvas = chartEl.querySelector("canvas");
        if (!sourceCanvas) return;

        // Get or create overlay canvas
        let overlay = chartEl.querySelector<HTMLCanvasElement>(".grid-vlines-canvas");
        if (!overlay) {
          overlay = document.createElement("canvas");
          overlay.className = "grid-vlines-canvas";
          overlay.style.position = "absolute";
          overlay.style.top = "0";
          overlay.style.left = "0";
          overlay.style.pointerEvents = "none";
          overlay.style.zIndex = "3";
          chartEl.style.position = "relative";
          chartEl.appendChild(overlay);
        }

        const dpr = window.devicePixelRatio || 1;
        const w = sourceCanvas.clientWidth;
        const h = sourceCanvas.clientHeight;
        overlay.width = w * dpr;
        overlay.height = h * dpr;
        overlay.style.width = `${w}px`;
        overlay.style.height = `${h}px`;

        const ctx = overlay.getContext("2d");
        if (!ctx) return;
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, w, h);

        const ts = chart.timeScale();
        for (const line of lines) {
          const x = ts.timeToCoordinate(line.time as import("lightweight-charts").UTCTimestamp);
          if (x === null) continue;
          const yTop = series.priceToCoordinate(line.topPrice);
          const yBottom = series.priceToCoordinate(line.bottomPrice);
          if (yTop === null || yBottom === null) continue;

          ctx.beginPath();
          ctx.strokeStyle = line.color;
          ctx.lineWidth = 1;
          ctx.moveTo(x, Math.min(yTop, yBottom));
          ctx.lineTo(x, Math.max(yTop, yBottom));
          ctx.stroke();
        }
      };

      chart.timeScale().subscribeVisibleLogicalRangeChange(drawVerticalLines);
      chart.subscribeCrosshairMove(drawVerticalLines);

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

  // ── Re-apply chart colors on theme change ──
  useEffect(() => {
    if (!chartRef.current || !chartModuleRef.current) return;
    const chart = chartRef.current;
    const mod = chartModuleRef.current;
    const observer = new MutationObserver(() => {
      const colors = getThemeColors();
      chart.applyOptions({
        layout: {
          background: { type: mod.ColorType.Solid, color: colors.bg },
          textColor: colors.text,
        },
        grid: {
          vertLines: { color: colors.grid },
          horzLines: { color: colors.grid },
        },
      });
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, [chartReady]);

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

  // Reset on pair/interval change, and when the chart jumps to another time
  // window (e.g. switching agent sessions) so it refits over the new candles.
  // Only the start is watched: a live executor pushes `endTime` forward as it
  // runs, and that must not yank the viewport back from where the user left it.
  useEffect(() => {
    initializedRef.current = false;
  }, [tradingPair, interval, startTime]);

  // Apply overlays: segments, price lines, markers
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    const mod = chartModuleRef.current;
    if (!series || !chart || !mod || !chartReady) return;

    // Clean up old segment series and vertical lines
    for (const s of segmentSeriesRef.current) {
      try { chart.removeSeries(s); } catch { /* ok */ }
    }
    gridVerticalLinesRef.current = [];
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

          // Bottom edge (start_price) — solid, matching the top edge: both are
          // bounds of the same grid, so neither gets a style of its own.
          const bottom = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 2,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          bottom.setData([
            { time: t1 as TS, value: box.startPrice },
            { time: t2 as TS, value: box.startPrice },
          ]);
          segmentSeriesRef.current.push(bottom);

          // Vertical edges — drawn on canvas overlay (see drawVerticalLines)
          gridVerticalLinesRef.current.push(
            { time: t1, topPrice: box.endPrice, bottomPrice: box.startPrice, color: boxColor },
            { time: t2, topPrice: box.endPrice, bottomPrice: box.startPrice, color: boxColor },
          );

          // Limit price line (if present) — dotted red
          if (box.limitPrice) {
            const limit = chart.addSeries(mod.LineSeries, {
              color: getThemeColors().red, lineWidth: 1, lineStyle: mod.LineStyle.Dotted,
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

    // Trigger a redraw of grid vertical lines on the overlay canvas
    if (gridVerticalLinesRef.current.length > 0) {
      // Small delay to let chart render first
      setTimeout(() => {
        chart.timeScale().scrollToPosition(chart.timeScale().scrollPosition(), false);
      }, 50);
    }
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
          <PairLabel tradingPair={tradingPair} connector={connector} /> &middot; {interval}
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

                  const { textMuted: muted, border: bdr } = getThemeColors();

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
                    <div style="font-size:11px;line-height:1.5;white-space:pre-wrap;word-break:break-word">${preview.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>
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
        {/* Executor tooltip overlay — rendered via portal to escape overflow-hidden */}
        {createPortal(
          <div
            ref={tooltipRef}
            className="chart-tooltip"
            style={{
              display: "none",
              position: "fixed",
              top: 0,
              left: 0,
              zIndex: 9999,
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
          />,
          document.body,
        )}
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
