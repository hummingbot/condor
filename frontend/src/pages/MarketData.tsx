import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { OrderBook } from "@/components/market/OrderBook";
import { PairSelector, useTradingRules } from "@/components/market/PairSelector";
import { PriceTicker } from "@/components/market/PriceTicker";
import { TradingRulesInfo } from "@/components/market/TradingRulesInfo";
import { useServer } from "@/hooks/useServer";
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

// ─── TradingView Chart ──────────────────────────────────────────

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

  const channel = `candles:${server}:${connector}:${pair}:${interval}`;
  const channels = useMemo(() => [channel], [channel]);
  const { wsRef } = useCondorWebSocket(channels, server);

  useEffect(() => {
    if (!containerRef.current || !window.TradingView) return;

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
      theme: "Dark",
      timezone: "Etc/UTC",
      toolbar_bg: "#1a1d27",
      loading_screen: {
        backgroundColor: "#1a1d27",
        foregroundColor: "#6366f1",
      },
      overrides: {
        "paneProperties.background": "#1a1d27",
        "paneProperties.backgroundType": "solid",
        "paneProperties.vertGridProperties.color": "#2a2d37",
        "paneProperties.horzGridProperties.color": "#2a2d37",
        "scalesProperties.textColor": "#71717a",
        "mainSeriesProperties.candleStyle.upColor": "#22c55e",
        "mainSeriesProperties.candleStyle.downColor": "#ef4444",
        "mainSeriesProperties.candleStyle.wickUpColor": "#22c55e",
        "mainSeriesProperties.candleStyle.wickDownColor": "#ef4444",
        "mainSeriesProperties.candleStyle.borderUpColor": "#22c55e",
        "mainSeriesProperties.candleStyle.borderDownColor": "#ef4444",
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
  }, [server, connector]); // eslint-disable-line react-hooks/exhaustive-deps

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
}: {
  server: string;
  connector: string;
  pair: string;
  interval: string;
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

  const { data: candles } = useQuery({
    queryKey: ["candles", server, connector, pair, interval],
    queryFn: () => api.getCandles(server, connector, pair, interval),
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

      const chart = mod.createChart(containerRef.current, {
        layout: {
          background: { type: mod.ColorType.Solid, color: "#1a1d27" },
          textColor: "#71717a",
        },
        grid: {
          vertLines: { color: "#2a2d37" },
          horzLines: { color: "#2a2d37" },
        },
        timeScale: { timeVisible: true, secondsVisible: false },
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
      chartRef.current = chart;

      const series = chart.addSeries(mod.CandlestickSeries, {
        upColor: "#22c55e",
        downColor: "#ef4444",
        wickUpColor: "#22c55e",
        wickDownColor: "#ef4444",
        borderVisible: false,
      });
      seriesRef.current = series;

      const ro = new ResizeObserver((entries) => {
        for (const e of entries)
          chart.applyOptions({
            width: e.contentRect.width,
            height: e.contentRect.height,
          });
      });
      ro.observe(containerRef.current);

      return () => {
        ro.disconnect();
        chart.remove();
      };
    });
    return () => {
      cancelled = true;
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
      {/* ── Top Bar: Connector + Pair Selector + Price Ticker ── */}
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
        <select
          value={connector}
          onChange={(e) => setConnector(e.target.value)}
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm capitalize focus:border-[var(--color-primary)] focus:outline-none"
        >
          {(connectors ?? []).map((c: string) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>

        <PairSelector
          server={server}
          connector={connector}
          value={pair}
          onChange={setPair}
        />

        {/* Interval buttons — only for lightweight-charts fallback */}
        {!tvAvailable && tvChecked && (
          <div className="flex rounded-md border border-[var(--color-border)]">
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
        )}

        <div className="ml-auto">
          <PriceTicker server={server} connector={connector} pair={pair} />
        </div>
      </div>

      {/* ── Main Area: Chart + Order Book ── */}
      <div className="flex min-h-0 flex-1">
        {/* Chart */}
        <div className="min-w-0 flex-1 border-r border-[var(--color-border)]">
          <div className="h-full overflow-hidden rounded-none border-0 bg-[var(--color-surface)]">
            {tvAvailable ? (
              <TradingViewChart
                key={`${connector}:${pair}`}
                server={server}
                connector={connector}
                pair={pair}
                interval={interval}
              />
            ) : tvChecked ? (
              <FallbackChart
                key={`${connector}:${pair}:${interval}`}
                server={server}
                connector={connector}
                pair={pair}
                interval={interval}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
                Loading chart...
              </div>
            )}
          </div>
        </div>

        {/* Order Book */}
        <div className="w-72 shrink-0 bg-[var(--color-surface)] xl:w-80">
          <OrderBook server={server} connector={connector} pair={pair} />
        </div>
      </div>

      {/* ── Bottom Bar: Trading Rules ── */}
      <TradingRulesInfo rule={selectedRule} />
    </div>
  );
}
