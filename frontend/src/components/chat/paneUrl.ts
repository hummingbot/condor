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

import { clearWorkspaceSearch } from "@/components/agent/workspace/workspaceUrl";
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
 * `deployed` is the conversation's own ledger (FEAT-110) — what this chat put
 * into the world. A pane member rather than a section of the context dock
 * because it is a table you read, not a list you watch out of the corner of an
 * eye while typing, and because `?panel=deployed` is what makes it linkable and
 * closable by Back.
 *
 * `agent` carries an optional slug since FEAT-114. The pane's subject used to
 * be the conversation's agent and nothing else, so an Execution row naming a
 * *different* agent had nowhere to open it; the slug is what gives that row a
 * destination. Absent still means the conversation's, which is what every link
 * already written to `?panel=agent` means.
 *
 * A strategy is a member rather than a sheet stacked on the agent panel: two
 * sheets portalled into one pane stack with no way to tell which scrollbar
 * belongs to what (see `WorkspaceSheet`'s `taken`). So the strategy *replaces*
 * the panel and closing it puts the panel back — which is why it carries the
 * agent slug it was opened from.
 */
export type PaneView =
  | { kind: "agent"; slug?: string }
  | { kind: "desk" }
  | { kind: "deployed" }
  | { kind: "routines"; focus: LibraryFocus }
  | { kind: "strategy"; agentSlug: string; strategySlug: string }
  | null;

export const PANEL_PARAM = "panel";
/** `{agentSlug}/{strategySlug}` — the strategy sheet's whole address. */
export const LOOP_PARAM = "loop";
/**
 * Whose agent panel is in the pane, when it is not the conversation's own
 * (FEAT-114) — the Execution panel's agent rows open a *different* agent.
 *
 * Spelled `who` and not `agent`, which is the obvious name and is taken: `/`
 * already reads `?agent=<slug>` as *start or focus a conversation with this
 * agent* and strips it a tick later (see `AgentChatTab`), so writing the pane's
 * subject there would spawn a chat and then erase itself.
 *
 * Written only when the slug differs from the conversation's, so every link
 * ever made to a bare `?panel=agent` keeps meaning "the agent I am talking to".
 */
export const AGENT_PARAM = "who";

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
    case "agent": {
      // `undefined` rather than `""`: a bare `?panel=agent` is not a request
      // for a nameless agent, it is the conversation's own, and the page
      // resolves it that way.
      const slug = params.get(AGENT_PARAM) || "";
      return slug ? { kind: "agent", slug } : { kind: "agent" };
    }
    case "desk":
      return { kind: "desk" };
    case "deployed":
      return { kind: "deployed" };
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

/**
 * The query string with this pane in it — or with every trace of one gone.
 *
 * "Every trace" includes the agent panel's *contents* since FEAT-117: the panel
 * is the whole workspace now, so `?view=`, `?strategy=`, `?run=` and `?tick=`
 * are as much a part of what is open as `?panel=` is. They are kept only while
 * the pane goes on showing the same agent — closing it, or opening a different
 * agent, leaves a scope and a run that belong to somebody else, and a Back
 * through them would restore a pane nobody asked for.
 */
export function writePane(
  params: URLSearchParams,
  pane: PaneView,
): URLSearchParams {
  const sameAgent =
    pane?.kind === "agent" &&
    params.get(PANEL_PARAM) === "agent" &&
    (pane.slug ?? "") === (params.get(AGENT_PARAM) ?? "");
  const next = sameAgent
    ? new URLSearchParams(params)
    : clearWorkspaceSearch(params);
  if (!pane) {
    next.delete(PANEL_PARAM);
    next.delete(LOOP_PARAM);
    next.delete(AGENT_PARAM);
    return next;
  }
  next.set(PANEL_PARAM, pane.kind);
  if (pane.kind === "strategy") {
    next.set(LOOP_PARAM, `${pane.agentSlug}/${pane.strategySlug}`);
  } else {
    next.delete(LOOP_PARAM);
  }
  if (pane.kind === "agent" && pane.slug) next.set(AGENT_PARAM, pane.slug);
  else next.delete(AGENT_PARAM);
  return next;
}
