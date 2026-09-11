import { useEffect } from "react";
import { AlertTriangle, Sparkles } from "lucide-react";

import {
  AdvancedSection,
  NumberField,
  PercentPresets,
  PriceField,
  SectionHeader,
  SelectField,
  ToggleField,
  ValidationMessages,
} from "./fields";
import type { ExecutorValidation } from "./types";
import type { DexPoolInfo } from "@/lib/api";
import {
  LP_SIDE_BUY,
  LP_SIDE_RANGE,
  LP_SIDE_SELL,
  type LPAction,
  type LPState,
  type LpSide,
  isMeteoraProvider,
  rangeWarnings,
} from "./lp-config";

// ── Options ──

const SIDE_OPTIONS: { value: LpSide; label: string; hint: string }[] = [
  { value: LP_SIDE_RANGE, label: "Range", hint: "Both tokens, range around price" },
  { value: LP_SIDE_BUY, label: "Buy", hint: "Quote only, range below price" },
  { value: LP_SIDE_SELL, label: "Sell", hint: "Base only, range above price" },
];

const PROVIDER_OPTIONS = [
  { value: "", label: "Select provider…" },
  { value: "meteora/clmm", label: "Meteora (DLMM)" },
  { value: "raydium/clmm", label: "Raydium (CLMM)" },
  { value: "orca/clmm", label: "Orca (Whirlpools)" },
  { value: "uniswap/clmm", label: "Uniswap V3" },
  { value: "pancakeswap/clmm", label: "PancakeSwap V3" },
];

const STRATEGY_TYPE_OPTIONS = [
  { value: "0", label: "Spot (uniform)" },
  { value: "1", label: "Curve (concentrated)" },
  { value: "2", label: "Bid-Ask (edges)" },
];

const RANGE_PCT_PRESETS = [0.01, 0.02, 0.05, 0.1, 0.2];

function truncateAddress(address: string): string {
  return address.length > 16
    ? `${address.slice(0, 6)}…${address.slice(-6)}`
    : address;
}

/** A percentage of an available balance, at a precision an input can render. */
function sizeFrom(available: number | null, pct: number): number {
  if (!available || available <= 0) return 0;
  return Number((available * pct).toPrecision(8));
}

function formatPoolPrice(price: number): string {
  if (price >= 1000) return price.toFixed(2);
  if (price >= 1) return price.toFixed(4);
  return price.toPrecision(6);
}

// ── Panel Component ──

interface Props {
  state: LPState;
  dispatch: React.Dispatch<LPAction>;
  validation: ExecutorValidation;
  currentPrice: number | null;
  pair?: string;
  pool?: DexPoolInfo;
  poolFetching?: boolean;
  /**
   * The pool's DLMM bin step in basis points, when the caller knows it. Only the
   * pool workspace does — the resolve-by-pair endpoint the create page uses does
   * not report one — and it is what turns a too-wide range into a warning here
   * rather than a Gateway error at open time.
   */
  binStep?: number | null;
  /** Wallet balance behind the percentage presets; `null` when it is unknown. */
  baseAvailable?: number | null;
  quoteAvailable?: number | null;
  /**
   * Display symbols for the two tokens. A DEX `trading_pair` is
   * `<base_mint>-<quote_symbol>`, so its base half is an address, not a ticker.
   */
  baseSymbol?: string;
  quoteSymbol?: string;
  /**
   * Set when the page itself pins the pool (the DEX pool workspace). The panel
   * then just states the address — resolution status, an editable address and a
   * provider picker would only second-guess a decision already made.
   */
  lockedPoolAddress?: string;
}

