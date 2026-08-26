/**
 * What the app can say about itself without asking the user.
 *
 * A report is only as good as the state it describes, and the person filing it
 * should not have to know which server was selected, which query had failed, or
 * what the route was called. All of that is already in the app — this module
 * reads it out of the router path, the preference stores, the query cache and
 * the socket, so the dialog can hand it over pre-written.
 */

import { queryClient } from "@/lib/queryClient";
import { socketStatus } from "@/lib/shared-socket";

/**
 * The areas the issue templates offer. Kept identical, string for string, to
 * the `area` dropdown options in `.github/ISSUE_TEMPLATE/*.yml`: GitHub matches
 * a prefilled dropdown by its label, so a rewording on either side has to
 * happen on both.
 */
export const AREAS = [
  "Agents & chat",
  "Portfolio",
  "Bots & controllers",
  "Trading & executors",
  "Routines & reports",
  "Settings & connections",
  "Dashboard (other)",
  "Telegram bot",
  "Gateway / DEX",
  "Not sure",
] as const;

export type Area = (typeof AREAS)[number];

export interface PageContext {
  /** Human name of the page, with the id it was showing. */
  page: string;
  /** The template area this page belongs to — the dialog's default. */
  area: Area;
  route: string;
}

/** Route → page name, area, and whatever the path segments identify. */
export function describePage(pathname: string, search = ""): PageContext {
  const [, first = "", second = "", third = "", fourth = ""] = pathname.split("/");
  const tab = new URLSearchParams(search).get("tab");
  const withTab = (name: string) => (tab ? `${name} · tab "${tab}"` : name);
  const route = pathname + search;

  const of = (page: string, area: Area): PageContext => ({ page, area, route });

  switch (first) {
    case "":
      return of("Chat workspace", "Agents & chat");
    // Only the two-and-three-segment agent routes are real: `/agents` itself is
    // a `<Navigate to="/">` in App.tsx, so it redirects before any consumer of
    // this module reads the location, and a bare-`/agents` branch here would be
    // dead code. The same is true of `/reports`, `/backtest`, `/archived`,
    // `/market` and `/executors/new*` — none of them gets a case.
    case "agents":
      if (second && third === "strategies" && fourth) {
        return of(`Strategy detail (agent "${second}", strategy "${fourth}")`, "Agents & chat");
      }
      return of(`Agent detail ("${second}")`, "Agents & chat");
    case "portfolio":
      return of(withTab("Portfolio"), "Portfolio");
    case "bots":
      return second
        ? of(`Bot detail ("${second}")`, "Bots & controllers")
        : of(withTab("Bots"), "Bots & controllers");
    case "trade":
      return of(withTab("Trade"), "Trading & executors");
    // Mirrors the `/dex` and `/dex/:network/:address` entries `lib/pageFacts.ts`
    // already carries, labels included, so a report and its facts agree.
    case "dex":
      return second && third
        ? of(`DEX pool ("${third}" on "${second}")`, "Gateway / DEX")
        : of(withTab("DEX pools"), "Gateway / DEX");
    case "executors":
      return of(withTab("Executors"), "Trading & executors");
    case "routines":
      return of(withTab("Routines"), "Routines & reports");
    case "settings":
      return of(withTab("Settings"), "Settings & connections");
    default:
      return of(pathname, "Dashboard (other)");
  }
}

/**
 * The requests that are currently failing, newest first.
 *
 * This is the half of a bug report users cannot write themselves: "the page is
 * empty" and "`portfolio/history` is 502-ing" are the same event, and only the
 * second one is actionable. Query keys are read as written — they name a
 * server or a bot, which is the user's own data, and the block is shown to
 * them before it goes anywhere.
 */
export function failingRequests(limit = 5): string[] {
  try {
    return queryClient
      .getQueryCache()
      .findAll()
      .filter((q) => q.state.status === "error")
      .sort((a, b) => (b.state.errorUpdatedAt ?? 0) - (a.state.errorUpdatedAt ?? 0))
      .slice(0, limit)
      .map((q) => {
        const key = q.queryKey
          .map((part) => (typeof part === "object" ? JSON.stringify(part) : String(part)))
          .join(" / ");
        const message = (q.state.error as Error | null)?.message ?? "unknown error";
        return `${key} → ${message}`;
      });
  } catch {
    // Diagnostics must never be the thing that breaks the report dialog.
    return [];
  }
}

/** Preferences that change what the page renders — and so what "wrong" means. */
export function displayPrefs(): string {
  const theme = localStorage.getItem("condor_theme") ?? "system";
  const currency = localStorage.getItem("condor_display_currency") ?? "USDT";
  return `theme ${theme} · currency ${currency}`;
}

/** Live-data health. A stale dashboard is usually a closed socket. */
export function liveDataStatus(): string {
  return socketStatus();
}
