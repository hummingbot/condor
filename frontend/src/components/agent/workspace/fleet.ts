// ── The fleet overview, as rules (FEAT-104) ──
//
// A card grid of the fleet already existed here and was deliberately deleted:
// "its only unique job was showing which agents are running, and a line at the
// top of the rail does that without a second page". That docstring is the bar
// this file has to clear, so what a row carries is not a presentational choice
// — it is the entire argument for the page existing. Three facts the rail's
// line cannot say: what the agent's fleet actually **made**, what it last
// **decided**, and when it **ticks next**.
//
// All of it comes off the one `["agents"]` response the rail already polls.
// Nothing here fetches and nothing here renders (the ARCH-300 split), so every
// judgement below — which strategy a row is about, whether money is real, how
// the list is ordered — is reachable from a test rather than only from a
// rendered page.

import {
  reconcile,
  type ReconcileInput,
} from "@/components/agent/workspace/reconcile";
import {
  alertsFor,
  type WorkspaceAlert,
} from "@/components/agent/workspace/views";
import type { AgentActionRow } from "@/lib/agent-attribution";
import type {
  AgentSummary,
  RunningInstance,
  StrategySummary,
} from "@/lib/api";
import { positionQuoteValue } from "@/lib/pnl-chart";
import type { ConvertQuote, PerfLeaf, PerfTotals } from "@/lib/perf-tree";

/** One agent, as the overview reports it. */
export interface FleetRow {
  slug: string;
  name: string;
  /** The model it answers on — `agent_key`, empty when it inherits. */
  agentKey: string;
  /** The strategy this row is about, or `null` when it owns none. */
  strategy: StrategySummary | null;
  /** The engine driving it, or `null` when nothing is looping. */
  live: RunningInstance | null;
  /**
   * What this agent's **runs** earned, or `null` when nothing has been
   * attributed to it at all — see {@link attributedMoney}. Never `0` standing
   * in for "unknown".
   *
   * The run rollup, not the fold (FEAT-109): `GET /agents` sums each strategy's
   * owner-window-tiled session totals, which is a different quantity from the
   * one `/bots` and the Money view print for the same agent. Both are correct
   * and they do not agree, so this column says which one it is and links to the
   * screen that reconciles them.
   */
  net: number | null;
  /** Volume from the same rollup, on the same rule. */
  volume: number | null;
  openPositions: number;
  /** How many sessions it has ever run. 0 is "never run". */
  sessionCount: number;
  /** What it last *did* — one mutating tool call — or `null`. */
  lastDid: AgentActionRow | null;
  /** What it last *said* — the journal's `Last action:` line. */
  lastSaid: string;
  /** Which server its tools trade on, when a loop is up to say. */
  serverName: string;
  /**
   * Where this row's records live, before the ambient fallback — the scoped
   * strategy's configured server, else the agent's pin, else `""` (ARCH-324).
   *
   * Resolved through {@link foldServerOf} at the point of use, because the
   * ambient server is the app's and not the agent's and nothing in this file
   * knows what the app is currently pointed at.
   */
  declaredServer: string;
  /** What wants a person, from `alertsFor` — the workspace's own rule. */
  alerts: WorkspaceAlert[];
}

/**
 * The strategy a row is about, and the engine driving it.
 *
 * A running loop wins, then a paused one: an agent is opened to ask about the
 * loop that is going. With nothing live, the first strategy that has ever run
 * — the newest run's strategy is not knowable from this payload, and the row
 * links into the workspace, which picks properly from the runs it fetches
 * (`pickStrategy`). Naming the *wrong* idle strategy costs a scope the
 * workspace immediately corrects; naming none would cost the link its address.
 */
export function scopeStrategy(agent: AgentSummary): {
  strategy: StrategySummary | null;
  live: RunningInstance | null;
} {
  const strategies = agent.strategies ?? [];
  let paused: { strategy: StrategySummary; live: RunningInstance } | null = null;
  for (const strategy of strategies) {
    for (const live of strategy.instances ?? []) {
      if (live.status === "running") return { strategy, live };
      if (!paused) paused = { strategy, live };
    }
  }
  if (paused) return paused;

  const ran = strategies.find((s) => s.session_count > 0);
  return { strategy: ran ?? strategies[0] ?? null, live: null };
}

