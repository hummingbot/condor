import { Layers, ListTree, ScrollText } from "lucide-react";

import type { SectionId } from "@/components/agent/workspace/sections";

/**
 * What each part of the run screen is, in one table (FEAT-120).
 *
 * The tab strip and its tooltips say the same three things about a section — its
 * name, its icon and what is behind it — so they read them from one place.
 *
 * Its own module for the reason `knowledgeTabs` is one: a file that exports a
 * component *and* a value cannot be hot-reloaded on its own, and this table is
 * read by more than one component. It is also not in `sections.ts`, which is the section
 * *rules* — parse, serialize, remember — and deliberately holds nothing that
 * ends up on screen.
 */
export const SECTION_META: Record<
  SectionId,
  { label: string; hint: string; Icon: typeof Layers }
> = {
  runs: {
    label: "Runs",
    hint: "Every run of every strategy — loops, dry runs, tasks and chats",
    Icon: ListTree,
  },
  fleet: {
    label: "Fleet",
    hint: "This agent's own records, in the fleet browser",
    Icon: Layers,
  },
  playbook: {
    label: "Playbook",
    hint: "The strategy's playbook, its config and what it has learned",
    Icon: ScrollText,
  },
};
