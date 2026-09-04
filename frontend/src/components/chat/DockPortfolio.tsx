import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useRates } from "@/hooks/useRates";
import { api } from "@/lib/api";
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

/**
 * The quote the totals are already in, asked for on its own behalf.
 *
 * Every figure this panel prints is a USD aggregate run through `convert`, so
 * USDT is the one rate it needs — and asking for it explicitly is what keeps a
 * BRL or EUR reader from being shown dollar totals under their own symbol.
 */
const TOTAL_QUOTES = ["USDT"];

/**
 * How many lines of the breakdown get a name, a colour and a segment; the rest
 * fold into one "Other".
 *
 * Four because there are four colours. `--chart-series-1..4` is the only
 * palette in this app validated for CVD separation and contrast, and the rule
 * recorded beside it in `index.css` — never reordered, never extended by
 * generating a hue — is what stops a fifth token from inventing one. A desk
 * with twenty tokens on it is a desk for `/portfolio`, which the footer opens.
 */
const MAX_SLICES = 4;

/** The four, in order — the same palette identity is drawn from elsewhere. */
const SLICE_COLORS = [
  "var(--chart-series-1)",
  "var(--chart-series-2)",
  "var(--chart-series-3)",
  "var(--chart-series-4)",
];

/** Everything past the fourth, drawn as one segment nobody has to decode. */
const OTHER_COLOR = "var(--color-text-muted)";

/** Which breakdown the panel is showing: what you hold, or where it sits. */
type Split = "asset" | "venue";

const SPLITS: { id: Split; label: string; hint: string }[] = [
  { id: "asset", label: "By asset", hint: "What you hold, summed across venues" },
  { id: "venue", label: "By venue", hint: "Where it sits, summed across tokens" },
];

/** One line of the breakdown, and one segment of the bar above it. */
type Slice = { key: string; label: string; usd: number; color: string };

/**
 * The breakdown, capped at the palette and with the tail folded into "Other".
 *
 * The fold is not a truncation: the segment carries the tail's whole value, so
 * the bar still adds up to the desk and a reader is never shown a distribution
 * with a piece quietly missing from it. Its label says how many lines it stands
 * for, which is the number that decides whether opening `/portfolio` is worth
 * it.
 */
function toSlices(entries: Omit<Slice, "color">[]): Slice[] {
  const head = entries
    .slice(0, MAX_SLICES)
    .map((e, i) => ({ ...e, color: SLICE_COLORS[i] }));
  const tail = entries.slice(MAX_SLICES);
  if (!tail.length) return head;
  return [
    ...head,
    {
      key: "__other",
      label: `Other · ${tail.length}`,
      usd: tail.reduce((sum, e) => sum + e.usd, 0),
      color: OTHER_COLOR,
    },
  ];
}

/**
 * What you own on the server this conversation trades on.
 *
 * A reader of `/portfolio`'s caches, never their owner: the same two query
 * keys that page uses, so a user with it warm pays nothing to open this, and
 * deliberately *not* the forced `getPortfolio(server, true)` warm-up it runs on
 * mount — that call walks every connector, and a panel is not the place to make
 * the server do it.
 *
 * Mounted only while the section is open (see `DockSection`), which is the
 * whole of the `enabled` gate: closed, this file's queries do not exist.
 *
 * No time series. The questions a panel beside a chat answers are "how much do
 * I have", "is it up or down" and "what is it in" — the shape of the *day* is
 * what the page is for, and it is one click away in the footer.
 *
 * **No open positions either.** They were here, three of them, under the
 * breakdown; they are gone because this panel answers what the desk *is worth*
 * and a position is what the desk is *doing* — which is the other panel's
 * subject, at controller granularity, with the executor counts and the PnL
 * split that make a hold mean something. Two half-answers to "what am I in"
 * in one column is worse than one whole answer in each.
 */
