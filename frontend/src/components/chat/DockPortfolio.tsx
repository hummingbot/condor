import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { useRates } from "@/hooks/useRates";
import { api, type ConsolidatedPosition } from "@/lib/api";
import {
  formatConnectorName,
  formatCurrency,
  formatCurrencyPnl,
  pnlColor,
} from "@/lib/formatters";

/**
 * The window the change beside the total is measured over.
 *
 * `"1D"` and not `"24h"`: it is the literal `/portfolio` sends as its `range`
 * and therefore the literal its cache entry is keyed on, so a reader who has
 * the page open on 1D pays nothing to open this panel. A prettier spelling here
 * would be a second cache entry for the same answer.
 */
const DAY_RANGE = "1D";

/** How many connector lines fit before the panel stops being a glance. */
const MAX_CONNECTORS = 4;
/** How many holds, likewise — the largest are the ones worth a look. */
const MAX_POSITIONS = 3;

/** The quote a hold's numbers are denominated in — its pair's, as elsewhere. */
function quoteOf(pair: string): string {
  return pair.split("-")[1] || "USDT";
}

/** LONG/SHORT as the API named it; an empty side is not guessed at. */
function sideLabel(side: string): string {
  return (side ?? "").split(".").pop()!.toUpperCase();
}

/**
 * What you own on the server this conversation trades on.
 *
 * A reader of `/portfolio`'s caches, never their owner: the same three query
 * keys that page uses, so a user with it warm pays nothing to open this, and
 * deliberately *not* the forced `getPortfolio(server, true)` warm-up it runs on
 * mount — that call walks every connector, and a panel is not the place to make
 * the server do it.
 *
 * Mounted only while the section is open (see `DockSection`), which is the
 * whole of the `enabled` gate: closed, this file's three queries do not exist.
 *
 * No chart. The question a panel beside a chat answers is "how much do I have,
 * and is it up or down" — the shape of the day is what the page is for.
 */
