// ── The agent's run screen, as rules (FEAT-103, FEAT-119) ──
//
// One route — `/agents/:slug` — and every state it can be in written down in
// the URL: which strategy is in scope, which run, which tick, and which of the
// screen's disclosures are open. Four pages, three tab strips and five URL-less
// panels collapsed onto that grammar, so the rules that read it have to be
// reachable from a test rather than only from a rendered page. Same split
// `lab/runs.ts` made, and the same reason: this route reads more parameters
// than any page before it, and none of the reading happens in JSX.
//
// `?view=` used to be the first of them and named the body on screen. It is not
// a grammar any more, only a redirect table (`sections.ts`), because there is
// no body to name: the seven **Being** sections went to the chat's panel
// (FEAT-118) and the five **Doing** views became one screen with disclosures on
// it (FEAT-119). The page reads a legacy `?view=` once, to send it somewhere,
// and nothing else in the app writes one.
//
// Nothing here fetches and nothing here renders.

import { isLoopRun, parseRunId, type RunRef } from "@/components/agent/lab/runs";
import { OPEN_PARAM } from "@/components/agent/workspace/sections";
import type { AgentRunRow, StrategySummary } from "@/lib/api";

/** Everything the URL says, once. */
export interface WorkspaceUrl {
  /** `?strategy=` — the scope, or `null` for "decide from the data". */
  strategy: string | null;
  /** `?run=` as a reference, or `null` for the newest run in scope. */
  run: RunRef | null;
  /** `?tick=`, or `null` when no tick is open over the screen. */
  tick: number | null;
  /** The raw `?open=`, for {@link useSections} — `null` when it names none. */
  open: string | null;
}

/** Read the whole screen's state out of a query string. */
export function parseWorkspace(search: string | URLSearchParams): WorkspaceUrl {
  const params =
    typeof search === "string" ? new URLSearchParams(search) : search;
  const tickParam = params.get("tick");
  return {
    strategy: params.get("strategy") || null,
    run: parseRunId(params.get("run")),
    tick: tickParam && /^\d+$/.test(tickParam) ? Number(tickParam) : null,
    open: params.get(OPEN_PARAM),
  };
}

// ── Scope ──

type StrategyScope = Pick<StrategySummary, "slug" | "status" | "instances">;
type RunScope = Pick<AgentRunRow, "strategy_slug" | "started_at">;

/**
 * Which strategy the workspace is scoped to.
 *
 * The URL wins when it names one this agent actually owns. Absent — which is
 * every bare `/agents/:slug` — the live one, because an agent with a loop
 * running is being opened to ask about that loop; else whichever strategy the
 * newest run belongs to, which is the last thing anybody looked at.
 */
export function pickStrategy(
  strategies: readonly StrategyScope[],
  runs: readonly RunScope[],
  named: string | null,
): string | null {
  if (named && strategies.some((s) => s.slug === named)) return named;

  const live = strategies.find(
    (s) => s.status === "running" || s.instances.length > 0,
  );
  if (live) return live.slug;

  let newest: RunScope | null = null;
  for (const run of runs) {
    if (!strategies.some((s) => s.slug === run.strategy_slug)) continue;
    if (!newest || (run.started_at ?? 0) > (newest.started_at ?? 0)) newest = run;
  }
  if (newest) return newest.strategy_slug;

  return strategies[0]?.slug ?? null;
}

/**
 * Which run is selected, given the scope.
 *
 * `?run=` naming a run outside the current scope falls back to the newest in
 * scope, which is the Lab's rule verbatim (FEAT-099) — a bare `/agents/:slug`
 * to open on something.
 *
 * The scope is a *strategy*, so it only bounds the two kinds that have one. A
 * conversation and a delegation belong to no strategy (FEAT-111), and scoping
 * them out would make `?run=c:7f3a` unopenable on any agent that also loops —
 * which is every agent the rail's union exists for. The fallback still comes
 * from the scope: a chat is addressable, it is never the default selection.
 */
export function pickRun(
  runs: readonly AgentRunRow[],
  strategy: string | null,
  run: RunRef | null,
): AgentRunRow | null {
  const scoped = strategy
    ? runs.filter((r) => r.strategy_slug === strategy)
    : runs;
  if (run) {
    const searched = isLoopRun(run.kind) ? scoped : runs;
    const match = searched.find(
      (r) =>
        r.kind === run.kind &&
        (isLoopRun(run.kind) ? r.number === run.number : r.id === run.id),
    );
    if (match) return match;
  }
  return scoped.find((r) => isLoopRun(r.kind)) ?? scoped[0] ?? null;
}

// ── The retired addresses ──

/**
 * Where `/agents/:slug/runs` goes now.
 *
 * A redirect and not a deletion: the Lab's address is in notification payloads,
 * in the chat's route facts and in whatever anyone has bookmarked. The whole
 * query string travels with it, which costs nothing because the Lab's grammar
 * (`?strategy=&run=&tick=`) is a subset of the screen's — that is what made the
 * merge possible at all. It lands with the Runs disclosure open, which is where
 * the rail it was addressing lives now (FEAT-119).
 */
