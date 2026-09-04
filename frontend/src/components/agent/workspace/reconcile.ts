// ── An agent's money is two numbers (FEAT-109) ──
//
// Condor computes what an agent made twice, by two methods that are both
// correct and that do **not** agree:
//
//  - **the run rollup** — `_compute_strategy_performance` over
//    `condor/agents/attribution.py`, per session, using owner-window tiling. It
//    answers *"how much did session 3 make while it owned this bot?"*: a
//    historical, time-sliced question about **runs**.
//  - **the fleet fold** — `foldLeaves` at `agent:` scope. Every record this
//    agent owns, folded as it stands right now. It answers *"what do this
//    agent's records show?"*: a present-tense question about **records**.
//
// They diverge for reasons that are all legitimate — a bot deployed by hand and
// later adopted carries history no run owns; a chat writes no session tiling at
// all, so a record it deployed (FEAT-106) has no owner-window to be sliced by.
// The temptation is to pick one and call it *the* number, which is how a
// dashboard starts lying: the Money view says `+$64`, `/bots` at the same scope
// says `+$91`, and the operator has no way to know both are right about
// different things.
//
// `attribution.py`'s docstring is this codebase's precedent for two consumers of
// one rule — it exists *"so the dashboard and the tick loop cannot disagree,
// because they no longer have separate copies of the rules to drift apart."*
// That works where one rule serves two callers. Here there are genuinely two
// rules, so the equivalent discipline is not a shared implementation: it is a
// **stated, tested relationship**, and this module is where it is stated.
//
// **Neither engine is touched.** The fold is `buildTree` + `foldLeaves`, called
// rather than reimplemented, so the headline is not *asserted* to equal `/bots`
// at `?scope=agent:{runKey}` — it is the same computation over the same leaves.
// The rollup arrives as a number the caller fetched from the endpoint that
// already serves it.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

import type { DeedIndex, Provenance } from "@/lib/agent-attribution";
import {
  AXIS_PREFIX,
  DEFAULT_GROUPING,
  buildTree,
  foldLeaves,
  indexTree,
  type ConvertQuote,
  type PerfLeaf,
  type PerfTotals,
} from "@/lib/perf-tree";

/**
 * The three pseudo-strategies, and what each one is called on screen.
 *
 * `condor/agents/deeds.py`'s `RESERVED_STRATEGY_SLUGS`, mirrored — a chat, a
 * delegation and the dashboard are runs without a strategy, but a run key needs
 * two halves, so each gets a reserved slug of its own. They are named apart
 * rather than lumped into one "not-a-loop" bucket for the reason Python names
 * them apart: *"the chat deployed it"* and *"somebody pressed Deploy"* are
 * different answers to the same question.
 *
 * A user-created strategy may not take these slugs, so a run key ending in one
 * is a pseudo-run with certainty rather than by convention.
 */
export const PSEUDO_LABELS: Record<string, string> = {
  chat: "Deployed from chat",
  delegation: "Deployed by a delegation",
  ui: "Deployed from the dashboard",
};

/** The order the pseudo-runs are listed in — the order they became possible. */
export const PSEUDO_STRATEGIES = ["chat", "delegation", "ui"] as const;

/** `"brigado.brl_mm"` → `{ agent: "brigado", strategy: "brl_mm" }`. */
export function splitRunKey(runKey: string): { agent: string; strategy: string } {
  const dot = runKey.indexOf(".");
  return dot < 0
    ? { agent: runKey, strategy: "" }
    : { agent: runKey.slice(0, dot), strategy: runKey.slice(dot + 1) };
}

/** Whether a run key names a chat, a delegation or the dashboard. */
export function isPseudoRunKey(runKey: string): boolean {
  return PSEUDO_LABELS[splitRunKey(runKey).strategy] !== undefined;
}

/** The fleet scope that opens exactly one run key's records. */
export function agentScope(runKey: string): string {
  return `${AXIS_PREFIX.agent}${runKey}`;
}

/** The fleet scope that opens exactly one bot's records. */
export function botScope(bot: string): string {
  return `${AXIS_PREFIX.bot}${bot}`;
}

/**
 * One named, clickable set of records in the reconciliation.
 *
 * **Every term is a set the reader can open**, or it is not in the line. That
 * is the whole discipline: the failure mode of a reconciliation is an "other"
 * bucket, where an approximation is filed under a label nobody can check and
 * the line becomes noise. A term whose records cannot be addressed is not
 * softened into a term — it stays in {@link Reconciliation.unaccounted}.
 */
