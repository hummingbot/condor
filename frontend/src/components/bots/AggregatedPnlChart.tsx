import { useEffect, useMemo, useRef } from "react";

import type { ControllerPerformanceSnapshot } from "@/lib/api";
import { formatCurrencyVolume, pnlColor } from "@/lib/formatters";

function getChartColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    bg: style.getPropertyValue("--chart-bg").trim() || "#0f1525",
    grid: style.getPropertyValue("--chart-grid").trim() || "#1c2541",
    text: style.getPropertyValue("--chart-text").trim() || "#6b7994",
    green: style.getPropertyValue("--chart-up").trim() || "#22c55e",
    red: style.getPropertyValue("--chart-down").trim() || "#ef4444",
    blue: "#3b82f6",
    orange: "#f59e0b",
  };
}

function toSeconds(ts: string | number): number {
  if (typeof ts === "number") return ts > 1e12 ? Math.floor(ts / 1000) : ts;
  const parsed = Date.parse(ts);
  if (!isNaN(parsed)) return Math.floor(parsed / 1000);
  return 0;
}

interface AggregatedPoint {
  time: number;
  realized: number;
  unrealized: number;
  total: number;
  volume: number;
}

interface Props {
  snapshots: ControllerPerformanceSnapshot[];
  currencySymbol?: string;
  height?: number;
}

/**
 * Aggregates all controller snapshots by timestamp bucket,
 * summing PnL and volume across all running controllers.
 */