export function DockPortfolio({ server }: { server: string }) {
  const navigate = useNavigate();

  const { data, isLoading, error } = useQuery({
    queryKey: ["portfolio", server],
    queryFn: () => api.getPortfolio(server),
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });

  const { data: history } = useQuery({
    queryKey: ["portfolio-history", server, DAY_RANGE],
    queryFn: () => api.getPortfolioHistory(server, DAY_RANGE),
    refetchInterval: 60_000,
  });

  const { data: positionsData } = useQuery({
    queryKey: ["consolidated-positions", server],
    queryFn: () => api.getConsolidatedPositions(server),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  });

  const holds = useMemo<ConsolidatedPosition[]>(
    () => [
      ...(positionsData?.executor_positions ?? []),
      ...(positionsData?.bot_positions ?? []),
    ],
    [positionsData],
  );

  // A hold's PnL is in its pair's quote, so the rates it needs are the pairs' —
  // `useRates` dedupes and skips stablecoin pairs, so on a USDT desk this asks
  // for nothing the page has not already asked for.
  const quotes = useMemo(
    () => holds.map((h) => quoteOf(h.trading_pair)),
    [holds],
  );
  const { convert, currencySymbol } = useRates(quotes);
  const fromUsd = (val: number) => convert(val, "USDT").value;

  const connectors = useMemo(
    () => [...(data?.connectors ?? [])].sort((a, b) => b.total_usd - a.total_usd),
    [data],
  );

  /**
   * The day's change, absolute and relative.
   *
   * `null` rather than zero whenever the history cannot answer — a single
   * point, or a first point of zero that no percentage can be taken against.
   * A total with a "+0.00%" beside it that means "we don't know" is worse than
   * a total on its own.
   */
  const change = useMemo(() => {
    const points = history?.points ?? [];
    if (points.length < 2) return null;
    const first = points[0].total_usd;
    const last = points[points.length - 1].total_usd;
    return { abs: last - first, pct: first > 0 ? ((last - first) / first) * 100 : null };
  }, [history]);

  // Biggest first, and comparable across pairs only once every notional is in
  // one currency — the rule the positions tab sorts by.
  const biggest = useMemo(
    () =>
      [...holds]
        .sort(
          (a, b) =>
            Math.abs(convert(b.notional_value ?? 0, quoteOf(b.trading_pair)).value) -
            Math.abs(convert(a.notional_value ?? 0, quoteOf(a.trading_pair)).value),
        )
        .slice(0, MAX_POSITIONS),
    [holds, convert],
  );

  const unrealised = useMemo(
    () =>
      holds.reduce(
        (sum, h) => sum + convert(h.unrealized_pnl ?? 0, quoteOf(h.trading_pair)).value,
        0,
      ),
    [holds, convert],
  );

  const footer = (
    <button
      type="button"
      onClick={() => navigate("/portfolio")}
      className="flex w-full items-center gap-1 px-3 py-1.5 text-left text-[11px] text-[var(--color-primary)] transition-colors hover:bg-[var(--color-surface-hover)]"
    >
      Open portfolio
      <ArrowRight className="h-3 w-3" />
    </button>
  );

  if (error) {
    return (
      <div className="flex flex-col">
        <p className="px-3 py-2 text-[11px] text-[var(--color-red)]">
          Could not read {server}&apos;s portfolio.
        </p>
        {footer}
      </div>
    );
  }

  if (isLoading && !data) {
    return (
      <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
        Reading {server}…
      </p>
    );
  }

  // No connector has ever reported: an empty desk and a desk whose keys are
  // missing look identical from here, so it says which it cannot tell.
  if (!connectors.length) {
    return (
      <div className="flex flex-col">
        <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
          No balances on {server}. Add exchange credentials and they appear here.
        </p>
        {footer}
      </div>
    );
  }

  const total = data?.total_usd ?? 0;

  return (
    <div className="flex flex-col">
      {/* The total, and the only thing that makes a total judgeable beside it. */}
      <div className="flex items-baseline gap-2 px-3 pb-1 pt-1.5">
        <span className="font-mono text-lg tabular-nums">
          {formatCurrency(fromUsd(total), currencySymbol)}
        </span>
        {change ? (
          <span
            className="font-mono text-[11px] tabular-nums"
            style={{ color: pnlColor(change.abs) }}
            title="Change over the last 24 hours"
          >
            {formatCurrencyPnl(fromUsd(change.abs), currencySymbol)}
            {change.pct !== null &&
              ` (${change.pct >= 0 ? "+" : ""}${change.pct.toFixed(2)}%)`}
          </span>
        ) : (
          <span
            className="text-[11px] text-[var(--color-text-muted)]"
            title="Not enough history on this server to measure a day's change"
          >
            24h unknown
          </span>
        )}
      </div>

      <ul className="px-1 pb-1">
        {connectors.slice(0, MAX_CONNECTORS).map((c) => (
          <li
            key={c.connector}
            className="flex items-baseline gap-2 rounded px-2 py-0.5 text-[11px]"
          >
            <span className="min-w-0 flex-1 truncate">
              {formatConnectorName(c.connector)}
            </span>
            <span className="shrink-0 font-mono tabular-nums">
              {formatCurrency(fromUsd(c.total_usd), currencySymbol)}
            </span>
            <span className="w-9 shrink-0 text-right tabular-nums text-[var(--color-text-muted)]">
              {total > 0 ? `${Math.round((c.total_usd / total) * 100)}%` : "—"}
            </span>
          </li>
        ))}
        {connectors.length > MAX_CONNECTORS && (
          <li className="px-2 py-0.5 text-[10px] text-[var(--color-text-muted)]">
            +{connectors.length - MAX_CONNECTORS} more
          </li>
        )}
      </ul>

      {holds.length > 0 && (
        <>
          <div className="flex items-baseline gap-2 border-t border-[var(--color-border)] px-3 pb-0.5 pt-1.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
            <span className="min-w-0 flex-1 truncate">
              Positions · {holds.length}
            </span>
            <span
              className="shrink-0 font-mono tabular-nums"
              style={{ color: pnlColor(unrealised) }}
            >
              {formatCurrencyPnl(unrealised, currencySymbol)}
            </span>
          </div>
          <ul className="px-1 pb-1">
            {biggest.map((pos, i) => {
              const quote = quoteOf(pos.trading_pair);
              const pnl = pos.unrealized_pnl ?? 0;
              const side = sideLabel(pos.position_side);
              return (
                <li key={`${pos.connector_name}-${pos.trading_pair}-${side}-${i}`}>
                  <button
                    type="button"
                    onClick={() => navigate("/portfolio?tab=positions")}
                    className="flex w-full items-baseline gap-2 rounded px-2 py-0.5 text-left text-[11px] transition-colors hover:bg-[var(--color-surface-hover)]"
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {pos.trading_pair}
                    </span>
                    {side && (
                      <span className="shrink-0 text-[10px] text-[var(--color-text-muted)]">
                        {side}
                      </span>
                    )}
                    <span
                      className="shrink-0 font-mono tabular-nums"
                      style={{ color: pnlColor(pnl) }}
                    >
                      {formatCurrencyPnl(convert(pnl, quote).value, currencySymbol)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {footer}
    </div>
  );
}