export interface Term {
  /** What this set of records is, in the reader's terms. */
  label: string;
  /** The run key that owns them — what decides where the link goes. */
  runKey: string;
  /** What it contributes to `fold − attributed`, in display currency. */
  delta: number;
  /** The fleet scope that opens exactly these records. */
  scope: string;
  /** How many records it holds. */
  count: number;
}

/**
 * Somewhere a residual is likely to have come from — a set of records, named.
 *
 * Not a term: a lead carries **no delta**, precisely because its contribution
 * is not computable here. An adopted bot's controller record is cumulative over
 * its whole life, and the run rollup counts only the part that falls inside an
 * owner window; splitting the record at the takeover instant would be a third
 * attribution engine, which is out of scope by design. So the honest shape is a
 * residual that says *"$X unaccounted"* and points at the records most likely
 * to explain it, rather than a term that quietly claims a number it guessed.
 */
export interface Lead {
  label: string;
  /** The run key that owns them — what decides where the link goes. */
  runKey: string;
  scope: string;
  count: number;
}

/**
 * Where a term's or a lead's records are actually read.
 *
 * The workspace's own Fleet view whenever it can be — following a link out of
 * the workspace unmounts the frame, the loop bar and the tick spine, which is
 * one of the six navigations FEAT-103 exists to delete. But that view is
 * **rooted** at one run key and the root is a floor, not a default
 * (`clampScope`, FEAT-108): a link to a sibling scope would be clamped back to
 * the root and the reader would land on the agent's own fleet believing they
 * were looking at what the chat deployed.
 *
 * So a scope inside the floor stays home, and one outside it goes to `/bots`,
 * where every scope resolves. Silently showing the wrong records is the one
 * outcome not on the table.
 */
export function recordsHref(
  slug: string,
  sslug: string,
  item: { runKey: string; scope: string },
): string {
  if (item.runKey === `${slug}.${sslug}`) {
    return (
      `/agents/${encodeURIComponent(slug)}?view=fleet` +
      `&strategy=${encodeURIComponent(sslug)}` +
      `&fscope=${encodeURIComponent(item.scope)}`
    );
  }
  return `/bots?scope=${encodeURIComponent(item.scope)}`;
}

/** What the two numbers are, and everything that stands between them. */
export interface Reconciliation {
  /** Every record this agent owns, folded as it stands now. */
  totals: PerfTotals;
  /** `totals.net` — the headline, and the number `/bots` shows at this scope. */
  fold: number;
  /** Summed over the agent's runs, or `null` when no rollup has arrived. */
  attributed: number | null;
  /** Named, clickable sets of records the rollup structurally cannot contain. */
  terms: Term[];
  /** `fold − attributed − Σ terms`. Shown when non-zero, never folded away. */
  unaccounted: number;
  /** Where the residual most likely comes from. Empty when there is no lead. */
  leads: Lead[];
  /** Every run key of this agent that has records in the fleet, in scope. */
  runKeys: string[];
  /**
   * The leaves {@link Reconciliation.totals} was folded over — the **accounting
   * spine** of this scope, not every leaf beneath it.
   *
   * Carried out rather than recomputed by callers (FEAT-112). The floor needs
   * more of this scope than its headline — the controller keys its chart line
   * is drawn from, the signed notional of its open positions, when it last
   * closed something — and every one of those is a reading of *these* leaves.
   * A caller that re-derived them from `runKeys` would be a second selection
   * of the same records, free to disagree with the one the number came from
   * the first time the tree's spine rule changed.
   */
  spine: PerfLeaf[];
  /**
   * Whether the fold has anything to say at all.
   *
   * FEAT-104's rule, applied to the fold: an agent whose records show no
   * volume, no PnL and no open position has not made `$0.00`, it has made *no
   * statement*, and the two look identical on a screen that prints the number
   * anyway. Judged as a whole and not field by field, for the same reason
   * `attributedMoney` judges it that way: real volume with a PnL that happens
   * to be exactly zero *is* a fact worth printing.
   */
  reported: boolean;
}

