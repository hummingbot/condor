import { useEffect, useRef, useState } from "react";

import { useCandleStore } from "@/hooks/useCandleStore";
import { api, type ConsolidatedPosition } from "@/lib/api";
import { candleStore } from "@/lib/candle-store";
import type { ExtraLine } from "@/components/executor/types";
import { getExecutorColor, type ExecutorOverlay } from "@/lib/executor-overlays";

type PickField = "start" | "end" | "limit" | null;

interface GridChartProps {
  server: string;
  connector: string;
  pair: string;
  interval: string;
  lookbackSeconds: number;
  startPrice: number;
  endPrice: number;
  limitPrice: number;
  side: 1 | 2;
  minSpread: number;
  activePickField: PickField;
  onPriceSet: (field: "start" | "end" | "limit", price: number) => void;
  pricePrecision?: number;
  extraLines?: ExtraLine[];
  executorOverlays?: ExecutorOverlay[];
  positions?: ConsolidatedPosition[];
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

export function GridChart({
  server,
  connector,
  pair,
  interval,
  lookbackSeconds,
  startPrice,
  endPrice,
  limitPrice,
  side,
  minSpread,
  activePickField,
  onPriceSet,
  pricePrecision,
  extraLines,
  executorOverlays,
  positions,
}: GridChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const chartModuleRef = useRef<typeof import("lightweight-charts") | null>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);
  const seriesRef = useRef<import("lightweight-charts").ISeriesApi<"Candlestick"> | null>(null);
  const initializedRef = useRef(false);
  const crosshairPriceRef = useRef<number | null>(null);
  const overlaysRef = useRef<ExecutorOverlay[]>([]);
  const [chartReady, setChartReady] = useState(false);

  // Price line refs
  const startLineRef = useRef<import("lightweight-charts").IPriceLine | null>(null);
  const endLineRef = useRef<import("lightweight-charts").IPriceLine | null>(null);
  const limitLineRef = useRef<import("lightweight-charts").IPriceLine | null>(null);
  const gridLinesRef = useRef<import("lightweight-charts").IPriceLine[]>([]);
  const extraLinesRef = useRef<import("lightweight-charts").IPriceLine[]>([]);
  const overlaySeriesRef = useRef<import("lightweight-charts").ISeriesApi<"Line">[]>([]);
  const overlayPriceLinesRef = useRef<import("lightweight-charts").IPriceLine[]>([]);
  const positionLinesRef = useRef<import("lightweight-charts").IPriceLine[]>([]);

  // ── Candle data from the singleton store (WS live + cached) ──
  const { candles, mergeCandles, setDuration } = useCandleStore(server, connector, pair, interval);

