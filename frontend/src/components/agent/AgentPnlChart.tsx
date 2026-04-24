import { useEffect, useRef } from "react";

import type { MetricEntry } from "@/lib/parse-agent";

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

interface PnlDataPoint {
  time: number; // unix seconds
  value: number; // pnl
}

interface AgentPnlChartProps {
  data: PnlDataPoint[];
  height?: number;
  title?: string;
}

export function AgentPnlChart({ data, height = 180, title }: AgentPnlChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);

  useEffect(() => {
    let cancelled = false;
    import("lightweight-charts").then((mod) => {
      if (cancelled || !containerRef.current) return;

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
          priceFormatter: (price: number) => `$${price >= 0 ? "+" : ""}${price.toFixed(2)}`,
        },
      });
      chartRef.current = chart;

      const series = chart.addSeries(mod.BaselineSeries, {
        baseValue: { type: "price", price: 0 },
        topLineColor: colors.up,
        topFillColor1: `${colors.up}33`,
        topFillColor2: `${colors.up}05`,
        bottomLineColor: colors.down,
        bottomFillColor1: `${colors.down}05`,
        bottomFillColor2: `${colors.down}33`,
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
      });

      // Set data
      if (data.length > 0) {
        const sorted = [...data].sort((a, b) => a.time - b.time);
        series.setData(
          sorted.map((d) => ({
            time: d.time as import("lightweight-charts").UTCTimestamp,
            value: d.value,
          })),
        );
        chart.timeScale().fitContent();
      }

      // Crosshair tooltip
      chart.subscribeCrosshairMove((param) => {
        const tooltip = tooltipRef.current;
        if (!tooltip || !containerRef.current) return;

        if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
          tooltip.style.display = "none";
          return;
        }

        const seriesData = param.seriesData.get(series);
        if (!seriesData || !("value" in seriesData)) {
          tooltip.style.display = "none";
          return;
        }

        const pnl = (seriesData as { value: number }).value;
        const pnlColor = pnl >= 0 ? colors.up : colors.down;
        const sign = pnl >= 0 ? "+" : "";
        const ts = typeof param.time === "number" ? param.time : 0;
        const date = new Date(ts * 1000);
        const timeStr = date.toLocaleString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });

        tooltip.innerHTML = `
          <div style="color:#9ca3af;font-size:10px;margin-bottom:2px">${timeStr}</div>
          <div style="color:${pnlColor};font-weight:600;font-size:13px">$${sign}${pnl.toFixed(2)}</div>
        `;
        tooltip.style.display = "block";

        const containerRect = containerRef.current.getBoundingClientRect();
        let left = param.point.x + 16;
        if (left + 160 > containerRect.width) left = param.point.x - 170;
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${Math.max(4, param.point.y - 20)}px`;
      });

    });

    return () => {
      cancelled = true;
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps


  if (data.length === 0) return null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] overflow-hidden">
      {title && (
        <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">{title}</p>
        </div>
      )}
      <div style={{ position: "relative" }}>
        <div ref={containerRef} style={{ height, width: "100%" }} />
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
            maxWidth: 180,
            whiteSpace: "nowrap",
            lineHeight: 1.4,
            backdropFilter: "blur(8px)",
          }}
        />
      </div>
    </div>
  );
}

// Helper to convert MetricEntry[] to PnlDataPoint[]
export function metricsToDataPoints(metrics: MetricEntry[]): PnlDataPoint[] {
  return metrics
    .filter((m) => m.timestamp)
    .map((m) => ({
      time: Math.floor(new Date(m.timestamp).getTime() / 1000),
      value: m.pnl,
    }))
    .sort((a, b) => a.time - b.time);
}

// Helper to convert session-level performance to PnlDataPoints (aggregate)
export function sessionsToDataPoints(
  sessions: { session_num: number; total_pnl: number; status: string }[],
): PnlDataPoint[] {
  if (sessions.length === 0) return [];
  // Use session_num as a proxy for time ordering — each session gets a synthetic timestamp
  // spaced 1 hour apart from a base time
  const base = Math.floor(Date.now() / 1000) - sessions.length * 3600;
  let cumPnl = 0;
  return sessions
    .slice()
    .sort((a, b) => a.session_num - b.session_num)
    .map((s, i) => {
      cumPnl += s.total_pnl;
      return {
        time: base + i * 3600,
        value: cumPnl,
      };
    });
}
