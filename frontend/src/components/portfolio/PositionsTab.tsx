import { ArrowRight, Layers } from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { feeAmount, lpStateStyle, rangeFraction, type LpPosition } from "@/components/dex/lp-position";
import { type ConsolidatedPosition } from "@/lib/api";
import { formatPnl, formatPriceSig, formatUsd, pnlColor } from "@/lib/formatters";

/** The quote a hold's numbers are denominated in — its pair's, as elsewhere. */
function quoteOf(pair: string): string {
  return pair.split("-")[1] || "USDT";
}

/**
 * A size, in base units.
 *
 * Never converted: a position of 40 SOL is 40 SOL whatever currency the header
 * above it is reading in, and running it through a rate would make it a value.
 */
function formatSize(val: number): string {
  const abs = Math.abs(val);
  if (abs === 0) return "0";
  if (abs < 0.0001) return val.toExponential(2);
  return val.toLocaleString("en-US", { maximumFractionDigits: abs >= 1 ? 4 : 6 });
}

/**
 * LONG is green, SHORT is red, and an empty side is not guessed at.
 *
 * A bot row's side arrives as `TradeType.SELL` — a Python enum stringified on
 * its way through `positions_summary` — so the token after the dot is what is
 * actually being reported. It is shown as the API named it rather than
 * translated into LONG/SHORT: a spot hold is not a perp.
 */
function sideStyle(side: string): { label: string; color: string; bg: string } | null {
  const upper = (side ?? "").split(".").pop()!.toUpperCase();
  if (upper === "LONG" || upper === "BUY") {
    return { label: upper, color: "var(--color-green)", bg: "rgba(34,197,94,0.14)" };
  }
  if (upper === "SHORT" || upper === "SELL") {
    return { label: upper, color: "var(--color-red)", bg: "rgba(239,68,68,0.14)" };
  }
  return null;
}

/** Which executors or which bot put this hold on — the only attribution the API gives. */
function sourceLabel(pos: ConsolidatedPosition): string {
  if (pos.source === "bot") return pos.source_name || pos.controller_id || "bot";
  const n = pos.executor_count ?? 0;
  return `${n} executor${n === 1 ? "" : "s"}`;
}

const TH = "px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]";
const THR = `${TH} text-right`;
const TD = "px-4 py-2.5";
const TDR = `${TD} text-right font-mono tabular-nums`;

interface Props {
  holds: ConsolidatedPosition[];
  lpPositions: LpPosition[];
  lpLabel: (pos: LpPosition) => string;
  isLoading: boolean;
  /** Page-level rate conversion, so a row reads in the header's currency. */
  convert: (value: number, quote: string) => { value: number; converted: boolean };
  formatValue: (val: number, quote: string) => string;
  formatPnlValue: (val: number, quote: string) => string;
}

/**
 * What you are *in*, as opposed to what you hold — the other half of `/portfolio`.
 *
 * The Assets tab beside this one answers "what is it worth"; this answers "what
 * did I pay for it, who opened it, and is it still earning". Holds (perp and
 * spot, from executors and from bot controllers) sit above liquidity ranges,
 * because a hold moves against you and a range merely stops paying.
 *
 * Every row is read-only and deep-links to the surface that owns its actions —
 * `/trade` for a hold, the pool's workspace for a range. Nothing here closes,
 * clears or stops anything, which is what makes the tab safe to leave open.
 */
