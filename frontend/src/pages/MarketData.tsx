import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { ExchangeSelector } from "@/components/market/ExchangeSelector";
import { OrderBook } from "@/components/market/OrderBook";
import { RecentTrades } from "@/components/market/RecentTrades";
import { PairSelector, useTradingRules } from "@/components/market/PairSelector";
import { PriceTicker } from "@/components/market/PriceTicker";
import { TradingRulesInfo } from "@/components/market/TradingRulesInfo";
import { useServer } from "@/hooks/useServer";
import { useTheme } from "@/hooks/useTheme";
import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { api, type CandleData } from "@/lib/api";
import { createCondorDatafeed } from "@/lib/tradingview-datafeed";

// Map our interval strings to TradingView resolution format
const INTERVAL_TO_RESOLUTION: Record<string, string> = {
  "1m": "1",
  "5m": "5",
  "15m": "15",
  "1h": "60",
  "4h": "240",
  "1d": "1D",
};

const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];

// Lookback options: label -> seconds
const LOOKBACK_OPTIONS: { label: string; seconds: number }[] = [
  { label: "1h", seconds: 3600 },
  { label: "6h", seconds: 6 * 3600 },
  { label: "1d", seconds: 86400 },
  { label: "3d", seconds: 3 * 86400 },
  { label: "7d", seconds: 7 * 86400 },
  { label: "14d", seconds: 14 * 86400 },
  { label: "30d", seconds: 30 * 86400 },
];

// ─── TradingView Chart ──────────────────────────────────────────

function getChartColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    bg: style.getPropertyValue("--chart-bg").trim() || "#0f1525",
    grid: style.getPropertyValue("--chart-grid").trim() || "#1c2541",
    text: style.getPropertyValue("--chart-text").trim() || "#6b7994",
    up: style.getPropertyValue("--chart-up").trim() || "#22c55e",
    down: style.getPropertyValue("--chart-down").trim() || "#ef4444",
    accent: style.getPropertyValue("--color-accent").trim() || "#d4a845",
  };
}

function TradingViewChart({
  server,
  connector,
  pair,
  interval,
}: {
  server: string;
  connector: string;
  pair: string;
  interval: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<TradingViewWidget | null>(null);
  const readyRef = useRef(false);
  const { theme } = useTheme();

  const channel = `candles:${server}:${connector}:${pair}:${interval}`;
  const channels = useMemo(() => [channel], [channel]);
  const { wsRef } = useCondorWebSocket(channels, server);

  useEffect(() => {
    if (!containerRef.current || !window.TradingView) return;

    const colors = getChartColors();
    const datafeed = createCondorDatafeed(server, connector, wsRef);
    const resolution = INTERVAL_TO_RESOLUTION[interval] || "1";

    const widget = new window.TradingView.widget({
      container: containerRef.current,
      datafeed,
      symbol: pair,
      interval: resolution,
      library_path: "/charting_library/",
      locale: "en",
      fullscreen: false,
      autosize: true,
      theme: theme === "dark" ? "Dark" : "Light",
      timezone: "Etc/UTC",
      toolbar_bg: colors.bg,
      loading_screen: {
        backgroundColor: colors.bg,
        foregroundColor: colors.accent,
      },
      overrides: {
        "paneProperties.background": colors.bg,
        "paneProperties.backgroundType": "solid",
        "paneProperties.vertGridProperties.color": colors.grid,
        "paneProperties.horzGridProperties.color": colors.grid,
        "scalesProperties.textColor": colors.text,
        "mainSeriesProperties.candleStyle.upColor": colors.up,
        "mainSeriesProperties.candleStyle.downColor": colors.down,
        "mainSeriesProperties.candleStyle.wickUpColor": colors.up,
        "mainSeriesProperties.candleStyle.wickDownColor": colors.down,
        "mainSeriesProperties.candleStyle.borderUpColor": colors.up,
        "mainSeriesProperties.candleStyle.borderDownColor": colors.down,
      },
      disabled_features: ["header_symbol_search", "header_compare"],
      enabled_features: ["study_templates", "drawing_templates"],
      auto_save_delay: 5,
    });

    widgetRef.current = widget;
    readyRef.current = false;

    widget.onChartReady(() => {
      readyRef.current = true;
    });

    return () => {
      readyRef.current = false;
      widgetRef.current = null;
      widget.remove();
    };
  }, [server, connector, theme]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!widgetRef.current || !readyRef.current) return;
    widgetRef.current.activeChart().setSymbol(pair);
  }, [pair]);

  useEffect(() => {
    if (!widgetRef.current || !readyRef.current) return;
    const resolution = INTERVAL_TO_RESOLUTION[interval] || "1";
    widgetRef.current.activeChart().setResolution(resolution);
  }, [interval]);

  return <div ref={containerRef} className="h-full w-full" />;
}

