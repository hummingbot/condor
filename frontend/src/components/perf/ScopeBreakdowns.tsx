import { useMemo } from "react";

import type { FloorBucket } from "@/components/agent/floor/floor";
import {
  formatCurrencyPnl,
  formatCurrencyVolume,
  formatConnectorName,
  pnlTextClass,
} from "@/lib/formatters";

/**
 * The scope's records, cut two other ways (FEAT-112, rehosted by FEAT-116).
 *
 * By instrument and by venue — two questions the scope tree cannot answer,
 * because they cut *across* whatever level the reader is on: what is this scope
 * actually holding, and where. It was the floor's, over the whole fleet; it is
 * the band's third entry now, at any scope, because the question does not stop
 * being asked one click into the tree.
 *
 * Both are slices of the one accounting spine — `scope.leaves` — folded by the
 * same `foldLeaves` with the same `ConvertQuote` as the KPI tiles above, so
 * `Σ buckets == the scope's own fold` by construction. That is what lets a
 * share be printed as a share: it is a fraction of a whole this scope actually
 * holds, not of a subtotal.
 *
 * Exposure is signed, and the sign is the most important thing about it, so it
 * is drawn as a bar diverging from a centre line rather than as a number in a
 * column. It comes from `positionQuoteValue` over each leaf's
 * `positions_summary` — not from a `side` field, which the leaf does not carry
 * and which a controller (a bag of both sides) could not answer anyway.
 *
 * **What nothing in this app measures is named at the foot of the band**, where
 * it is read next to the numbers it qualifies rather than as an empty panel,
 * which reads as a number still loading.
 */
export function ScopeBreakdowns({
  byPair,
  byVenue,
  symbol,
}: {
  byPair: FloorBucket[];
  byVenue: FloorBucket[];
  symbol: string;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin border-t border-[var(--color-border)]/60 p-3">
      <div className="grid gap-3 md:grid-cols-2">
        <Breakdown
          title="By instrument"
          test="pair"
          buckets={byPair}
          symbol={symbol}
          caption="Signed exposure from each record's open positions."
        />
        <Breakdown
          title="By venue"
          test="venue"
          buckets={byVenue}
          symbol={symbol}
          label={formatConnectorName}
          caption="Leverage and margin health are not measured — no route reports them."
        />
      </div>

      {/* The four subjects this app has no record of, said once, under the
          numbers they qualify. `leafFromController` hardcodes `fees: 0`, so the
          Fees tile above is a floor and says so in its own title; the other
          three have no source at all — there is no accounts or orders route,
          and no record names an account. */}
      <p
        data-not-measured
        className="mt-3 text-[10px] leading-relaxed text-[var(--color-text-muted)]"
      >
        Not measured here: margin, leverage and account health; live orders and
        fill-level flow; sub-accounts; and fees, which are executor-only and
        therefore a floor. Condor's records carry none of them, so nothing on
        this screen stands in for them.
      </p>
    </div>
  );
}

function Breakdown({
  title,
  test,
  buckets,
  symbol,
  caption,
  label = (value: string) => value,
}: {
  title: string;
  test: string;
  buckets: FloorBucket[];
  symbol: string;
  caption: string;
  label?: (value: string) => string;
}) {
  // Ranked by what the scope is most exposed to. `groupSpine` emits buckets in
  // the spine's own order, which is the order the records arrived in — an order
  // about nothing. Sorted here rather than at the call site, because it is a
  // reading order and not a property of the fold.
  const ranked = useMemo(
    () => [...buckets].sort((a, b) => Math.abs(b.exposure) - Math.abs(a.exposure)),
    [buckets],
  );
  // The widest bar is the scale, so a scope whose whole book is one instrument
  // still shows a full bar rather than a sliver measured against a notional
  // maximum nobody chose.
  const widest = ranked.reduce((max, b) => Math.max(max, Math.abs(b.exposure)), 0);
  const gross = ranked.reduce((sum, b) => sum + Math.abs(b.exposure), 0);

  return (
    <section
      data-breakdown={test}
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
    >
      <header className="flex items-baseline justify-between border-b border-[var(--color-border)] px-3 py-1.5">
        <h2 className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          {title}
        </h2>
        <span className="text-[10px] text-[var(--color-text-muted)]">
          {ranked.length}
        </span>
      </header>

      {ranked.length === 0 ? (
        <p className="px-3 py-4 text-center text-xs text-[var(--color-text-muted)]">
          Nothing to fold yet.
        </p>
      ) : (
        <ul className="divide-y divide-[var(--color-border)]">
          {ranked.map((bucket) => (
            <li
              key={bucket.key}
              data-bucket={bucket.key}
              className="grid grid-cols-[1fr_auto] items-center gap-x-3 px-3 py-1.5"
            >
              <div className="min-w-0">
                <p className="truncate font-mono text-xs">{label(bucket.label)}</p>
                <ExposureBar value={bucket.exposure} widest={widest} />
              </div>
              <div className="text-right">
                <span
                  data-bucket-net
                  className={`block font-mono text-xs font-semibold ${pnlTextClass(
                    bucket.totals.net,
                  )}`}
                >
                  {formatCurrencyPnl(bucket.totals.net, symbol)}
                </span>
                <span className="block font-mono text-[10px] text-[var(--color-text-muted)]">
                  {formatCurrencyVolume(bucket.totals.volume, symbol)} vol
                  {gross > 0 && (
                    <>
                      {" · "}
                      {Math.round((Math.abs(bucket.exposure) / gross) * 100)}% of gross
                    </>
                  )}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="border-t border-[var(--color-border)] px-3 py-1 text-[10px] text-[var(--color-text-muted)]">
        {caption}
      </p>
    </section>
  );
}

/**
 * A signed exposure, diverging from a centre line.
 *
 * Long grows right and short grows left from the same centre, so a book that is
 * net short is legible as short at a glance — which a bar starting at the left
 * edge, however coloured, is not.
 */
function ExposureBar({ value, widest }: { value: number; widest: number }) {
  const width = widest > 0 ? (Math.abs(value) / widest) * 50 : 0;
  const long = value >= 0;
  return (
    <div className="relative mt-0.5 h-1.5 w-full rounded-sm bg-[var(--color-bg)]">
      <span
        aria-hidden="true"
        className="absolute inset-y-0 left-1/2 w-px bg-[var(--color-border)]"
      />
      <span
        data-exposure={long ? "long" : "short"}
        className={`absolute inset-y-0 rounded-sm ${
          long ? "bg-[var(--color-green)]" : "bg-[var(--color-red)]"
        }`}
        style={
          long
            ? { left: "50%", width: `${width}%` }
            : { right: "50%", width: `${width}%` }
        }
      />
    </div>
  );
}