export function PositionsTab({
  holds,
  lpPositions,
  lpLabel,
  isLoading,
  convert,
  formatValue,
  formatPnlValue,
}: Props) {
  const navigate = useNavigate();

  // Biggest first: the row worth looking at is the one with the most in it, and
  // it is only comparable across pairs once every notional is in one currency.
  const sortedHolds = useMemo(() => {
    return [...holds].sort(
      (a, b) =>
        Math.abs(convert(b.notional_value ?? 0, quoteOf(b.trading_pair)).value) -
        Math.abs(convert(a.notional_value ?? 0, quoteOf(a.trading_pair)).value),
    );
  }, [holds, convert]);

  if (isLoading && !holds.length && !lpPositions.length) {
    return (
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4 space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-4 rounded bg-[var(--color-border)] animate-pulse"
            style={{ width: `${85 - i * 10}%` }}
          />
        ))}
      </div>
    );
  }

  // An empty tab the user deliberately clicked has to say it is empty. The /dex
  // strip renders `null` because nobody asked for it; this one was asked for.
  if (!sortedHolds.length && !lpPositions.length) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
        <Layers className="h-10 w-10" />
        <p>No open positions.</p>
        <p className="text-sm">
          A position appears here when an executor or bot opens one.
        </p>
        <button
          onClick={() => navigate("/trade")}
          className="mt-1 text-sm text-[var(--color-primary)] hover:underline"
        >
          Open the trade workspace
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Holds ── */}
      {sortedHolds.length > 0 && (
        <div>
          <div className="mb-2 flex items-baseline justify-between gap-4">
            <h2 className="text-sm font-semibold">Holds</h2>
            {/* Load-bearing, not decoration: this is the honest scope of the data. */}
            <span className="text-xs text-[var(--color-text-muted)]">
              Opened by Condor&apos;s executors and bots
            </span>
          </div>

          <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
                  <th className={TH}>Pair</th>
                  <th className={TH}>Side</th>
                  <th className={THR}>Size</th>
                  <th className={THR}>Entry</th>
                  <th className={THR}>Mark</th>
                  <th className={THR}>Unrl PnL</th>
                  <th className={THR}>Lev</th>
                  <th className={TH}>Source</th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {sortedHolds.map((pos, i) => {
                  const quote = quoteOf(pos.trading_pair);
                  const side = sideStyle(pos.position_side);
                  const pnl = pos.unrealized_pnl ?? 0;
                  return (
                    <tr
                      key={`${pos.connector_name}-${pos.trading_pair}-${pos.position_side}-${i}`}
                      data-hold-row
                      onClick={() =>
                        navigate(
                          `/trade?connector=${encodeURIComponent(pos.connector_name)}&pair=${encodeURIComponent(pos.trading_pair)}`,
                        )
                      }
                      className="group cursor-pointer border-b border-[var(--color-border)]/40 transition-colors last:border-0 hover:bg-[var(--color-surface-hover)]"
                    >
                      <td className={TD}>
                        <div className="font-medium">{pos.trading_pair}</div>
                        <div className="text-xs text-[var(--color-text-muted)]">
                          {pos.connector_name}
                        </div>
                      </td>
                      <td className={TD}>
                        {side ? (
                          <span
                            className="rounded px-1.5 py-0.5 text-[10px] font-bold"
                            style={{ color: side.color, backgroundColor: side.bg }}
                          >
                            {side.label}
                          </span>
                        ) : (
                          <span className="text-[var(--color-text-muted)]">—</span>
                        )}
                      </td>
                      <td className={TDR}>{formatSize(pos.amount ?? 0)}</td>
                      <td className={`${TDR} text-[var(--color-text-muted)]`}>
                        {formatValue(pos.entry_price ?? 0, quote)}
                      </td>
                      <td className={TDR}>{formatValue(pos.current_price ?? 0, quote)}</td>
                      <td className={TDR} style={{ color: pnlColor(pnl) }}>
                        {formatPnlValue(pnl, quote)}
                      </td>
                      <td className={`${TDR} text-[var(--color-text-muted)]`}>
                        {/* `1x` on a spot hold reads like a claim about margin. */}
                        {(pos.leverage ?? 0) > 1 ? `${pos.leverage}x` : "—"}
                      </td>
                      <td
                        className={`${TD} text-xs text-[var(--color-text-muted)]`}
                        title={pos.executor_ids?.join(", ") || undefined}
                      >
                        {sourceLabel(pos)}
                      </td>
                      <td className="px-2 text-right">
                        <ArrowRight className="h-3.5 w-3.5 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100" />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Liquidity ── */}
      {lpPositions.length > 0 && (
        <div>
          <div className="mb-2 flex items-baseline justify-between gap-4">
            <h2 className="text-sm font-semibold">Liquidity</h2>
            <span className="text-xs text-[var(--color-text-muted)]">
              {lpPositions.length} open range{lpPositions.length === 1 ? "" : "s"}
            </span>
          </div>

          <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
                  <th className={TH}>Pool</th>
                  <th className={TH}>State</th>
                  <th className={TH}>Range</th>
                  <th className={THR}>Value</th>
                  <th className={THR}>Fees</th>
                  <th className={THR}>PnL</th>
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody>
                {lpPositions.map((pos) => {
                  const state = lpStateStyle(pos.state);
                  const fraction = rangeFraction(pos, pos.currentPrice);
                  return (
                    <tr
                      key={pos.id}
                      data-lp-row
                      onClick={() => navigate(`/dex/${pos.network}/${pos.poolAddress}`)}
                      className="group cursor-pointer border-b border-[var(--color-border)]/40 transition-colors last:border-0 hover:bg-[var(--color-surface-hover)]"
                    >
                      <td className={TD}>
                        <div className="font-medium">{lpLabel(pos)}</div>
                        <div className="text-xs text-[var(--color-text-muted)]">
                          {pos.provider || pos.network}
                        </div>
                      </td>
                      <td className={TD}>
                        <span
                          className="rounded px-1.5 py-0.5 text-[10px] font-medium"
                          style={{ color: state.color, backgroundColor: state.bg }}
                        >
                          {state.label}
                        </span>
                      </td>
                      <td className={TD}>
                        {/* The same marker the pool workspace draws: a range you are
                            95% through is one swap from earning nothing. */}
                        <span className="relative block h-1 w-24 overflow-hidden rounded-full bg-[var(--color-border)]">
                          {fraction !== null && (
                            <span
                              className="absolute top-1/2 h-2 w-0.5 -translate-y-1/2 rounded-full"
                              style={{
                                left: `calc(${fraction * 100}% - 1px)`,
                                backgroundColor: state.color,
                              }}
                            />
                          )}
                        </span>
                        {/* A range bound is a pool price, not a portfolio value, so
                            it stays in the pair's own quote. */}
                        <span className="mt-1 block font-mono text-[10px] tabular-nums text-[var(--color-text-muted)]">
                          {formatPriceSig(pos.lowerPrice)}–{formatPriceSig(pos.upperPrice)}
                        </span>
                      </td>
                      <td className={TDR}>
                        {pos.valueQuote === null ? "—" : formatUsd(pos.valueQuote)}
                      </td>
                      <td className={`${TDR} text-[var(--color-text-muted)]`}>
                        {pos.feesQuote === null ? "—" : feeAmount(pos.feesQuote)}
                      </td>
                      <td className={TDR} style={{ color: pnlColor(pos.pnl) }}>
                        {formatPnl(pos.pnl)}
                      </td>
                      <td className="px-2 text-right">
                        <ArrowRight className="h-3.5 w-3.5 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100" />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
