// ── The agent workspace, as rules (FEAT-103) ──
//
// One route — `/agents/:slug` — and every state it can be in written down in
// the URL: which section is open, which strategy is in scope, which run, which
// tick. Four pages, three tab strips and five URL-less panels collapse onto
// that grammar, so the rules that read it have to be reachable from a test
// rather than only from a rendered page. Same split `lab/runs.ts` made, and the
// same reason: `AgentWorkspace` reads more parameters than any page before it,
// and none of the reading happens in JSX.
//
// Nothing here fetches and nothing here renders.

import {
  KNOWLEDGE_TABS,
  type KnowledgeTabId,
} from "@/components/agent/knowledgeTabs";
import { parseRunId, type RunRef } from "@/components/agent/lab/runs";
import type { AgentRunRow, StrategySummary } from "@/lib/api";

/**
 * What the agent is *doing*, as opposed to what it *is*.
 *
 * The second half of the taxonomy is `KNOWLEDGE_TABS`, imported rather than
 * restated — the sections an agent is read in already have a module, and two
 * lists of the same seven names is how they drift.
 */
export const DOING_VIEWS = [
  "now",
  "runs",
  "tick",
  "money",
  "fleet",
  "playbook",
] as const;

export type DoingViewId = (typeof DOING_VIEWS)[number];

/** Every value `?view=` can take. */
export const WORKSPACE_VIEWS = [...DOING_VIEWS, ...KNOWLEDGE_TABS] as const;

export type WorkspaceViewId = DoingViewId | KnowledgeTabId;

/** The view a bare `/agents/:slug` opens on — never Brain (FEAT-103). */
export const DEFAULT_VIEW: WorkspaceViewId = "now";

/**
 * Whether a string off a URL names a view, so a hand-typed `?view=` can be
 * trusted. Mirrors `isKnowledgeTab`, which it partly delegates to.
 */
export function isWorkspaceView(id: string | null | undefined): id is WorkspaceViewId {
  return !!id && (WORKSPACE_VIEWS as readonly string[]).includes(id);
}

/** Everything the URL says, once. */
export interface WorkspaceUrl {
  view: WorkspaceViewId;
  /** `?strategy=` — the scope, or `null` for "decide from the data". */
  strategy: string | null;
  /** `?run=` as a reference, or `null` for the newest run in scope. */
  run: RunRef | null;
  /** `?tick=`, or `null` for the run overview. */
  tick: number | null;
}

/**
 * Read the whole workspace state out of a query string.
 *
 * `?tab=` is honoured as a synonym for `?view=` because it is what the agent
 * page spelled its section as (FEAT-081) and those links are in notification
 * payloads and in bookmarks. It is never *written* — one grammar goes out.
 */
export function parseWorkspace(search: string | URLSearchParams): WorkspaceUrl {
  const params =
    typeof search === "string" ? new URLSearchParams(search) : search;
  const named = params.get("view") ?? params.get("tab");
  const tickParam = params.get("tick");
  return {
    view: isWorkspaceView(named) ? named : DEFAULT_VIEW,
    strategy: params.get("strategy") || null,
    run: parseRunId(params.get("run")),
    tick: tickParam && /^\d+$/.test(tickParam) ? Number(tickParam) : null,
  };
}

/**
 * Which spine entry reads as current for a view.
 *
 * A tick is not a section, it is a moment of a run — so the spine keeps saying
 * *Runs* while you read one, and going back up is one click on the entry that
 * is already lit rather than a hunt for where you came from.
 */
export function spineSectionFor(view: WorkspaceViewId): WorkspaceViewId {
  return view === "tick" ? "runs" : view;
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
 * scope, which is the Lab's rule verbatim (FEAT-099) — a bare `?view=runs` has
 * to open on something.
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
    const match = scoped.find(
      (r) => r.kind === run.kind && r.number === run.number,
    );
    if (match) return match;
  }
  return scoped[0] ?? null;
}
