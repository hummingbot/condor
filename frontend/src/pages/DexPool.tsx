import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { NoServerCard } from "@/components/NoServerCard";
import { LiquidityDepthColumn } from "@/components/dex/LiquidityDepthColumn";
import { LpPositionBar } from "@/components/dex/LpPositionBar";
import { PoolAddress, PoolStats } from "@/components/dex/PoolStats";
import { UpstreamNotice } from "@/components/dex/UpstreamNotice";
import {
  ErrorToast,
  ExecutorSuccessModal,
} from "@/components/executor/ExecutorSuccessModal";
import { LPConfigPanel } from "@/components/executor/LPConfigPanel";
import { useLpConfig } from "@/components/executor/lp-config";
import {
  OrderConfigPanel,
  useOrderConfig,
} from "@/components/executor/OrderConfigPanel";
import { TradeBottomPane } from "@/components/trade/TradeBottomPane";
import { TradeChart, type ChartPriceAxis } from "@/components/trade/TradeChart";
import { useDexUpstream } from "@/hooks/useDexUpstream";
import { useMainControllerData } from "@/hooks/useMainControllerData";
import { usePairBalances } from "@/hooks/usePairBalances";
import { useResizeDrag } from "@/hooks/useResizeDrag";
import { useServer } from "@/hooks/useServer";
import { useCondorWebSocket } from "@/hooks/useWebSocket";
import { api } from "@/lib/api";
import { connectorCapabilities } from "@/lib/connector-capabilities";
import { LOOKBACK_OPTIONS } from "@/lib/gridExecutor";

type Tab = "order" | "lp";

