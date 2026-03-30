import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useReducer, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, CheckCircle, Loader2, Rocket } from "lucide-react";

import { ExchangeSelector } from "@/components/market/ExchangeSelector";
import { PairSelector, useTradingRules } from "@/components/market/PairSelector";
import { PriceTicker } from "@/components/market/PriceTicker";
import { GridChart } from "@/components/grid/GridChart";
import { GridConfigPanel, useGridValidation } from "@/components/grid/GridConfigPanel";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";

// ── State ──

export interface GridState {
  connector: string;
  pair: string;
  interval: string;
  lookbackSeconds: number;
  side: 1 | 2;
  start_price: number;
  end_price: number;
  limit_price: number;
  total_amount_quote: number;
  min_order_amount_quote: number;
  min_spread_between_orders: number;
  max_open_orders: number;
  max_orders_per_batch: number;
  order_frequency: number;
  leverage: number;
  take_profit: number;
  open_order_type: number;
  take_profit_order_type: number;
  activation_bounds: number;
  keep_position: boolean;
  coerce_tp_to_step: boolean;
  activePickField: "start" | "end" | "limit" | null;
  showAdvanced: boolean;
}

export type GridAction =
  | { type: "SET_FIELD"; field: string; value: unknown }
  | { type: "SET_CONNECTOR"; value: string }
  | { type: "SET_PAIR"; value: string };

const DEFAULTS: GridState = {
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

const STORAGE_KEY = "condor_grid_defaults";

/** Fields persisted across sessions (no prices — those are per-trade). */
const PERSISTED_FIELDS: (keyof GridState)[] = [
  "connector", "pair", "interval", "lookbackSeconds", "side",
  "total_amount_quote", "min_order_amount_quote", "min_spread_between_orders",
  "max_open_orders", "max_orders_per_batch", "order_frequency", "leverage",
  "take_profit", "open_order_type", "take_profit_order_type",
  "activation_bounds", "keep_position", "coerce_tp_to_step",
];

function loadSavedDefaults(): GridState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULTS;
    const saved = JSON.parse(raw);
    const merged = { ...DEFAULTS };
    for (const key of PERSISTED_FIELDS) {
      if (key in saved && saved[key] !== undefined) {
        (merged as Record<string, unknown>)[key] = saved[key];
      }
    }
    return merged;
  } catch {
    return DEFAULTS;
  }
}

