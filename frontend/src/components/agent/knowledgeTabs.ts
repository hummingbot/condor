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
 * `"tabs"` is a horizontal strip, which is right on a page. `"rail"` is a
 * column of icons down the left, which is the only thing that fits in the
 * chat's 400–700px pane: eight tabs wrap to three rows there, and three rows
 * of chrome above a 400px column is most of the column.
 */
export type KnowledgeLayout = "tabs" | "rail";

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