export function DockPortfolio({ server }: { server: string }) {
  const navigate = useNavigate();
  const [split, setSplit] = useState<Split>("asset");

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

  const { convert, currencySymbol } = useRates(TOTAL_QUOTES);
  const fromUsd = (val: number) => convert(val, "USDT").value;

  const connectors = useMemo(
    () => [...(data?.connectors ?? [])].sort((a, b) => b.total_usd - a.total_usd),
    [data],
  );

  /**
   * What you hold, by token rather than by venue.
   *
   * Summed across connectors, because a token is one exposure however many
   * desks it is spread over: USDC on two exchanges is one answer to "how much
   * dry powder do I have", and reporting it as two lines is the venue question
   * wearing the asset question's clothes. Zero and dust rows are dropped — a
   * connector reports every token it has ever seen, and a distribution whose
   * bottom half rounds to 0% is a list, not a distribution.
   */
  const assets = useMemo(() => {
    const byToken = new Map<string, number>();
    for (const c of data?.connectors ?? [])
      for (const b of c.balances ?? [])
        byToken.set(b.token, (byToken.get(b.token) ?? 0) + (b.usd_value ?? 0));
    return [...byToken.entries()]
      .map(([token, usd]) => ({ key: token, label: token, usd }))
      .filter((a) => a.usd > 0)
      .sort((a, b) => b.usd - a.usd);
  }, [data]);

  /**
   * Which breakdown is actually drawn.
   *
   * A connector that reports a total and no per-token balances — a DEX wallet
   * behind a gateway that only answers in aggregate — would leave "By asset"
   * on an empty column, and a control that hides everything is worse than one
   * that widens, so it falls back to the venue split rather than showing
   * nothing. The reader's own pick still stands wherever it can be honoured.
   */
  const effectiveSplit: Split =
    split === "asset" && !assets.length ? "venue" : split;

  const slices = useMemo(
    () =>
      toSlices(
        effectiveSplit === "asset"
          ? assets
          : connectors.map((c) => ({
              key: c.connector,
              label: formatConnectorName(c.connector),
              usd: c.total_usd,
            })),
      ),
    [effectiveSplit, assets, connectors],
  );

  /**
   * The denominator the bar and the percentages share.
   *
   * The slices' own sum, not `total_usd`: they are within float drift of each
   * other, and taking each from its own base is how a bar whose segments fill
   * the track ends up beside four numbers that add to 99%. One base means the
   * two can only ever agree.
   */
  const shown = useMemo(
    () => slices.reduce((sum, sl) => sum + sl.usd, 0),
    [slices],
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

      {/* How the total is made up, and of what. "By asset" is the default
          because it is the question the total itself raises — what am I
          holding — while the venue split answers where it sits, which only
          matters once you want to move some of it. */}
      <div className="flex items-center gap-1 px-3 pb-1">
        {SPLITS.map(({ id, label, hint }) => (
          <button
            key={id}
            type="button"
            onClick={() => setSplit(id)}
            aria-pressed={effectiveSplit === id}
            title={hint}
            className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wider transition-colors ${
              effectiveSplit === id
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* The distribution as one bar: a share is read off a width in a glance
          and out of four percentages only by comparing them in your head. It
          is decoration over the rows, never instead of them — the same figures
          are printed underneath, which is what keeps it readable without
          colour. */}
      {shown > 0 && (
        <div className="px-3 pb-1.5" aria-hidden="true">
          <div className="flex h-1.5 gap-px overflow-hidden rounded-full">
            {slices.map((sl) => (
              <span
                key={sl.key}
                className="h-full first:rounded-l-full last:rounded-r-full"
                style={{
                  width: `${(sl.usd / shown) * 100}%`,
                  backgroundColor: sl.color,
                }}
              />
            ))}
          </div>
        </div>
      )}

      <ul className="px-1 pb-1" data-testid="portfolio-breakdown">
        {slices.map((sl) => (
          <li
            key={sl.key}
            className="flex items-center gap-2 rounded px-2 py-0.5 text-[11px]"
          >
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: sl.color }}
            />
            <span className="min-w-0 flex-1 truncate">{sl.label}</span>
            <span className="shrink-0 font-mono tabular-nums">
              {formatCurrency(fromUsd(sl.usd), currencySymbol)}
            </span>
            <span className="w-9 shrink-0 text-right font-mono tabular-nums text-[var(--color-text-muted)]">
              {Math.round((sl.usd / shown) * 100)}%
            </span>
          </li>
        ))}
      </ul>

      {footer}
    </div>
  );
}
