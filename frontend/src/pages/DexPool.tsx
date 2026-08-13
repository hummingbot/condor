import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle, ChevronLeft, ChevronRight, Copy, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { LiquidityDepthColumn } from "@/components/dex/LiquidityDepthColumn";
import { PoolStats } from "@/components/dex/PoolStats";
import {
  LPConfigPanel,
  useLpConfig,
} from "@/components/executor/LPConfigPanel";
import {
  OrderConfigPanel,
  useOrderConfig,
} from "@/components/executor/OrderConfigPanel";
import { TradeBottomPane } from "@/components/trade/TradeBottomPane";
import { TradeChart, type ChartPriceAxis } from "@/components/trade/TradeChart";
import { useMainControllerData } from "@/hooks/useMainControllerData";
import { useResizeDrag } from "@/hooks/useResizeDrag";
import { useServer } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { connectorCapabilities } from "@/lib/connector-capabilities";
import { INTERVALS, LOOKBACK_OPTIONS } from "@/lib/gridExecutor";

type Tab = "order" | "lp";

const DEPTH_KEY = "condor.dex.depth-collapsed";

/**
 * One pool, and everything you can do in it.
 *
 * Reads the pool from the URL rather than from the browser that linked here, so
 * a deep link or a reload renders the same workspace with no prior state. The
 * chart, both executors and the bottom pane are all pinned to this exact pool —
 * which is the whole point of a pool-first page: on Gateway, `SOL-USDC` names
 * dozens of pools and which one you are in *is* the decision.
 */
