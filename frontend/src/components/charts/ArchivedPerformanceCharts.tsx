import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { api, type CandleData, type ExecutorInfo, type PnlPoint } from "@/lib/api";
import {
  computeMultiOverlays,
  getExecutorColor,
  getOverlayTimeRange,
  type ExecutorOverlay,
} from "@/lib/executor-overlays";

interface Props {
  server: string;
  executors: ExecutorInfo[];
  cumulativePnl: PnlPoint[];
  connector: string;
  tradingPair: string;
  startTime?: number;
  endTime?: number;
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

function tsToSeconds(ts: number): number {
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

function pickCandleInterval(startSec: number, endSec: number): { interval: string; limit: number } {
  const dur = endSec - startSec;
  if (dur < 2 * 3600) return { interval: "1m", limit: 500 };
  if (dur < 12 * 3600) return { interval: "5m", limit: 500 };
  if (dur < 3 * 86400) return { interval: "15m", limit: 700 };
  if (dur < 14 * 86400) return { interval: "1h", limit: 500 };
  return { interval: "4h", limit: 1000 };
}

/** Interval string to seconds */
function intervalToSeconds(interval: string): number {
  const m = interval.match(/^(\d+)(m|h)$/);
  if (!m) return 60;
  return parseInt(m[1]) * (m[2] === "h" ? 3600 : 60);
}

// ── PnL evolution from closed executors ──

interface PnlEvolutionPoint {
  time: number;
  netPnl: number;
  tradePnl: number;
  cumFees: number;
}

function computePnlEvolution(executors: ExecutorInfo[]): PnlEvolutionPoint[] {
  const closed = executors
    .filter((e) => e.close_timestamp > 0)
    .sort((a, b) => tsToSeconds(a.close_timestamp) - tsToSeconds(b.close_timestamp));
  if (closed.length === 0) return [];

  let cumNetPnl = 0;
  let cumFees = 0;

  return closed.map((e) => {
    cumNetPnl += e.pnl;
    cumFees += e.cum_fees_quote;
    return {
      time: tsToSeconds(e.close_timestamp),
      netPnl: cumNetPnl,
      tradePnl: cumNetPnl + cumFees,
      cumFees: -cumFees,
    };
  });
}

// ── Buy/Sell volume per candle bucket ──

interface VolumeBucket {
  time: number;
  buyVol: number;
  sellVol: number;
  buyCount: number;
  sellCount: number;
}

function computeVolumeBuckets(executors: ExecutorInfo[], intervalSec: number): VolumeBucket[] {
  const buckets = new Map<number, VolumeBucket>();

  for (const e of executors) {
    const t = tsToSeconds(e.timestamp);
    if (t <= 0) continue;

    // Floor to candle bucket boundary
    const bucket = Math.floor(t / intervalSec) * intervalSec;
    const isBuy = e.side?.toUpperCase() === "BUY";
    const vol = e.volume > 0 ? e.volume : 0;

    let b = buckets.get(bucket);
    if (!b) {
      b = { time: bucket, buyVol: 0, sellVol: 0, buyCount: 0, sellCount: 0 };
      buckets.set(bucket, b);
    }
    if (isBuy) {
      b.buyVol += vol;
      b.buyCount++;
    } else {
      b.sellVol += vol;
      b.sellCount++;
    }
  }

  return Array.from(buckets.values()).sort((a, b) => a.time - b.time);
}

// ── Position held resampled to candle intervals ──

interface PositionSample {
  time: number;
  net: number;
}

function computeResampledPosition(
  executors: ExecutorInfo[],
  candleTimes: number[],
): PositionSample[] {
  if (candleTimes.length === 0) return [];

  // Build sorted events
  const events: { time: number; delta: number }[] = [];
  for (const e of executors) {
    const amount =
      Number(e.config?.amount) ||
      Number(e.custom_info?.amount) ||
      (e.volume > 0 && e.entry_price > 0 ? e.volume / e.entry_price : 0);
    if (amount <= 0) continue;

    const isBuy = e.side?.toUpperCase() === "BUY";
    const sign = isBuy ? 1 : -1;
    const openTime = tsToSeconds(e.timestamp);
    const closeTime = e.close_timestamp > 0 ? tsToSeconds(e.close_timestamp) : 0;

    if (openTime > 0) events.push({ time: openTime, delta: sign * amount });
    if (closeTime > 0) events.push({ time: closeTime, delta: -sign * amount });
  }

  if (events.length === 0) return [];
  events.sort((a, b) => a.time - b.time);

  // Sample position at each candle time
  let eventIdx = 0;
  let net = 0;
  const result: PositionSample[] = [];

  for (const t of candleTimes) {
    // Apply all events up to this candle time
    while (eventIdx < events.length && events[eventIdx].time <= t) {
      net += events[eventIdx].delta;
      eventIdx++;
    }
    result.push({ time: t, net: Math.round(net * 10000) / 10000 });
  }

  // Apply remaining events after last candle
  while (eventIdx < events.length) {
    net += events[eventIdx].delta;
    eventIdx++;
  }

  return result;
}

type ChartApi = import("lightweight-charts").IChartApi;
type UTCTimestamp = import("lightweight-charts").UTCTimestamp;

function capExecutorsForOverlays(executors: ExecutorInfo[], max = 50): ExecutorInfo[] {
  if (executors.length <= max) return executors;
  return [...executors].sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl)).slice(0, max);
}

