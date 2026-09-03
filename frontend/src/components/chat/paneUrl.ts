/**
 * What is in the workspace pane, written down in the URL (FEAT-103).
 *
 * The chat's five rail tiles opened four panels that never touched the address
 * bar, so Escape was the only way out of any of them, browser Back did not close
 * one, and none could be sent to anyone — the same complaint the four agent
 * pages answered with `?view=`, one column to the left. `?panel=` is that
 * answer here.
 *
 * A pure module rather than logic inside `AgentChatTab`, for the reason
 * `workspace/views.ts` is one: the page that composes this reads several
 * parameters and none of the rules for reading them should live in JSX.
 *
 * ## What is in the URL and what is not
 *
 * *Which* panel is open is in it, and so are the two slugs of a strategy sheet
 * — a loop somebody is reading is exactly the thing they want to send. The
 * routine library's focus is not: it is set by the library's own navigation
 * (a report, a run), it changes several times a minute while somebody browses,
 * and a URL that grew a parameter per click would be a history stack nobody can
 * press Back through. It is held beside this instead, so a pasted
 * `?panel=routines` opens the library unfocused, which is where the reader
 * would have started anyway.
 */

import type { LibraryFocus } from "@/components/chat/DockRoutines";

/**
 * The pane is one column, so its occupant is one union rather than four
 * booleans: opening the agent panel puts the routine library away and vice
 * versa, and that is the shape of the state rather than a rule four components
 * have to remember (FEAT-081).
 *
 * `desk` joined it when the account panels stopped being a column of their own
 * — see `AccountDock`. The three big surfaces of this workspace are the agent,
 * the portfolio and the execution table, and they are exactly the three nobody
 * reads at the same time; making them one union is what stopped the row from
 * asking for more width than a laptop has. Which *sections* the desk is showing
 * is `useAccountPanels`', not this: this only says the desk is on.
 *
 * A strategy is a member rather than a sheet stacked on the agent panel: two
 * sheets portalled into one pane stack with no way to tell which scrollbar
 * belongs to what (see `WorkspaceSheet`'s `taken`). So the strategy *replaces*
 * the panel and closing it puts the panel back — which is why it carries the
 * agent slug it was opened from.
 */
export type PaneView =
  | { kind: "agent" }
  | { kind: "desk" }
  | { kind: "routines"; focus: LibraryFocus }
  | { kind: "strategy"; agentSlug: string; strategySlug: string }
  | null;

export const PANEL_PARAM = "panel";
/** `{agentSlug}/{strategySlug}` — the strategy sheet's whole address. */
export const LOOP_PARAM = "loop";

/**
 * Read the pane out of the query string.
 *
 * A `?panel=` nobody has, or a `strategy` with no loop to show, is not an error
 * page: it is a closed pane, the same as no parameter at all.
 */
export function readPane(
  params: URLSearchParams,
  libraryFocus: LibraryFocus,
): PaneView {
  switch (params.get(PANEL_PARAM)) {
    case "agent":
      return { kind: "agent" };
    case "desk":
      return { kind: "desk" };
    case "routines":
      return { kind: "routines", focus: libraryFocus };
    case "strategy": {
      const loop = params.get(LOOP_PARAM) ?? "";
      const slash = loop.indexOf("/");
      if (slash <= 0 || slash === loop.length - 1) return null;
      return {
        kind: "strategy",
        agentSlug: loop.slice(0, slash),
        strategySlug: loop.slice(slash + 1),
      };
    }
    default:
      return null;
  }
}

/** The query string with this pane in it — or with every trace of one gone. */
export function writePane(
  params: URLSearchParams,
  pane: PaneView,
): URLSearchParams {
  const next = new URLSearchParams(params);
  if (!pane) {
    next.delete(PANEL_PARAM);
    next.delete(LOOP_PARAM);
    return next;
  }
  next.set(PANEL_PARAM, pane.kind);
  if (pane.kind === "strategy") {
    next.set(LOOP_PARAM, `${pane.agentSlug}/${pane.strategySlug}`);
  } else {
    next.delete(LOOP_PARAM);
  }
  return next;
}