// Only the fine intervals: 1h/4h/1d candles read as duplicates of the 1h/1d
// lookback buttons sitting next to them, and a 3-day window of 15m candles
// already fits GeckoTerminal's 1000-candle cap.
const DEX_INTERVALS = ["1m", "5m", "15m"];

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
  // The pool, its stats and its candles all come from the same shared
  // GeckoTerminal budget: when it is spent this page silently stops updating.
  const upstream = useDexUpstream(server ?? null);

  const [wantedTab, setTab] = useState<Tab>("order");
  // 5m over 3 days, matching the executor pages. Not 1m: GeckoTerminal caps a
  // response at 1000 candles, so 1m can only ever fill ~16h of this window (the
  // buffer then sits permanently short and re-backfills), while 5m covers the
  // full 3 days in one request and halves the poll rate on a shared budget.
  const [interval, setIntervalValue] = useState("5m");
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

  // Registering the pool's tokens with Gateway is part of opening the pool, not
  // part of sending an order: an unlisted mint reads as a zero balance, so the
  // percentage presets and the LP amounts would be sized against a wallet that
  // looks empty long before anything is submitted. Idempotent and memoized
  // server-side, so a reload or a second visit sends this and gets a no-op.
  const { data: tokenVerdicts } = useQuery({
    queryKey: ["dex-ensure-tokens", server, network, pool?.address],
    queryFn: () =>
      api
        .ensureDexTokens(server!, network, [
          pool!.base_token_address,
          pool!.quote_token_address,
        ])
        .then((r) => r.tokens),
    enabled:
      !!server && !!network && !!(pool?.base_token_address || pool?.quote_token_address),
    staleTime: Infinity,
    retry: false,
  });

  // A token that was *just* added is one the portfolio was fetched without, so
  // its balance is still missing from the copy the panels are reading.
  useEffect(() => {
    if (Object.values(tokenVerdicts ?? {}).includes("added")) {
      queryClient.invalidateQueries({ queryKey: ["portfolio", server] });
    }
  }, [tokenVerdicts, queryClient, server]);

  // Reported rather than swallowed: Gateway refuses to list a token whose ticker
  // another token already holds, and the only visible symptom is a balance that
  // stays 0 for a wallet that is not empty.
  const unlistedTokens = useMemo(
    () =>
      Object.entries(tokenVerdicts ?? {})
        .filter(([, verdict]) => verdict === "symbol_taken" || verdict === "failed")
        .map(([address]) => address),
    [tokenVerdicts],
  );

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

  // `pair` is the executor's `<base_mint>-<quote_symbol>` form. The tickers only
  // exist on the pool, so every symbol-shaped consumer — balance lookups, amount
  // labels — is handed them rather than left to split the pair.
  const baseSymbol =
    pool?.base_symbol && pool.base_symbol !== "???" ? pool.base_symbol : undefined;
  const quoteSymbol =
    pool?.quote_symbol && pool.quote_symbol !== "???" ? pool.quote_symbol : undefined;
  const balances = usePairBalances(server ?? null, network, baseSymbol, quoteSymbol);

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

  // An executor opened from here is filed under `<base_mint>-<quote>`, but the
  // same position opened from Telegram or MCP is filed under `<base>-<quote>`.
  // Both name this pool, so both are asked for; `pool.address` then keeps the
  // answer to the pool actually on screen.
  const symbolPair =
    baseSymbol && quoteSymbol ? `${baseSymbol}-${quoteSymbol}` : "";

  // WS for executor data, same subscription CreateExecutor holds: without it
  // the shared-socket bridge has no executors frames to fan out here, and the
  // pair queries below (staleTime 30s, no polling) never see a stop or a fill.
  const wsChannels = useMemo(
    () => (server ? [`executors:${server}`] : []),
    [server],
  );
  useCondorWebSocket(wsChannels, server ?? null);

  const { executors, overlays, positions, isLoadingPositions } =
    useMainControllerData(server ?? null, network, pair, {
      altPair: symbolPair,
      poolAddress: pool?.address,
    });

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
    // A pool that could not be *read* is not a pool that does not exist: the
    // route answers 503 with a reason when GeckoTerminal refused, and retrying
    // in a few seconds is the fix — not walking back to the browser.
    const throttled = upstream.limited || /rate limit/i.test(poolError?.message ?? "");
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
        <p className="max-w-md text-center text-sm text-[var(--color-text-muted)]">
          {throttled
            ? (poolError?.message ??
              "GeckoTerminal is rate limiting Condor, so this pool could not be read.")
            : `No pool at ${address.slice(0, 8)}… on ${network}.`}
        </p>
        <div className="flex gap-2">
          {throttled && (
            <button
              onClick={() =>
                queryClient.invalidateQueries({
                  queryKey: ["dex-pool-by-address", server, network, address],
                })
              }
              className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
            >
              {upstream.retryIn > 0 ? `Retry (${upstream.retryIn}s)` : "Retry"}
            </button>
          )}
          <button
            onClick={() => navigate("/dex")}
            className="rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            Back to pools
          </button>
        </div>
      </div>
    );
  }

  const pairLabel =
    pool.base_symbol !== "???" && pool.quote_symbol !== "???"
      ? `${pool.base_symbol}-${pool.quote_symbol}`
      : pool.name;

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] flex-col">
      {/* Nothing on this page fails loudly when the budget is spent — the chart
          just stops moving and the stats freeze — so it is said here instead. */}
      <UpstreamNotice state={upstream} className="shrink-0 border-b" />

      {/* Gateway lists a token by ticker, so a pool whose token shares a ticker
          with one already on the list cannot be registered — and the only symptom
          is a balance that reads 0 for a wallet that is not empty. */}
      {unlistedTokens.length > 0 && (
        <div
          role="status"
          className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-[var(--color-yellow)]" />
          <span className="font-medium text-[var(--color-yellow)]">
            {unlistedTokens.length === 1 ? "A token is" : "Tokens are"} not in
            Gateway&apos;s token list
          </span>
          <span className="text-[var(--color-text-muted)]">
            Balances for {unlistedTokens.map((a) => `${a.slice(0, 6)}…`).join(", ")}{" "}
            will read 0 — add {unlistedTokens.length === 1 ? "it" : "them"} under
            Gateway tokens to size orders from this wallet.
          </span>
        </div>
      )}

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
          {/* The address identifies the pool the way the ticker never can, so it
              sits with the name instead of hiding at the far right. */}
          <PoolAddress pool={pool} />
        </div>
        <div className="min-w-0 flex-1">
          <PoolStats pool={pool} network={network} />
        </div>
      </div>

      {/* Interval / lookback */}
      <div className="flex shrink-0 items-center gap-4 border-b border-[var(--color-border)] px-3 py-1.5">
        <div className="flex items-center gap-1">
          <span className="text-[11px] text-[var(--color-text-muted)]">Interval</span>
          {DEX_INTERVALS.map((iv) => (
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
        <div className="h-4 w-px bg-[var(--color-border)]" />
        <div className="flex items-center gap-1">
          <span className="text-[11px] text-[var(--color-text-muted)]">Window</span>
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
          {/* What you already hold here, above the chart that shows where it sits */}
          <LpPositionBar
            executors={executors}
            currentPrice={currentPrice}
            selectedExecutorId={selectedExecutorId}
            onSelect={(id) => setSelectedExecutorId((prev) => (prev === id ? null : id))}
          />

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
              baseSymbol={baseSymbol}
              quoteSymbol={quoteSymbol}
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
                baseAvailable={balances.base}
                quoteAvailable={balances.quote}
                baseSymbol={baseSymbol}
                quoteSymbol={quoteSymbol}
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
                // Gateway's number, from the bins call — the GeckoTerminal pool
                // row does not carry one.
                binStep={depth?.bin_step}
                // The pool was chosen by opening this page, so the panel shows it
                // instead of offering to resolve or re-enter it.
                lockedPoolAddress={pool.address}
                baseAvailable={balances.base}
                quoteAvailable={balances.quote}
                baseSymbol={baseSymbol}
                quoteSymbol={quoteSymbol}
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
        <ExecutorSuccessModal
          executorId={successId}
          title={`${tab === "lp" ? "LP Position" : "Order"} Created`}
          subtitle={`In ${pairLabel} on ${pool.dex_id}`}
          onClose={() => setSuccessId(null)}
        />
      )}

      {createMutation.isError && (
        <ErrorToast message={(createMutation.error as Error).message} />
      )}
    </div>
  );
}