/** What {@link reconcile} needs, none of which it fetches. */
export interface ReconcileInput {
  /** The agent whose money this is. */
  slug: string;
  /**
   * `?strategy=` when the URL names one, else `null` for every strategy.
   *
   * It narrows the **real** run keys only. A chat's deploys belong to the
   * agent and to no strategy, so narrowing the loop's scope cannot make them
   * stop being this agent's money.
   */
  strategy: string | null;
  /** The live fleet, as leaves — `runningLeaves` (lib/perf-population). */
  leaves: PerfLeaf[];
  deeds: DeedIndex | null;
  /** Display-currency conversion, the same one `/bots` folds with. */
  convert: ConvertQuote;
  /** Epoch ms, for the fold's measured runtime. */
  now: number;
  /** The run rollup's total, or `null` while it has not arrived. */
  attributed: number | null;
}

/** The two ways a record can be this agent's without the agent having made it. */
const ADOPTED: readonly Provenance[] = ["declared", "deed"];

/**
 * The two numbers, and the terms between them.
 *
 * The fold is read out of the scope tree rather than summed over the leaves
 * directly, and that is not incidental: `node.leaves` is the tree's **accounting
 * spine**, where a controller stands for the executors working under it and is
 * not counted alongside them. Summing `leaves` here would double-count every
 * live executor, which is exactly the fold `/bots` does not do — so the tree is
 * built, and the same nodes `?scope=agent:{runKey}` selects are the ones folded.
 */
export function reconcile(input: ReconcileInput): Reconciliation {
  const { slug, strategy, leaves, deeds, convert, now, attributed } = input;

  const tree = buildTree(leaves, "All", { grouping: DEFAULT_GROUPING, deeds });
  const nodes = indexTree(tree);

  // Which of this agent's run keys actually have records. Insertion-ordered by
  // the leaves, then sorted, so the list is stable across polls.
  const real = new Set<string>();
  const pseudo = new Set<string>();
  for (const leaf of leaves) {
    if (!leaf.agent) continue;
    const parts = splitRunKey(leaf.agent);
    if (parts.agent !== slug) continue;
    if (isPseudoRunKey(leaf.agent)) pseudo.add(leaf.agent);
    else if (!strategy || parts.strategy === strategy) real.add(leaf.agent);
  }

  const pseudoKeys = PSEUDO_STRATEGIES.map((s) => `${slug}.${s}`).filter((k) =>
    pseudo.has(k),
  );
  const runKeys = [...[...real].sort(), ...pseudoKeys];

  const spineOf = (runKey: string): PerfLeaf[] =>
    nodes.get(agentScope(runKey))?.leaves ?? [];

  const mine = runKeys.flatMap(spineOf);
  const totals = foldLeaves(mine, convert, now);

  // A pseudo-run's records are the one part of the gap that is exact: a chat
  // writes no session ledger, so no owner window can ever have sliced them and
  // the rollup cannot contain a cent of them. The whole fold of that scope is
  // the term.
  const terms: Term[] = pseudoKeys.map((runKey) => {
    const spine = spineOf(runKey);
    return {
      label: PSEUDO_LABELS[splitRunKey(runKey).strategy],
      runKey,
      delta: foldLeaves(spine, convert, now).net,
      scope: agentScope(runKey),
      count: spine.length,
    };
  });

  const named = terms.reduce((sum, term) => sum + term.delta, 0);
  const unaccounted = attributed === null ? 0 : totals.net - attributed - named;

  // What is left over, pointed at rather than explained. An adopted bot — one a
  // strategy *declared* or that Condor only knows by a recorded deed — is the
  // case that produces a residual by construction: the fold has its whole
  // history and the rollup has only the part inside an owner window.
  const adopted = new Map<string, { runKey: string; count: number }>();
  for (const runKey of runKeys) {
    if (isPseudoRunKey(runKey)) continue;
    for (const leaf of spineOf(runKey)) {
      if (!ADOPTED.includes(leaf.how)) continue;
      const seen = adopted.get(leaf.bot);
      if (seen) seen.count += 1;
      else adopted.set(leaf.bot, { runKey, count: 1 });
    }
  }
  const leads: Lead[] = [...adopted]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([bot, { runKey, count }]) => ({
      label: `${bot} — adopted, so its history starts before this agent's runs`,
      runKey,
      scope: botScope(bot),
      count,
    }));

  return {
    totals,
    fold: totals.net,
    attributed,
    terms,
    unaccounted,
    leads,
    runKeys,
    spine: mine,
    reported: totals.volume > 0 || totals.net !== 0 || totals.positions > 0,
  };
}