/**
 * The server a row declares, before any ambient fallback (ARCH-324).
 *
 * `AgentWorkspace`'s rule, to the letter: the strategy's own configured server,
 * else the agent's pin. Both can be empty — an agent that declares no pin
 * follows whichever server the chat is on, which is what most of them do — and
 * empty is passed on as empty rather than substituted here, because this file
 * does not know what the app is currently pointed at.
 */
export function declaredServerOf(
  agent: Pick<AgentSummary, "server_name">,
  strategy: Pick<StrategySummary, "server_name"> | null,
): string {
  return strategy?.server_name || agent.server_name || "";
}

/**
 * What this agent's **runs** earned — or nothing, honestly.
 *
 * The rule FEAT-099 set for fake zeros, applied to the money FEAT-102 made
 * real. An agent whose runs have no trading attributed to them has not made
 * `$0.00`; it has made *no statement*, and the two look identical on a page
 * that prints the number anyway. So the ledger is judged as a whole — any
 * volume, any non-zero PNL, any open position means there is something to
 * report — and when there is not, both fields are `null` and the row shows a
 * dash.
 *
 * Judged as a whole and not field by field on purpose: an agent with real
 * volume and a PNL that happens to be exactly zero *has* made zero, and that
 * is a fact worth printing.
 *
 * **This is the run rollup** (FEAT-109). `GET /agents` rolls up
 * `_compute_strategy_performance`, which tiles each bot's history across the
 * owner windows its sessions declared — so a bot a chat deployed, and the
 * history of a bot adopted after it had already traded, are not in it.
 *
 * The money column no longer *prints* this number — since ARCH-324 it prints
 * the fold, the same quantity the Money view leads with — but the ordering
 * still uses it, and deliberately: the rollup arrives with the `["agents"]`
 * response every row is built from, while a fold arrives per server, one
 * answer at a time. Sorting on the fold would reorder the list under the
 * reader's cursor as each server replied. So the list is ranked by what the
 * runs earned and each row states what its records show.
 */
export function attributedMoney(agent: {
  total_pnl: number;
  total_volume: number;
  open_positions: number;
}): { net: number | null; volume: number | null } {
  const attributed =
    agent.total_volume > 0 || agent.total_pnl !== 0 || agent.open_positions > 0;
  return attributed
    ? { net: agent.total_pnl, volume: agent.total_volume }
    : { net: null, volume: null };
}

/**
 * What this row wants a person for, from the workspace's own rule.
 *
 * `alertsFor` (FEAT-103) unchanged, fed what the fleet payload actually knows.
 * Two of its three rules can fire from here and one cannot, which is the whole
 * reason this wrapper exists rather than a second copy of the logic:
 *
 * - **A deed came back not ok** — `last_did` is exactly one action row, so the
 *   overview sees the newest failure and no earlier one. That is the same row
 *   the rule would have picked out of the whole log.
 * - **The tick is late** — the live engine carries its own cadence.
 * - **It says it deployed and the ledger is empty** cannot fire: the journal
 *   and the ledger are per-run reads this page deliberately does not make. It
 *   is passed as *not claimed* rather than as unknown, so the overview never
 *   raises an alarm it has not actually checked.
 */
export function fleetAlerts(
  live: RunningInstance | null,
  nowSec: number,
): WorkspaceAlert[] {
  const did = live?.last_did ?? null;
  return alertsFor({
    actions: did ? [{ tick: did.tick, ok: did.ok, summary: did.summary }] : [],
    deployments: 0,
    journalNamesDeploy: false,
    loop: live,
    nowSec,
  });
}

/** Seconds until the next tick, negative when overdue, `null` with no loop. */
export function dueInSec(
  live: RunningInstance | null,
  nowSec: number,
): number | null {
  if (!live || live.last_tick_at <= 0 || live.frequency_sec <= 0) return null;
  return live.last_tick_at + live.frequency_sec - nowSec;
}

/** Running, then paused, then everything idle — the sort's first key. */
function loopRank(live: RunningInstance | null): number {
  if (!live) return 2;
  return live.status === "running" ? 0 : 1;
}

/**
 * Every agent that owns a strategy, in the order a reader wants them.
 *
 * Running first, because a loop trading unattended is the only thing on this
 * page that can change while it is being read. Then by attributed net,
 * descending, with the agents that have made no statement at the bottom —
 * ranking a dash above a real loss would be the fake zero again, one level up.
 * Name last, so the order is stable across polls.
 */
