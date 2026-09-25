import type { StrategyUnavailable } from "@/lib/api";

/**
 * The one line that says why a strategy's figures are missing (CORR-430), or
 * `""` when they are not — the reader's cue to render exactly as before.
 *
 * Without it every no-client state (CORR-706) reads as a session that traded
 * nothing: an empty executor table, no vitals, a run that filters to nothing.
 */
export function unavailableLabel(reason: StrategyUnavailable | undefined): string {
  switch (reason) {
    case "no_access":
      return "Server access unavailable for this strategy";
    case "unreachable":
      return "Server unreachable";
    case "no_server":
      return "No server configured";
    default:
      return "";
  }
}