export function AggregatedPnlChart({ snapshots, currencySymbol = "$", height = 280 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);

  const aggregated = useMemo(() => {
    if (!snapshots || snapshots.length === 0) return [];

    // Group by controller, then align on time buckets
    const byController: Record<string, ControllerPerformanceSnapshot[]> = {};
    for (const snap of snapshots) {
      const key = snap.controller_id || snap.controller_name;
      if (!key) continue;
      (byController[key] ??= []).push(snap);
    }

    // Sort each controller's snapshots by time
    for (const snaps of Object.values(byController)) {
      snaps.sort((a, b) => toSeconds(a.timestamp) - toSeconds(b.timestamp));
    }

    // Collect all unique timestamps
    const timeSet = new Set<number>();
    for (const snaps of Object.values(byController)) {
      for (const s of snaps) timeSet.add(toSeconds(s.timestamp));
    }
    const times = Array.from(timeSet).sort((a, b) => a - b);
    if (times.length === 0) return [];

    // For each time, sum the latest known value per controller up to that time
    const controllerIds = Object.keys(byController);
    const points: AggregatedPoint[] = [];
    // Track index cursor per controller for efficient lookup
    const cursors: Record<string, number> = {};
    for (const cid of controllerIds) cursors[cid] = 0;

    for (const t of times) {
      let realized = 0;
      let unrealized = 0;
      let volume = 0;

      for (const cid of controllerIds) {
        const snaps = byController[cid];
        // Advance cursor to latest snap <= t
        while (cursors[cid] < snaps.length - 1 && toSeconds(snaps[cursors[cid] + 1].timestamp) <= t) {
          cursors[cid]++;
        }
        // Only include if this controller has data at or before time t
        if (toSeconds(snaps[cursors[cid]].timestamp) <= t) {
          const s = snaps[cursors[cid]];
          realized += s.realized_pnl_quote;
          unrealized += s.unrealized_pnl_quote;
          volume += s.volume_traded;
        }
      }

      points.push({ time: t, realized, unrealized, total: realized + unrealized, volume });
    }

    return points;
  }, [snapshots]);

  // Latest values for the header
  const latest = aggregated.length > 0 ? aggregated[aggregated.length - 1] : null;

  useEffect(() => {
    if (!containerRef.current || aggregated.length === 0) return;

    let cancelled = false;

    import("lightweight-charts").then((mod) => {
      if (cancelled || !containerRef.current) return;

      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }

      const colors = getChartColors();
      const chart = mod.createChart(containerRef.current!, {
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
          priceFormatter: (price: number) => `${currencySymbol}${price >= 0 ? "+" : ""}${price.toFixed(2)}`,
        },
      });
      chartRef.current = chart;

      // Total PnL area
      const totalSeries = chart.addSeries(mod.AreaSeries, {
        lineColor: aggregated[aggregated.length - 1]?.total >= 0 ? colors.green : colors.red,
        topColor: aggregated[aggregated.length - 1]?.total >= 0 ? `${colors.green}20` : `${colors.red}20`,
        bottomColor: "transparent",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: "Total PnL",
        crosshairMarkerVisible: true,
      });

      // Realized PnL line
      const realizedSeries = chart.addSeries(mod.LineSeries, {
        color: colors.green,
        lineWidth: 1,
        lineStyle: mod.LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "Realized",
        crosshairMarkerVisible: true,
      });

      // Unrealized PnL line
      const unrealizedSeries = chart.addSeries(mod.LineSeries, {
        color: colors.orange,
        lineWidth: 1,
        lineStyle: mod.LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        title: "Unrealized",
        crosshairMarkerVisible: true,
      });

      // Volume (secondary axis)
      const volumeSeries = chart.addSeries(mod.LineSeries, {
        color: `${colors.blue}70`,
        lineWidth: 1,
        priceScaleId: "volume",
        priceLineVisible: false,
        lastValueVisible: false,
        title: "Cum. Vol",
        priceFormat: { type: "volume" },
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });

      type TS = import("lightweight-charts").UTCTimestamp;

      totalSeries.setData(aggregated.map((p) => ({ time: p.time as TS, value: p.total })));
      realizedSeries.setData(aggregated.map((p) => ({ time: p.time as TS, value: p.realized })));
      unrealizedSeries.setData(aggregated.map((p) => ({ time: p.time as TS, value: p.unrealized })));
      volumeSeries.setData(aggregated.map((p) => ({ time: p.time as TS, value: p.volume })));

      chart.timeScale().fitContent();

      // Crosshair tooltip
      chart.subscribeCrosshairMove((param) => {
        const tooltip = tooltipRef.current;
        if (!tooltip || !containerRef.current) return;

        if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
          tooltip.style.display = "none";
          return;
        }

        const totalData = param.seriesData.get(totalSeries);
        const realizedData = param.seriesData.get(realizedSeries);
        const unrealizedData = param.seriesData.get(unrealizedSeries);
        const volData = param.seriesData.get(volumeSeries);

        if (!totalData || !("value" in totalData)) {
          tooltip.style.display = "none";
          return;
        }

        const total = (totalData as { value: number }).value;
        const realized = realizedData && "value" in realizedData ? (realizedData as { value: number }).value : 0;
        const unrealized = unrealizedData && "value" in unrealizedData ? (unrealizedData as { value: number }).value : 0;
        const vol = volData && "value" in volData ? (volData as { value: number }).value : 0;
        const ts = typeof param.time === "number" ? param.time : 0;
        const date = new Date(ts * 1000);
        const timeStr = date.toLocaleString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });

        const sign = (v: number) => (v >= 0 ? "+" : "");
        const totalColor = total >= 0 ? colors.green : colors.red;

        tooltip.innerHTML = `
          <div style="color:#9ca3af;font-size:10px;margin-bottom:3px">${timeStr}</div>
          <div style="display:flex;justify-content:space-between;gap:12px">
            <span style="color:#9ca3af;font-size:10px">Total</span>
            <span style="color:${totalColor};font-weight:600;font-size:12px">${currencySymbol}${sign(total)}${total.toFixed(2)}</span>
          </div>
          <div style="display:flex;justify-content:space-between;gap:12px">
            <span style="color:#9ca3af;font-size:10px">Realized</span>
            <span style="color:${colors.green};font-size:11px">${currencySymbol}${sign(realized)}${realized.toFixed(2)}</span>
          </div>
          <div style="display:flex;justify-content:space-between;gap:12px">
            <span style="color:#9ca3af;font-size:10px">Unrealized</span>
            <span style="color:${colors.orange};font-size:11px">${currencySymbol}${sign(unrealized)}${unrealized.toFixed(2)}</span>
          </div>
          ${vol > 0 ? `<div style="display:flex;justify-content:space-between;gap:12px;margin-top:2px;border-top:1px solid rgba(107,121,148,0.2);padding-top:2px"><span style="color:#9ca3af;font-size:10px">Cum. Vol</span><span style="color:${colors.blue};font-size:10px">${currencySymbol}${vol >= 1000 ? (vol / 1000).toFixed(1) + "K" : vol.toFixed(0)}</span></div>` : ""}
        `;
        tooltip.style.display = "block";

        const containerRect = containerRef.current.getBoundingClientRect();
        const tooltipW = 200;
        let left = containerRect.left + param.point.x + 16;
        if (left + tooltipW > window.innerWidth - 8) left = containerRect.left + param.point.x - tooltipW - 10;
        const top = containerRect.top + Math.max(4, param.point.y - 30);
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${top}px`;
      });
    });

    return () => {
      cancelled = true;
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, [aggregated, currencySymbol]);

  if (!snapshots || snapshots.length === 0) return null;

  if (aggregated.length < 2) return null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <div className="flex items-center gap-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Portfolio PnL
          </p>
          {latest && (
            <div className="flex items-center gap-3 text-xs tabular-nums">
              <span style={{ color: pnlColor(latest.total) }} className="font-semibold">
                {latest.total >= 0 ? "+" : ""}{formatCurrencyVolume(latest.total, currencySymbol)}
              </span>
              <span className="text-[var(--color-text-muted)]">
                R: <span style={{ color: "var(--color-green)" }}>{latest.realized >= 0 ? "+" : ""}{formatCurrencyVolume(latest.realized, currencySymbol)}</span>
              </span>
              <span className="text-[var(--color-text-muted)]">
                U: <span style={{ color: "#f59e0b" }}>{latest.unrealized >= 0 ? "+" : ""}{formatCurrencyVolume(latest.unrealized, currencySymbol)}</span>
              </span>
              <span className="text-[var(--color-text-muted)]">
                Vol: {formatCurrencyVolume(latest.volume, currencySymbol)}
              </span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 text-[9px]">
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-0.5 rounded" style={{ background: "#22c55e" }} />
            Realized
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-0.5 rounded" style={{ background: "#f59e0b" }} />
            Unrealized
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-0.5 rounded" style={{ background: "#3b82f690" }} />
            Volume
          </span>
        </div>
      </div>
      <div>
        <div ref={containerRef} style={{ height, width: "100%" }} />
        <div
          ref={tooltipRef}
          style={{
            display: "none",
            position: "fixed",
            top: 0,
            left: 0,
            zIndex: 9999,
            pointerEvents: "none",
            background: "rgba(15, 21, 37, 0.95)",
            border: "1px solid rgba(107, 121, 148, 0.3)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 11,
            color: "#e2e8f0",
            minWidth: 160,
            whiteSpace: "nowrap",
            lineHeight: 1.5,
            backdropFilter: "blur(8px)",
          }}
        />
      </div>
    </div>
  );
}
