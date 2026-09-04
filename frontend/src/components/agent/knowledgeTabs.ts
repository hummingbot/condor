/**
 * The sections an agent is read in, and how a host lays them out.
 *
 * Their own module rather than `AgentKnowledge`'s exports: the agent page reads
 * a section name off `?tab=` and the chat's panel links back to one, so both
 * need the taxonomy without pulling in the 1300-line panel — and a component
 * file that also exports values cannot be hot-reloaded on its own.
 */

import { KNOWLEDGE_TAB_KEY } from "@/lib/sessionState";

/**
 * How the sections are offered.
 *
 * `"rail"` is an 80px column down the right edge, each section a key with its
 * name set flat under its icon, which is the only thing that fits in the chat's
 * 400–700px pane: eight tabs wrap to three rows there, and three rows of chrome
 * above a 400px column is most of the column. On the right because in the chat
 * that edge is against the dock, where every other control that opens something
 * into the pane already lives.
 *
 * `"bare"` is the bodies with no chrome at all, for a host that already draws
 * the navigation. The agent workspace does — its spine carries these seven
 * sections beside the loop's own views (FEAT-103) — and a panel that drew a
 * second strip inside that would be two navigations for one thing.
 *
 * There used to be a third, `"tabs"`: a horizontal strip, for the agent page.
 * The page is gone and the spine replaced the strip, so the layout went with
 * it rather than being kept beside its replacement.
 */
export type KnowledgeLayout = "rail" | "bare";

/** The sections, in the order every host shows them. */
export const KNOWLEDGE_TABS = [
  "brain",
  "skills",
  "memories",
  "tools",
  "strategies",
  "routines",
  "activity",
] as const;

export type KnowledgeTabId = (typeof KNOWLEDGE_TABS)[number];

/** Whether a string off a URL names a section, so `?tab=` can be trusted. */
export function isKnowledgeTab(id: string | null): id is KnowledgeTabId {
  return !!id && (KNOWLEDGE_TABS as readonly string[]).includes(id);
}

/**
 * The section the panel was last read in, remembered across a close (FEAT-118).
 *
 * The open pane carries its section in the URL, but closing it takes the whole
 * address with it — so without this, shutting the panel while reading
 * Strategies and opening it again put the reader back on Brain. Written on
 * every section change and read only when whatever opens the panel does not
 * name one, so a link to a section still wins.
 */
export function rememberKnowledgeTab(tab: KnowledgeTabId): void {
  try {
    localStorage.setItem(KNOWLEDGE_TAB_KEY, tab);
  } catch {
    // Storage a browser will not write to costs the reader one extra click,
    // which is not worth an error anybody has to see.
  }
}

/** The remembered section, or `undefined` for a browser that has none. */
export function lastKnowledgeTab(): KnowledgeTabId | undefined {
  try {
    const raw = localStorage.getItem(KNOWLEDGE_TAB_KEY);
    return isKnowledgeTab(raw) ? raw : undefined;
  } catch {
    return undefined;
  }
}
