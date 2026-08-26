import type { ViewFacts } from "./viewFacts";

/**
 * URL → what screen this is and what it is about. `null` on `/`.
 *
 * The route baseline for the chat's page context: a flat table over the
 * routes in `App.tsx`, contributing a label and a URL-derived subject through
 * the same `useViewFacts` seam every richer contributor uses. `onScreen` is
 * deliberately left empty here — what each page actually renders is the
 * page's own contribution (FEAT-060), not the router's.
 */
export function routeFacts(pathname: string, search: string): ViewFacts | null {
  // The workspace already *is* the chat; telling the agent "the user is
  // looking at a chat with you" is noise.
  if (pathname === "/") return null;

  const params = new URLSearchParams(search);
  const tab = params.get("tab") || "";

  for (const { pattern, facts } of ROUTES) {
    const m = pathname.match(pattern);
    if (m) return facts(m.slice(1).map(decode), tab);
  }
  return null;
}

function decode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

const ROUTES: {
  pattern: RegExp;
  facts: (parts: string[], tab: string) => ViewFacts;
}[] = [
  { pattern: /^\/portfolio$/, facts: () => ({ label: "Portfolio" }) },
  {
    pattern: /^\/bots$/,
    facts: (_p, tab) => ({
      label:
        tab === "backtest"
          ? "Backtests"
          : tab === "archived"
            ? "Archived bots"
            : "Bots",
    }),
  },
  {
    pattern: /^\/bots\/([^/]+)$/,
    facts: ([id]) => ({ label: "Bot detail", subject: `bot id ${id}` }),
  },
  { pattern: /^\/trade$/, facts: () => ({ label: "Trade — create executor" }) },
  { pattern: /^\/dex$/, facts: () => ({ label: "DEX pools" }) },
  {
    pattern: /^\/dex\/([^/]+)\/([^/]+)$/,
    facts: ([network, address]) => ({
      label: "DEX pool",
      subject: `pool ${address} on ${network}`,
    }),
  },
  { pattern: /^\/executors$/, facts: () => ({ label: "Executors" }) },
  {
    pattern: /^\/routines$/,
    facts: (_p, tab) => ({
      label: tab === "reports" ? "Routine reports" : "Routines",
    }),
  },
  {
    pattern: /^\/agents\/([^/]+)\/strategies\/([^/]+)$/,
    facts: ([slug, sslug]) => ({
      label: "Strategy detail",
      subject: `strategy "${sslug}" of agent "${slug}"`,
    }),
  },
  {
    pattern: /^\/agents\/([^/]+)$/,
    facts: ([slug]) => ({ label: "Agent page", subject: `agent "${slug}"` }),
  },
  { pattern: /^\/settings$/, facts: () => ({ label: "Settings" }) },
];