  // ── REST backfill on pair/interval/lookback change ──
  const backfillKeyRef = useRef("");
  useEffect(() => {
    const backfillKey = `${server}:${connector}:${pair}:${interval}:${lookbackSeconds}`;
    if (backfillKey === backfillKeyRef.current) return;
    backfillKeyRef.current = backfillKey;

    setDuration(lookbackSeconds);

    let cancelled = false;
    const startTime = Math.floor(Date.now() / 1000) - lookbackSeconds;

    const fetchWithRetry = (attempt: number) => {
      if (cancelled) return;
      api
        .getCandles(server, connector, pair, interval, 5000, startTime)
        .then((fetched) => {
          if (!cancelled && fetched?.length) mergeCandles(fetched);
        })
        .catch(() => {
          if (!cancelled && attempt < 2) {
            setTimeout(() => fetchWithRetry(attempt + 1), 2000 * (attempt + 1));
          }
        });
    };
    fetchWithRetry(0);

    return () => { cancelled = true; };
  }, [server, connector, pair, interval, lookbackSeconds]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Initialize chart ONCE ──
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
      });
      chartRef.current = chart;

      const series = chart.addSeries(mod.CandlestickSeries, {
        upColor: colors.up,
        downColor: colors.down,
        wickUpColor: colors.up,
        wickDownColor: colors.down,
        borderVisible: false,
        ...(pricePrecision != null && {
          priceFormat: { type: "price" as const, precision: pricePrecision, minMove: 1 / 10 ** pricePrecision },
        }),
      });
      seriesRef.current = series;
      setChartReady(true);

      // Track crosshair price for click-to-set + executor tooltip
      chart.subscribeCrosshairMove((param) => {
        if (!param.point || !param.seriesData) {
          crosshairPriceRef.current = null;
          if (tooltipRef.current) tooltipRef.current.style.display = "none";
          return;
        }
        const data = param.seriesData.get(series);
        if (data && "close" in data) {
          crosshairPriceRef.current = (data as { close: number }).close;
        } else if (param.point.y !== undefined) {
          const price = series.coordinateToPrice(param.point.y);
          if (price !== null) {
            crosshairPriceRef.current = price as number;
          }
        }

        // Executor tooltip
        const tooltip = tooltipRef.current;
        if (!tooltip || !containerRef.current) return;

        const crosshairTime = typeof param.time === "number" ? param.time : 0;
        if (!crosshairTime || !param.point || param.point.x < 0 || param.point.y < 0) {
          tooltip.style.display = "none";
          return;
        }

        const cursorY = param.point.y;
        let bestOverlay: ExecutorOverlay | null = null;
        let bestDist = Infinity;

        for (const overlay of overlaysRef.current) {
          const box = overlay.gridBox;
          if (box) {
            const t1 = box.startTime > 1e12 ? Math.floor(box.startTime / 1000) : box.startTime;
            const t2 = box.endTime > 1e12 ? Math.floor(box.endTime / 1000) : box.endTime;
            if (crosshairTime < t1 - 60 || crosshairTime > t2 + 60) continue;
            const topY = series.priceToCoordinate(Math.max(box.startPrice, box.endPrice));
            const botY = series.priceToCoordinate(Math.min(box.startPrice, box.endPrice));
            if (topY === null || botY === null) continue;
            const minY = Math.min(topY, botY);
            const maxY = Math.max(topY, botY);
            const dist = cursorY >= minY && cursorY <= maxY ? 0 : Math.min(Math.abs(cursorY - minY), Math.abs(cursorY - maxY));
            if (dist < bestDist && dist < 30) {
              bestDist = dist;
              bestOverlay = overlay;
            }
            continue;
          }

          const seg = overlay.segment;
          if (!seg) continue;
          const entryT = seg.entryTime > 1e12 ? Math.floor(seg.entryTime / 1000) : seg.entryTime;
          const exitT = seg.exitTime > 1e12 ? Math.floor(seg.exitTime / 1000) : seg.exitTime;
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
        const pnlSign = o.pnl >= 0 ? "+" : "";
        const pnlStr = Math.abs(o.pnl) >= 1000 ? `${pnlSign}$${(o.pnl / 1000).toFixed(1)}K` : `${pnlSign}$${o.pnl.toFixed(2)}`;
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

        const containerRect = containerRef.current.getBoundingClientRect();
        let left = param.point.x + 16;
        if (left + 200 > containerRect.width) left = param.point.x - 210;
        let top = param.point.y - 10;
        if (top < 0) top = 4;

        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${top}px`;
      });
    });
    return () => {
      cancelled = true;
      setChartReady(false);
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        seriesRef.current = null;
        startLineRef.current = null;
        endLineRef.current = null;
        limitLineRef.current = null;
        gridLinesRef.current = [];
        extraLinesRef.current = [];
        overlayPriceLinesRef.current = [];
        positionLinesRef.current = [];
      }
    };
  }, []);

  // ── Re-apply chart colors on theme change ──
  useEffect(() => {
    if (!chartRef.current || !chartModuleRef.current) return;
    const chart = chartRef.current;
    const mod = chartModuleRef.current;
    const observer = new MutationObserver(() => {
      const colors = getChartColors();
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

  // ── Push candle data to chart ──
  useEffect(() => {
    if (!chartReady || !seriesRef.current || !candles.length) return;

    const mapped = candles.map((c) => ({
      time: c.timestamp as import("lightweight-charts").UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    seriesRef.current.setData(mapped);

    if (!initializedRef.current) {
      chartRef.current?.timeScale().fitContent();
      initializedRef.current = true;
    }
  }, [candles, chartReady]);

  // ── Real-time last candle update via candle store listener ──
  useEffect(() => {
    if (!chartReady || !seriesRef.current) return;
    const key = `candles:${server}:${connector}:${pair}:${interval}`;

    const removeListener = candleStore.onUpdate(key, (updated) => {
      if (!seriesRef.current || !updated.length) return;
      const last = updated[updated.length - 1];
      seriesRef.current.update({
        time: last.timestamp as import("lightweight-charts").UTCTimestamp,
        open: last.open,
        high: last.high,
        low: last.low,
        close: last.close,
      });
    });

    return removeListener;
  }, [chartReady, server, connector, pair, interval]);

  // ── Reset auto-fit on pair/interval/range change ──
  useEffect(() => {
    initializedRef.current = false;
  }, [pair, interval, lookbackSeconds]);

  // ── Update price precision ──
  useEffect(() => {
    if (!seriesRef.current || pricePrecision == null) return;
    seriesRef.current.applyOptions({
      priceFormat: { type: "price" as const, precision: pricePrecision, minMove: 1 / 10 ** pricePrecision },
    });
  }, [pricePrecision]);

  // ── Price lines (start/end/limit/grid levels/extras) ──
  useEffect(() => {
    const series = seriesRef.current;
    const mod = chartModuleRef.current;
    if (!series || !mod) return;

    // Remove existing lines
    if (startLineRef.current) { try { series.removePriceLine(startLineRef.current); } catch { /* ok */ } }
    if (endLineRef.current) { try { series.removePriceLine(endLineRef.current); } catch { /* ok */ } }
    if (limitLineRef.current) { try { series.removePriceLine(limitLineRef.current); } catch { /* ok */ } }
    for (const gl of gridLinesRef.current) {
      try { series.removePriceLine(gl); } catch { /* ok */ }
    }
    gridLinesRef.current = [];

    if (startPrice > 0) {
      startLineRef.current = series.createPriceLine({
        price: startPrice,
        color: activePickField === "start" ? "#22c55e" : "#16a34a",
        lineWidth: 2,
        lineStyle: mod.LineStyle.Solid,
        axisLabelVisible: true,
        title: "Start",
      });
    }

    if (endPrice > 0) {
      endLineRef.current = series.createPriceLine({
        price: endPrice,
        color: activePickField === "end" ? "#22c55e" : "#16a34a",
        lineWidth: 2,
        lineStyle: mod.LineStyle.Dashed,
        axisLabelVisible: true,
        title: "End",
      });
    }

    if (limitPrice > 0) {
      const limitColor = side === 1 ? "#ef4444" : "#f97316";
      limitLineRef.current = series.createPriceLine({
        price: limitPrice,
        color: activePickField === "limit" ? "#fbbf24" : limitColor,
        lineWidth: 2,
        lineStyle: mod.LineStyle.Dotted,
        axisLabelVisible: true,
        title: "Limit",
      });
    }

    // Clear extra lines
    for (const el of extraLinesRef.current) {
      try { series.removePriceLine(el); } catch { /* ok */ }
    }
    extraLinesRef.current = [];

    // Grid level preview lines
    if (startPrice > 0 && endPrice > 0 && minSpread > 0 && startPrice < endPrice) {
      const range = endPrice - startPrice;
      const stepSize = startPrice * minSpread;
      if (stepSize > 0) {
        const numLevels = Math.floor(range / stepSize);
        if (numLevels >= 2 && numLevels <= 200) {
          const maxDraw = Math.min(numLevels, 50);
          const drawStep = numLevels > maxDraw ? numLevels / maxDraw : 1;
          for (let idx = 0; idx < maxDraw; idx++) {
            const i = Math.round((idx + 1) * drawStep);
            const levelPrice = startPrice + stepSize * i;
            if (levelPrice >= endPrice) break;
            const gl = series.createPriceLine({
              price: levelPrice,
              color: "rgba(34, 197, 94, 0.15)",
              lineWidth: 1,
              lineStyle: mod.LineStyle.Dotted,
              axisLabelVisible: false,
              title: "",
            });
            gridLinesRef.current.push(gl);
          }
        }
      }
    }

    // Render extra lines
    if (extraLines?.length) {
      const styleMap: Record<string, number> = {
        solid: mod.LineStyle.Solid,
        dashed: mod.LineStyle.Dashed,
        dotted: mod.LineStyle.Dotted,
      };
      for (const el of extraLines) {
        if (el.price <= 0) continue;
        const pl = series.createPriceLine({
          price: el.price,
          color: el.color,
          lineWidth: (el.lineWidth ?? 1) as import("lightweight-charts").LineWidth,
          lineStyle: styleMap[el.lineStyle] ?? mod.LineStyle.Dashed,
          axisLabelVisible: true,
          title: el.label,
        });
        extraLinesRef.current.push(pl);
      }
    }
  }, [startPrice, endPrice, limitPrice, side, minSpread, activePickField, extraLines]);

  // ── Executor overlays ──
  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    const mod = chartModuleRef.current;
    if (!chart || !mod) return;

    for (const s of overlaySeriesRef.current) {
      try { chart.removeSeries(s); } catch { /* ok */ }
    }
    overlaySeriesRef.current = [];

    if (series) {
      for (const pl of overlayPriceLinesRef.current) {
        try { series.removePriceLine(pl); } catch { /* ok */ }
      }
    }
    overlayPriceLinesRef.current = [];

    if (!executorOverlays?.length) return;

    const isMulti = executorOverlays.length > 1;

    executorOverlays.forEach((overlay, idx) => {
      const color = isMulti ? getExecutorColor(idx, overlay.pnl) : undefined;

      const box = overlay.gridBox;
      if (box) {
        const boxColor = color ?? box.color;
        const t1 = box.startTime > 1e12 ? Math.floor(box.startTime / 1000) : box.startTime;
        const t2 = box.endTime > 1e12 ? Math.floor(box.endTime / 1000) : box.endTime;
        type TS = import("lightweight-charts").UTCTimestamp;

        const span = t2 - t1;
        if (span < 4) {
          const seg = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 2,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          seg.setData([
            { time: t1 as TS, value: box.startPrice },
            { time: (t1 + 1) as TS, value: box.endPrice },
          ]);
          overlaySeriesRef.current.push(seg);
          return;
        }

        try {
          const top = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 2,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          top.setData([
            { time: t1 as TS, value: box.endPrice },
            { time: t2 as TS, value: box.endPrice },
          ]);
          overlaySeriesRef.current.push(top);

          const bottom = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 2, lineStyle: mod.LineStyle.Dashed,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          bottom.setData([
            { time: t1 as TS, value: box.startPrice },
            { time: t2 as TS, value: box.startPrice },
          ]);
          overlaySeriesRef.current.push(bottom);

          const left = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          left.setData([
            { time: t1 as TS, value: box.startPrice },
            { time: (t1 + 1) as TS, value: box.endPrice },
          ]);
          overlaySeriesRef.current.push(left);

          const right = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: 1,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          right.setData([
            { time: (t2 - 1) as TS, value: box.endPrice },
            { time: t2 as TS, value: box.startPrice },
          ]);
          overlaySeriesRef.current.push(right);

          if (box.limitPrice) {
            const limit = chart.addSeries(mod.LineSeries, {
              color: "#ef4444", lineWidth: 1, lineStyle: mod.LineStyle.Dotted,
              priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
            });
            limit.setData([
              { time: t1 as TS, value: box.limitPrice },
              { time: t2 as TS, value: box.limitPrice },
            ]);
            overlaySeriesRef.current.push(limit);
          }
        } catch { /* grid box rendering failed */ }
        return;
      }

      const seg = overlay.segment;
      if (!seg) return;

      const segColor = color ?? seg.color;
      const entryT = seg.entryTime > 1e12 ? Math.floor(seg.entryTime / 1000) : seg.entryTime;
      const exitT = seg.exitTime > 1e12 ? Math.floor(seg.exitTime / 1000) : seg.exitTime;

      const isOrderActive = overlay.type === "order" && (overlay.status?.toLowerCase() === "running" || overlay.status?.toLowerCase() === "active");
      const lineStyle = isOrderActive ? mod.LineStyle.Solid : mod.LineStyle.Dashed;

      const lineSeries = chart.addSeries(mod.LineSeries, {
        color: segColor, lineWidth: 2, lineStyle,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      lineSeries.setData([
        { time: entryT as import("lightweight-charts").UTCTimestamp, value: seg.entryPrice },
        { time: exitT as import("lightweight-charts").UTCTimestamp, value: seg.exitPrice },
      ]);
      overlaySeriesRef.current.push(lineSeries);
    });

    // Full-width price lines for active executors
    if (series && executorOverlays?.length) {
      const styleMap: Record<string, number> = {
        solid: mod.LineStyle.Solid,
        dashed: mod.LineStyle.Dashed,
        dotted: mod.LineStyle.Dotted,
      };
      for (const overlay of executorOverlays) {
        const isRunning = overlay.status?.toLowerCase() === "running" || overlay.status?.toLowerCase() === "active";
        if (!isRunning) continue;
        for (const pl of overlay.priceLines) {
          if (pl.price <= 0) continue;
          const priceLine = series.createPriceLine({
            price: pl.price,
            color: pl.color,
            lineWidth: (pl.lineWidth ?? 1) as import("lightweight-charts").LineWidth,
            lineStyle: styleMap[pl.style] ?? mod.LineStyle.Solid,
            axisLabelVisible: true,
            title: pl.label,
          });
          overlayPriceLinesRef.current.push(priceLine);
        }
      }
    }
  }, [executorOverlays]);

  // Keep overlaysRef in sync for tooltip
  useEffect(() => {
    overlaysRef.current = executorOverlays ?? [];
  }, [executorOverlays]);

  // ── Position hold lines ──
  useEffect(() => {
    const series = seriesRef.current;
    const mod = chartModuleRef.current;
    if (!series || !mod) return;

    for (const pl of positionLinesRef.current) {
      try { series.removePriceLine(pl); } catch { /* ok */ }
    }
    positionLinesRef.current = [];

    if (!positions?.length) return;

    for (const pos of positions) {
      if (pos.entry_price <= 0) continue;
      const isLong = pos.position_side?.toUpperCase() === "LONG";
      const pnl = pos.unrealized_pnl ?? 0;
      const pnlSign = pnl >= 0 ? "+" : "";
      const pnlStr = Math.abs(pnl) >= 1000
        ? `${pnlSign}$${(pnl / 1000).toFixed(1)}K`
        : `${pnlSign}$${pnl.toFixed(2)}`;
      const amt = Math.abs(pos.amount);
      const color = pnl >= 0 ? "#22c55e" : "#ef4444";
      const label = `${isLong ? "LONG" : "SHORT"} ${amt.toFixed(4)} · ${pnlStr}`;
      const pl = series.createPriceLine({
        price: pos.entry_price,
        color,
        lineWidth: 1,
        lineStyle: mod.LineStyle.Solid,
        axisLabelVisible: true,
        title: label,
      });
      positionLinesRef.current.push(pl);
    }
  }, [positions]);

  // ── Click-to-set price ──
  const handleClick = () => {
    if (!activePickField || crosshairPriceRef.current === null) return;
    onPriceSet(activePickField, crosshairPriceRef.current);
  };

  return (
    <div className="flex h-full flex-col">
      {activePickField && (
        <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
          <p className="text-[10px] text-[var(--color-text-muted)]">
            Click on chart to set {activePickField} price
          </p>
          <span className="animate-pulse rounded bg-[var(--color-primary)]/20 px-2 py-0.5 text-xs text-[var(--color-primary)]">
            Pick mode: {activePickField}
          </span>
        </div>
      )}
      <div className="relative flex-1">
        <div
          ref={containerRef}
          className="absolute inset-0"
          style={{ cursor: activePickField ? "crosshair" : "default" }}
          onClick={handleClick}
        />
        {/* Executor tooltip overlay */}
        <div
          ref={tooltipRef}
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