export function DexPool() {
  const { server } = useServer();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { network = "", address = "" } = useParams();

  const [wantedTab, setTab] = useState<Tab>("order");
  const [interval, setIntervalValue] = useState("1m");
  const [lookbackSeconds, setLookbackSeconds] = useState(3 * 86400);
  const [rightPanelWidth, setRightPanelWidth] = useState(288);
  const [bottomPaneHeight, setBottomPaneHeight] = useState(200);
  const [selectedExecutorId, setSelectedExecutorId] = useState<string | null>(null);
  const [successId, setSuccessId] = useState<string | null>(null);
  // The chart lends out its price mapping; the depth column draws on it.
  const [priceAxis, setPriceAxis] = useState<ChartPriceAxis | null>(null);
  const [depthCollapsed, setDepthCollapsed] = useState(() => {
    try {
      return localStorage.getItem(DEPTH_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(DEPTH_KEY, depthCollapsed ? "1" : "0");
    } catch {
      /* private mode; the column just forgets */
    }
  }, [depthCollapsed]);

  const { onMouseDown: startHDrag } = useResizeDrag({
    axis: "x",
    value: rightPanelWidth,
    onChange: setRightPanelWidth,
    min: 260,
    max: 500,
    direction: "inverted",
    cursor: "col-resize",
  });
  const { onMouseDown: startVDrag } = useResizeDrag({
    axis: "y",
    value: bottomPaneHeight,
    onChange: setBottomPaneHeight,
    min: 100,
    max: 500,
    direction: "inverted",
    cursor: "row-resize",
  });

  const {
    data: pool,
    isLoading: poolLoading,
    error: poolError,
  } = useQuery({
    queryKey: ["dex-pool-by-address", server, network, address],
    queryFn: () => api.getDexPoolByAddress(server!, address, network),
    enabled: !!server && !!network && !!address,
    staleTime: 60 * 1000,
  });

  const { data: venues = [] } = useQuery({
    queryKey: ["venues", server],
    queryFn: () => api.getVenues(server!),
    enabled: !!server,
    staleTime: 5 * 60 * 1000,
  });

  // Bins move with every swap through the active bin, so the server caches them
  // for a minute and this polls at the same cadence — every viewer of the pool
  // shares that one Gateway call. Asked only when the pool says it has bins:
  // `has_bins` is decided by the same predicate the route gates on.
  const { data: depth } = useQuery({
    queryKey: ["dex-pool-bins", server, network, address, pool?.dex_id],
    queryFn: () => api.getPoolBins(server!, address, network, pool?.dex_id ?? ""),
    enabled: !!server && !!address && !!pool?.has_bins,
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
  });

  // The same function Trade feeds; it is handed a gateway network here instead
  // of a CLOB venue, and answers ["order"] (+ "lp" when the venue does CLMM).
  const caps = useMemo(
    () => connectorCapabilities(network, venues),
    [network, venues],
  );

  const pair = pool?.trading_pair ?? "";
  const lpAvailable = caps.supportsLp && !!pool?.lp_supported;

  const orderConfig = useOrderConfig();
  const lpConfig = useLpConfig(server ?? null, network, pair, lpAvailable);

  // The user picked a pool. Auto-resolution must not second-guess it, so the
  // opened pool is written straight into the LP config — which sets poolTouched
  // and makes every later RESOLVED a no-op.
  useEffect(() => {
    if (!pool?.lp_supported || !pool.lp_provider) return;
    lpConfig.dispatch({
      type: "SET_FIELD",
      field: "pool_address",
      value: pool.address,
    });
    lpConfig.dispatch({
      type: "SET_FIELD",
      field: "lp_provider",
      value: pool.lp_provider,
    });
  }, [pool?.address, pool?.lp_provider, pool?.lp_supported]); // eslint-disable-line react-hooks/exhaustive-deps

  // Derived, not synced: a pool with no CLMM provider has no LP tab to select,
  // and the pool is only known after the fetch resolves.
  const tab: Tab = wantedTab === "lp" && !lpAvailable ? "order" : wantedTab;

  const { executors, overlays, positions, isLoadingPositions } =
    useMainControllerData(server ?? null, network, pair);

  const active = tab === "lp" ? lpConfig : orderConfig;
  const currentPrice = pool?.current_price ?? null;

  /**
   * How many decimals the price axis shows.
   *
   * A CEX pair gets this from its trading rules, but a DEX pool has none — so it
   * comes from the price itself. Without it lightweight-charts falls back to 2
   * decimals, which renders a memecoin at 0.000312032 as a flat "0.00" axis with
   * every candle stacked on one line.
   *
   * Held to ~5 significant digits: enough to separate ticks at any magnitude,
   * short enough that a $150 pair does not get an axis of trailing zeros.
   */
  const pricePrecision = useMemo(() => {
    if (!currentPrice || !Number.isFinite(currentPrice) || currentPrice <= 0) {
      return undefined;
    }
    const leadingZeros = Math.ceil(-Math.log10(currentPrice));
    return Math.min(12, Math.max(2, leadingZeros + 4));
  }, [currentPrice]);

  const createMutation = useMutation({
    mutationFn: () => {
      if (!server) throw new Error("No server");
      const payload =
        tab === "lp"
          ? // No isSpot: an LP position has no leverage, and connector is the network.
            lpConfig.buildPayload(network, pair)
          : orderConfig.buildPayload(network, pair, true);
      return api.createExecutor(server, payload);
    },
    onSuccess: (data) => {
      active.save();
      setSuccessId(data.executor_id);
      queryClient.invalidateQueries({ queryKey: ["executors", server, "main", pair] });
      queryClient.invalidateQueries({ queryKey: ["consolidated-positions", server] });
      setSelectedExecutorId(data.executor_id);
    },
  });

  if (!server) {
    return <NoServerCard message="Select a server from the sidebar to open a pool." />;
  }

  if (poolLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center text-sm text-[var(--color-text-muted)]">
        Loading pool…
      </div>
    );
  }

  if (poolError || !pool) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
        <p className="text-sm text-[var(--color-text-muted)]">
          No pool at {address.slice(0, 8)}… on {network}.
        </p>
        <button
          onClick={() => navigate("/dex")}
          className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          Back to pools
        </button>
      </div>
    );
  }

  const pairLabel =
    pool.base_symbol !== "???" && pool.quote_symbol !== "???"
      ? `${pool.base_symbol}-${pool.quote_symbol}`
      : pool.name;

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] flex-col">
      {/* Top bar */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-border)] px-3 py-2">
        <button
          onClick={() => navigate("/dex")}
          className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          title="Back to pools"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="flex flex-col">
          <span className="text-sm font-semibold">{pairLabel}</span>
          <span className="text-[10px] text-[var(--color-text-muted)]">{network}</span>
        </div>
        <div className="min-w-0 flex-1">
          <PoolStats pool={pool} />
        </div>
      </div>

      {/* Interval / lookback */}
      <div className="flex shrink-0 items-center gap-4 border-b border-[var(--color-border)] px-3 py-1.5">
        <div className="flex items-center gap-1">
          {INTERVALS.map((iv) => (
            <button
              key={iv}
              onClick={() => setIntervalValue(iv)}
              className={`rounded px-1.5 py-0.5 text-[11px] transition-colors ${
                interval === iv
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {iv}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          {LOOKBACK_OPTIONS.map((o) => (
            <button
              key={o.label}
              onClick={() => setLookbackSeconds(o.seconds)}
              className={`rounded px-1.5 py-0.5 text-[11px] transition-colors ${
                lookbackSeconds === o.seconds
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1">
            <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
            <TradeChart
              key={`${network}:${pool.address}:${interval}`}
              server={server}
              connector={network}
              pair={pair}
              // The pool the user opened, not the deepest pool for the pair.
              poolAddress={pool.address}
              interval={interval}
              lookbackSeconds={lookbackSeconds}
              pricePrecision={pricePrecision}
              startPrice={active.chartProps.startPrice}
              endPrice={active.chartProps.endPrice}
              limitPrice={active.chartProps.limitPrice}
              side={active.chartProps.side}
              minSpread={0}
              activePickField={active.chartProps.activePickField}
              onPriceSet={active.handleChartPriceSet}
              extraLines={active.chartProps.extraLines}
              executorOverlays={overlays}
              positions={positions}
              selectedExecutorId={selectedExecutorId}
              onExecutorDeselect={() => setSelectedExecutorId(null)}
              onChartReady={setPriceAxis}
            />
            </div>

            {/* Liquidity depth, on the chart's own price axis. Only where there
                are bins to read: a plain AMM pool has none to draw. */}
            {pool.has_bins &&
              (depthCollapsed ? (
                <button
                  onClick={() => setDepthCollapsed(false)}
                  title="Show liquidity depth"
                  className="flex w-5 shrink-0 items-center justify-center border-l border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                </button>
              ) : (
                <div className="relative flex shrink-0">
                  {depth && !depth.available ? (
                    <p className="w-[120px] shrink-0 border-l border-[var(--color-border)] px-2 py-2 text-[10px] leading-relaxed text-[var(--color-text-muted)]">
                      {depth.reason ?? "No liquidity bins for this pool."}
                    </p>
                  ) : (
                    <LiquidityDepthColumn
                      bins={depth?.bins ?? []}
                      activePrice={depth?.active_price ?? pool.current_price ?? null}
                      axis={priceAxis}
                      rangeStart={active.chartProps.startPrice}
                      rangeEnd={active.chartProps.endPrice}
                    />
                  )}
                  <button
                    onClick={() => setDepthCollapsed(true)}
                    title="Hide liquidity depth"
                    className="absolute right-0 top-0 z-10 rounded-bl bg-[var(--color-bg)]/80 p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  >
                    <ChevronRight className="h-3 w-3" />
                  </button>
                </div>
              ))}
          </div>

          <div
            className="group/hdrag relative h-1.5 shrink-0 cursor-row-resize border-y border-[var(--color-border)] bg-[var(--color-bg)] transition-colors hover:bg-[var(--color-primary)]/10 active:bg-[var(--color-primary)]/20"
            onMouseDown={startVDrag}
          >
            <div className="absolute inset-x-0 top-1/2 mx-auto h-px w-12 -translate-y-1/2 rounded bg-amber-400/60 transition-colors group-hover/hdrag:bg-amber-400" />
          </div>

          <div style={{ height: bottomPaneHeight }} className="shrink-0 overflow-hidden">
            <TradeBottomPane
              executors={executors}
              positions={positions}
              isLoadingPositions={isLoadingPositions}
              connector={network}
              pair={pair}
              isSpot
              selectedExecutorId={selectedExecutorId}
              onExecutorSelect={(ex) => setSelectedExecutorId(ex?.id ?? null)}
            />
          </div>
        </div>

        <div
          className="group/vdrag relative w-1.5 shrink-0 cursor-col-resize border-x border-[var(--color-border)] bg-[var(--color-bg)] transition-colors hover:bg-[var(--color-primary)]/10 active:bg-[var(--color-primary)]/20"
          onMouseDown={startHDrag}
        >
          <div className="absolute inset-y-0 left-1/2 my-auto h-12 w-px -translate-x-1/2 rounded bg-amber-400/60 transition-colors group-hover/vdrag:bg-amber-400" />
        </div>

        <div
          className="flex shrink-0 flex-col border-l border-[var(--color-border)]"
          style={{ width: rightPanelWidth }}
        >
          <div className="flex border-b border-[var(--color-border)]">
            <button
              onClick={() => setTab("order")}
              className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                tab === "order"
                  ? "border-b-2 border-[var(--color-primary)] text-[var(--color-text)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              Order
            </button>
            {lpAvailable && (
              <button
                onClick={() => setTab("lp")}
                className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                  tab === "lp"
                    ? "border-b-2 border-[var(--color-primary)] text-[var(--color-text)]"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                }`}
              >
                LP
              </button>
            )}
          </div>

          {/* A pool with no CLMM provider is a state, not an error: a router pool,
              a plain AMM or a Uniswap v4 pool can still be swapped in. */}
          {!lpAvailable && (
            <p className="border-b border-[var(--color-border)] px-3 py-2 text-[10px] leading-relaxed text-[var(--color-text-muted)]">
              No liquidity position available here —{" "}
              {caps.supportsLp
                ? `${pool.dex_id} is not a concentrated-liquidity venue Condor can add to.`
                : `${network} takes no CLMM position.`}{" "}
              Market swaps still work.
            </p>
          )}

          <div className="flex-1 overflow-y-auto">
            {tab === "order" ? (
              <OrderConfigPanel
                state={orderConfig.state}
                dispatch={orderConfig.dispatch}
                validation={orderConfig.validation}
                currentPrice={currentPrice}
                isSpot
                pair={pairLabel}
                strategies={caps.orderStrategies}
              />
            ) : (
              <LPConfigPanel
                state={lpConfig.state}
                dispatch={lpConfig.dispatch}
                validation={lpConfig.validation}
                currentPrice={currentPrice}
                pair={pairLabel}
                pool={lpConfig.pool}
                poolFetching={lpConfig.poolFetching}
              />
            )}
          </div>

          <div className="border-t border-[var(--color-border)] p-3">
            {!active.validation.valid && (
              <p className="mb-2 text-[11px] text-[var(--color-red)]">
                {active.validation.errors[0]}
              </p>
            )}
            <button
              onClick={() => createMutation.mutate()}
              disabled={!active.validation.valid || createMutation.isPending || !pair}
              className="w-full rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {createMutation.isPending
                ? "Creating…"
                : tab === "lp"
                  ? "Create LP Position"
                  : "Create Order"}
            </button>
          </div>
        </div>
      </div>

      {successId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="relative w-[360px] rounded-xl border border-[var(--color-green)]/30 bg-[var(--color-surface)] p-6 shadow-2xl shadow-black/40">
            <button
              onClick={() => setSuccessId(null)}
              className="absolute right-3 top-3 rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            >
              <X className="h-4 w-4" />
            </button>
            <div className="mb-4 flex justify-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-green)]/15">
                <CheckCircle className="h-6 w-6 text-[var(--color-green)]" />
              </div>
            </div>
            <h3 className="mb-1 text-center text-sm font-semibold">
              {tab === "lp" ? "LP Position" : "Order"} Created
            </h3>
            <p className="mb-4 text-center text-[11px] text-[var(--color-text-muted)]">
              In {pairLabel} on {pool.dex_id}
            </p>
            <div className="mb-4 flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] p-3 text-[11px]">
              <span className="text-[var(--color-text-muted)]">Executor ID</span>
              <div className="flex items-center gap-1.5">
                <span className="font-mono">{successId.slice(0, 12)}…</span>
                <button
                  onClick={() => navigator.clipboard.writeText(successId)}
                  className="rounded p-0.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                  title="Copy full ID"
                >
                  <Copy className="h-3 w-3" />
                </button>
              </div>
            </div>
            <button
              onClick={() => setSuccessId(null)}
              className="w-full rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white transition-colors hover:brightness-110"
            >
              Continue
            </button>
          </div>
        </div>
      )}

      {createMutation.isError && (
        <div className="absolute bottom-16 right-4 rounded-lg border border-[var(--color-red)]/30 bg-[var(--color-red)]/10 px-4 py-2 text-sm text-[var(--color-red)]">
          {(createMutation.error as Error).message}
        </div>
      )}
    </div>
  );
}
