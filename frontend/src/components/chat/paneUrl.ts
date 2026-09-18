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

import {
  isKnowledgeTab,
  type KnowledgeTabId,
} from "@/components/agent/knowledgeTabs";
import {
  isPaneSection,
  type PaneSection,
} from "@/components/agent/workspace/sections";
import { parseRunId } from "@/components/agent/lab/runs";
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
 * `agent` carries an optional slug since FEAT-114. The pane's subject used to
 * be the conversation's agent and nothing else, so an Execution row naming a
 * *different* agent had nowhere to open it; the slug is what gives that row a
 * destination. Absent still means the conversation's, which is what every link
 * already written to `?panel=agent` means.
 *
 * It carries its open section too, since FEAT-118: the panel is the agent's
 * seven Being sections again, and which one is open is a fact about the pane
 * like the slug is. It used to be `useState` inside the panel, which meant the
 * pane could not be sent, Back did not step through the sections and a reload
 * landed on Brain whatever you had been reading.
 *
 * A strategy is a member rather than a sheet stacked on the agent panel: two
 * sheets portalled into one pane stack with no way to tell which scrollbar
 * belongs to what (see `WorkspaceSheet`'s `taken`). So the strategy *replaces*
 * the panel and closing it puts the panel back — which is why it carries the
 * agent slug it was opened from.
 */
export type PaneView =
  | { kind: "agent"; slug?: string; tab?: KnowledgeTabId }
  | { kind: "desk" }
  | { kind: "routines"; focus: LibraryFocus }
  | {
      kind: "strategy";
      agentSlug: string;
      strategySlug: string;
      /** Which tab of the run screen is open — Now when absent. */
      section?: PaneSection;
      /** The run in scope, as the page spells `?run=`. */
      run?: string | null;
      /** A tick opened over the pane, as the page spells `?tick=`. */
      tick?: number | null;
    }
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
 * Which of the agent panel's seven sections is open (FEAT-118).
 *
 * The same word the agent page spells its section with — `parseWorkspace`
 * already honours `?tab=` as a synonym for `?view=` there — so a section can be
 * carried between the two hosts as a value rather than translated.
 */
export const TAB_PARAM = "tab";
/**
 * Which tab of a strategy's run screen the pane is on, and the run and tick in
 * scope — the page's own `?run=`/`?tick=` spellings, so expanding the pane to
 * the page and collapsing it back carries them as values (see `paneHref`).
 */
export const SECTION_PARAM = "sec";
export const RUN_PARAM = "run";
export const TICK_PARAM = "tick";
const STRATEGY_PARAMS = [SECTION_PARAM, RUN_PARAM, TICK_PARAM] as const;

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
      // A hand-typed section that names nothing is a panel open on Brain, not
      // an error: the pane is still exactly the thing the link asked for.
      const raw = params.get(TAB_PARAM);
      const tab = isKnowledgeTab(raw) ? raw : undefined;
      return {
        kind: "agent",
        ...(slug ? { slug } : {}),
        ...(tab ? { tab } : {}),
      };
    }
    case "desk":
      return { kind: "desk" };
    case "routines":
      return { kind: "routines", focus: libraryFocus };
    case "strategy": {
      const loop = params.get(LOOP_PARAM) ?? "";
      const slash = loop.indexOf("/");
      if (slash <= 0 || slash === loop.length - 1) return null;
      const sec = params.get(SECTION_PARAM);
      // Kept as spelled, once it parses: the page's grammar owns the spelling.
      const runRaw = params.get(RUN_PARAM);
      const run = runRaw && parseRunId(runRaw) ? runRaw : null;
      const tickRaw = params.get(TICK_PARAM);
      const tick = tickRaw && /^\d+$/.test(tickRaw) ? Number(tickRaw) : null;
      return {
        kind: "strategy",
        agentSlug: loop.slice(0, slash),
        strategySlug: loop.slice(slash + 1),
        ...(isPaneSection(sec) ? { section: sec } : {}),
        ...(run ? { run } : {}),
        ...(tick !== null ? { tick } : {}),
      };
    }
    default:
      return null;
  }
}

/**
 * The query string with this pane in it — or with every trace of one gone.
 *
 * "Every trace" includes the agent panel's open section. The pane spent the
 * workspace's four parameters while it *was* the workspace (FEAT-117); it
 * spends `?tab=` instead now that it is the seven Being sections again
 * (FEAT-118), which is one key rather than four and has no cascade rules at
 * all. The section is kept only while the pane goes on showing the same agent:
 * pointing it at somebody else must not carry the previous agent's section,
 * because a Back through that would restore a pane nobody asked for.
 */
