import type { FloorBucket, FloorModel } from "@/components/agent/floor/floor";
import {
  formatCurrencyPnl,
  formatCurrencyVolume,
  formatConnectorName,
  pnlTextClass,
} from "@/lib/formatters";

/**
 * The same records, cut two other ways (FEAT-112).
 *
 * By instrument and by venue — two questions the per-agent rows cannot answer
 * because they cut *across* every agent at once: what is the fleet actually
 * holding, and where.
 *
 * Both are slices of the one accounting spine, folded by the same `foldLeaves`
 * with the same `ConvertQuote`, so `Σ buckets == the fleet fold` by
 * construction. That is what lets a share be printed as a share: it is a
 * fraction of a whole this page actually holds, not of a subtotal.
 *
 * Exposure is signed, and the sign is the most important thing about it, so it
 * is drawn as a bar diverging from a centre line rather than as a number in a
 * column. It comes from `positionQuoteValue` over each leaf's
 * `positions_summary` — not from a `side` field, which the leaf does not carry
 * and which a controller (a bag of both sides) could not answer anyway.
 *
 * **Leverage and margin health are absent and captioned as absent.** There is
 * no accounts or margin route in this app; an empty panel would read as a
 * number still loading.
 */
export function FloorBreakdowns({ model }: { model: FloorModel }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Breakdown
        title="By instrument"
        test="pair"
        buckets={model.byPair}
        symbol={model.symbol}
        caption="Signed exposure from each record's open positions."
      />
      <Breakdown
        title="By venue"
        test="venue"
        buckets={model.byVenue}
        symbol={model.symbol}
        label={formatConnectorName}
        caption="Leverage and margin health are not measured — no route reports them."
      />
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
  // The widest bar is the scale, so a fleet whose whole book is one instrument
  // still shows a full bar rather than a sliver measured against a notional
  // maximum nobody chose.
  const widest = buckets.reduce((max, b) => Math.max(max, Math.abs(b.exposure)), 0);
  const gross = buckets.reduce((sum, b) => sum + Math.abs(b.exposure), 0);

  return (
    <section
      data-floor-breakdown={test}
      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]"
    >
      <header className="flex items-baseline justify-between border-b border-[var(--color-border)] px-3 py-1.5">
        <h2 className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          {title}
        </h2>
        <span className="text-[10px] text-[var(--color-text-muted)]">
          {buckets.length} of {buckets.length}
        </span>
      </header>

      {buckets.length === 0 ? (
        <p className="px-3 py-4 text-center text-xs text-[var(--color-text-muted)]">
          Nothing to fold yet.
        </p>
      ) : (
        <ul className="divide-y divide-[var(--color-border)]">
          {buckets.map((bucket) => (
            <li
              key={bucket.key}
              data-floor-bucket={bucket.key}
              className="grid grid-cols-[1fr_auto] items-center gap-x-3 px-3 py-1.5"
            >
              <div className="min-w-0">
                <p className="truncate font-mono text-xs">{label(bucket.label)}</p>
                <ExposureBar value={bucket.exposure} widest={widest} />
              </div>
              <div className="text-right">
                <span
                  data-floor-bucket-net
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
        data-floor-exposure={long ? "long" : "short"}
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
