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
  totalAmountQuote?: number;
  minOrderAmountQuote?: number;
  activePickField: PickField;
  onPriceSet: (field: "start" | "end" | "limit", price: number) => void;
  pricePrecision?: number;
  extraLines?: ExtraLine[];
  executorOverlays?: ExecutorOverlay[];
  positions?: ConsolidatedPosition[];
  selectedExecutorId?: string | null;
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
  totalAmountQuote,
  minOrderAmountQuote,
  activePickField,
  onPriceSet,
  pricePrecision,
  extraLines,
  executorOverlays,
  positions,
  selectedExecutorId,
}: GridChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const chartModuleRef = useRef<typeof import("lightweight-charts") | null>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);
  const seriesRef = useRef<import("lightweight-charts").ISeriesApi<"Candlestick"> | null>(null);
  const initializedRef = useRef(false);
  const crosshairPriceRef = useRef<number | null>(null);
  const overlaysRef = useRef<ExecutorOverlay[]>([]);
  const lastZoomedIdRef = useRef<string | null>(null);
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
        const pctStr = o.pnlPct !== 0 ? `${o.pnlPct > 0 ? "+" : ""}${(o.pnlPct * 100).toFixed(2)}%` : "";
        const volStr = Math.abs(o.volume) >= 1000 ? `$${(o.volume / 1000).toFixed(1)}K` : `$${o.volume.toFixed(0)}`;
        const feesStr = o.fees ? `$${o.fees.toFixed(2)}` : "";

        const sideClr = o.side === "buy" ? "#22c55e" : "#ef4444";
        const sideBg = o.side === "buy" ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)";
        const statusBg = o.status?.toLowerCase() === "running" || o.status?.toLowerCase() === "active"
          ? "rgba(34,197,94,0.15)" : "rgba(156,163,175,0.15)";
        const statusClr = o.status?.toLowerCase() === "running" || o.status?.toLowerCase() === "active"
          ? "#22c55e" : "#9ca3af";

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
          detailRows += `<div style="display:flex;justify-content:space-between;gap:12px"><span style="color:#6b7994">${label}</span><span style="font-family:monospace;${color ? `color:${color}` : ""}">${value}</span></div>`;
        };

        // Grid-specific details
        if (o.type === "grid" && o.gridBox) {
          addRow("Start Price", fmtPrice(o.gridBox.startPrice));
          addRow("End Price", fmtPrice(o.gridBox.endPrice));
          if (o.gridBox.limitPrice) addRow("Limit Price", fmtPrice(o.gridBox.limitPrice));
        } else if (o.entryPrice && o.entryPrice > 0) {
          addRow("Entry", fmtPrice(o.entryPrice));
          if (o.exitPrice && o.exitPrice > 0 && o.exitPrice !== o.entryPrice) {
            addRow(o.status?.toLowerCase() === "running" ? "Current" : "Close", fmtPrice(o.exitPrice));
          }
        }

        if (cfg.leverage != null && Number(cfg.leverage) > 1) addRow("Leverage", `${cfg.leverage}x`);
        if (cfg.total_amount_quote != null) addRow("Amount", fmtUsd(Number(cfg.total_amount_quote)));
        else if (cfg.amount != null && Number(cfg.amount) > 0) addRow("Amount", String(cfg.amount));

        const tp = Number(tripleBarrier.take_profit || cfg.take_profit);
        if (tp > 0 && tp !== -1) addRow("Take Profit", `${(tp * 100).toFixed(2)}%`, "#22c55e");
        const sl = Number(cfg.stop_loss);
        if (sl > 0 && sl !== -1) addRow("Stop Loss", `${(sl * 100).toFixed(2)}%`, "#ef4444");
        if (cfg.keep_position != null) addRow("Keep Position", String(cfg.keep_position) === "true" ? "Yes" : "No");

        tooltip.innerHTML = `
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
            <span style="font-weight:700;font-size:12px;font-family:monospace">${o.executorId.slice(0, 10)}\u2026</span>
            <span style="background:${sideBg};color:${sideClr};font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;text-transform:uppercase">${o.side}</span>
            <span style="background:${statusBg};color:${statusClr};font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px">${o.status}</span>
          </div>
          <div style="display:flex;align-items:center;gap:4px;margin-bottom:2px">
            <span style="background:rgba(255,255,255,0.06);padding:1px 5px;border-radius:3px;font-size:10px;border:1px solid rgba(255,255,255,0.08)">${o.type.toUpperCase()}</span>
            ${o.closeType ? `<span style="font-size:10px;color:#6b7994">${o.closeType}</span>` : ""}
          </div>
          <div style="border-top:1px solid rgba(255,255,255,0.08);margin:6px 0;padding-top:6px;display:grid;grid-template-columns:1fr 1fr;gap:4px 16px">
            <div><div style="color:#6b7994;font-size:9px;text-transform:uppercase;margin-bottom:1px">Net PnL</div><div style="font-weight:600;font-size:13px;color:${pnlClr};font-family:monospace">${pnlStr}</div></div>
            <div><div style="color:#6b7994;font-size:9px;text-transform:uppercase;margin-bottom:1px">PnL %</div><div style="font-weight:600;font-size:13px;color:${pnlClr};font-family:monospace">${pctStr || "—"}</div></div>
            <div><div style="color:#6b7994;font-size:9px;text-transform:uppercase;margin-bottom:1px">Volume</div><div style="font-family:monospace;font-size:11px">${volStr}</div></div>
            <div><div style="color:#6b7994;font-size:9px;text-transform:uppercase;margin-bottom:1px">Fees</div><div style="font-family:monospace;font-size:11px">${feesStr || "—"}</div></div>
          </div>
          ${detailRows ? `<div style="border-top:1px solid rgba(255,255,255,0.08);margin-top:4px;padding-top:6px;font-size:11px;display:flex;flex-direction:column;gap:3px">${detailRows}</div>` : ""}
        `;
        tooltip.style.display = "block";

        const containerRect = containerRef.current.getBoundingClientRect();
        const tooltipW = 280;
        // Show tooltip on opposite side of cursor to avoid covering the executor
        const cursorInRightHalf = param.point.x > containerRect.width / 2;
        let left = cursorInRightHalf
          ? param.point.x - tooltipW - 16
          : param.point.x + 16;
        // Clamp to container bounds
        if (left < 4) left = 4;
        if (left + tooltipW > containerRect.width - 4) left = containerRect.width - tooltipW - 4;
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

    // Grid level preview lines (mirrors _generate_grid_levels logic)
    if (startPrice > 0 && endPrice > 0 && startPrice < endPrice) {
      const range = (endPrice - startPrice) / startPrice;
      const levelsBySpread = minSpread > 0 ? Math.floor(range / minSpread) : Infinity;
      const levelsByAmount = (totalAmountQuote && minOrderAmountQuote && minOrderAmountQuote > 0)
        ? Math.floor(totalAmountQuote / minOrderAmountQuote)
        : Infinity;
      const numLevels = Math.max(1, Math.min(levelsBySpread, levelsByAmount));
      if (numLevels >= 2 && numLevels <= 200) {
        const maxDraw = Math.min(numLevels, 50);
        const drawStep = numLevels > maxDraw ? numLevels / maxDraw : 1;
        for (let idx = 0; idx < maxDraw; idx++) {
          const i = Math.round(idx * drawStep);
          const levelPrice = startPrice + (endPrice - startPrice) * (i / (numLevels - 1));
          if (levelPrice <= startPrice || levelPrice >= endPrice) continue;
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
  }, [startPrice, endPrice, limitPrice, side, minSpread, totalAmountQuote, minOrderAmountQuote, activePickField, extraLines]);

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

    const hasSelection = !!selectedExecutorId;
    const isMulti = executorOverlays.length > 1;

    executorOverlays.forEach((overlay, idx) => {
      const isSelectedOverlay = hasSelection && overlay.executorId === selectedExecutorId;
      const isDimmed = hasSelection && !isSelectedOverlay;
      // When selected: use bright color and thicker lines; when dimmed: reduce opacity
      const baseColor = isMulti ? getExecutorColor(idx, overlay.pnl) : undefined;
      const dimAlpha = 0.2;

      function applyDim(c: string): string {
        if (!isDimmed) return c;
        // Add alpha to hex colors
        if (c.startsWith("#") && c.length === 7) return c + "33";
        if (c.startsWith("#") && c.length === 4) return c + "3";
        if (c.startsWith("rgba")) return c.replace(/[\d.]+\)$/, `${dimAlpha})`);
        return c;
      }

      const box = overlay.gridBox;
      if (box) {
        const boxColor = applyDim(baseColor ?? box.color);
        const lineW = (isSelectedOverlay ? 3 : 2) as import("lightweight-charts").LineWidth;
        const t1 = box.startTime > 1e12 ? Math.floor(box.startTime / 1000) : box.startTime;
        const t2 = box.endTime > 1e12 ? Math.floor(box.endTime / 1000) : box.endTime;
        type TS = import("lightweight-charts").UTCTimestamp;

        const span = t2 - t1;
        if (span < 4) {
          const seg = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: lineW,
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
            color: boxColor, lineWidth: lineW,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          });
          top.setData([
            { time: t1 as TS, value: box.endPrice },
            { time: t2 as TS, value: box.endPrice },
          ]);
          overlaySeriesRef.current.push(top);

          const bottom = chart.addSeries(mod.LineSeries, {
            color: boxColor, lineWidth: lineW, lineStyle: mod.LineStyle.Dashed,
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
              color: applyDim("#ef4444"), lineWidth: 1, lineStyle: mod.LineStyle.Dotted,
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

      const segColor = applyDim(baseColor ?? seg.color);
      const entryT = seg.entryTime > 1e12 ? Math.floor(seg.entryTime / 1000) : seg.entryTime;
      const exitT = seg.exitTime > 1e12 ? Math.floor(seg.exitTime / 1000) : seg.exitTime;

      const isOrderActive = overlay.type === "order" && (overlay.status?.toLowerCase() === "running" || overlay.status?.toLowerCase() === "active");
      const lineStyle = isOrderActive ? mod.LineStyle.Solid : mod.LineStyle.Dashed;
      const lineW = (isSelectedOverlay ? 3 : 2) as import("lightweight-charts").LineWidth;

      const lineSeries = chart.addSeries(mod.LineSeries, {
        color: segColor, lineWidth: lineW, lineStyle,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      });
      lineSeries.setData([
        { time: entryT as import("lightweight-charts").UTCTimestamp, value: seg.entryPrice },
        { time: exitT as import("lightweight-charts").UTCTimestamp, value: seg.exitPrice },
      ]);
      overlaySeriesRef.current.push(lineSeries);
    });

    // Full-width price lines for active or selected executor
    if (series && executorOverlays?.length) {
      const styleMap: Record<string, number> = {
        solid: mod.LineStyle.Solid,
        dashed: mod.LineStyle.Dashed,
        dotted: mod.LineStyle.Dotted,
      };
      for (const overlay of executorOverlays) {
        const isRunning = overlay.status?.toLowerCase() === "running" || overlay.status?.toLowerCase() === "active";
        const isSelectedOverlay = overlay.executorId === selectedExecutorId;
        // Show price lines for active executors, and always for the selected one
        if (!isRunning && !isSelectedOverlay) continue;
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
  }, [executorOverlays, selectedExecutorId]);

  // ── Zoom to selected executor (one-time action) ──
  useEffect(() => {
    if (!selectedExecutorId || !chartRef.current || !executorOverlays?.length) return;
    // Only zoom once per selection — don't re-zoom on overlay data refresh
    if (lastZoomedIdRef.current === selectedExecutorId) return;
    const overlay = executorOverlays.find((o) => o.executorId === selectedExecutorId);
    if (!overlay) return;

    lastZoomedIdRef.current = selectedExecutorId;

    const toSec = (ts: number) => (ts > 1e12 ? Math.floor(ts / 1000) : ts);
    const start = toSec(overlay.timeRange.start);
    const end = toSec(overlay.timeRange.end);
    const padding = Math.max((end - start) * 0.3, 300);

    chartRef.current.timeScale().setVisibleRange({
      from: (start - padding) as import("lightweight-charts").UTCTimestamp,
      to: (end + padding) as import("lightweight-charts").UTCTimestamp,
    });
  }, [selectedExecutorId, executorOverlays]);

  // Reset zoom tracking when executor is deselected
  useEffect(() => {
    if (!selectedExecutorId) lastZoomedIdRef.current = null;
  }, [selectedExecutorId]);

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
            borderRadius: 8,
            padding: "10px 14px",
            fontSize: 11,
            color: "var(--color-text)",
            width: 280,
            lineHeight: 1.4,
            backdropFilter: "blur(12px)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
          }}
        />
      </div>
    </div>
  );
}