export function runsRedirect(slug: string, search: string): string {
  const params = new URLSearchParams(search);
  params.delete("view");
  params.set(OPEN_PARAM, "runs");
  return `/agents/${encodeURIComponent(slug)}?${params}`;
}

/**
 * Where `/agents/:slug/strategies/:sslug` goes now.
 *
 * The strategy stops being a path segment and becomes the scope it always was,
 * so the same address lands on the screen with that strategy in scope and the
 * Playbook disclosure open — and the loop bar above can then move the scope
 * without another navigation.
 */
export function strategyRedirect(
  slug: string,
  sslug: string,
  search: string,
): string {
  const params = new URLSearchParams(search);
  params.delete("view");
  params.set(OPEN_PARAM, "playbook");
  params.set("strategy", sslug);
  return `/agents/${encodeURIComponent(slug)}?${params}`;
}

// ── Alerts ──

/**
 * Something on this screen that wants a person.
 *
 * Derived, never fetched: every one of the three rules below reads data the
 * workspace already has on screen for other reasons, which is what lets Now
 * lead with a problem instead of with a spinner.
 */
export interface WorkspaceAlert {
  kind: "failed" | "unledgered" | "overdue";
  /** One sentence, in the reader's terms. */
  text: string;
  /** The tick to open, when the alert is about one. */
  tick?: number;
}

/** Whether a journal entry claims the agent put something into the world. */
const DEPLOY_WORDS = /\bdeploy(?:ed|ing|ment|ments|s)?\b/i;

/**
 * Did this run's own narrative say it deployed something?
 *
 * The second alert compares what the agent *said* against what the ledger
 * *recorded*, so this reads the words rather than the deeds — that is the whole
 * point of the comparison.
 */
export function journalNamesDeploy(
  decisions: readonly { action: string; reasoning: string }[],
): boolean {
  return decisions.some(
    (d) => DEPLOY_WORDS.test(d.action) || DEPLOY_WORDS.test(d.reasoning),
  );
}

/**
 * What Now leads with.
 *
 * Three rules, in the order a reader would want them:
 *
 * 1. **A deed came back not ok.** Only knowable since [[FEAT-102]], which gave
 *    the action log its arguments back and started recording controller writes
 *    with `ok: false` — before it, a tick that assembled a six-controller fleet
 *    and had six of its writes rejected left two indistinguishable rows.
 * 2. **It says it deployed and the ledger is empty.** Either the ownership
 *    claim failed or the narrative is wrong, and both are worth a person.
 * 3. **The tick is late.** The rule `LoopPulse` already draws in amber, said
 *    once more where somebody reading anything else will see it.
 *
 * `nowSec` is a parameter and not a `Date.now()` inside, for the reason every
 * clock in this codebase is: an alert that appears on its own is not testable,
 * and a render-phase read of a moving value is what the compiler forbids.
 */
export function alertsFor(input: {
  /** This run's deeds, from `actions.jsonl`. */
  actions: readonly { tick: number; ok: boolean; summary: string }[];
  /** How many rows the deployment ledger has for this run. */
  deployments: number;
  /** Whether the run's journal claims a deploy — {@link journalNamesDeploy}. */
  journalNamesDeploy: boolean;
  /** The live engine, or `null` when nothing is looping. */
  loop: {
    status: string;
    last_tick_at: number;
    frequency_sec: number;
  } | null;
  nowSec: number;
}): WorkspaceAlert[] {
  const alerts: WorkspaceAlert[] = [];

  // The newest failure, not every one: a run that failed forty times has one
  // problem, and forty rows is a wall rather than an alert.
  const failed = input.actions.filter((a) => !a.ok);
  const newest = failed[failed.length - 1];
  if (newest) {
    alerts.push({
      kind: "failed",
      tick: newest.tick,
      text:
        failed.length === 1
          ? `An action failed on tick ${newest.tick}: ${newest.summary}`
          : `${failed.length} actions failed, the last on tick ${newest.tick}: ${newest.summary}`,
    });
  }

  if (input.journalNamesDeploy && input.deployments === 0) {
    alerts.push({
      kind: "unledgered",
      text:
        "This run says it deployed something, but nothing is in its ledger — " +
        "either the deploy did not land or the run does not own what it made.",
    });
  }

  const loop = input.loop;
  if (
    loop &&
    loop.status === "running" &&
    loop.last_tick_at > 0 &&
    loop.frequency_sec > 0
  ) {
    const late = input.nowSec - (loop.last_tick_at + loop.frequency_sec);
    if (late > 0) {
      alerts.push({
        kind: "overdue",
        text: `The next tick is ${Math.floor(late)}s overdue.`,
      });
    }
  }

  return alerts;
}
