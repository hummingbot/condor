import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { api, type CandleData } from "@/lib/api";

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
}: GridChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartModuleRef = useRef<typeof import("lightweight-charts") | null>(null);
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);
  const seriesRef = useRef<import("lightweight-charts").ISeriesApi<"Candlestick"> | null>(null);
  const initializedRef = useRef(false);
  const crosshairPriceRef = useRef<number | null>(null);

  // Price line refs
  const startLineRef = useRef<import("lightweight-charts").IPriceLine | null>(null);
  const endLineRef = useRef<import("lightweight-charts").IPriceLine | null>(null);
  const limitLineRef = useRef<import("lightweight-charts").IPriceLine | null>(null);
  const gridLinesRef = useRef<import("lightweight-charts").IPriceLine[]>([]);

  const channel = `candles:${server}:${connector}:${pair}:${interval}`;
  const channels = useMemo(() => [channel], [channel]);
  const { wsRef, wsVersion } = useCondorWebSocket(channels, server);

  const startTime = useMemo(
    () => Math.floor(Date.now() / 1000) - lookbackSeconds,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [lookbackSeconds, pair, interval],
  );

  const { data: candles } = useQuery({
    queryKey: ["candles", server, connector, pair, interval],
    queryFn: () => api.getCandles(server, connector, pair, interval, 5000, startTime),
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
        crosshair: {
          mode: mod.CrosshairMode.Normal,
        },
        timeScale: { timeVisible: true, secondsVisible: false },
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

      // Track crosshair price
      chart.subscribeCrosshairMove((param) => {
        if (!param.point || !param.seriesData) {
          crosshairPriceRef.current = null;
          return;
        }
        const data = param.seriesData.get(series);
        if (data && "close" in data) {
          crosshairPriceRef.current = (data as { close: number }).close;
        } else if (param.point.y !== undefined) {
          // Use coordinate-to-price conversion
          const price = series.coordinateToPrice(param.point.y);
          if (price !== null) {
            crosshairPriceRef.current = price as number;
          }
        }
      });
    });
    return () => {
      cancelled = true;
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        seriesRef.current = null;
        startLineRef.current = null;
        endLineRef.current = null;
        limitLineRef.current = null;
        gridLinesRef.current = [];
      }
    };
  }, []);

  // Handle WebSocket candle updates
  useEffect(() => {
    const currentWs = wsRef.current;
    if (!currentWs) return;

    const removeHandler = currentWs.onMessage((msgChannel: string, data: unknown) => {
      if (msgChannel !== channel || !seriesRef.current) return;

      const payload = data as {
        type: string;
        candle?: CandleData;
        data?: CandleData[];
      };

      if (payload.type === "candle_update" && payload.candle) {
        const c = payload.candle;
        const ts = c.timestamp > 1e12 ? c.timestamp / 1000 : c.timestamp;
        seriesRef.current.update({
          time: ts as import("lightweight-charts").UTCTimestamp,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        });
      } else if (payload.type === "candles" && payload.data?.length) {
        const c = payload.data[payload.data.length - 1];
        const ts = c.timestamp > 1e12 ? c.timestamp / 1000 : c.timestamp;
        seriesRef.current.update({
          time: ts as import("lightweight-charts").UTCTimestamp,
          open: c.open,
          high: c.high,
          low: c.low,
          close: c.close,
        });
      }
    });

    return removeHandler;
  }, [channel, wsVersion]); // eslint-disable-line react-hooks/exhaustive-deps

  // Set initial candle data
  useEffect(() => {
    if (!seriesRef.current || !candles?.length || !chartModuleRef.current) return;

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
  }, [candles]);

  // Reset on pair/interval change
  useEffect(() => {
    initializedRef.current = false;
  }, [pair, interval]);

  // Update price lines
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

    // Start price line
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

    // End price line
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

    // Limit price line
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

    // Grid level preview lines
    if (startPrice > 0 && endPrice > 0 && minSpread > 0 && startPrice < endPrice) {
      const range = endPrice - startPrice;
      const stepSize = startPrice * minSpread;
      if (stepSize > 0) {
        const numLevels = Math.floor(range / stepSize);
        // Only draw grid lines if there's a reasonable number (2-200)
        // Skip if too many (would clutter) or too few
        if (numLevels >= 2 && numLevels <= 200) {
          const maxDraw = Math.min(numLevels, 50);
          // If more levels than we can draw, sample evenly
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
  }, [startPrice, endPrice, limitPrice, side, minSpread, activePickField]);

  // Handle click-to-set price
  const handleClick = () => {
    if (!activePickField || crosshairPriceRef.current === null) return;
    onPriceSet(activePickField, crosshairPriceRef.current);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <p className="text-[10px] text-[var(--color-text-muted)]">
          {activePickField
            ? `Click on chart to set ${activePickField} price`
            : "Grid executor chart"}
        </p>
        {activePickField && (
          <span className="animate-pulse rounded bg-[var(--color-primary)]/20 px-2 py-0.5 text-xs text-[var(--color-primary)]">
            Pick mode: {activePickField}
          </span>
        )}
      </div>
      <div
        ref={containerRef}
        className="flex-1"
        style={{ cursor: activePickField ? "crosshair" : "default" }}
        onClick={handleClick}
      />
    </div>
  );
}
