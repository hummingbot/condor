// ── Fleet rows, as rules (FEAT-104) ──
//
// What the execution dock (`components/chat/DockExecution.tsx`) knows about
// each agent: which strategy and loop it is about, what its runs earned, what
// it last decided and when it ticks next — the dock reads each agent's
// liveness through `fleetRows`. The spine readers are shared with
// `floor/floor.ts` and `perf/scopeOwners.ts`.
//
// The rows come off one `["agents"]` response. Nothing here fetches and
// nothing here renders (the ARCH-300 split), so every judgement below — which
// strategy a row is about, whether money is real, how the list is ordered — is
// reachable from a test rather than only from a rendered component.

import {
  alertsFor,
  type WorkspaceAlert,
} from "@/components/agent/workspace/views";
import { workspaceHref } from "@/components/agent/workspace/workspaceUrl";
import { countdown, type AgentActionRow } from "@/lib/agent-attribution";
import type {
  AgentSummary,
  RunningInstance,
  StrategySummary,
  VenueCondition,
} from "@/lib/api";
import { positionQuoteValue } from "@/lib/pnl-chart";
import type { ConvertQuote, PerfLeaf } from "@/lib/perf-tree";

/** One agent, as a fleet row reports it. */
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
   * and they do not agree, so a surface printing it has to say which one it is.
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
   * The ambient fallback is applied by the caller, because the ambient server
   * is the app's and not the agent's and nothing in this file knows what the
   * app is currently pointed at.
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
 * `fleetRows` ranks on it, and deliberately rather than on a fold: the rollup
 * arrives with the `["agents"]` response every row is built from, while a fold
 * arrives per server, one answer at a time. Sorting on the fold would reorder
 * the list under the reader's cursor as each server replied.
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
 *   row sees the newest failure and no earlier one. That is the same row the
 *   rule would have picked out of the whole log.
 * - **The tick is late** — the live engine carries its own cadence.
 * - **It says it deployed and the ledger is empty** cannot fire: the journal
 *   and the ledger are per-run reads a fleet row deliberately does not make.
 *   It is passed as *not claimed* rather than as unknown, so a row never raises
 *   an alarm it has not actually checked.
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

/**
 * The words that go with `dueInSec`, so the four surfaces that print a
 * countdown cannot word the same state differently. A tick due exactly now is
 * overdue, not "next in 0s" — the beat has already slipped.
 */
export function tickCountdownLabel(due: number): string {
  return due > 0 ? `next in ${countdown(due)}` : `overdue ${countdown(-due)}`;
}

/**
 * The drifted fields of a live engine, as one line per field (FEAT-136).
 *
 * The backend sends both values precisely so a reader does not have to go back
 * to the file; this only words them. Empty array = the process and the file
 * agree, which is what a badge should read off rather than a boolean nobody
 * computed.
 */
export function driftLines(live: RunningInstance | null): string[] {
  const drift = live?.config_drift;
  if (!drift) return [];
  return Object.entries(drift).map(
    ([field, both]) =>
      `${field}: running ${describeValue(both?.running)} · file ${describeValue(both?.file)}`,
  );
}

/** A drift value as text. `""` has to read as empty, not as nothing at all. */
function describeValue(value: unknown): string {
  if (value === null || value === undefined) return "unset";
  if (value === "") return '""';
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/**
 * The model this session is on, and whether its pin took (FEAT-136).
 *
 * `agent_key` carries the *requested* pin as a suffix (`claude-acp:opus`);
 * `active_model_id` is what the adapter answered. A pin that did not apply
 * leaves the adapter on its own default, and before this the only record of
 * that was a log line — so the mismatch is the whole point of showing it.
 *
 * `null` when there is no adapter answer to report: a pydantic-ai backend has
 * no adapter to ask, and an ACP session has none until its first tick.
 */
export function effectiveModel(
  live: RunningInstance | null,
): { active: string; requested: string; mismatch: boolean } | null {
  const active = live?.active_model_id ?? "";
  if (!active) return null;
  const requested = (live?.agent_key ?? "").split(":").slice(1).join(":");
  // Mirrors the backend's own alias matching (`resolve_model_id`): a pin of
  // "opus" is honoured by "claude-opus-4-8", so only a pair that shares no
  // substring either way is a pin that did not take.
  const a = active.toLowerCase();
  const r = requested.toLowerCase();
  const mismatch = !!r && !a.includes(r) && !r.includes(a);
  return { active, requested, mismatch };
}

/**
 * The venues refusing this session right now, worst first (FEAT-138).
 *
 * Only the ones whose `condition` is still `throttled`: the counts stay on the
 * payload as the record, but a venue that refused an hour ago is not something
 * an operator can act on now. Reported as a *venue* condition and never
 * alongside the session's errors — the agent did nothing wrong, and the whole
 * reason throttling read as a mysterious strategy bug is that nobody drew that
 * line (GitHub #276).
 *
 * Empty array for a session nothing is refusing, and for an older backend that
 * does not send the field at all — which read the same way before this existed.
 */
export function throttledVenues(live: RunningInstance | null): VenueCondition[] {
  const rows = live?.throttled_venues ?? [];
  return rows
    .filter((row) => row.condition === "throttled")
    .slice()
    .sort((a, b) => b.refusals - a.refusals || a.venue.localeCompare(b.venue));
}

/**
 * How a throttled venue reads: what refused, and the count behind the claim.
 *
 * The counts are the point. "Intermittent, may recover" was what sampling the
 * logs said about a host that had answered 0 of 8 calls, so a surface naming
 * the venue has to print attempts and successes beside the refusals.
 */
export function throttleLines(venues: readonly VenueCondition[]): string[] {
  return venues.map(
    (v) =>
      `${v.venue}${v.host ? ` on ${v.host}` : ""}: ${v.attempts} attempt${
        v.attempts === 1 ? "" : "s"
      }, ${v.successes} ok, ${v.refusals} refused`,
  );
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
 * list that can change while it is being read. Then by attributed net,
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

/** The workspace this row opens — `/agents/:slug`, scoped when we know it. */
export function rowHref(row: Pick<FleetRow, "slug" | "strategy">): string {
  return workspaceHref(row.slug, { strategy: row.strategy?.slug ?? null });
}

/**
 * The tick a decision came from, as an address.
 *
 * The screen's own grammar (`?strategy=&tick=`), so a row's last decision is
 * a link into the whole tick that wrote it rather than a dead-end
 * summary. A `?tick=` opens the tick over the screen (FEAT-119), which is why
 * there is no section to name. Falls back to the row's workspace when there is
 * no deed to point at.
 */
export function decisionHref(
  row: Pick<FleetRow, "slug" | "strategy" | "lastDid">,
): string {
  if (!row.lastDid || !row.strategy || row.lastDid.tick <= 0) return rowHref(row);
  return workspaceHref(row.slug, {
    strategy: row.strategy.slug,
    tick: row.lastDid.tick,
  });
}
