import { useMutation, useQuery } from "@tanstack/react-query";
import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpDown,
  BarChart3,
  CheckCircle,
  Grid3X3,
  Layers,
  Loader2,
  Rocket,
  Settings2,
  TrendingUp,
} from "lucide-react";

import { ExchangeSelector } from "@/components/market/ExchangeSelector";
import { PairSelector, useTradingRules } from "@/components/market/PairSelector";
import { PriceTicker } from "@/components/market/PriceTicker";
import { TradingRulesInfo } from "@/components/market/TradingRulesInfo";
import { MarketDepthPanel } from "@/components/market/MarketDepthPanel";
import { GridChart } from "@/components/grid/GridChart";
import { GridConfigPanel, useGridValidation } from "@/components/grid/GridConfigPanel";
import { PositionConfigPanel, usePositionConfig } from "@/components/executor/PositionConfigPanel";
import { OrderConfigPanel, useOrderConfig } from "@/components/executor/OrderConfigPanel";
import { DCAConfigPanel, useDCAConfig } from "@/components/executor/DCAConfigPanel";
import { TradeBottomPane } from "@/components/trade/TradeBottomPane";
import { useServer } from "@/hooks/useServer";
import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { useMainControllerData } from "@/hooks/useMainControllerData";
import { api } from "@/lib/api";
import type { ExecutorType } from "@/components/executor/types";
import {
  type GridState,
  type GridAction,
  isSpotConnector,
} from "@/pages/CreateGridExecutor";

// ── Grid state management (reuse from CreateGridExecutor) ──

const GRID_DEFAULTS: GridState = {
  connector: "binance_perpetual",
  pair: "BTC-USDT",
  interval: "5m",
  lookbackSeconds: 3 * 86400,
  side: 1,
  start_price: 0,
  end_price: 0,
  limit_price: 0,
  total_amount_quote: 300,
  min_order_amount_quote: 10,
  min_spread_between_orders: 0.0001,
  max_open_orders: 5,
  max_orders_per_batch: 2,
  order_frequency: 1,
  leverage: 10,
  take_profit: 0.0002,
  open_order_type: 2,
  take_profit_order_type: 2,
  activation_bounds: 0.05,
  keep_position: false,
  coerce_tp_to_step: false,
  activePickField: null,
  showAdvanced: false,
};

const GRID_STORAGE_KEY = "condor_grid_defaults";

const GRID_PERSISTED_FIELDS: (keyof GridState)[] = [
  "connector", "pair", "interval", "lookbackSeconds", "side",
  "total_amount_quote", "min_order_amount_quote", "min_spread_between_orders",
  "max_open_orders", "max_orders_per_batch", "order_frequency", "leverage",
  "take_profit", "open_order_type", "take_profit_order_type",
  "activation_bounds", "keep_position", "coerce_tp_to_step",
];

const LAST_MARKET_KEY = "condor_last_market";

function loadGridDefaults(): GridState {
  try {
    const raw = localStorage.getItem(GRID_STORAGE_KEY);
    const merged = raw ? { ...GRID_DEFAULTS } : { ...GRID_DEFAULTS };
    if (raw) {
      const saved = JSON.parse(raw);
      for (const key of GRID_PERSISTED_FIELDS) {
        if (key in saved && saved[key] !== undefined) {
          (merged as Record<string, unknown>)[key] = saved[key];
        }
      }
    }
    // Override connector/pair from last-used market
    try {
      const market = localStorage.getItem(LAST_MARKET_KEY);
      if (market) {
        const { connector, pair } = JSON.parse(market);
        if (connector) merged.connector = connector;
        if (pair) merged.pair = pair;
      }
    } catch { /* ok */ }
    return merged;
  } catch {
    return GRID_DEFAULTS;
  }
}

function saveGridDefaults(state: GridState) {
  const toSave: Record<string, unknown> = {};
  for (const key of GRID_PERSISTED_FIELDS) toSave[key] = state[key];
  localStorage.setItem(GRID_STORAGE_KEY, JSON.stringify(toSave));
}