// ─── Fallback Chart (lightweight-charts) ────────────────────────

function FallbackChart({
  server,
  connector,
  pair,
  interval,
  lookbackSeconds,
}: {
  server: string;
  connector: string;
  pair: string;
  interval: string;
  lookbackSeconds: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartModuleRef = useRef<typeof import("lightweight-charts") | null>(
    null,
  );
  const chartRef = useRef<import("lightweight-charts").IChartApi | null>(null);
  const seriesRef =
    useRef<import("lightweight-charts").ISeriesApi<"Candlestick"> | null>(null);
  const initializedRef = useRef(false);

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
    queryFn: () =>
      api.getCandles(server, connector, pair, interval, 5000, startTime),
  });

  const { data: candleStatus } = useQuery<{
    status: string;
    message?: string;
  }>({
    queryKey: ["candles-status", server, connector, pair, interval],
    enabled: false,
  });

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
    });
    return () => {
      cancelled = true;
      if (chartRef.current) {
        chartRef.current.remove();
        chartRef.current = null;
        seriesRef.current = null;
      }
    };
  }, []);

  useEffect(() => {
    const currentWs = wsRef.current;
    if (!currentWs) return;

    const removeHandler = currentWs.onMessage((msgChannel, data) => {
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

  useEffect(() => {
    if (!seriesRef.current || !candles?.length || !chartModuleRef.current)
      return;

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

  useEffect(() => {
    initializedRef.current = false;
  }, [pair, interval]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
        <p className="text-[10px] text-[var(--color-text-muted)]">
          Lightweight chart (install TradingView Charting Library for drawing
          tools)
        </p>
        {candleStatus?.status === "error" && (
          <span className="rounded bg-red-500/20 px-2 py-0.5 text-xs text-red-400">
            {candleStatus.message ?? "Stream error"}
          </span>
        )}
        {candleStatus?.status === "connected" && (
          <span className="rounded bg-green-500/20 px-2 py-0.5 text-xs text-green-400">
            Live
          </span>
        )}
      </div>
      <div ref={containerRef} className="flex-1" />
    </div>
  );
}

// ─── Main Page ──────────────────────────────────────────────────

export function MarketData() {
  const { server } = useServer();
  const [connector, setConnector] = useState("binance");
  const [pair, setPair] = useState("BTC-USDT");
  const [interval, setInterval] = useState("1m");
  const [lookbackSeconds, setLookbackSeconds] = useState(3 * 86400); // 3 days default
  const [tvAvailable, setTvAvailable] = useState(false);
  const [tvChecked, setTvChecked] = useState(false);

  useEffect(() => {
    if (window.TradingView) {
      setTvAvailable(true);
      setTvChecked(true);
      return;
    }
    const timer = setTimeout(() => {
      setTvAvailable(!!window.TradingView);
      setTvChecked(true);
    }, 1000);
    return () => clearTimeout(timer);
  }, []);

  const { data: connectors } = useQuery({
    queryKey: ["connectors", server],
    queryFn: () => api.getConnectors(server!),
    enabled: !!server,
  });

  // Trading rules for the selected connector (shared with PairSelector)
  const rulesData = useTradingRules(server ?? "", connector);
  const selectedRule = useMemo(
    () => rulesData?.rules?.find((r) => r.trading_pair === pair),
    [rulesData, pair],
  );

  useEffect(() => {
    if (connectors?.length && !connectors.includes(connector)) {
      setConnector(connectors[0]);
    }
  }, [connectors, connector]);

  // Reset pair to first available when connector changes
  useEffect(() => {
    if (rulesData?.rules?.length) {
      const pairs = rulesData.rules.map((r) => r.trading_pair);
      if (!pairs.includes(pair)) {
        // Default to BTC-USDT if available, otherwise first pair
        const defaultPair = pairs.find((p) => p === "BTC-USDT") ?? pairs[0];
        setPair(defaultPair);
      }
    }
  }, [rulesData, connector]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!server)
    return (
      <p className="p-6 text-[var(--color-text-muted)]">Select a server</p>
    );

  return (
    <div className="flex h-full flex-col">
      {/* ── Top Bar: Exchange-style header ── */}
      <div className="flex items-center border-b border-[var(--color-border)] bg-[var(--color-surface)]">
        {/* Left: Pair selector + Exchange selector */}
        <div className="flex items-center border-r border-[var(--color-border)]">
          <PairSelector
            server={server}
            connector={connector}
            value={pair}
            onChange={setPair}
          />

          {/* Exchange dropdown styled as a subtle badge */}
          <div className="relative border-l border-[var(--color-border)]">
            <ExchangeSelector
              connectors={connectors ?? []}
              value={connector}
              onChange={setConnector}
            />
          </div>
        </div>

        {/* Center: Price ticker stats */}
        <div className="flex flex-1 items-center px-4 py-2">
          <PriceTicker server={server} connector={connector} pair={pair} />
        </div>

        {/* Right: Interval + Range (lightweight fallback only) */}
        {!tvAvailable && tvChecked && (
          <div className="flex items-center gap-3 border-l border-[var(--color-border)] px-4 py-2">
            <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
              {INTERVALS.map((iv) => (
                <button
                  key={iv}
                  onClick={() => setInterval(iv)}
                  className={`px-2.5 py-1 text-xs ${
                    interval === iv
                      ? "bg-[var(--color-primary)] text-white"
                      : "bg-[var(--color-bg)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  {iv}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-[var(--color-text-muted)]">Range:</span>
              <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
                {LOOKBACK_OPTIONS.map((opt) => (
                  <button
                    key={opt.label}
                    onClick={() => setLookbackSeconds(opt.seconds)}
                    className={`px-2 py-1 text-xs ${
                      lookbackSeconds === opt.seconds
                        ? "bg-[var(--color-primary)] text-white"
                        : "bg-[var(--color-bg)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Main Area: Chart + Order Book ── */}
      <div className="flex min-h-0 flex-1">
        {/* Chart */}
        <div className="min-w-0 flex-1 border-r border-[var(--color-border)]">
          <div className="h-full overflow-hidden rounded-none border-0 bg-[var(--color-surface)]">
            {tvAvailable ? (
              <TradingViewChart
                key={`${server}:${connector}`}
                server={server}
                connector={connector}
                pair={pair}
                interval={interval}
              />
            ) : tvChecked ? (
              <FallbackChart
                key={`${connector}:${pair}:${interval}:${lookbackSeconds}`}
                server={server}
                connector={connector}
                pair={pair}
                interval={interval}
                lookbackSeconds={lookbackSeconds}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
                Loading chart...
              </div>
            )}
          </div>
        </div>

        {/* Order Book + Recent Trades */}
        <div className="flex w-72 shrink-0 flex-col bg-[var(--color-surface)] xl:w-80">
          <div className="flex-[3] overflow-hidden">
            <OrderBook server={server} connector={connector} pair={pair} />
          </div>
          <div className="flex-[2] overflow-hidden border-t border-[var(--color-border)]">
            <RecentTrades server={server} connector={connector} pair={pair} />
          </div>
        </div>
      </div>

      {/* ── Bottom Bar: Trading Rules ── */}
      <TradingRulesInfo rule={selectedRule} />
    </div>
  );
}