export function fleetRows(
  agents: readonly AgentSummary[],
  nowSec: number,
): FleetRow[] {
  const rows = agents
    .filter((agent) => (agent.strategies ?? []).length > 0)
    .map((agent): FleetRow => {
      const { strategy, live } = scopeStrategy(agent);
      const { net, volume } = attributedMoney(agent);
      return {
        slug: agent.slug,
        name: agent.name,
        agentKey: agent.agent_key ?? "",
        strategy,
        live,
        net,
        volume,
        openPositions: agent.open_positions,
        sessionCount: agent.session_count,
        lastDid: live?.last_did ?? null,
        lastSaid: live?.last_action ?? "",
        serverName: live?.server_name ?? "",
        declaredServer: declaredServerOf(agent, strategy),
        alerts: fleetAlerts(live, nowSec),
      };
    });

  return rows.sort((a, b) => {
    const rank = loopRank(a.live) - loopRank(b.live);
    if (rank !== 0) return rank;
    if (a.net !== b.net) {
      if (a.net === null) return 1;
      if (b.net === null) return -1;
      return b.net - a.net;
    }
    return a.name.localeCompare(b.name);
  });
}

/** One row's fold, in the shape the money column prints it. */
export interface RowFold {
  /** `PerfTotals.net`, in display currency — the Money view's headline. */
  net: number;
  volume: number;
  /** The display currency's symbol, so both screens print one currency. */
  symbol: string;
  /**
   * Whether the records say anything at all.
   *
   * `reconcile`'s own judgement, unchanged: no volume, no PnL and no open
   * position is *no statement*, not `$0.00`, and the column shows a dash.
   */
  reported: boolean;
  /**
   * The whole fold, for a caller that needs more than the headline (FEAT-112).
   *
   * The overview's row printed `net` and `volume` and nothing else; the
   * floor's row prints six more figures out of the same fold, and its strip
   * sums them. Additive, so the overview was untouched — and additive *here*
   * rather than a second `foldLeaves` at the floor, which is the whole rule
   * ARCH-324 set: one producer, read by every screen.
   */
  totals: PerfTotals;
  /**
   * The owner's controller keys (`bot:controller_id`), for `aggregatePnlSeries`.
   *
   * Taken off the spine the fold was measured over, so the line and the number
   * describe the same records. Executor leaves carry no controller history and
   * so contribute no key — which is exactly the gap the floor's honesty note
   * names rather than hides.
   */
  keys: string[];
  /**
   * Signed quote notional over the spine, in display currency.
   *
   * `positionQuoteValue`, the existing tested reader of a `positions_summary`
   * — not a `side` field. `ExecutorInfo.side` exists but `leafFromExecutor`
   * does not copy it, and a controller is a bag of both sides anyway, so the
   * sign has to come from the positions themselves.
   */
  exposure: number;
  /** `max(endedAt)` over the spine, or `null` — the honest half of "liveness". */
  lastClose: number | null;
  /** How many leaves in the spine are still live. */
  running: number;
}

/**
 * The signed notional a scope's open positions add up to, in display currency.
 *
 * Converted per leaf with the leaf's own pair, for the same reason `foldLeaves`
 * converts per leaf: a scope spanning two quotes summed at one rate reports a
 * number nobody holds.
 */
export function spineExposure(spine: readonly PerfLeaf[], cv: ConvertQuote): number {
  let value = 0;
  for (const leaf of spine) value += cv(positionQuoteValue(leaf.positions), leaf.pair);
  return value;
}

/** When a scope last closed something, or `null` when it never has. */
export function spineLastClose(spine: readonly PerfLeaf[]): number | null {
  let last: number | null = null;
  for (const leaf of spine) {
    if (leaf.endedAt !== null && (last === null || leaf.endedAt > last)) last = leaf.endedAt;
  }
  return last;
}

/** The controller keys in a spine — what a chart line is drawn from. */
export function spineKeys(spine: readonly PerfLeaf[]): string[] {
  return spine.filter((leaf) => leaf.kind === "controller").map((leaf) => leaf.id);
}

/** One agent's fold, addressed the way its money link addresses it. */
export interface FoldTarget {
  slug: string;
  /** The scoped strategy, or `null` — what narrows the fold, as `?strategy=`. */
  strategy: string | null;
}