function gridReducer(state: GridState, action: GridAction): GridState {
  switch (action.type) {
    case "SET_FIELD": {
      const next = { ...state, [action.field]: action.value };
      if (action.field === "leverage" && isSpotConnector(next.connector)) {
        next.leverage = 1;
      }
      return next;
    }
    case "SET_CONNECTOR": {
      const spot = isSpotConnector(action.value);
      return {
        ...state,
        connector: action.value,
        start_price: 0,
        end_price: 0,
        limit_price: 0,
        leverage: spot ? 1 : state.leverage,
      };
    }
    case "SET_PAIR":
      return { ...state, pair: action.value, start_price: 0, end_price: 0, limit_price: 0 };
    default:
      return state;
  }
}

// ── Intervals ──

const INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"];

const LOOKBACK_OPTIONS: { label: string; seconds: number }[] = [
  { label: "1h", seconds: 3600 },
  { label: "6h", seconds: 6 * 3600 },
  { label: "1d", seconds: 86400 },
  { label: "3d", seconds: 3 * 86400 },
  { label: "7d", seconds: 7 * 86400 },
  { label: "14d", seconds: 14 * 86400 },
  { label: "30d", seconds: 30 * 86400 },
];

// ── Type tabs config ──

const TYPE_TABS: { value: ExecutorType; label: string; icon: React.ReactNode }[] = [
  { value: "order", label: "Order", icon: <ArrowUpDown className="h-3.5 w-3.5" /> },
  { value: "position", label: "Position", icon: <TrendingUp className="h-3.5 w-3.5" /> },
  { value: "grid", label: "Grid", icon: <Grid3X3 className="h-3.5 w-3.5" /> },
  { value: "dca", label: "DCA", icon: <Layers className="h-3.5 w-3.5" /> },
];

const TYPE_LABELS: Record<ExecutorType, string> = {
  grid: "Grid Executor",
  position: "Position Executor",
  order: "Order Executor",
  dca: "DCA Executor",
};

// ── Page ──

