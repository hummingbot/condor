// ── The lines a scope splits into (FEAT-116) ──
//
// The floor was a page that drew one line per agent over the whole fleet. That
// is not a fact about the fleet, though — it is a fact about **the level below
// the one you are on**. At the root under the default `agent.bot` grouping the
// children are the agents, so the floor's chart is what falls out of the rule;
// one click into an agent the same rule draws one line per bot, and under
// `?groupBy=pair` one line per pair. Neither of those was answerable anywhere
// in the app before, and neither needs a branch keyed on the scope's kind.
//
// **The sum is a property of the tree, not a promise made here.** A node's
// `leaves` is its accounting spine and its children's spines partition it
// (`perf-tree.ts`: *a node folds its own leaf when it has one, and its
// children's spines when it does not*), so the owner lines add up to the Total
// line by construction — the argument the floor made over a whole fleet, made
// one level at a time.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

import { spineKeys } from "@/components/agent/workspace/fleet";
import type { SeriesOwner } from "@/lib/owner-series";
import { foldLeaves, type ConvertQuote, type NodeKind, type PerfNode } from "@/lib/perf-tree";

/** One child of the scope, as both a chart line and a thing Relative can divide. */
export interface ScopeOwner extends SeriesOwner {
  /**
   * The child's declared capital, in display currency — what `Relative`
   * measures its line against. Folded through the same `ConvertQuote` the
   * scope's own tiles are folded with, so the two cannot disagree by an FX
   * fallback.
   */
  capital: number;
  /**
   * The child's net PnL, out of that same fold — what the legend's number and
   * the chat's "which line is dragging this down" are read off. Free: it comes
   * out of the call the capital already needed.
   */
  net: number;
}

/**
 * The lines a scope splits into: its children, or **none**.
 *
 * `key` is the child's node id, so a legend entry, a tree row and a chart line
 * share one identity; `label` is the row's own label. An empty result is what
 * makes the browser fall back to its single aggregate series, which is why
 * there is no scope-kind test at the call site — the three refusals are all
 * here:
 *
 *  - **A controller or executor scope splits into nothing.** A controller's
 *    children are its executors, and an executor carries no controller
 *    performance history to draw a line from. The controller *is* the line.
 *  - **A child whose spine holds no controller is left out.** Its line would be
 *    a legend entry that can never draw — the money is still in the scope's
 *    fold, and the chart's own gap note is where that difference is named.
 *  - **Fewer than two lines is not a split.** One line beside a Total it equals
 *    is the same picture drawn twice.
 */
export function scopeOwners(
  scope: PerfNode,
  convert: ConvertQuote,
  now: number,
): ScopeOwner[] {
  if (scope.kind === "controller" || scope.kind === "executor") return [];
  const owners = scope.children
    .map((child) => {
      const totals = foldLeaves(child.leaves, convert, now);
      return {
        key: child.id,
        label: child.label,
        keys: spineKeys(child.leaves),
        capital: totals.capital,
        net: totals.net,
      };
    })
    .filter((owner) => owner.keys.length > 0);
  return owners.length >= 2 ? owners : [];
}

/**
 * What one line *is*, for the chart's title — read off the children's kind.
 *
 * The children of one node are all the same kind (`buildTree` spends one axis
 * per level), so the first one answers for the set.
 */
export function ownerNoun(kind: NodeKind): string {
  switch (kind) {
    case "agent":
      return "agent";
    case "bot":
      return "bot";
    case "pair":
      return "pair";
    case "ctrlType":
      return "controller type";
    case "controller":
      return "controller";
    case "executor":
      return "executor";
    default:
      // `orphans`, `group` and a fleet nested in a fleet have no noun of their
      // own worth inventing; "part" is what they are and says nothing false.
      return "part";
  }
}
