/**
 * The sections an agent is read in, and how a host lays them out.
 *
 * Their own module rather than `AgentKnowledge`'s exports: the agent page reads
 * a section name off `?tab=` and the chat's panel links back to one, so both
 * need the taxonomy without pulling in the 1300-line panel — and a component
 * file that also exports values cannot be hot-reloaded on its own.
 */

import { KNOWLEDGE_TAB_KEY } from "@/lib/sessionState";

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
