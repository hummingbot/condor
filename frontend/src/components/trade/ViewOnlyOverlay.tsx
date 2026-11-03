import { KeyRound } from "lucide-react";
import { Link } from "react-router-dom";

import { formatConnectorName } from "@/lib/formatters";

/**
 * What the Execute panel shows on a venue Condor can read but not trade (ARCH-272).
 *
 * The trade surface now offers every venue whose market endpoints answer, not
 * just the ones the account holds keys on, so most of the list is chartable and
 * unexecutable. Blurring only the Execute panel is the whole point: the chart,
 * the ticker, the market browser, the favourites strip and the Data/depth tab
 * behind this are all live and stay usable — the single thing missing is an
 * account to place the order against, and that is the single thing covered.
 *
 * `ConnectKeysOverlay` is the page-sized sibling for "no venues at all"; this one
 * is scoped to one panel and names the venue, because the fix is per-venue.
 */
export function ViewOnlyOverlay({ connector }: { connector: string }) {
  return (
    <div className="absolute inset-0 z-20 flex items-center justify-center">
      <div className="absolute inset-0 bg-[var(--color-bg)]/60 backdrop-blur-sm" />

      <div className="relative z-10 mx-4 flex flex-col items-center rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 text-center shadow-xl shadow-black/20">
        <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--color-primary)]/10 ring-1 ring-[var(--color-primary)]/20">
          <KeyRound className="h-5 w-5 text-[var(--color-primary)]" />
        </div>

        <h3 className="mb-1.5 text-sm font-bold text-[var(--color-text)]">
          View only — no API keys for {formatConnectorName(connector)}
        </h3>
        <p className="mb-4 max-w-[15rem] text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          Charts, order book and market data are live. Add keys to trade here.
        </p>

        <Link
          to="/settings?tab=keys"
          className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-xs font-semibold text-white transition-all hover:bg-[var(--color-primary-hover)] active:scale-[0.98]"
        >
          Add API keys
        </Link>
      </div>
    </div>
  );
}