export function writePane(
  params: URLSearchParams,
  pane: PaneView,
): URLSearchParams {
  const sameAgent =
    pane?.kind === "agent" &&
    params.get(PANEL_PARAM) === "agent" &&
    (pane.slug ?? "") === (params.get(AGENT_PARAM) ?? "");
  const next = new URLSearchParams(params);
  // `?run=`/`?tick=` are the strategy pane's only while it is in the pane, so
  // they are cleared only on the way out of one — never somebody else's.
  const leavingStrategy = params.get(PANEL_PARAM) === "strategy";
  if (!pane) {
    next.delete(PANEL_PARAM);
    next.delete(LOOP_PARAM);
    next.delete(AGENT_PARAM);
    next.delete(TAB_PARAM);
    if (leavingStrategy) for (const key of STRATEGY_PARAMS) next.delete(key);
    return next;
  }
  next.set(PANEL_PARAM, pane.kind);
  if (pane.kind === "strategy") {
    const loop = `${pane.agentSlug}/${pane.strategySlug}`;
    // The same loop keeps its tab unless told otherwise; its run and tick are
    // only ever what the caller says, because a run of one loop is not a run of
    // the next and "unset" is how a caller clears one.
    const sameLoop =
      params.get(PANEL_PARAM) === "strategy" && params.get(LOOP_PARAM) === loop;
    const carried = params.get(SECTION_PARAM);
    const section =
      pane.section ?? (sameLoop && isPaneSection(carried) ? carried : undefined);
    next.set(LOOP_PARAM, loop);
    if (section) next.set(SECTION_PARAM, section);
    else next.delete(SECTION_PARAM);
    if (pane.run) next.set(RUN_PARAM, pane.run);
    else next.delete(RUN_PARAM);
    if (pane.tick !== null && pane.tick !== undefined)
      next.set(TICK_PARAM, String(pane.tick));
    else next.delete(TICK_PARAM);
  } else {
    next.delete(LOOP_PARAM);
    if (leavingStrategy) for (const key of STRATEGY_PARAMS) next.delete(key);
  }
  if (pane.kind === "agent" && pane.slug) next.set(AGENT_PARAM, pane.slug);
  else next.delete(AGENT_PARAM);
  if (pane.kind === "agent") {
    // Said outright, else whatever the same agent's pane was already open on —
    // so the rail's own tile re-opens the section you left it on rather than
    // resetting to Brain under you.
    const carried = isKnowledgeTab(params.get(TAB_PARAM))
      ? (params.get(TAB_PARAM) as KnowledgeTabId)
      : undefined;
    const tab = pane.tab ?? (sameAgent ? carried : undefined);
    if (tab) next.set(TAB_PARAM, tab);
    else next.delete(TAB_PARAM);
  } else {
    next.delete(TAB_PARAM);
  }
  return next;
}

/**
 * Whether putting `next` in the pane unmounts the agent panel that is there
 * now (CORR-395) — and with it any editor holding unsaved text.
 *
 * True when an agent panel is open and `next` is anything else: no pane, the
 * desk, the routine library, a strategy sheet, or an agent panel that resolves
 * to a *different* agent. Both slugs resolve by the rule `AgentChatTab` reads
 * the pane with, `slug || panelSlug`, so a bare `{kind: "agent"}` while an
 * Execution row's agent is open counts as a hand-off, and a section change on
 * the same agent (the panel's own tab strip) does not.
 */
export function paneHandoffDropsPanel(
  pane: PaneView,
  next: PaneView,
  panelSlug: string,
): boolean {
  if (pane?.kind !== "agent") return false;
  if (next?.kind !== "agent") return true;
  return (next.slug || panelSlug) !== (pane.slug || panelSlug);
}

/**
 * The conversation, with this strategy in its side panel — the page's way back.
 *
 * `returnTo` is where the pane's full-screen door was pressed (`/?…`, which
 * names the conversation and anything else the chat had in its URL); without
 * one — a page opened from a link — it is a bare `/`. Either way the pane is
 * re-pointed at what the page is showing *now*, so a loop, run or section
 * changed on the page is what the panel opens on.
 */
export function paneReturnHref(
  returnTo: string | undefined,
  pane: {
    agentSlug: string;
    strategySlug: string;
    section: PaneSection;
    run?: string | null;
  },
): string {
  const [path, query = ""] = (returnTo || "/").split("?");
  const next = writePane(new URLSearchParams(query), {
    kind: "strategy",
    ...pane,
  });
  const qs = next.toString();
  return `${path || "/"}${qs ? `?${qs}` : ""}`;
}
