/**
 * The sections an agent is read in, and how a host lays them out.
 *
 * Their own module rather than `AgentKnowledge`'s exports: the agent page reads
 * a section name off `?tab=` and the chat's panel links back to one, so both
 * need the taxonomy without pulling in the 1300-line panel — and a component
 * file that also exports values cannot be hot-reloaded on its own.
 */

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
