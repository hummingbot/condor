// ── The order the fleet is read in (FEAT-107) ──
//
// `buildTree` takes the nesting as a list of axes; this is that list as the URL
// writes it, as the picker offers it, and as the browser trims it before it is
// handed over. Pure and React-free (the ARCH-300 split), so every rule below is
// reachable from a test rather than only from a rendered sidebar.
//
// It sits *above* `perf-tree` and imports from it, never the other way round:
// the axis vocabulary and `keyFor` live down there beside the id grammar they
// write, because the tree cannot be built without them.

import {
  axisOfNodeId,
  DEFAULT_GROUPING,
  keyFor,
  type GroupAxis,
  type PerfLeaf,
} from "@/lib/perf-tree";
import type { DeedIndex } from "@/lib/agent-attribution";

export { DEFAULT_GROUPING, type GroupAxis };

/** Every axis the parser will accept, in the order the picker lists them. */
export const GROUP_AXES: readonly GroupAxis[] = ["agent", "bot", "pair", "ctrlType"];

/**
 * What `formatGrouping` writes for a fleet read with no levels at all.
 *
 * A word rather than an empty string, so that the parameter round-trips: an
 * empty `?groupBy=` is indistinguishable from an absent one, and an absent one
 * has to keep meaning the default.
 */
const FLAT = "none";

/**
 * `"agent.bot"` → `["agent", "bot"]`.
 *
 * Falls back to the default rather than throwing, for the reason
 * `parsePopulation` gives: a hand-edited or stale query parameter should land
 * the reader on the fleet they asked for, not on an error. Unknown axes are
 * dropped and repeats collapse, so `?groupBy=pair.pair.type` reads as `pair`
 * — and a string that names nothing at all reads as the default.
 */
export function parseGrouping(raw: string | null | undefined): GroupAxis[] {
  const text = (raw || "").trim();
  if (!text) return [...DEFAULT_GROUPING];
  if (text === FLAT) return [];
  const axes = text
    .split(".")
    .map((part) => part.trim())
    .filter((part): part is GroupAxis => (GROUP_AXES as readonly string[]).includes(part));
  const seen = new Set<GroupAxis>();
  const unique = axes.filter((axis) => !seen.has(axis) && (seen.add(axis), true));
  return unique.length > 0 ? unique : [...DEFAULT_GROUPING];
}

/** The inverse: what the picker writes into the URL. */
export function formatGrouping(axes: readonly GroupAxis[]): string {
  return axes.length === 0 ? FLAT : axes.join(".");
}

/** One button on the picker: a question, and the nesting that answers it. */
export interface GroupingPreset {
  key: string;
  label: string;
  hint: string;
  axes: readonly GroupAxis[];
}

/**
 * The four readings the picker offers.
 *
 * Presets rather than a drag-to-reorder list, deliberately: these cover every
 * question anyone has actually asked of this page, and a reordering UI over
 * four items is a lot of interaction for a choice made once. Nothing stops a
 * deeper nesting — `?groupBy=pair.agent.bot` parses and builds — and no preset
 * offers one until somebody asks.
 */
export const GROUPING_PRESETS: readonly GroupingPreset[] = [
  {
    key: "owner",
    label: "Owner",
    hint: "Every controller and executor under the agent that made it, Condor included.",
    axes: ["agent", "bot"],
  },
  {
    key: "bot",
    label: "Bot",
    hint: "One row per bot, whoever deployed it — the thing you stop.",
    axes: ["bot"],
  },
  {
    key: "pair",
    label: "Pair",
    hint: "One row per trading pair, across the whole fleet.",
    axes: ["pair"],
  },
  {
    key: "ctrlType",
    label: "Type",
    hint: "One row per controller class — pmm_simple, grid_strike, and so on.",
    axes: ["ctrlType"],
  },
];

/** Which preset a grouping is, or `null` for one nobody offers a button for. */
export function presetOf(grouping: readonly GroupAxis[]): GroupingPreset | null {
  const wanted = formatGrouping(grouping);
  return GROUPING_PRESETS.find((preset) => formatGrouping(preset.axes) === wanted) ?? null;
}

/**
 * Whether an axis tells anything in this population apart.
 *
 * The collapse rule the bot level has always had, generalised: *a fleet running
 * a single bot would spend a chevron saying so.* An empty population counts as
 * distinguishing, which is what keeps a filtered-to-nothing tree from silently
 * changing shape under the reader.
 */
export function distinguishes(
  leaves: readonly PerfLeaf[],
  axis: GroupAxis,
  deeds?: DeedIndex | null,
): boolean {
  const seen = new Set<string>();
  for (const leaf of leaves) {
    seen.add(keyFor(leaf, axis, deeds));
    if (seen.size > 1) return true;
  }
  return seen.size !== 1;
}

/**
 * The grouping actually worth building, given what is in scope.
 *
 * Owner-first is not owner-always: an axis whose leaves all share one key is
 * dropped before the tree is built, so a one-owner fleet draws no owner level
 * and a one-bot fleet draws no bot level, exactly as before. What makes this
 * pay off is [[FEAT-106]] — its two buckets mean the owner axis distinguishes
 * something on almost every install, where a bare run key distinguished
 * nothing on most of them.
 *
 * `keep` is never dropped whatever it says, because a rooted browser's floor
 * needs a node to be: see {@link groupingForRoot}.
 */
export function collapseGrouping(
  grouping: readonly GroupAxis[],
  leaves: readonly PerfLeaf[],
  deeds?: DeedIndex | null,
  keep?: GroupAxis | null,
): GroupAxis[] {
  return grouping.filter((axis) => axis === keep || distinguishes(leaves, axis, deeds));
}

/**
 * The grouping a browser rooted at `rootScope` may actually use.
 *
 * **The browser always draws the level its root lives on** — the one rule
 * [[FEAT-108]] left behind, and the one a reorderable nesting is most likely to
 * lose. A floor needs a node to be: root the browser at `agent:{runKey}` under
 * a grouping with no owner level and the root resolves to nothing, so the
 * agent workspace's fleet view reports an empty fleet without saying why.
 *
 * *First*, and not merely present, for a second reason. A level nested under
 * another is drawn once per parent, so an `agent:` row under `?groupBy=pair`
 * would be one row per pair — several nodes for one root, of which the sidebar
 * could draw only one. Forced to the front it is a single child of the fleet
 * holding everything of that owner, which is what a floor has to be.
 *
 * A root that names no axis — the fleet itself, or a single controller — leaves
 * the grouping alone: those rows exist under every reading.
 */
export function groupingForRoot(
  grouping: readonly GroupAxis[],
  rootScope: string,
): GroupAxis[] {
  const axis = axisOfNodeId(rootScope);
  if (!axis) return [...grouping];
  return [axis, ...grouping.filter((other) => other !== axis)];
}

/** The axis a root sits on, which is the one its browser may never drop. */
export function rootAxis(rootScope: string): GroupAxis | null {
  return axisOfNodeId(rootScope);
}
