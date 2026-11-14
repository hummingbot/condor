import { Link } from "react-router-dom";

import { useGatewayStatus } from "@/hooks/useGatewayStatus";
import { useServer } from "@/hooks/useServer";

/**
 * Gateway's health, in the top bar beside the server it belongs to.
 *
 * Every DEX surface depends on Gateway, and its state used to be visible only
 * under Settings → Gateway: with it stopped, the server dot stayed green and
 * the DEX pages failed with messages that blamed the wrong cause. Down, this
 * is a red pill that goes straight to where it is started; up, a quiet dot.
 *
 * Reads `useGatewayStatus`, the cache entry Settings and the DEX pages share,
 * so this adds no request of its own.
 */
export function GatewayIndicator() {
  const { server } = useServer();
  const { known, running, unreachable: isError } = useGatewayStatus(server);

  // Nothing to say until there is an answer — a server still loading is not
  // a server with Gateway down.
  if (!known) return null;

  if (running) {
    return (
      <span
        className="hidden items-center gap-1.5 text-xs font-medium text-[var(--color-text-muted)] md:inline-flex"
        title="Gateway is running — DEX trading and liquidity are available"
      >
        <span className="h-2 w-2 rounded-full bg-[var(--color-green)]" />
        <span className="hidden xl:inline">Gateway</span>
      </span>
    );
  }

  return (
    <Link
      to="/settings?tab=gateway"
      title={
        isError
          ? "Could not reach Gateway's status — open Gateway settings"
          : "Gateway is stopped — DEX trading and liquidity are unavailable. Open Gateway settings to start it."
      }
      className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-red)]/30 bg-[var(--color-red)]/10 px-2.5 py-1 text-xs font-medium text-[var(--color-red)] transition-colors hover:bg-[var(--color-red)]/15"
    >
      <span className="h-2 w-2 rounded-full bg-current" />
      {isError ? "Gateway status?" : "Gateway off"}
    </Link>
  );
}