function snapToCandle(time: number, candleTimes: number[]): number {
  if (candleTimes.length === 0) return time;
  let lo = 0;
  let hi = candleTimes.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (candleTimes[mid] < time) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0) {
    const diffLo = Math.abs(candleTimes[lo] - time);
    const diffPrev = Math.abs(candleTimes[lo - 1] - time);
    return diffPrev < diffLo ? candleTimes[lo - 1] : candleTimes[lo];
  }
  return candleTimes[lo];
}

/** Deduplicate sorted time-value data, keeping last value per timestamp */
function dedupSorted<T extends { time: number | UTCTimestamp }>(data: T[]): T[] {
  if (data.length <= 1) return data;
  const result: T[] = [data[0]];
  for (let i = 1; i < data.length; i++) {
    if ((data[i].time as number) === (data[i - 1].time as number)) {
      result[result.length - 1] = data[i];
    } else {
      result.push(data[i]);
    }
  }
  return result;
}

// ── Main component ──

export function ArchivedPerformanceCharts({
  server,
  executors,
  cumulativePnl,
  connector,
  tradingPair,
  startTime: propStartTime,
  endTime: propEndTime,
}: Props) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ChartApi | null>(null);

  const pnlEvolution = useMemo(() => computePnlEvolution(executors), [executors]);

  const cappedExecutors = useMemo(() => capExecutorsForOverlays(executors), [executors]);
  const overlays = useMemo(() => computeMultiOverlays(cappedExecutors), [cappedExecutors]);
  const executorTimeRange = useMemo(() => getOverlayTimeRange(overlays), [overlays]);

  // Compute full time range: union of bot range, executor range, and PnL range
  const timeRange = useMemo(() => {
    let start = executorTimeRange.start;
    let end = executorTimeRange.end;

    if (propStartTime) start = Math.min(start, propStartTime);
    if (propEndTime) end = Math.max(end, propEndTime);

    if (cumulativePnl.length > 0) {
      const pnlStart = tsToSeconds(cumulativePnl[0].timestamp);
      const pnlEnd = tsToSeconds(cumulativePnl[cumulativePnl.length - 1].timestamp);
      if (pnlStart > 0 && pnlStart < start) start = pnlStart;
      if (pnlEnd > end) end = pnlEnd;
    }

    return { start, end };
  }, [propStartTime, propEndTime, executorTimeRange, cumulativePnl]);

  const paddingSeconds = 300;
  const fetchStart = Math.floor(timeRange.start - paddingSeconds);
  const fetchEnd = Math.ceil(timeRange.end + paddingSeconds);

  const { interval, limit } = pickCandleInterval(fetchStart, fetchEnd);
  const intervalSec = intervalToSeconds(interval);

  // Volume buckets for executor activity visualization
  const volumeBuckets = useMemo(
    () => computeVolumeBuckets(executors, intervalSec),
    [executors, intervalSec],
  );

  const isManyExecutors = executors.length > 15;

  const { data: candles } = useQuery({
    queryKey: ["archived-candles", server, connector, tradingPair, fetchStart, fetchEnd, interval],
    queryFn: () => api.getCandles(server, connector, tradingPair, interval, limit, fetchStart, fetchEnd),
    enabled: !!server && !!connector && !!tradingPair && timeRange.start > 0,
    staleTime: Infinity,
    retry: 1,
  });

  const hasPnl = cumulativePnl.length > 0 || pnlEvolution.length > 0;

  useEffect(() => {
    if (!candles?.length || !chartContainerRef.current) return;
    let cancelled = false;

    import("lightweight-charts").then((mod) => {
      if (cancelled || !chartContainerRef.current) return;

      const colors = getChartColors();

      const chart = mod.createChart(chartContainerRef.current, {
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
        rightPriceScale: { borderVisible: false },
        timeScale: { timeVisible: true, secondsVisible: false },
      });

      chartRef.current = chart;

      // ═════════════════════════════════════════════════
      // PANE 0: Candlesticks + Executor Overlays + Volume
      // ═════════════════════════════════════════════════

      const candleSeries = chart.addSeries(mod.CandlestickSeries, {
        upColor: colors.up,
        downColor: colors.down,
        wickUpColor: colors.up,
        wickDownColor: colors.down,
        borderVisible: false,
      });

      const mappedCandles = candles.map((c: CandleData) => ({
        time: (c.timestamp > 1e12 ? c.timestamp / 1000 : c.timestamp) as UTCTimestamp,
        open: c.open, high: c.high, low: c.low, close: c.close,
      })).sort((a, b) => (a.time as number) - (b.time as number));

      const dedupedCandles = dedupSorted(mappedCandles);
      candleSeries.setData(dedupedCandles);

      const candleTimes = dedupedCandles.map((c) => c.time as number);

      // ── Executor Overlays ──
      if (isManyExecutors) {
        // HIGH-FREQUENCY MODE: Show buy/sell volume histogram on candle pane
        // Buy volume (green bars)
        if (volumeBuckets.length > 0) {
          const buyVolSeries = chart.addSeries(mod.HistogramSeries, {
            color: "#22c55e60",
            priceLineVisible: false,
            lastValueVisible: false,
            priceScaleId: "vol",
            priceFormat: {
              type: "custom",
              formatter: (v: number) => `$${Math.abs(v).toFixed(0)}`,
            },
          });
          buyVolSeries.setData(
            volumeBuckets
              .filter((b) => b.buyVol > 0)
              .map((b) => ({
                time: b.time as UTCTimestamp,
                value: b.buyVol,
                color: "#22c55e60",
              })),
          );

          // Sell volume (red bars, shown as negative direction via separate series)
          const sellVolSeries = chart.addSeries(mod.HistogramSeries, {
            color: "#ef444460",
            priceLineVisible: false,
            lastValueVisible: false,
            priceScaleId: "vol",
          });
          sellVolSeries.setData(
            volumeBuckets
              .filter((b) => b.sellVol > 0)
              .map((b) => ({
                time: b.time as UTCTimestamp,
                value: -b.sellVol,
                color: "#ef444460",
              })),
          );

          // Configure volume price scale (overlay on left, small)
          try {
            chart.priceScale("vol").applyOptions({
              scaleMargins: { top: 0.8, bottom: 0 },
              borderVisible: false,
            });
          } catch { /* */ }
        }
      } else {
        // LOW-FREQUENCY MODE: Individual markers + segments
        const isMulti = overlays.length > 1;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const allMarkers: any[] = [];

        for (let idx = 0; idx < overlays.length; idx++) {
          const overlay: ExecutorOverlay = overlays[idx];
          const color = isMulti ? getExecutorColor(idx, overlay.pnl) : undefined;

          for (const m of overlay.markers) {
            const snappedTime = snapToCandle(tsToSeconds(m.time), candleTimes);
            allMarkers.push({
              time: snappedTime as UTCTimestamp,
              position: m.position,
              color: m.color,
              shape: m.shape,
              text: m.text,
            });
          }

          // Grid boxes
          const box = overlay.gridBox;
          if (box) {
            const boxColor = color ?? box.color;
            const t1 = tsToSeconds(box.startTime);
            const t2 = tsToSeconds(box.endTime);
            if (t2 - t1 >= 4) {
              try {
                const top = chart.addSeries(mod.LineSeries, {
                  color: boxColor, lineWidth: 2,
                  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
                });
                top.setData([
                  { time: t1 as UTCTimestamp, value: box.endPrice },
                  { time: t2 as UTCTimestamp, value: box.endPrice },
                ]);
                const bottom = chart.addSeries(mod.LineSeries, {
                  color: boxColor, lineWidth: 2, lineStyle: mod.LineStyle.Dashed,
                  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
                });
                bottom.setData([
                  { time: t1 as UTCTimestamp, value: box.startPrice },
                  { time: t2 as UTCTimestamp, value: box.startPrice },
                ]);
              } catch { /* skip */ }
            }
            continue;
          }

          // Segments
          const seg = overlay.segment;
          if (!seg) continue;
          const segColor = color ?? seg.color;
          const entryT = tsToSeconds(seg.entryTime);
          const exitT = tsToSeconds(seg.exitTime);
          if (entryT === exitT && seg.entryPrice === seg.exitPrice) continue;
          try {
            const line = chart.addSeries(mod.LineSeries, {
              color: segColor, lineWidth: 2, lineStyle: mod.LineStyle.Dashed,
              priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
            });
            line.setData([
              { time: entryT as UTCTimestamp, value: seg.entryPrice },
              { time: exitT as UTCTimestamp, value: seg.exitPrice },
            ]);
          } catch { /* skip */ }
        }

        if (allMarkers.length > 0) {
          allMarkers.sort((a: { time: number }, b: { time: number }) => a.time - b.time);
          try {
            mod.createSeriesMarkers(candleSeries, allMarkers);
          } catch {
            try {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              (candleSeries as any).setMarkers(allMarkers);
            } catch { /* markers not supported */ }
          }
        }
      }

      // ═════════════════════════════════════════════════
      // PANE 1: Cumulative PnL
      // ═════════════════════════════════════════════════

      if (hasPnl) {
        const pnlPane = 1;

        const netPnlSeries = chart.addSeries(mod.BaselineSeries, {
          baseValue: { type: "price" as const, price: 0 },
          topLineColor: "#22c55e",
          topFillColor1: "#22c55e22",
          topFillColor2: "#22c55e08",
          bottomLineColor: "#ef4444",
          bottomFillColor1: "#ef444408",
          bottomFillColor2: "#ef444422",
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: true,
          priceFormat: {
            type: "custom",
            formatter: (price: number) => {
              const sign = price >= 0 ? "+" : "";
              if (Math.abs(price) >= 1000) return `${sign}$${(price / 1000).toFixed(1)}K`;
              return `${sign}$${price.toFixed(2)}`;
            },
          },
        }, pnlPane);

        if (cumulativePnl.length > 0) {
          const sorted = cumulativePnl
            .map((p) => ({ time: tsToSeconds(p.timestamp) as UTCTimestamp, value: p.pnl }))
            .sort((a, b) => (a.time as number) - (b.time as number));
          netPnlSeries.setData(dedupSorted(sorted));
        } else if (pnlEvolution.length > 0) {
          const sorted = pnlEvolution
            .map((p) => ({ time: p.time as UTCTimestamp, value: p.netPnl }))
            .sort((a, b) => (a.time as number) - (b.time as number));
          netPnlSeries.setData(dedupSorted(sorted));
        }

        if (pnlEvolution.length > 0) {
          const tradePnlSeries = chart.addSeries(mod.LineSeries, {
            color: "#3b82f6",
            lineWidth: 1,
            lineStyle: mod.LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          }, pnlPane);
          const tradeData = pnlEvolution
            .map((p) => ({ time: p.time as UTCTimestamp, value: p.tradePnl }))
            .sort((a, b) => (a.time as number) - (b.time as number));
          tradePnlSeries.setData(dedupSorted(tradeData));

          const feesSeries = chart.addSeries(mod.LineSeries, {
            color: "#f59e0b",
            lineWidth: 1,
            lineStyle: mod.LineStyle.Dotted,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          }, pnlPane);
          const feesData = pnlEvolution
            .map((p) => ({ time: p.time as UTCTimestamp, value: p.cumFees }))
            .sort((a, b) => (a.time as number) - (b.time as number));
          feesSeries.setData(dedupSorted(feesData));
        }
      }

      // ═════════════════════════════════════════════════
      // PANE 2: Net Position (resampled to candle intervals)
      // ═════════════════════════════════════════════════

      const positionSamples = computeResampledPosition(executors, candleTimes);
      const hasPosition = positionSamples.some((p) => p.net !== 0);

      if (hasPosition) {
        const posPane = hasPnl ? 2 : 1;

        // Baseline series: green above 0, red below 0
        const posSeries = chart.addSeries(mod.BaselineSeries, {
          baseValue: { type: "price" as const, price: 0 },
          topLineColor: "#22c55e",
          topFillColor1: "#22c55e33",
          topFillColor2: "#22c55e08",
          bottomLineColor: "#ef4444",
          bottomFillColor1: "#ef444408",
          bottomFillColor2: "#ef444433",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: true,
          crosshairMarkerVisible: false,
          priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
        }, posPane);

        posSeries.setData(
          positionSamples.map((p) => ({
            time: p.time as UTCTimestamp,
            value: p.net,
          })),
        );
      }

      // ── Set pane stretch factors ──
      try {
        const panes = chart.panes();
        const numPanes = panes.length;
        if (numPanes === 3) {
          panes[0].setStretchFactor(0.60);
          panes[1].setStretchFactor(0.25);
          panes[2].setStretchFactor(0.15);
        } else if (numPanes === 2) {
          panes[0].setStretchFactor(0.70);
          panes[1].setStretchFactor(0.30);
        }
      } catch { /* pane API not available */ }

      chart.timeScale().fitContent();
    });

    return () => {
      cancelled = true;
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, [candles, overlays, cumulativePnl, pnlEvolution, executors, volumeBuckets, isManyExecutors, hasPnl, intervalSec]);

  const overlayNote = executors.length > 50
    ? ` (top 50 of ${executors.length} by PnL)`
    : "";

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-[var(--color-bg)]">
        <p className="text-[10px] text-[var(--color-text-muted)]">
          {tradingPair} &middot; {interval} &middot; {executors.length} executors{overlayNote}
        </p>
        <div className="flex items-center gap-4 text-[10px]">
          {isManyExecutors && (
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1">
                <span className="inline-block h-2 w-1.5 bg-emerald-500/60" /> Buy Vol
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-2 w-1.5 bg-red-500/60" /> Sell Vol
              </span>
            </div>
          )}
          {hasPnl && (
            <div className="flex items-center gap-2">
              <span className="flex items-center gap-1">
                <span className="inline-block h-0.5 w-3 bg-emerald-500" /> Net PnL
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-0.5 w-3 bg-blue-500 opacity-60" style={{ borderTop: "1px dashed" }} /> Trade PnL
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-0.5 w-3 bg-amber-500 opacity-60" style={{ borderTop: "1px dotted" }} /> Fees
              </span>
            </div>
          )}
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-emerald-500/40" /> Net Pos
          </span>
        </div>
      </div>

      <div
        ref={chartContainerRef}
        style={{ height: 560, width: "100%" }}
      />
    </div>
  );
}
