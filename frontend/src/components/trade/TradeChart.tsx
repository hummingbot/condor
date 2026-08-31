import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useCandleStore } from "@/hooks/useCandleStore";
import { useRates } from "@/hooks/useRates";
import { api, type ConsolidatedPosition } from "@/lib/api";
import { candleChannelKey, candleStore } from "@/lib/candle-store";
import type { ExtraLine, PickSlot } from "@/components/executor/types";
import { getExecutorColor, type ExecutorOverlay } from "@/lib/executor-overlays";
import { getThemeColors, pnlHexColor, sideColor } from "@/lib/theme-colors";
import { escapeHtml, formatPriceSig, roundToPricePrecision } from "@/lib/formatters";

type PickField = PickSlot | null;

/** What each pick slot is called in the hint the chart shows while picking. */
const PICK_LABELS: Record<PickSlot, string> = {
  start: "start",
  end: "end",
  limit: "limit",
  limit2: "lower limit",
};

/**
 * How far the pointer may drift between press and release and still count as a
 * click. Past it the gesture was a pan — the chart scrolls on a pressed move —
 * and none of the pane's click actions should fire.
 */
const CLICK_SLOP_PX = 4;

/**
 * The chart's price mapping, and nothing else.
 *
 * A sibling drawn beside the chart — the DEX liquidity-depth column — has to put
 * a bin at the same vertical position the candles put its price, at every zoom
 * level. Inventing a second scale is the one failure such a column exists to
 * prevent, so the chart lends out its own mapping instead. Three methods, no
 * chart instance: nothing outside can mutate the chart through this.
 */
export interface ChartPriceAxis {
  /** Pixel row for a price, from the pane's top. `null` when it is off-scale. */
  priceToCoordinate(price: number): number | null;
  /** Pane height in CSS pixels, so a sibling canvas can match it. Cached, so
   * polling it costs no layout. */
  height(): number;
  /** Subscribe to scale changes; returns its own unsubscribe. */
  onScaleChange(cb: () => void): () => void;
}

interface TradeChartProps {
  server: string;
  connector: string;
  pair: string;
  interval: string;
  /**
   * Chart this exact DEX pool instead of letting the backend resolve one from
   * the pair. Set by the DEX pool workspace; every CLOB chart omits it.
   */
  poolAddress?: string;
  lookbackSeconds: number;
  startPrice: number;
  endPrice: number;
  limitPrice: number;
  side: 1 | 2;
  minSpread: number;
  totalAmountQuote?: number;
  minOrderAmountQuote?: number;
  activePickField: PickField;
  /** Per-slot overrides for the line titles and pick hints (see ChartPriceMapping). */
  lineLabels?: Partial<Record<PickSlot, string>>;
  onPriceSet: (field: PickSlot, price: number) => void;
  pricePrecision?: number;
  extraLines?: ExtraLine[];
  executorOverlays?: ExecutorOverlay[];
  positions?: ConsolidatedPosition[];
  selectedExecutorId?: string | null;
  /** Callback when user clicks chart background to deselect executor */
  onExecutorDeselect?: () => void;
  /** Called once the series exists, with the chart's price mapping. */
  onChartReady?: (axis: ChartPriceAxis) => void;
}

