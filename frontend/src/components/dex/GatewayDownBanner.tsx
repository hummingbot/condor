import { AlertTriangle, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

/**
 * The one message a DEX page shows when Gateway is stopped.
 *
 * With Gateway down, the things that depend on it fail in ways that blame
 * something else — a token "not in Gateway's token list", a chain that "takes
 * no CLMM position", a raw 503 in the order panel. The cause is one fact, so it
 * is said once, at the top, with the way to fix it; the pages hide the
 * misleading messages while this is showing.
 */
export function GatewayDownBanner({
  detail,
  className = "",
}: {
  /** What still works and what does not, on this page. */
  detail: string;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-[var(--color-red)]/30 bg-[var(--color-red)]/10 px-4 py-2.5 text-xs ${className}`}
    >
      <AlertTriangle className="h-4 w-4 shrink-0 text-[var(--color-red)]" />
      <span className="font-semibold text-[var(--color-red)]">Gateway is stopped</span>
      <span className="text-[var(--color-text-muted)]">{detail}</span>
      <Link
        to="/settings?tab=gateway"
        className="ml-auto inline-flex items-center gap-1 rounded-md bg-[var(--color-red)] px-2.5 py-1 font-medium text-white transition-colors hover:bg-[var(--color-red)]/90"
      >
        Start Gateway
        <ArrowRight className="h-3 w-3" />
      </Link>
    </div>
  );
}