function saveDefaults(state: GridState) {
  const toSave: Record<string, unknown> = {};
  for (const key of PERSISTED_FIELDS) {
    toSave[key] = state[key];
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}

export function isSpotConnector(connector: string): boolean {
  return !connector.includes("perpetual");
}

function gridReducer(state: GridState, action: GridAction): GridState {
  switch (action.type) {
    case "SET_FIELD": {
      const next = { ...state, [action.field]: action.value };
      // Force leverage=1 for spot connectors
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

// ── Page ──

export function CreateGridExecutor() {
  const { server } = useServer();
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(gridReducer, undefined, loadSavedDefaults);
  const validation = useGridValidation(state);
  const isSpot = isSpotConnector(state.connector);

  const [successId, setSuccessId] = useState<string | null>(null);
  const [onlyConnected, setOnlyConnected] = useState(true);

  const { data: allConnectors } = useQuery({
    queryKey: ["connectors", server],
    queryFn: () => api.getConnectors(server!),
    enabled: !!server,
  });

  const { data: connectedExchanges } = useQuery({
    queryKey: ["connected-exchanges", server],
    queryFn: () => api.getConnectedExchanges(server!),
    enabled: !!server,
  });

  const connectors = useMemo(() => {
    if (!allConnectors) return [];
    if (!onlyConnected || !connectedExchanges?.length) return allConnectors;
    // Match connected base names to all connectors (e.g. "binance" matches "binance_perpetual")
    const connectedBases = new Set(connectedExchanges.map((c) => c.replace(/_perpetual$/, "")));
    return allConnectors.filter((c) => {
      const base = c.replace(/_perpetual$/, "");
      return connectedBases.has(base);
    });
  }, [allConnectors, connectedExchanges, onlyConnected]);

  const rulesData = useTradingRules(server ?? "", state.connector);

  // Sync connector to filtered list
  useEffect(() => {
    if (connectors.length && !connectors.includes(state.connector)) {
      dispatch({ type: "SET_CONNECTOR", value: connectors[0] });
    }
  }, [connectors, state.connector]);

  // Reset pair when connector changes
  useEffect(() => {
    if (rulesData?.rules?.length) {
      const pairs = rulesData.rules.map((r) => r.trading_pair);
      if (!pairs.includes(state.pair)) {
        const defaultPair = pairs.find((p) => p === "BTC-USDT") ?? pairs[0];
        dispatch({ type: "SET_PAIR", value: defaultPair });
      }
    }
  }, [rulesData, state.connector]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch current price for auto-fill
  const { data: priceData } = useQuery({
    queryKey: ["price", server, state.connector, state.pair],
    queryFn: () => api.getPrice(server!, state.connector, state.pair),
    enabled: !!server && !!state.connector && !!state.pair,
    refetchInterval: 5000,
  });

  const currentPrice = priceData?.mid_price ?? null;

  // Create executor mutation
  const createMutation = useMutation({
    mutationFn: () => {
      if (!server) throw new Error("No server");
      return api.createExecutor(server, {
        executor_type: "grid_executor",
        config: {
          connector_name: state.connector,
          trading_pair: state.pair,
          side: state.side,
          start_price: state.start_price,
          end_price: state.end_price,
          limit_price: state.limit_price,
          total_amount_quote: state.total_amount_quote,
          min_order_amount_quote: state.min_order_amount_quote,
          min_spread_between_orders: state.min_spread_between_orders,
          max_open_orders: state.max_open_orders,
          max_orders_per_batch: state.max_orders_per_batch,
          order_frequency: state.order_frequency,
          leverage: isSpot ? 1 : state.leverage,
          activation_bounds: state.activation_bounds,
          keep_position: state.keep_position,
          coerce_tp_to_step: state.coerce_tp_to_step,
          triple_barrier_config: {
            take_profit: state.take_profit,
            open_order_type: state.open_order_type,
            take_profit_order_type: state.take_profit_order_type,
          },
        },
      });
    },
    onSuccess: (data) => {
      saveDefaults(state);
      setSuccessId(data.executor_id);
      setTimeout(() => navigate("/executors"), 2500);
    },
  });

  const handlePriceSet = useMemo(
    () => (field: "start" | "end" | "limit", price: number) => {
      dispatch({ type: "SET_FIELD", field: `${field}_price`, value: price });
      dispatch({ type: "SET_FIELD", field: "activePickField", value: null });
    },
    [],
  );

  if (!server) {
    return <p className="p-6 text-[var(--color-text-muted)]">Select a server</p>;
  }

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] flex-col">
      {/* Top Bar */}
      <div className="flex items-center border-b border-[var(--color-border)] bg-[var(--color-surface)]">
        {/* Back button */}
        <button
          onClick={() => navigate("/executors")}
          className="flex items-center gap-1 border-r border-[var(--color-border)] px-3 py-2.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
        </button>

        {/* Pair + Exchange */}
        <div className="flex items-center border-r border-[var(--color-border)]">
          <PairSelector
            server={server}
            connector={state.connector}
            value={state.pair}
            onChange={(v) => dispatch({ type: "SET_PAIR", value: v })}
          />
          <div className="relative border-l border-[var(--color-border)]">
            <ExchangeSelector
              connectors={connectors}
              value={state.connector}
              onChange={(v) => dispatch({ type: "SET_CONNECTOR", value: v })}
            />
          </div>
          {connectedExchanges && connectedExchanges.length > 0 && (
            <label className="flex cursor-pointer items-center gap-1.5 border-l border-[var(--color-border)] px-3 py-2.5 text-[10px] text-[var(--color-text-muted)] select-none hover:bg-[var(--color-surface-hover)]">
              <input
                type="checkbox"
                checked={onlyConnected}
                onChange={(e) => setOnlyConnected(e.target.checked)}
                className="h-3 w-3 rounded border-[var(--color-border)] accent-[var(--color-primary)]"
              />
              Connected only
            </label>
          )}
        </div>

        {/* Price ticker */}
        <div className="flex flex-1 items-center px-4 py-2">
          <PriceTicker server={server} connector={state.connector} pair={state.pair} />
        </div>

        {/* Interval + Range */}
        <div className="flex items-center gap-3 border-l border-[var(--color-border)] px-4 py-2">
          <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
            {INTERVALS.map((iv) => (
              <button
                key={iv}
                onClick={() => dispatch({ type: "SET_FIELD", field: "interval", value: iv })}
                className={`px-2.5 py-1 text-xs ${
                  state.interval === iv
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
                  onClick={() => dispatch({ type: "SET_FIELD", field: "lookbackSeconds", value: opt.seconds })}
                  className={`px-2 py-1 text-xs ${
                    state.lookbackSeconds === opt.seconds
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
        {/* Chart */}
        <div className="min-w-0 flex-1 border-r border-[var(--color-border)]">
          <div className="h-full overflow-hidden bg-[var(--color-surface)]">
            <GridChart
              key={`${state.connector}:${state.pair}:${state.interval}:${state.lookbackSeconds}`}
              server={server}
              connector={state.connector}
              pair={state.pair}
              interval={state.interval}
              lookbackSeconds={state.lookbackSeconds}
              startPrice={state.start_price}
              endPrice={state.end_price}
              limitPrice={state.limit_price}
              side={state.side}
              minSpread={state.min_spread_between_orders}
              activePickField={state.activePickField}
              onPriceSet={handlePriceSet}
            />
          </div>
        </div>

        {/* Right Panel */}
        <div className="flex w-72 shrink-0 flex-col bg-[var(--color-surface)] xl:w-80">
          <div className="border-b border-[var(--color-border)] px-3 py-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
              Grid Config
            </h3>
          </div>
          <div className="flex-1 overflow-y-auto">
            <GridConfigPanel state={state} dispatch={dispatch} currentPrice={currentPrice} isSpot={isSpot} />
          </div>
        </div>
      </div>

      {/* Bottom Bar: Launch */}
      <div className="flex items-center justify-between border-t border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
        <div className="flex items-center gap-3 text-xs">
          {validation.valid ? (
            <span className="text-[var(--color-green)]">Ready to launch</span>
          ) : (
            <span className="text-[var(--color-red)]">
              {validation.errors[0]}
            </span>
          )}
          {state.side === 1 ? (
            <span className="rounded bg-[var(--color-green)]/20 px-1.5 py-0.5 text-[10px] font-bold text-[var(--color-green)]">LONG</span>
          ) : (
            <span className="rounded bg-[var(--color-red)]/20 px-1.5 py-0.5 text-[10px] font-bold text-[var(--color-red)]">SHORT</span>
          )}
          <span className="text-[var(--color-text-muted)]">
            {state.connector} / {state.pair}
          </span>
        </div>

        <button
          onClick={() => createMutation.mutate()}
          disabled={!validation.valid || createMutation.isPending}
          className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-5 py-2 text-sm font-bold text-white transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {createMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Rocket className="h-4 w-4" />
          )}
          Create Grid Executor
        </button>
      </div>

      {/* Success toast */}
      {successId && (
        <div className="absolute bottom-16 right-4 flex items-center gap-2 rounded-lg border border-[var(--color-green)]/30 bg-[var(--color-green)]/10 px-4 py-2.5 text-sm text-[var(--color-green)] animate-in fade-in slide-in-from-bottom-2">
          <CheckCircle className="h-4 w-4 shrink-0" />
          <div>
            <span className="font-medium">Grid created!</span>
            <span className="ml-1.5 font-mono text-xs opacity-75">{successId.slice(0, 8)}…</span>
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
