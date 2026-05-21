import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";

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
    purple: "#a855f7",
  };
}

interface Props {
  server: string;
  controllerId: string;
  botName: string;
  height?: number;
}

export function ControllerPnlChart({ server, controllerId, botName, height = 400 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["controller-perf-history", server, controllerId],
    queryFn: () =>
      api.getControllerPerformanceHistory(server, {
        controller_id: controllerId,
        bot_name: botName,
        interval: "5m",
        limit: 1000,
      }),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const snapshots = data?.snapshots ?? [];

  useEffect(() => {
    if (!containerRef.current || snapshots.length === 0) return;

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
          priceFormatter: (price: number) => `$${price >= 0 ? "+" : ""}${price.toFixed(2)}`,
        },
      });
      chartRef.current = chart;

      const sorted = [...snapshots].sort((a, b) => toSeconds(a.timestamp) - toSeconds(b.timestamp));

      // Realized PnL line (green)
      const realizedSeries = chart.addSeries(mod.LineSeries, {
        color: colors.green,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: "Realized",
      });

      // Unrealized PnL line (orange)
      const unrealizedSeries = chart.addSeries(mod.LineSeries, {
        color: colors.orange,
        lineWidth: 2,
        lineStyle: mod.LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: true,
        title: "Unrealized",
      });

      // Net position (purple, secondary axis)
      const positionSeries = chart.addSeries(mod.LineSeries, {
        color: colors.purple,
        lineWidth: 1,
        priceScaleId: "position",
        priceLineVisible: false,
        lastValueVisible: true,
        title: "Position",
      });
      chart.priceScale("position").applyOptions({
        scaleMargins: { top: 0.7, bottom: 0.02 },
      });

      // Cumulative volume line (blue, secondary axis)
      const volumeSeries = chart.addSeries(mod.LineSeries, {
        color: `${colors.blue}90`,
        lineWidth: 1,
        priceScaleId: "volume",
        priceLineVisible: false,
        lastValueVisible: true,
        title: "Cum. Vol",
        priceFormat: { type: "volume" },
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.75, bottom: 0 },
      });

      realizedSeries.setData(
        sorted.map((s) => ({
          time: toSeconds(s.timestamp) as import("lightweight-charts").UTCTimestamp,
          value: s.realized_pnl_quote,
        })),
      );

      unrealizedSeries.setData(
        sorted.map((s) => ({
          time: toSeconds(s.timestamp) as import("lightweight-charts").UTCTimestamp,
          value: s.unrealized_pnl_quote,
        })),
      );

      // Extract net position from positions_summary
      const hasPosition = sorted.some((s) => s.positions_summary?.length > 0);
      if (hasPosition) {
        positionSeries.setData(
          sorted.map((s) => {
            let netAmount = 0;
            if (s.positions_summary) {
              for (const pos of s.positions_summary) {
                netAmount += Number(pos.amount || pos.net_amount || pos.position_amount || 0);
              }
            }
            return {
              time: toSeconds(s.timestamp) as import("lightweight-charts").UTCTimestamp,
              value: netAmount,
            };
          }),
        );
      } else {
        // Hide position scale if no data
        positionSeries.applyOptions({ visible: false });
      }

      volumeSeries.setData(
        sorted.map((s) => ({
          time: toSeconds(s.timestamp) as import("lightweight-charts").UTCTimestamp,
          value: s.volume_traded,
        })),
      );

      chart.timeScale().fitContent();

      // Crosshair tooltip
      chart.subscribeCrosshairMove((param) => {
        const tooltip = tooltipRef.current;
        if (!tooltip || !containerRef.current) return;

        if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
          tooltip.style.display = "none";
          return;
        }

        const realizedData = param.seriesData.get(realizedSeries);
        const unrealizedData = param.seriesData.get(unrealizedSeries);
        const volData = param.seriesData.get(volumeSeries);
        const posData = param.seriesData.get(positionSeries);

        if (!realizedData || !("value" in realizedData)) {
          tooltip.style.display = "none";
          return;
        }

        const realized = (realizedData as { value: number }).value;
        const unrealized = unrealizedData && "value" in unrealizedData ? (unrealizedData as { value: number }).value : 0;
        const total = realized + unrealized;
        const vol = volData && "value" in volData ? (volData as { value: number }).value : 0;
        const pos = posData && "value" in posData ? (posData as { value: number }).value : null;
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
            <span style="color:${totalColor};font-weight:600;font-size:12px">$${sign(total)}${total.toFixed(2)}</span>
          </div>
          <div style="display:flex;justify-content:space-between;gap:12px">
            <span style="color:#9ca3af;font-size:10px">Realized</span>
            <span style="color:${colors.green};font-size:11px">$${sign(realized)}${realized.toFixed(2)}</span>
          </div>
          <div style="display:flex;justify-content:space-between;gap:12px">
            <span style="color:#9ca3af;font-size:10px">Unrealized</span>
            <span style="color:${colors.orange};font-size:11px">$${sign(unrealized)}${unrealized.toFixed(2)}</span>
          </div>
          ${vol > 0 ? `<div style="display:flex;justify-content:space-between;gap:12px;margin-top:2px;border-top:1px solid rgba(107,121,148,0.2);padding-top:2px"><span style="color:#9ca3af;font-size:10px">Cum. Vol</span><span style="color:${colors.blue};font-size:10px">$${vol >= 1000 ? (vol / 1000).toFixed(1) + "K" : vol.toFixed(0)}</span></div>` : ""}
          ${pos !== null && hasPosition ? `<div style="display:flex;justify-content:space-between;gap:12px"><span style="color:#9ca3af;font-size:10px">Position</span><span style="color:${colors.purple};font-size:10px">${pos.toFixed(4)}</span></div>` : ""}
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
  }, [snapshots]);

  if (isLoading) {
    return (
      <div
        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] flex items-center justify-center"
        style={{ height }}
      >
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
          Loading performance history...
        </div>
      </div>
    );
  }

  if (snapshots.length === 0) {
    return (
      <div
        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] flex items-center justify-center"
        style={{ height }}
      >
        <p className="text-xs text-[var(--color-text-muted)]">No performance history available</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <div className="flex items-center gap-3">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            PnL Evolution
          </p>
          <div className="flex items-center gap-2 text-[9px]">
            <span className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-0.5 rounded" style={{ background: "#22c55e" }} />
              Realized
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-0.5 rounded" style={{ background: "#f59e0b", borderTop: "1px dashed #f59e0b" }} />
              Unrealized
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-0.5 rounded" style={{ background: "#3b82f690" }} />
              Volume
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-0.5 rounded" style={{ background: "#a855f7" }} />
              Position
            </span>
          </div>
        </div>
        <span className="text-[10px] text-[var(--color-text-muted)]">
          {snapshots.length} snapshots
        </span>
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

function toSeconds(ts: string | number): number {
  if (typeof ts === "number") return ts > 1e12 ? Math.floor(ts / 1000) : ts;
  const parsed = Date.parse(ts);
  if (!isNaN(parsed)) return Math.floor(parsed / 1000);
  return 0;
}
