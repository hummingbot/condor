/**
 * The sections an agent is read in.
 *
 * Their own module rather than `AgentKnowledge`'s exports: the agent page reads
 * a section name off `?tab=` and the chat's panel links back to one, so both
 * need the taxonomy without pulling in the 1300-line panel — and a component
 * file that also exports values cannot be hot-reloaded on its own.
 */

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
