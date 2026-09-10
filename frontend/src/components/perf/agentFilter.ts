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
import {
  inNamespace,
  ownerRowLabel,
  stripDeploySuffix,
  type DeedIndex,
  type FleetOwner,
} from "@/lib/agent-attribution";
import type { PerfLeaf } from "@/lib/perf-tree";

/**
 * The two bubble values for a leaf no run owns (FEAT-106).
 *
 * `PerfLeaf.agent` is `""` for a leaf nobody owns, and `""` cannot be a bubble
 * value: an empty selection is how every group on this page says *everything*,
 * so an option carrying `""` would be indistinguishable from not being ticked.
 * The leading space keeps them out of the run-key namespace for good — a run
 * key is `{agentSlug}.{strategySlug}` and neither slug can begin with one.
 *
 * There used to be one of these, called `Unattributed`, and it was doing the
 * work of two unrelated facts: *the log was complete when this started and
 * still holds nothing that joins to it*, and *Condor made things before it
 * wrote them down and this may be one of them*. The first is a real and useful
 * category; the second is a number that visibly drains to zero as the ledger
 * fills. Folded together they were just a mystery.
 *
 * The **values** are frozen — they are in every saved filter URL — so `OUTSIDE`
 * keeps the name of the value it holds. The label above it does not: it stopped
 * naming a culprit when the ledger stopped being able to name one (READ-365).
 */
export const OUTSIDE = " outside";
export const OUTSIDE_LABEL = "No record found";
export const BEFORE_LEDGER = " pre";
export const BEFORE_LEDGER_LABEL = "Before the ledger";

/**
 * Which bucket an unowned leaf falls in: the cut is one timestamp.
 *
 * `deeds.since` is the instant from which this install has been recording at
 * *every* door — the chat, a delegation, the dashboard and Telegram (FEAT-105,
 * finished by CORR-622). A record older than that predates the ledger and
 * cannot be judged. A record newer than it, with no deed the join can reach, is
 * one the ledger has no answer for — which is a fact about the search, not
 * about the author, and the label says exactly that much and no more.
 *
 * **Why not the stronger claim.** Writing a deed down and leaving a mark the
 * join can follow are two different things, so "no deed found" does not license
 * "not Condor's". The join (`attributionIndex`) reaches a deed by bot base name
 * or by a `controller_id` tag, and the two ref-less doors can supply neither
 * for an executor: `attribution_tag` hands a ref-less owner `""`
 * (`REFLESS_STRATEGIES` is the dashboard and Telegram, `deed_index.py`), and
 * only a *deploy* claims a bot name (`deeds.record_direct`). An executor opened
 * from the dashboard or from Telegram is therefore written down and still lands
 * here. Calling this bucket foreign would accuse those records on Condor's own
 * evidence; saying no record was found is true of them and of a genuinely
 * foreign bot alike. Tagging the ref-less doors is what would license the
 * stronger wording — and is deliberately not done, because the dashboard's own
 * views query `controller_ids=["main"]` (`hooks/useMainControllerData.ts`) and
 * a tagged executor would vanish from the page that created it.
 *
 * The cut is stamped by the build and not read off the oldest deed on disk,
 * because a deed proves only that the door *it* came through was recording. For
 * as long as the Telegram door was unwired, the oldest chat deed dated a
 * completeness the log did not have, and every bot a person deployed from
 * Telegram after it was told it came from outside Condor — the verdict this
 * bucket has since stopped making.
 *
 * Two ways to end up unjudgeable, and both read *Before the ledger*: an install
 * whose log has never been complete (`since` is 0), and a record that does not
 * say when it started. Reading missing evidence as no answer rather than as an
 * answer is the conservative direction, and the right one.
 */
export function agentBucket(leaf: PerfLeaf, deeds: DeedIndex | null | undefined): string {
  if (leaf.agent) return leaf.agent;
  const since = deeds?.since ?? 0;
  if (since <= 0 || leaf.startedAt === null) return BEFORE_LEDGER;
  // `since` is epoch seconds (the wire's convention); a leaf's is epoch ms.
  return leaf.startedAt >= since * 1000 ? OUTSIDE : BEFORE_LEDGER;
}

/** No fleet map in hand. Shared so a default argument is not a new array a poll. */
const NO_OWNERS: readonly FleetOwner[] = [];

/**
 * **What a bucket value is called on screen** — every surface's one namer.
 *
 * Two fixed labels for the two things that are not a run, and
 * {@link ownerRowLabel} for everything that is: slugs for a strategy, the fleet
 * map's own words for a chat, a delegation or the dashboard. The sidebar row,
 * its filter bubble, the chat's filter chip and the Dock's execution rows all
 * come through here, which is what stops them saying four different things
 * about one run key.
 *
 * `owners` is optional and defaults to *no map*, which degrades to slugs — the
 * same honest fallback `ownerTitle` makes for an owner the map no longer holds.
 * A caller that has the map should pass it.
 */
export function agentBucketLabel(
  value: string,
  owners: readonly FleetOwner[] = NO_OWNERS,
): string {
  if (value === OUTSIDE) return OUTSIDE_LABEL;
  if (value === BEFORE_LEDGER) return BEFORE_LEDGER_LABEL;
  return ownerRowLabel(owners, value);
}

/**
 * One bubble per attributed run, plus the two unowned buckets.
 *
 * Counted over whatever population it is handed — the caller passes the
 * *unfiltered* leaves, like the type groups beside it, so a bubble never
 * renumbers itself as a consequence of being ticked.
 *
 * The unowned buckets sort last rather than alphabetically: they are what the
 * fleet could not credit, not two more owners, and on a real server they are
 * usually the biggest. Naming them after the named runs keeps the runs readable
 * as a list. Between themselves, *No record found* comes first: it is the one
 * that stays true, and *Before the ledger* is the one that drains.
 */
export function agentOptions(
  leaves: readonly PerfLeaf[],
  deeds: DeedIndex | null | undefined = null,
  owners: readonly FleetOwner[] = NO_OWNERS,
): BubbleOption[] {
  const counts = new Map<string, number>();
  for (const leaf of leaves) {
    const value = agentBucket(leaf, deeds);
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  const unowned = new Set<string>([OUTSIDE, BEFORE_LEDGER]);
  // Through the same namer as the sidebar row, so a bubble and the row it
  // ticks cannot read as two different owners — and sorted on what it *says*,
  // which is the order a reader scans.
  const named = [...counts]
    .filter(([value]) => !unowned.has(value))
    .map(([value, count]) => ({ value, label: agentBucketLabel(value, owners), count }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const tail = [OUTSIDE, BEFORE_LEDGER]
    .filter((value) => counts.has(value))
    .map((value) => ({ value, label: agentBucketLabel(value, owners), count: counts.get(value)! }));
  return [...named, ...tail];
}

/** Whether a leaf survives the agent bubbles. An empty selection filters nothing. */
export function matchesAgents(
  leaf: PerfLeaf,
  agents: readonly string[],
  deeds: DeedIndex | null | undefined = null,
): boolean {
  if (agents.length === 0) return true;
  return agents.includes(agentBucket(leaf, deeds));
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