export function TradeChart({
  server,
  connector,
  pair,
  interval,
  poolAddress,
  lookbackSeconds,
  startPrice,
  endPrice,
  limitPrice,
  side,
  minSpread,
  totalAmountQuote,
  minOrderAmountQuote,
  activePickField,
  lineLabels,
  onPriceSet,
  pricePrecision,
  extraLines,
  executorOverlays,
  positions,
  selectedExecutorId,
  onExecutorDeselect,
  onChartReady,
}: TradeChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const chartModuleRef = useRef<typeof import("lightweight-charts") | null>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);
  const seriesRef = useRef<import("lightweight-charts").ISeriesApi<"Candlestick"> | null>(null);
  const initializedRef = useRef(false);
  // Exact price under the pointer, never snapped to the hovered candle's close:
  // the crosshair is free vertically, so this is the price the axis label shows
  // and the only one a click or a measurement may report.
  const cursorPriceRef = useRef<number | null>(null);
  const crosshairTimeRef = useRef<number | null>(null);
  // ── Measure tool (Shift+click anchor → live % of range) ──
  const measureAnchorRef = useRef<{ price: number; time: number } | null>(null);
  const measureBadgeRef = useRef<HTMLDivElement>(null);
  // Measure box is a DOM overlay (not a chart series) so it never triggers a relayout
  const measureBoxRef = useRef<HTMLDivElement>(null);
  const overlaysRef = useRef<ExecutorOverlay[]>([]);
  const lastZoomedIdRef = useRef<string | null>(null);
  // Currency conversion for the pair's quote asset, owned here rather than
  // threaded in as props -- the pane below the chart (TradeBottomPane) derives
  // it the same way, and a chart that took it optionally rendered raw quote
  // dollars next to that pane's converted rows. The refs keep the imperative
  // lightweight-charts callbacks reading the latest formatter without being
  // re-subscribed on every rate tick -- they are read at call time, so the
  // crosshair and tooltip always format with the current currency.
  //
  // `convertPnl` is memoized rather than only ref-held because the position
  // price lines bake their label at creation time: that effect depends on this
  // identity to repaint on a currency switch, and would tear down and redraw
  // every line on each render if the closure were minted fresh. `useRates`
  // memoizes `formatPnlValue` on the rates themselves, so this changes only
  // when the conversion actually does.
  const quoteCurrency = pair.split("-")[1] || "USDT";
  const quoteCurrencies = useMemo(() => [quoteCurrency], [quoteCurrency]);
  const { formatPnlValue, formatValue } = useRates(quoteCurrencies);
  const convertPnl = useMemo(
    () => (val: number) => formatPnlValue(val, quoteCurrency),
    [formatPnlValue, quoteCurrency],
  );
  const convertValueRef = useRef<(val: number) => string>(() => "");
  const convertPnlRef = useRef<(val: number) => string>(() => "");
  convertValueRef.current = (val: number) => formatValue(val, quoteCurrency);
  convertPnlRef.current = convertPnl;
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
  const { candles, mergeCandles, setDuration } = useCandleStore(
    server,
    connector,
    pair,
    interval,
    poolAddress,
  );

  // ── Filter executor overlays to those within candle time range ──
  // Depend on the earliest candle timestamp (not the candles array, whose reference
  // changes on every WS tick) so filteredOverlays keeps a stable identity across
  // live ticks and the overlay rebuild effect below doesn't churn per tick.
  const minCandleTime = candles.length ? candles[0].timestamp : 0;
  const filteredOverlays = useMemo(() => {
    if (!executorOverlays?.length) return executorOverlays;
    return executorOverlays.filter((o) => {
      const s = o.status?.toLowerCase();
      if (s === "running" || s === "active") return true;
      if (selectedExecutorId && o.executorId === selectedExecutorId) return true;
      const end = o.timeRange.end > 1e12 ? o.timeRange.end / 1000 : o.timeRange.end;
      return end >= minCandleTime; // minCandleTime === 0 (no candles) → show all
    });
  }, [executorOverlays, minCandleTime, selectedExecutorId]);

  // ── REST backfill on pair/interval/lookback change ──
  const backfillKeyRef = useRef("");
  useEffect(() => {
    const backfillKey = `${server}:${connector}:${pair}:${interval}:${lookbackSeconds}:${poolAddress ?? ""}`;
    if (backfillKey === backfillKeyRef.current) return;
    backfillKeyRef.current = backfillKey;

    setDuration(lookbackSeconds);

    let cancelled = false;
    const startTime = Math.floor(Date.now() / 1000) - lookbackSeconds;

    const fetchWithRetry = (attempt: number) => {
      if (cancelled) return;
      api
        .getCandles(server, connector, pair, interval, 5000, startTime, undefined, poolAddress)
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
  }, [server, connector, pair, interval, poolAddress, lookbackSeconds]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Initialize chart ONCE ──
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

      // Track the pointer's price/time for click-to-set, the measure tool and
      // the executor tooltip
      chart.subscribeCrosshairMove((param) => {
        if (!param.point || !param.seriesData) {
          cursorPriceRef.current = null;
          crosshairTimeRef.current = null;
          if (tooltipRef.current) tooltipRef.current.style.display = "none";
          // Leave the measure box/badge frozen at their last position — a
          // measurement persists until cleared (click / Esc), so moving off
          // the pane edge doesn't make it vanish.
          return;
        }
        // `seriesData` is the bar at the crosshair's *time* and carries no
        // vertical component, so its close would be the same price for every
        // height inside one candle's column — it serves only as a last resort
        // when the pane can't map the pixel back to a price at all.
        const cursorP = param.point.y !== undefined ? series.coordinateToPrice(param.point.y) : null;
        if (cursorP !== null && cursorP !== undefined) {
          cursorPriceRef.current = cursorP as number;
        } else {
          const data = param.seriesData.get(series);
          cursorPriceRef.current = data && "close" in data ? (data as { close: number }).close : null;
        }
        crosshairTimeRef.current = typeof param.time === "number" ? param.time : null;

        // ── Measure tool: live % of range from the anchor to the cursor ──
        const anchor = measureAnchorRef.current;
        const badge = measureBadgeRef.current;
        const mbox = measureBoxRef.current;
        if (anchor && badge && mbox && containerRef.current) {
          const curPrice = cursorPriceRef.current;
          // The box is drawn purely from pixels, so it works anywhere on the
          // pane — even past the last candle where param.time is undefined.
          if (curPrice != null) {
            const diff = curPrice - anchor.price;
            const pct = anchor.price !== 0 ? (diff / anchor.price) * 100 : 0;
            const up = diff >= 0;
            const tc = getThemeColors();
            const clr = up ? tc.green : tc.red;

            // Draw the box as a pixel overlay — no chart series, so no relayout/flicker
            const ax = chart.timeScale().timeToCoordinate(anchor.time as import("lightweight-charts").UTCTimestamp);
            const ay = series.priceToCoordinate(anchor.price);
            const cx = param.point.x;
            const cy = param.point.y;
            if (ax !== null && ay !== null) {
              mbox.style.left = `${Math.min(ax, cx)}px`;
              mbox.style.top = `${Math.min(ay, cy)}px`;
              mbox.style.width = `${Math.abs(cx - ax)}px`;
              mbox.style.height = `${Math.abs(cy - ay)}px`;
              mbox.style.borderColor = clr;
              mbox.style.background = up ? "rgba(34,197,94,0.10)" : "rgba(239,68,68,0.10)";
              mbox.style.display = "block";
            }

            // Duration needs a time; fall back to the time at the cursor pixel,
            // and omit it if the cursor is beyond the data range.
            const curTime = crosshairTimeRef.current
              ?? (chart.timeScale().coordinateToTime(cx) as number | null);
            const durStr = curTime != null
              ? (() => {
                  const d = Math.abs(curTime - anchor.time);
                  return d >= 86400 ? `${(d / 86400).toFixed(1)}d` : d >= 3600 ? `${(d / 3600).toFixed(1)}h` : d >= 60 ? `${Math.round(d / 60)}m` : `${d}s`;
                })()
              : null;
            const absDiff = Math.abs(diff);
            const diffStr = absDiff >= 1 ? absDiff.toFixed(2) : absDiff.toPrecision(4);
            badge.innerHTML = `<div style="font-weight:700;font-size:14px;color:${clr};font-family:monospace">${up ? "+" : "-"}${Math.abs(pct).toFixed(2)}%</div><div style="font-size:10px;color:#6b7994;font-family:monospace">Δ ${up ? "+" : "-"}${diffStr}${durStr ? ` · ${durStr}` : ""}</div>`;
            badge.style.display = "block";
            const mRect = containerRef.current.getBoundingClientRect();
            let bLeft = mRect.left + param.point.x + 16;
            let bTop = mRect.top + param.point.y - 44;
            if (bLeft + 150 > window.innerWidth - 4) bLeft = mRect.left + param.point.x - 150 - 16;
            if (bTop < 4) bTop = mRect.top + param.point.y + 16;
            badge.style.left = `${bLeft}px`;
            badge.style.top = `${bTop}px`;
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
        const pnlClr = pnlHexColor(o.pnl);
        const cvtPnl = convertPnlRef.current;
        const cvtVal = convertValueRef.current;
        const pnlStr = cvtPnl(o.pnl);
        const pctStr = o.pnlPct !== 0 ? `${o.pnlPct > 0 ? "+" : ""}${(o.pnlPct * 100).toFixed(2)}%` : "";
        const volStr = cvtVal(o.volume);
        const feesStr = o.fees ? cvtVal(o.fees) : "";

        // An LP position has no direction -- it is `RANGE` -- and the buy/sell
        // normalization files everything that is not a buy under "sell", which
        // labelled a live two-sided range position `SELL`. Neutral for that type.
        const isRangeSide = o.type === "lp";
        const sideLabel = isRangeSide ? "range" : o.side;
        const sideClr = isRangeSide ? "#9ca3af" : sideColor(o.side);
        const sideBg = isRangeSide
          ? "rgba(156,163,175,0.15)"
          : o.side === "buy"
            ? "rgba(34,197,94,0.15)"
            : "rgba(239,68,68,0.15)";
        const statusBg = o.status?.toLowerCase() === "running" || o.status?.toLowerCase() === "active"
          ? "rgba(34,197,94,0.15)" : "rgba(156,163,175,0.15)";
        const statusClr = o.status?.toLowerCase() === "running" || o.status?.toLowerCase() === "active"
          ? getThemeColors().green : "#9ca3af";

        // Build config detail rows
        const cfg = o.config || {};
        const tripleBarrier: Record<string, unknown> = (() => {
          const raw = cfg.triple_barrier_config;
          if (!raw) return {};
          if (typeof raw === "string") { try { return JSON.parse(raw); } catch { return {}; } }
          return typeof raw === "object" ? (raw as Record<string, unknown>) : {};
        })();

        let detailRows = "";
        const addRow = (label: string, value: string, color?: string) => {
          detailRows += `<div style="display:flex;justify-content:space-between;gap:12px"><span style="color:#6b7994">${escapeHtml(label)}</span><span style="font-family:monospace;${color ? `color:${color}` : ""}">${escapeHtml(value)}</span></div>`;
        };

        // Range-box details. Any executor drawn as a box describes itself by its
        // bounds, not by an entry→exit pair; only the labels differ per type.
        if (o.gridBox) {
          if (o.type === "lp") {
            // startPrice is the box's upper edge (see computeLpOverlay).
            addRow("Upper Price", formatPriceSig(o.gridBox.startPrice));
            addRow("Lower Price", formatPriceSig(o.gridBox.endPrice));
            if (cfg.lp_provider != null) addRow("Provider", String(cfg.lp_provider));
          } else {
            addRow("Start Price", formatPriceSig(o.gridBox.startPrice));
            addRow("End Price", formatPriceSig(o.gridBox.endPrice));
            if (o.gridBox.limitPrice) addRow("Limit Price", formatPriceSig(o.gridBox.limitPrice));
          }
        } else if (o.entryPrice && o.entryPrice > 0) {
          addRow("Entry", formatPriceSig(o.entryPrice));
          if (o.exitPrice && o.exitPrice > 0 && o.exitPrice !== o.entryPrice) {
            addRow(o.status?.toLowerCase() === "running" ? "Current" : "Close", formatPriceSig(o.exitPrice));
          }
        }

        if (cfg.leverage != null && Number(cfg.leverage) > 1) addRow("Leverage", `${cfg.leverage}x`);
        if (cfg.total_amount_quote != null) addRow("Amount", cvtVal(Number(cfg.total_amount_quote)));
        else if (cfg.amount != null && Number(cfg.amount) > 0) addRow("Amount", String(cfg.amount));

        const tp = Number(tripleBarrier.take_profit || cfg.take_profit);
        if (tp > 0 && tp !== -1) addRow("Take Profit", `${(tp * 100).toFixed(2)}%`, getThemeColors().green);
        const sl = Number(cfg.stop_loss);
        if (sl > 0 && sl !== -1) addRow("Stop Loss", `${(sl * 100).toFixed(2)}%`, getThemeColors().red);
        if (cfg.keep_position != null) addRow("Keep Position", String(cfg.keep_position) === "true" ? "Yes" : "No");

        tooltip.innerHTML = `
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
            <span style="font-weight:700;font-size:12px;font-family:monospace">${escapeHtml(o.executorId.slice(0, 10))}\u2026</span>
            <span style="background:${sideBg};color:${sideClr};font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;text-transform:uppercase">${escapeHtml(sideLabel)}</span>
            <span style="background:${statusBg};color:${statusClr};font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px">${escapeHtml(o.status)}</span>
          </div>
          <div style="display:flex;align-items:center;gap:4px;margin-bottom:2px">
            <span style="background:rgba(255,255,255,0.06);padding:1px 5px;border-radius:3px;font-size:10px;border:1px solid rgba(255,255,255,0.08)">${escapeHtml(o.type.toUpperCase())}</span>
            ${o.closeType ? `<span style="font-size:10px;color:#6b7994">${escapeHtml(o.closeType)}</span>` : ""}
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

        // Position tooltip using viewport-fixed coords (rendered via portal)
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
        measureAnchorRef.current = null;
      }
    };
  }, []);

  // ── Pane height, cached ──
  // `height()` is handed out and polled by siblings, and a `clientHeight` read
  // on a page whose layout is being dirtied by streaming data is a forced
  // reflow every time it is asked. The pane's height only changes when the pane
  // is resized, so it is taken from the observer that already hears about that
  // — inside the callback, where layout is clean — and `height()` just hands
  // the number back.
  const paneHeightRef = useRef(0);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    paneHeightRef.current = el.clientHeight;
    const observer = new ResizeObserver(() => {
      paneHeightRef.current = el.clientHeight;
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // ── Lend the price mapping out, once the series exists ──
  // Read before the series is created, priceToCoordinate answers for an empty
  // scale — plausible pixels for the wrong prices — so this waits on chartReady
  // rather than firing from the init effect. The callback is held in a ref so a
  // parent that passes an inline arrow does not re-hand the axis every render.
  const onChartReadyRef = useRef(onChartReady);
  onChartReadyRef.current = onChartReady;
  useEffect(() => {
    if (!chartReady) return;
    const notify = onChartReadyRef.current;
    if (!notify) return;
    notify({
      priceToCoordinate: (price) => {
        const coord = seriesRef.current?.priceToCoordinate(price);
        return coord == null ? null : (coord as number);
      },
      height: () => paneHeightRef.current || containerRef.current?.clientHeight || 0,
      onScaleChange: (cb) => {
        const chart = chartRef.current;
        if (!chart) return () => {};
        const timeScale = chart.timeScale();
        timeScale.subscribeVisibleLogicalRangeChange(cb);
        return () => timeScale.unsubscribeVisibleLogicalRangeChange(cb);
      },
    });
  }, [chartReady]);

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

  // Signature of the last full setData() render: channel key + earliest
  // timestamp + count. Lets us tell a wholesale change (first load, pair/
  // interval switch, history backfill/prepend) apart from a live tick, where
  // the listener below already applied a cheap series.update().
  const lastSetDataSigRef = useRef<string>("");

  // ── Push candle data to chart (full setData only on structural changes) ──
  useEffect(() => {
    if (!chartReady || !seriesRef.current || !candles.length) return;

    const key = candleChannelKey(server, connector, pair, interval, poolAddress);
    const first = candles[0].timestamp;
    const prevSig = lastSetDataSigRef.current;
    const [prevKey, prevFirstStr, prevLenStr] = prevSig.split("|");
    const prevFirst = Number(prevFirstStr);
    const prevLen = Number(prevLenStr);

    // lightweight-charts series.update() only handles the last bar or a single
    // newer appended bar. So a full setData() is only required when:
    //   • first load for this chart instance (no prior signature), or
    //   • the channel key changed (pair/interval/connector/server switch), or
    //   • the earliest candle moved back in time, i.e. older history was
    //     prepended (REST backfill) — update() can't insert before the data.
    // A plain live tick keeps the same key and earliest timestamp (last-bar
    // update) or grows the count by appending a newer bar; both are already
    // handled incrementally by the candleStore listener below, so we skip the
    // expensive map + setData over the whole array on every tick.
    const isFirstLoad = prevSig === "";
    const keyChanged = prevKey !== key;
    const historyPrepended = candles.length > prevLen && first < prevFirst;
    const needsFullReset = isFirstLoad || keyChanged || historyPrepended;

    lastSetDataSigRef.current = `${key}|${first}|${candles.length}`;

    if (!needsFullReset) return;

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
  }, [candles, chartReady, server, connector, pair, interval, poolAddress]);

  // ── Real-time last candle update via candle store listener ──
  useEffect(() => {
    if (!chartReady || !seriesRef.current) return;
    const key = candleChannelKey(server, connector, pair, interval, poolAddress);

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
  }, [chartReady, server, connector, pair, interval, poolAddress]);

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
  }, [pricePrecision, chartReady]);

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
        color: getThemeColors().green,
        lineWidth: 2,
        lineStyle: mod.LineStyle.Solid,
        axisLabelVisible: true,
        title: lineLabels?.start ?? "Start",
      });
    }

    if (endPrice > 0) {
      endLineRef.current = series.createPriceLine({
        price: endPrice,
        color: getThemeColors().green,
        lineWidth: 2,
        lineStyle: mod.LineStyle.Dashed,
        axisLabelVisible: true,
        title: lineLabels?.end ?? "End",
      });
    }

    if (limitPrice > 0) {
      const limitColor = side === 1 ? getThemeColors().red : "#f97316";
      limitLineRef.current = series.createPriceLine({
        price: limitPrice,
        color: activePickField === "limit" ? "#fbbf24" : limitColor,
        lineWidth: 2,
        lineStyle: mod.LineStyle.Dotted,
        axisLabelVisible: true,
        title: lineLabels?.limit ?? "Limit",
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
  }, [startPrice, endPrice, limitPrice, side, minSpread, totalAmountQuote, minOrderAmountQuote, activePickField, extraLines, lineLabels, chartReady]);

  // ── Executor overlays ──
  // `chartReady` is a dependency, not a guard for its own sake: lightweight-charts
  // is imported lazily, so on a warm cache the overlays exist before the chart
  // does and this effect bails out. Without the flag in the deps nothing re-runs
  // it -- executors keep a stable reference across refetches by design -- and the
  // page renders candles with no box on them until an executor's PnL happens to
  // move. Every effect below that draws through `chartModuleRef` needs the same.
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

    if (!filteredOverlays?.length) return;

    const hasSelection = !!selectedExecutorId;
    const isMulti = filteredOverlays.length > 1;

    filteredOverlays.forEach((overlay, idx) => {
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
              color: applyDim(getThemeColors().red), lineWidth: 1, lineStyle: mod.LineStyle.Dotted,
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
    if (series && filteredOverlays?.length) {
      const styleMap: Record<string, number> = {
        solid: mod.LineStyle.Solid,
        dashed: mod.LineStyle.Dashed,
        dotted: mod.LineStyle.Dotted,
      };
      for (const overlay of filteredOverlays) {
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
  }, [filteredOverlays, selectedExecutorId, chartReady]);

  // ── Zoom to selected executor (one-time action) ──
  useEffect(() => {
    if (!selectedExecutorId || !chartRef.current || !filteredOverlays?.length) return;
    // Only zoom once per selection — don't re-zoom on overlay data refresh
    if (lastZoomedIdRef.current === selectedExecutorId) return;
    const overlay = filteredOverlays.find((o) => o.executorId === selectedExecutorId);
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
  }, [selectedExecutorId, filteredOverlays]);

  // Reset zoom tracking when executor is deselected
  useEffect(() => {
    if (!selectedExecutorId) lastZoomedIdRef.current = null;
  }, [selectedExecutorId]);

  // Keep overlaysRef in sync for tooltip
  useEffect(() => {
    overlaysRef.current = filteredOverlays ?? [];
  }, [filteredOverlays]);

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
      const pnlStr = convertPnl(pnl);
      const amt = Math.abs(pos.amount);
      const color = pnlHexColor(pnl);
      // A hold nothing ties to this pool still belongs on the chart -- the price
      // is the same market -- but it must not read as this pool's position, so
      // it says whose it is: the pair's, across whatever pool the router used.
      const scope = pos.pool_scoped === false ? " · any pool" : "";
      const label = `${isLong ? "LONG" : "SHORT"} ${amt.toFixed(4)} · ${pnlStr}${scope}`;
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
    // `convertPnl` is a dependency, not a ref read: the label is a string the
    // chart owns once created, so a currency switch has to redraw the lines.
    // The effect already clears every line it drew, so re-running is idempotent.
  }, [positions, chartReady, convertPnl]);

  // ── Measure tool helpers ──
  const clearMeasure = () => {
    measureAnchorRef.current = null;
    if (measureBoxRef.current) measureBoxRef.current.style.display = "none";
    if (measureBadgeRef.current) measureBadgeRef.current.style.display = "none";
  };

  // Esc clears an active measurement
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") clearMeasure(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ── Click-to-set price / deselect executor / measure ──
  //
  // A press-drag-release inside the pane is a chart pan, and the browser still
  // reports it as a `click` on the container: the event fires on the common
  // ancestor of press and release whatever the distance travelled. Bound to
  // `onClick`, every pan would set a price or drop the executor selection. So
  // the gesture is tracked by hand — press position recorded, release gated on
  // having stayed put — and the modifier is read from the press, so releasing
  // shift mid-gesture cannot switch which branch runs.
  const pointerDownRef = useRef<{
    x: number;
    y: number;
    button: number;
    shiftKey: boolean;
  } | null>(null);

  const handlePointerDown = (e: React.PointerEvent) => {
    pointerDownRef.current = {
      x: e.clientX,
      y: e.clientY,
      button: e.button,
      shiftKey: e.shiftKey,
    };
  };

  const handlePointerUp = (e: React.PointerEvent) => {
    const down = pointerDownRef.current;
    pointerDownRef.current = null;
    if (!down || down.button !== 0) return;
    if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > CLICK_SLOP_PX) return;

    // Shift+click drops the measure anchor; move the mouse to see the % of the range
    if (down.shiftKey) {
      if (cursorPriceRef.current != null && crosshairTimeRef.current != null) {
        clearMeasure();
        measureAnchorRef.current = { price: cursorPriceRef.current, time: crosshairTimeRef.current };
      }
      return;
    }
    // A plain click clears an active measurement first
    if (measureAnchorRef.current) {
      clearMeasure();
      return;
    }
    if (activePickField && cursorPriceRef.current !== null) {
      onPriceSet(activePickField, roundToPricePrecision(cursorPriceRef.current, pricePrecision));
      return;
    }
    // Click on chart background deselects executor
    if (selectedExecutorId && onExecutorDeselect) {
      onExecutorDeselect();
    }
  };

  return (
    <div className="flex h-full flex-col">
      {activePickField && (
        <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
          <p className="text-[10px] text-[var(--color-text-muted)]">
            Click on chart to set{" "}
            {(lineLabels?.[activePickField] ?? PICK_LABELS[activePickField]).toLowerCase()}{" "}
            price
          </p>
          <span className="animate-pulse rounded bg-[var(--color-primary)]/20 px-2 py-0.5 text-xs text-[var(--color-primary)]">
            Pick mode:{" "}
            {(lineLabels?.[activePickField] ?? PICK_LABELS[activePickField]).toLowerCase()}
          </span>
        </div>
      )}
      <div className="relative flex-1">
        <div
          ref={containerRef}
          className="absolute inset-0"
          style={{ cursor: activePickField ? "crosshair" : "default" }}
          onPointerDown={handlePointerDown}
          onPointerUp={handlePointerUp}
        />
        {/* Measure box overlay — pure DOM, never touches chart series */}
        <div
          ref={measureBoxRef}
          className="pointer-events-none absolute z-10"
          style={{ display: "none", border: "1px dashed", borderRadius: 2 }}
        />
        {/* Measure-tool discoverability hint */}
        <div className="pointer-events-none absolute bottom-1 left-2 z-10 text-[9px] text-[var(--color-text-muted)] opacity-60">
          ⇧+click: measure range
        </div>
        {/* Executor tooltip overlay — rendered via portal to escape overflow-hidden */}
        {createPortal(
          <div
            ref={tooltipRef}
            style={{
              display: "none",
              position: "fixed",
              top: 0,
              left: 0,
              zIndex: 9999,
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
          />,
          document.body,
        )}
        {/* Measure badge — floats near cursor showing % of range */}
        {createPortal(
          <div
            ref={measureBadgeRef}
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
              padding: "4px 8px",
              textAlign: "right",
              lineHeight: 1.25,
              backdropFilter: "blur(12px)",
              boxShadow: "0 4px 16px rgba(0,0,0,0.4)",
            }}
          />,
          document.body,
        )}
      </div>
    </div>
  );
}