/**
 * Every row that folds, grouped by the server its records are fetched from.
 *
 * A hook cannot be called in a loop and a fleet is fetched per server, so the
 * page needs to know its servers before it renders anything — and an agent with
 * no server anywhere in the chain is simply absent from this list, which is
 * what makes its row a dash instead of a fold of somebody else's fleet.
 *
 * Sorted by server name so the components mount in a stable order across polls.
 */
export function foldTargets(
  agents: readonly AgentSummary[],
  ambient: string | null,
): { server: string; targets: FoldTarget[] }[] {
  const by = new Map<string, FoldTarget[]>();
  for (const agent of agents) {
    if ((agent.strategies ?? []).length === 0) continue;
    const { strategy } = scopeStrategy(agent);
    const server = declaredServerOf(agent, strategy) || ambient || "";
    if (!server) continue;
    const target: FoldTarget = { slug: agent.slug, strategy: strategy?.slug ?? null };
    const listed = by.get(server);
    if (listed) listed.push(target);
    else by.set(server, [target]);
  }
  return [...by]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([server, targets]) => ({ server, targets }));
}

/**
 * Every agent entire, grouped by every server it declares (FEAT-112).
 *
 * The sibling of {@link foldTargets}, and the difference is the whole reason
 * both exist. `foldTargets` narrows an agent to `scopeStrategy(agent)` because
 * an overview row *is about* one strategy and links into it. A floor row is
 * about the agent, and using the scoped rule here would be a silent loss:
 * records belonging to an agent's other strategies are **attributed** — so
 * they are in neither *Outside Condor* nor *Before the ledger* — and no row
 * would claim them. They would vanish out of a total whose entire job is to be
 * complete.
 *
 * So the strategy is `null`, which `reconcile` reads as *every real run key of
 * this agent*, with the three pseudo keys (`chat`/`delegation`/`ui`) included
 * as they always are.
 *
 * And an agent is emitted on **every distinct server any of its strategies
 * declares** — else its own pin, else the ambient one — rather than on the one
 * its scoped strategy names. A generalisation `foldTargets` does not need,
 * because one strategy has one server; the floor sums an agent's per-server
 * folds, so an agent whose two strategies run on two servers is one row over
 * both fleets rather than one fleet arbitrarily picked.
 *
 * Sorted by server name so the components mount in a stable order across polls.
 */
export function floorTargets(
  agents: readonly AgentSummary[],
  ambient: string | null,
): { server: string; targets: FoldTarget[] }[] {
  const by = new Map<string, FoldTarget[]>();
  for (const agent of agents) {
    const strategies = agent.strategies ?? [];
    if (strategies.length === 0) continue;
    const servers = new Set<string>();
    for (const strategy of strategies) {
      const server = declaredServerOf(agent, strategy) || ambient || "";
      if (server) servers.add(server);
    }
    for (const server of servers) {
      // `strategy: null` — the agent entire, every door included.
      const target: FoldTarget = { slug: agent.slug, strategy: null };
      const listed = by.get(server);
      if (listed) listed.push(target);
      else by.set(server, [target]);
    }
  }
  return [...by]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([server, targets]) => ({ server, targets }));
}

/**
 * One server's rows, folded — `reconcile` called, not reimplemented (ARCH-324).
 *
 * The whole point of the item this closes: the overview's row and the Money
 * view headline are the same quantity, and the only way to guarantee that is for
 * both to run the same function over the same leaves at the same scope. A
 * second conversion here — even a correct one — would make the two screens
 * agree by coincidence and drift the first time a leaf gained a field, which is
 * the conflation FEAT-109 extracted `lib/perf-population.ts` to end.
 *
 * `attributed` is `null` because an overview row does not reconcile: it prints
 * the fold and links to the screen that accounts for the difference.
 */
export function foldRows(
  targets: readonly FoldTarget[],
  input: Omit<ReconcileInput, "slug" | "strategy" | "attributed"> & { symbol: string },
): Map<string, RowFold> {
  const { symbol, ...rest } = input;
  const folds = new Map<string, RowFold>();
  for (const target of targets) {
    const r = reconcile({
      ...rest,
      slug: target.slug,
      strategy: target.strategy,
      attributed: null,
    });
    folds.set(target.slug, {
      net: r.fold,
      volume: r.totals.volume,
      symbol,
      reported: r.reported,
      totals: r.totals,
      keys: spineKeys(r.spine),
      exposure: spineExposure(r.spine, rest.convert),
      lastClose: spineLastClose(r.spine),
      running: r.spine.filter((leaf) => leaf.running).length,
    });
  }
  return folds;
}