export function CreateExecutor() {
  const { server } = useServer();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Executor type from URL param or default to grid
  const [executorType, setExecutorType] = useState<ExecutorType>(
    () => (searchParams.get("type") as ExecutorType) || "grid",
  );

  // Update URL when type changes
  const handleTypeChange = (type: ExecutorType) => {
    setExecutorType(type);
    setSearchParams({ type }, { replace: true });
  };

  // ── Grid state (always initialized for hooks rules) ──
  const [gridState, gridDispatch] = React.useReducer(gridReducer, undefined, loadGridDefaults);
  const gridValidation = useGridValidation(gridState);

  // ── Other executor configs ──
  const positionConfig = usePositionConfig();
  const orderConfig = useOrderConfig();
  const dcaConfig = useDCAConfig();

  // ── Shared market state ──
  // Use grid state for connector/pair/interval since it's always present
  // Sync other types' connector/pair changes through grid state
  const connector = gridState.connector;
  const pair = gridState.pair;
  const isSpot = isSpotConnector(connector);

  const [successId, setSuccessId] = useState<string | null>(null);
  const [rightPanel, setRightPanel] = useState<"config" | "depth">("config");

  const { data: connectors = [] } = useQuery({
    queryKey: ["connected-exchanges", server],
    queryFn: () => api.getConnectedExchanges(server!),
    enabled: !!server,
  });

  // WS for executor data (candle streams are managed by candleStore)
  const wsChannels = useMemo(
    () => server ? [`executors:${server}`] : [],
    [server],
  );
  useCondorWebSocket(wsChannels, server);

  // Main controller data (executors + positions filtered by connector/pair)
  const { executors: mainExecutors, overlays: mainOverlays, positions: mainPositions, isLoadingPositions } =
    useMainControllerData(server, connector, pair);

  const rulesData = useTradingRules(server ?? "", connector);

  // Persist last-used connector/pair to localStorage
  useEffect(() => {
    try {
      localStorage.setItem(LAST_MARKET_KEY, JSON.stringify({ connector, pair }));
    } catch { /* ok */ }
  }, [connector, pair]);

  // Sync connector to filtered list
  useEffect(() => {
    if (connectors.length && !connectors.includes(connector)) {
      gridDispatch({ type: "SET_CONNECTOR", value: connectors[0] });
    }
  }, [connectors, connector]);

  // Reset pair when connector changes
  useEffect(() => {
    if (rulesData?.rules?.length) {
      const pairs = rulesData.rules.map((r) => r.trading_pair);
      if (!pairs.includes(pair)) {
        const defaultPair = pairs.find((p) => p === "BTC-USDT") ?? pairs[0];
        gridDispatch({ type: "SET_PAIR", value: defaultPair });
      }
    }
  }, [rulesData, connector]); // eslint-disable-line react-hooks/exhaustive-deps

  // Propagate connector/pair changes to other config types
  useEffect(() => {
    positionConfig.dispatch({ type: "SET_CONNECTOR", value: connector });
    orderConfig.dispatch({ type: "SET_CONNECTOR", value: connector });
    dcaConfig.dispatch({ type: "SET_CONNECTOR", value: connector });
  }, [connector]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    positionConfig.dispatch({ type: "SET_PAIR", value: pair });
    orderConfig.dispatch({ type: "SET_PAIR", value: pair });
    dcaConfig.dispatch({ type: "SET_PAIR", value: pair });
  }, [pair]); // eslint-disable-line react-hooks/exhaustive-deps

  // Current price
  const { data: priceData } = useQuery({
    queryKey: ["price", server, connector, pair],
    queryFn: () => api.getPrice(server!, connector, pair),
    enabled: !!server && !!connector && !!pair,
    refetchInterval: 5000,
  });

  const currentPrice = priceData?.mid_price ?? null;

  // Price precision
  const pricePrecision = useMemo(() => {
    if (!rulesData?.rules) return undefined;
    const rule = rulesData.rules.find((r) => r.trading_pair === pair);
    if (!rule || !rule.min_price_increment) return undefined;
    const inc = rule.min_price_increment;
    if (inc >= 1) return 0;
    return Math.max(0, Math.ceil(-Math.log10(inc)));
  }, [rulesData, pair]);

  const selectedRule = useMemo(
    () => rulesData?.rules?.find((r) => r.trading_pair === pair),
    [rulesData, pair],
  );

  // ── Active config derived values ──
  const activeValidation = useMemo(() => {
    switch (executorType) {
      case "grid": return gridValidation;
      case "position": return positionConfig.validation;
      case "order": return orderConfig.validation;
      case "dca": return dcaConfig.validation;
    }
  }, [executorType, gridValidation, positionConfig.validation, orderConfig.validation, dcaConfig.validation]);

  // Chart props depend on active type
  const chartProps = useMemo(() => {
    switch (executorType) {
      case "grid":
        return {
          startPrice: gridState.start_price,
          endPrice: gridState.end_price,
          limitPrice: gridState.limit_price,
          side: gridState.side,
          minSpread: gridState.min_spread_between_orders,
          activePickField: gridState.activePickField,
        };
      case "position": return positionConfig.chartProps;
      case "order": return orderConfig.chartProps;
      case "dca": return dcaConfig.chartProps;
    }
  }, [executorType, gridState, positionConfig.chartProps, orderConfig.chartProps, dcaConfig.chartProps]);

  // Chart price set handler
  const handlePriceSet = useMemo(
    () => (field: "start" | "end" | "limit", price: number) => {
      switch (executorType) {
        case "grid":
          gridDispatch({ type: "SET_FIELD", field: `${field}_price`, value: price });
          gridDispatch({ type: "SET_FIELD", field: "activePickField", value: null });
          break;
        case "position":
          positionConfig.handleChartPriceSet(field, price);
          break;
        case "order":
          orderConfig.handleChartPriceSet(field, price);
          break;
        case "dca":
          dcaConfig.handleChartPriceSet(field, price);
          break;
      }
    },
    [executorType], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Create mutation
  const createMutation = useMutation({
    mutationFn: () => {
      if (!server) throw new Error("No server");

      let payload: { executor_type: string; config: Record<string, unknown> };

      switch (executorType) {
        case "grid":
          payload = {
            executor_type: "grid_executor",
            config: {
              connector_name: connector,
              trading_pair: pair,
              side: gridState.side,
              start_price: gridState.start_price,
              end_price: gridState.end_price,
              limit_price: gridState.limit_price,
              total_amount_quote: gridState.total_amount_quote,
              min_order_amount_quote: gridState.min_order_amount_quote,
              min_spread_between_orders: gridState.min_spread_between_orders,
              max_open_orders: gridState.max_open_orders,
              max_orders_per_batch: gridState.max_orders_per_batch,
              order_frequency: gridState.order_frequency,
              leverage: isSpot ? 1 : gridState.leverage,
              activation_bounds: gridState.activation_bounds,
              keep_position: gridState.keep_position,
              coerce_tp_to_step: gridState.coerce_tp_to_step,
              triple_barrier_config: {
                take_profit: gridState.take_profit,
                open_order_type: gridState.open_order_type,
                take_profit_order_type: gridState.take_profit_order_type,
              },
            },
          };
          break;
        case "position":
          payload = positionConfig.buildPayload(connector, pair, isSpot);
          break;
        case "order":
          payload = orderConfig.buildPayload(connector, pair, isSpot);
          break;
        case "dca":
          payload = dcaConfig.buildPayload(connector, pair, isSpot);
          break;
      }

      return api.createExecutor(server, payload);
    },
    onSuccess: (data) => {
      // Save defaults for the active type
      switch (executorType) {
        case "grid": saveGridDefaults(gridState); break;
        case "position": positionConfig.save(); break;
        case "order": orderConfig.save(); break;
        case "dca": dcaConfig.save(); break;
      }
      setSuccessId(data.executor_id);
      // Auto-dismiss success toast after 2.5s — stay on this page
      // so the new executor appears in the bottom pane via WS
      setTimeout(() => setSuccessId(null), 2500);
    },
  });

  if (!server) {
    return <p className="p-6 text-[var(--color-text-muted)]">Select a server</p>;
  }

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] flex-col">
      {/* Top Bar */}
      <div className="flex items-center border-b border-[var(--color-border)] bg-[var(--color-surface)]">
        {/* Back button */}
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-1 border-r border-[var(--color-border)] px-3 py-2.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
        </button>

        {/* Pair + Exchange */}
        <div className="flex items-center border-r border-[var(--color-border)]">
          <PairSelector
            server={server}
            connector={connector}
            value={pair}
            onChange={(v) => gridDispatch({ type: "SET_PAIR", value: v })}
          />
          <div className="relative border-l border-[var(--color-border)]">
            <ExchangeSelector
              connectors={connectors}
              value={connector}
              onChange={(v) => gridDispatch({ type: "SET_CONNECTOR", value: v })}
            />
          </div>
        </div>

        {/* Price ticker */}
        <div className="flex flex-1 items-center px-4 py-2">
          <PriceTicker server={server} connector={connector} pair={pair} />
        </div>

        {/* Interval + Range */}
        <div className="flex items-center gap-3 border-l border-[var(--color-border)] px-4 py-2">
          <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
            {INTERVALS.map((iv) => (
              <button
                key={iv}
                onClick={() => gridDispatch({ type: "SET_FIELD", field: "interval", value: iv })}
                className={`px-2.5 py-1 text-xs ${
                  gridState.interval === iv
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
                  onClick={() => gridDispatch({ type: "SET_FIELD", field: "lookbackSeconds", value: opt.seconds })}
                  className={`px-2 py-1 text-xs ${
                    gridState.lookbackSeconds === opt.seconds
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
      </div>

      {/* Main Area: Chart + Right Panel */}
      <div className="flex min-h-0 flex-1">
        {/* Chart + Bottom Pane */}
        <div className="min-w-0 flex-1 flex flex-col border-r border-[var(--color-border)]">
          <div className="flex-1 min-h-0 overflow-hidden bg-[var(--color-surface)]">
            <GridChart
              key={`${connector}:${pair}:${gridState.interval}`}
              server={server}
              connector={connector}
              pair={pair}
              interval={gridState.interval}
              lookbackSeconds={gridState.lookbackSeconds}

              startPrice={chartProps.startPrice}
              endPrice={chartProps.endPrice}
              limitPrice={chartProps.limitPrice}
              side={chartProps.side}
              minSpread={chartProps.minSpread}
              activePickField={chartProps.activePickField}
              onPriceSet={handlePriceSet}
              pricePrecision={pricePrecision}
              extraLines={chartProps.extraLines}
              executorOverlays={mainOverlays}
              positions={mainPositions}
            />
          </div>
          <TradingRulesInfo rule={selectedRule} />
          <TradeBottomPane
            executors={mainExecutors}
            positions={mainPositions}
            isLoadingPositions={isLoadingPositions}
          />
        </div>

        {/* Right Panel */}
        <div className="flex w-72 shrink-0 flex-col bg-[var(--color-surface)] xl:w-80">
          {/* Panel Mode Toggle */}
          <div className="flex border-b border-[var(--color-border)]">
            <button
              onClick={() => setRightPanel("config")}
              className={`flex flex-1 items-center justify-center gap-1.5 px-2 py-2 text-[11px] font-medium transition-colors ${
                rightPanel === "config"
                  ? "border-b-2 border-[var(--color-primary)] text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              <Settings2 className="h-3.5 w-3.5" />
              Execute
            </button>
            <button
              onClick={() => setRightPanel("depth")}
              className={`flex flex-1 items-center justify-center gap-1.5 px-2 py-2 text-[11px] font-medium transition-colors ${
                rightPanel === "depth"
                  ? "border-b-2 border-[var(--color-primary)] text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
            >
              <BarChart3 className="h-3.5 w-3.5" />
              Data
            </button>
          </div>

          {rightPanel === "config" ? (
            <>
              {/* Type Tabs */}
              <div className="border-b border-[var(--color-border)]">
                <div className="flex">
                  {TYPE_TABS.map((tab) => (
                    <button
                      key={tab.value}
                      onClick={() => handleTypeChange(tab.value)}
                      className={`flex flex-1 items-center justify-center gap-1.5 px-2 py-2.5 text-[11px] font-medium transition-colors ${
                        executorType === tab.value
                          ? "border-b-2 border-[var(--color-primary)] text-[var(--color-primary)]"
                          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                      }`}
                    >
                      {tab.icon}
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Config Panel */}
              <div className="flex-1 overflow-y-auto">
                {executorType === "grid" && (
                  <GridConfigPanel state={gridState} dispatch={gridDispatch} currentPrice={currentPrice} isSpot={isSpot} />
                )}
                {executorType === "position" && (
                  <PositionConfigPanel state={positionConfig.state} dispatch={positionConfig.dispatch} currentPrice={currentPrice} isSpot={isSpot} pair={pair} />
                )}
                {executorType === "order" && (
                  <OrderConfigPanel state={orderConfig.state} dispatch={orderConfig.dispatch} currentPrice={currentPrice} isSpot={isSpot} pair={pair} />
                )}
                {executorType === "dca" && (
                  <DCAConfigPanel state={dcaConfig.state} dispatch={dcaConfig.dispatch} currentPrice={currentPrice} isSpot={isSpot} pair={pair} />
                )}
              </div>

              {/* Sticky Create Footer */}
              <div className="border-t border-[var(--color-border)] p-3">
                {!activeValidation.valid && (
                  <p className="mb-2 text-[11px] text-[var(--color-red)]">
                    {activeValidation.errors[0]}
                  </p>
                )}
                <button
                  onClick={() => createMutation.mutate()}
                  disabled={!activeValidation.valid || createMutation.isPending}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2.5 text-sm font-bold text-white transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {createMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Rocket className="h-4 w-4" />
                  )}
                  Create {TYPE_LABELS[executorType]}
                </button>
              </div>
            </>
          ) : (
            <MarketDepthPanel server={server} connector={connector} pair={pair} />
          )}
        </div>
      </div>

      {/* Success toast */}
      {successId && (
        <div className="absolute bottom-16 left-1/2 z-50 -translate-x-1/2">
          <div className="flex items-center gap-2 rounded-lg border border-[var(--color-green)]/30 bg-[var(--color-surface)] px-4 py-2.5 shadow-2xl shadow-black/40">
            <CheckCircle className="h-4 w-4 text-[var(--color-green)]" />
            <span className="text-xs font-medium text-[var(--color-text)]">{TYPE_LABELS[executorType]} Created</span>
            <span className="font-mono text-[10px] text-[var(--color-text-muted)]">{successId.slice(0, 8)}</span>
          </div>
        </div>
      )}

      {/* Error toast */}
      {createMutation.isError && (
        <div className="absolute bottom-16 right-4 rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/10 px-4 py-2 text-sm text-[var(--color-red)]">
          {(createMutation.error as Error).message}
        </div>
      )}
    </div>
  );
}