export function LPConfigPanel({
  state,
  dispatch,
  validation,
  currentPrice,
  pair,
  pool,
  poolFetching,
  binStep,
  baseAvailable = null,
  quoteAvailable = null,
  baseSymbol,
  quoteSymbol,
  lockedPoolAddress,
}: Props) {
  // A DEX pair's chart can be blank (no candles for the pool) while the pool's own
  // price is known, so the resolved pool is a second source for the anchor.
  const price = currentPrice && currentPrice > 0 ? currentPrice : pool?.current_price ?? null;

  const baseAsset = baseSymbol || pool?.base_symbol || pair?.split("-")[0] || "base";
  const quoteAsset = quoteSymbol || pool?.quote_symbol || pair?.split("-")[1] || "quote";

  // Anchor the range the first time a price is known. Not a reset: once the bounds
  // are non-zero they are the user's, and only an explicit action moves them.
  const unanchored = state.lower_price === 0 && state.upper_price === 0;
  useEffect(() => {
    if (price && price > 0 && unanchored) {
      dispatch({ type: "AUTO_RANGE", price });
    }
  }, [price, unanchored]); // eslint-disable-line react-hooks/exhaustive-deps

  // Only DLMM pools carry a bin step, so passing it through unconditionally is
  // enough to scope the bin-count warning to the pools the cap applies to.
  const warnings = rangeWarnings(state, price, binStep);
  const unsupported = !!pool && !pool.lp_supported;

  return (
    <div className="flex flex-col gap-4 overflow-y-auto p-3">
      {/* Resolved pool */}
      {lockedPoolAddress ? (
        <p
          className="font-mono text-[10px] text-[var(--color-text-muted)]"
          title={lockedPoolAddress}
        >
          {truncateAddress(lockedPoolAddress)}
        </p>
      ) : (
        <div className="space-y-2">
          <SectionHeader>Pool</SectionHeader>
          {poolFetching && !pool ? (
            <p className="text-[10px] text-[var(--color-text-muted)]">Resolving pool…</p>
          ) : unsupported ? (
            <div className="rounded border border-amber-400/30 bg-amber-400/10 p-2">
              <p className="flex items-start gap-1 text-[10px] text-amber-400">
                <AlertTriangle className="mt-px h-3 w-3 shrink-0" />
                <span>
                  The deepest pool for this pair is on{" "}
                  <span className="font-mono">{pool?.dex_id ?? "an unknown venue"}</span>, which
                  is not a CLMM venue. Enter a pool address and provider by hand.
                </span>
              </p>
            </div>
          ) : pool?.pool_address ? (
            <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-[var(--color-text-muted)]">
              <span className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 font-medium text-[var(--color-text)]">
                {pool.dex_id}
              </span>
              <span className="font-mono" title={pool.pool_address}>
                {truncateAddress(pool.pool_address)}
              </span>
              {pool.current_price != null && (
                <span className="font-mono">
                  {formatPoolPrice(pool.current_price)} {quoteAsset}
                </span>
              )}
              {state.poolTouched && (
                <span className="rounded bg-[var(--color-primary)]/15 px-1.5 py-0.5 text-[var(--color-primary)]">
                  overridden
                </span>
              )}
            </div>
          ) : (
            <p className="text-[10px] text-[var(--color-text-muted)]">
              No pool found for this pair. Enter one by hand.
            </p>
          )}

          <div>
            <label className="mb-1 block text-xs text-[var(--color-text-muted)]">
              Pool Address
            </label>
            <input
              type="text"
              value={state.pool_address}
              onChange={(e) =>
                dispatch({ type: "SET_FIELD", field: "pool_address", value: e.target.value.trim() })
              }
              placeholder="Pool contract address"
              spellCheck={false}
              className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-[11px] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/40 focus:border-[var(--color-primary)] focus:outline-none"
            />
          </div>
          <SelectField
            label="LP Provider"
            value={state.lp_provider}
            field="lp_provider"
            dispatch={dispatch}
            options={PROVIDER_OPTIONS}
          />
        </div>
      )}

      {/* Side */}
      <div className="space-y-2">
        <SectionHeader>Position Side</SectionHeader>
        <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
          {SIDE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => dispatch({ type: "SET_SIDE", value: opt.value, price })}
              title={opt.hint}
              className={`flex-1 px-2 py-1.5 text-[11px] font-medium transition-colors ${
                state.side === opt.value
                  ? "bg-[var(--color-primary)] text-white"
                  : "bg-[var(--color-bg)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-[var(--color-text-muted)]">
          {SIDE_OPTIONS.find((o) => o.value === state.side)?.hint}
        </p>
      </div>

      {/* Range */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between">
          <SectionHeader>Range</SectionHeader>
          {price != null && price > 0 && (
            <button
              onClick={() => dispatch({ type: "AUTO_RANGE", price })}
              className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-[10px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <Sparkles className="h-3 w-3" />
              Auto
            </button>
          )}
        </div>
        <div className="flex overflow-hidden rounded-md border border-[var(--color-border)]">
          {RANGE_PCT_PRESETS.map((pct) => (
            <button
              key={pct}
              onClick={() => dispatch({ type: "SET_RANGE_PCT", value: pct, price })}
              className={`flex-1 px-1.5 py-1 text-[10px] transition-colors ${
                state.range_pct === pct
                  ? "bg-[var(--color-primary)] text-white"
                  : "bg-[var(--color-bg)] text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              }`}
            >
              {pct * 100}%
            </button>
          ))}
        </div>
        <PriceField
          label="Upper Price"
          value={state.upper_price}
          field="upper_price"
          activePickField={state.activePickField}
          dispatch={dispatch}
          valid={state.upper_price > state.lower_price}
        />
        <PriceField
          label="Lower Price"
          value={state.lower_price}
          field="lower_price"
          activePickField={state.activePickField}
          dispatch={dispatch}
          valid={state.lower_price > 0 && state.lower_price < state.upper_price}
        />
      </div>

      {/* Auto-close triggers */}
      <div className="space-y-2.5">
        <SectionHeader>Auto-Close Triggers</SectionHeader>
        <PriceField
          label="Upper Limit"
          value={state.upper_limit_price}
          field="upper_limit_price"
          activePickField={state.activePickField}
          dispatch={dispatch}
          valid={state.upper_limit_price > state.upper_price}
          hint="Close when price rises to this level"
        />
        <PriceField
          label="Lower Limit"
          value={state.lower_limit_price}
          field="lower_limit_price"
          activePickField={state.activePickField}
          dispatch={dispatch}
          valid={
            state.lower_limit_price > 0 && state.lower_limit_price < state.lower_price
          }
          hint="Close when price falls to this level"
        />
        <p className="text-[10px] text-[var(--color-text-muted)]">
          Both only fire while the position is out of range. Leave at 0 for no trigger.
        </p>
      </div>

      {/* Amounts */}
      <div className="space-y-2.5">
        <SectionHeader>Amounts</SectionHeader>
        <div>
          <NumberField
            label={`Base Amount (${baseAsset})`}
            value={state.base_amount}
            field="base_amount"
            dispatch={dispatch}
            step={0.001}
            min={0}
          />
          <PercentPresets
            available={baseAvailable}
            symbol={baseAsset}
            onPick={(pct) =>
              dispatch({ type: "SET_FIELD", field: "base_amount", value: sizeFrom(baseAvailable, pct) })
            }
          />
        </div>
        <div>
          <NumberField
            label={`Quote Amount (${quoteAsset})`}
            value={state.quote_amount}
            field="quote_amount"
            dispatch={dispatch}
            step={0.001}
            min={0}
          />
          <PercentPresets
            available={quoteAvailable}
            symbol={quoteAsset}
            onPick={(pct) =>
              dispatch({ type: "SET_FIELD", field: "quote_amount", value: sizeFrom(quoteAvailable, pct) })
            }
          />
        </div>
      </div>

      {/* On Close — a decision about the tokens you get back, not a tuning knob. */}
      <div className="space-y-2">
        <SectionHeader>On Close</SectionHeader>
        <ToggleField
          label="Keep Position"
          value={state.keep_position}
          field="keep_position"
          dispatch={dispatch}
        />
        <p className="text-[10px] text-[var(--color-text-muted)]">
          On close, keep the net token change as a spot position instead of swapping
          back to {quoteAsset}. This sets the config field; stopping from the
          executors table asks again.
        </p>
      </div>

      {/* Rendered only when there is something under it: everything else that
          once lived here is now a first-class control. */}
      {isMeteoraProvider(state.lp_provider) && (
        <AdvancedSection
          open={state.showAdvanced}
          onToggle={() =>
            dispatch({ type: "SET_FIELD", field: "showAdvanced", value: !state.showAdvanced })
          }
        >
          <SelectField
            label="Meteora Strategy"
            value={state.strategy_type}
            field="strategy_type"
            dispatch={dispatch}
            options={STRATEGY_TYPE_OPTIONS}
          />
        </AdvancedSection>
      )}

      <ValidationMessages errors={validation.errors} warnings={warnings} />
    </div>
  );
}