/**
 * Whether two folds say the same thing — the guard on lifting them into state.
 *
 * Still the four scalars after FEAT-112 widened `RowFold`, and deliberately.
 * The new fields are derived from the same leaves in the same `reconcile` call
 * as `net` and `volume`, so a scalar-equal fold is a fold-equal fold; while
 * `keys` is a fresh array on every poll, so comparing it by identity — or
 * `totals` by object identity — would fail this guard every single time and
 * put the host into a render loop.
 */
export function sameFolds(
  a: ReadonlyMap<string, RowFold> | undefined,
  b: ReadonlyMap<string, RowFold>,
): boolean {
  if (!a || a.size !== b.size) return false;
  for (const [slug, fold] of b) {
    const seen = a.get(slug);
    if (
      !seen ||
      seen.net !== fold.net ||
      seen.volume !== fold.volume ||
      seen.symbol !== fold.symbol ||
      seen.reported !== fold.reported
    ) {
      return false;
    }
  }
  return true;
}

/**
 * The agents that own no strategy at all.
 *
 * A name and a "never run" — not a card, and not hidden. Hidden would make the
 * home lie about how many agents this install has; a card would give a thing
 * with no loop, no money and no decision the same weight as one that is
 * trading.
 */
export function strategylessAgents(
  agents: readonly AgentSummary[],
): AgentSummary[] {
  return agents
    .filter((agent) => (agent.strategies ?? []).length === 0)
    .sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * The server a row's records are actually fetched from (ARCH-324).
 *
 * `AgentWorkspace` opens the Fleet and Money views against *the strategy's
 * configured server, else the agent's pin, else the ambient one*. An overview
 * row links into exactly that screen, so it has to resolve the server exactly
 * that way — a different rule would have the two screens fold two different
 * fleets and disagree by construction, which is the conflation this row exists
 * to end.
 *
 * `""` when none of the three says anything. That is an answer, not a gap: it
 * means nobody has said where this agent trades, and the caller shows a dash
 * rather than folding whichever fleet happens to be at hand.
 */
export function foldServerOf(
  row: Pick<FleetRow, "declaredServer">,
  ambient: string | null,
): string {
  return row.declaredServer || ambient || "";
}

/** The workspace this row opens — `/agents/:slug`, scoped when we know it. */
export function rowHref(row: Pick<FleetRow, "slug" | "strategy">): string {
  const base = `/agents/${encodeURIComponent(row.slug)}`;
  return row.strategy
    ? `${base}?strategy=${encodeURIComponent(row.strategy.slug)}`
    : base;
}

/**
 * Where the two numbers are read side by side.
 *
 * Since ARCH-324 the row shows the *fold*, at this exact scope — same agent,
 * same strategy, same server — so the headline on the other side of this link
 * is the same number the reader just clicked, and the band under it accounts
 * for the difference against what the runs earned (FEAT-109). The `?strategy=`
 * is load-bearing for that: it is what makes the Money view's fold narrow to
 * the run keys this row folded.
 */
export function moneyHref(row: Pick<FleetRow, "slug" | "strategy">): string {
  const base = `/agents/${encodeURIComponent(row.slug)}?open=money`;
  return row.strategy
    ? `${base}&strategy=${encodeURIComponent(row.strategy.slug)}`
    : base;
}

/**
 * The tick a decision came from, as an address.
 *
 * The screen's own grammar (`?strategy=&tick=`), so the last line on the
 * overview is a link into the whole tick that wrote it rather than a dead-end
 * summary. A `?tick=` opens the tick over the screen (FEAT-119), which is why
 * there is no section to name. Falls back to the row's workspace when there is
 * no deed to point at.
 */
export function decisionHref(
  row: Pick<FleetRow, "slug" | "strategy" | "lastDid">,
): string {
  if (!row.lastDid || !row.strategy || row.lastDid.tick <= 0) return rowHref(row);
  return (
    `/agents/${encodeURIComponent(row.slug)}` +
    `?strategy=${encodeURIComponent(row.strategy.slug)}` +
    `&tick=${row.lastDid.tick}`
  );
}
