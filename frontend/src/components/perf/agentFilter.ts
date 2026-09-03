// ── Whose trading is this, and which run made it (FEAT-101) ──
//
// The two questions `/bots` could not answer before: *show me only this
// agent's trading*, and *show me only what one run put into the world*.
//
// Both are answered over the **flat population** rather than over the tree, and
// that is the whole point. FEAT-096 put an agent level in the scope tree, but a
// tree only grows a level where it has something to tell apart: on a server
// whose bots sit outside every strategy's namespace there is no agent node at
// all, and nothing on the page says which of the trading is an agent's — or
// that none of it is. A filter over the leaves works at every scope, in both
// populations, and can express the more useful half of the question:
// *everything that is nobody's*.
//
// Pure, no React and no fetching (the ARCH-300 split), so every rule below is
// reachable from a test rather than only from a rendered sidebar.

import type { BubbleOption } from "@/components/perf/FilterBubbles";
import type { DeploymentRow } from "@/lib/api";
import { inNamespace, runKeyLabel, stripDeploySuffix } from "@/lib/agent-attribution";
import type { PerfLeaf } from "@/lib/perf-tree";

/**
 * The bubble value that means "owned by no agent".
 *
 * `PerfLeaf.agent` is `""` for a leaf nobody owns, and `""` cannot be a bubble
 * value: an empty selection is how every group on this page says *everything*,
 * so an option carrying `""` would be indistinguishable from not being ticked.
 * The leading space keeps it out of the run-key namespace for good — a run key
 * is `{agentSlug}.{strategySlug}` and neither slug can begin with one.
 */
export const UNATTRIBUTED = " none";

/** What the Unattributed bubble is called, spelled the way the question is asked. */
export const UNATTRIBUTED_LABEL = "Unattributed";

/**
 * One bubble per attributed `(agent, strategy)`, plus Unattributed.
 *
 * Counted over whatever population it is handed — the caller passes the
 * *unfiltered* leaves, like the type groups beside it, so a bubble never
 * renumbers itself as a consequence of being ticked.
 *
 * Unattributed sorts last rather than alphabetically: it is the bucket for
 * everything the fleet map could not credit, not one more owner, and on a real
 * server it is usually the biggest. Naming it after the named ones keeps the
 * agents readable as a list.
 */
export function agentOptions(leaves: readonly PerfLeaf[]): BubbleOption[] {
  const counts = new Map<string, number>();
  for (const leaf of leaves) {
    const value = leaf.agent || UNATTRIBUTED;
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  const named = [...counts]
    .filter(([value]) => value !== UNATTRIBUTED)
    .map(([value, count]) => ({ value, label: runKeyLabel(value), count }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const none = counts.get(UNATTRIBUTED);
  return none === undefined
    ? named
    : [...named, { value: UNATTRIBUTED, label: UNATTRIBUTED_LABEL, count: none }];
}

/** Whether a leaf survives the agent bubbles. An empty selection filters nothing. */
export function matchesAgents(leaf: PerfLeaf, agents: readonly string[]): boolean {
  if (agents.length === 0) return true;
  return agents.includes(leaf.agent || UNATTRIBUTED);
}

/**
 * The records one run put into the world, as the ids the fleet joins on.
 *
 * Mapped from FEAT-100's `deployments`, which is the same answer the runtime
 * enforced. Narrowing by the run's time window instead would be a guess: a
 * position opened by hand inside the window is not the run's, and a position
 * the run opened that outlived it is.
 */
export interface RunRecords {
  /** Bot **bases**, not deploy instances — a base and its `-20260807-022130` sibling are one bot. */
  bots: string[];
  controllerIds: string[];
  executorIds: string[];
}

/**
 * `deployments` → the ids to filter by, or `null` for "there is nothing to say".
 *
 * `null` while the ledger has not arrived, so a page waiting on the fetch shows
 * its whole scope rather than blinking through an empty one. An *empty* ledger
 * is a different answer and keeps its records: a run that deployed nothing
 * narrows the fleet to nothing, which is true.
 *
 * An executor's id lives in its `scope` (`exec:{id}`) rather than in its label,
 * which reads `grid SOL-USDC` — the label is for a human and the scope is the
 * address, so the address is what an id is read from.
 */
export function runRecords(rows: readonly DeploymentRow[] | null | undefined): RunRecords | null {
  if (!rows) return null;
  const records: RunRecords = { bots: [], controllerIds: [], executorIds: [] };
  for (const row of rows) {
    if (row.kind === "bot") {
      if (row.label) records.bots.push(row.label);
    } else if (row.kind === "controller") {
      if (row.label) records.controllerIds.push(row.label);
    } else if (row.scope.startsWith("exec:")) {
      records.executorIds.push(row.scope.slice(5));
    }
  }
  return records;
}

/**
 * Whether a leaf is one of the records this run made.
 *
 * A bot is matched by **family**, the rule the runtime itself owns
 * (`inNamespace`): the ledger records the base a run deployed, the fleet
 * reports the instance that deploy became, and they are one bot. That also
 * covers the controllers and executors hanging under it, which is what makes
 * the id lists a supplement rather than the whole rule — a controller of the
 * run's bot belongs to the run even if the ledger's performance snapshot was
 * taken before it existed.
 *
 * A `run` of `null` means no run was asked for, and filters nothing.
 */
export function inRun(leaf: PerfLeaf, run: RunRecords | null): boolean {
  if (run === null) return true;
  const bot = stripDeploySuffix(leaf.bot || "");
  if (bot && run.bots.some((base) => inNamespace(bot, base))) return true;
  if (leaf.kind === "controller") return run.controllerIds.includes(leaf.controllerId);
  return run.executorIds.includes(leaf.id);
}

/** `"s3"` → `3`. Anything else is not a run, and is read as no run at all. */
export function parseRunParam(value: string | null | undefined): number | null {
  const match = /^s(\d+)$/.exec((value || "").trim());
  if (!match) return null;
  const num = Number(match[1]);
  return Number.isFinite(num) && num > 0 ? num : null;
}

/** The `run` parameter for a session number: the inverse of `parseRunParam`. */
export function runParam(sessionNum: number): string {
  return `s${sessionNum}`;
}

/** What the removable chip says. A filter that cannot be seen cannot be undone. */
export function runChipLabel(sessionNum: number): string {
  return `run S${sessionNum} only`;
}

/**
 * The `(agentSlug, strategySlug)` behind an `agent:` scope id.
 *
 * The run's ledger is fetched per `(agent, strategy, session)`, and the scope
 * already carries the first two — so the `run` parameter stays the bare `s3`
 * the design specifies instead of having to repeat the owner in the URL.
 */
export function runOwner(scopeId: string): { slug: string; sslug: string } | null {
  if (!scopeId.startsWith("agent:")) return null;
  const key = scopeId.slice(6);
  const dot = key.indexOf(".");
  if (dot <= 0 || dot === key.length - 1) return null;
  return { slug: key.slice(0, dot), sslug: key.slice(dot + 1) };
}
