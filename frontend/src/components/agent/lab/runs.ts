// ── Runs and ticks, as rules (FEAT-099) ──
//
// Everything the Lab decides *about* a run that is not a decision about pixels:
// how a run is named in the URL, how long it lasted, and what colour a tick's
// beat is. Pure, no React, no fetching (the ARCH-300 split), so every rule below
// is reachable from a test rather than only from a rendered page.
//
// The beat rule is the one that earns the file. It has four outcomes and one of
// them exists only because of history: every session written before FEAT-097 has
// no `actions.jsonl` and journals `actions=0` on every tick, so the naive rule
// ("no actions = did nothing") would paint hundreds of real ticks as idle. The
// honest answer is a fourth state that says *we have no record*, and it has to
// be a state and not a footnote.

import type { AgentActionRow } from "@/lib/agent-attribution";
import type { AgentRunRow } from "@/lib/api";

/** A run's identity in the URL: `s3` is `session_3`, `e1` is `experiment_1`. */
export interface RunRef {
  kind: "session" | "experiment";
  number: number;
}

/** Parse a `?run=` value. `null` for anything that is not one. */
export function parseRunId(value: string | null | undefined): RunRef | null {
  if (!value) return null;
  const m = /^([se])(\d+)$/.exec(value.trim());
  if (!m) return null;
  const number = Number(m[2]);
  if (!Number.isInteger(number) || number <= 0) return null;
  return { kind: m[1] === "s" ? "session" : "experiment", number };
}

/** The inverse of `parseRunId` — what goes back into the URL. */
export function formatRunId(ref: RunRef): string {
  return `${ref.kind === "session" ? "s" : "e"}${ref.number}`;
}

/**
 * The short badge on a rail row: `S3`, `D1`, `R1`.
 *
 * A dry run and a single real tick both land in `dry_runs/` and only one of them
 * can lose money, so they are named apart here the same way `MODE_STYLES` colours
 * them apart.
 */
export function runLabel(run: Pick<AgentRunRow, "kind" | "number" | "execution_mode">): string {
  if (run.kind === "session") return `S${run.number}`;
  if (run.execution_mode === "run_once") return `R${run.number}`;
  if (run.execution_mode === "dry_run") return `D${run.number}`;
  return `E${run.number}`;
}

/** True while the run is still going — the rail's filled dot. */
export function isLiveRun(run: Pick<AgentRunRow, "status">): boolean {
  return run.status === "running" || run.status === "paused";
}

/**
 * How long the run lasted, in seconds — or `null` when it cannot be known.
 *
 * A live run is measured against the clock, which is why `nowSec` is a parameter
 * and not a `Date.now()` inside: a duration that changes on its own is not
 * testable.
 */
export function runDurationSec(
  run: Pick<AgentRunRow, "started_at" | "ended_at">,
  nowSec: number,
): number | null {
  const started = run.started_at;
  if (!started || started <= 0) return null;
  const ended = run.ended_at && run.ended_at > 0 ? run.ended_at : nowSec;
  const seconds = ended - started;
  return seconds >= 0 ? seconds : null;
}

/** `4h12m`, `12m`, `45s`. Compact enough to sit beside a tick count. */
export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "";
  const s = Math.floor(seconds);
  if (s >= 86400) {
    const days = Math.floor(s / 86400);
    return `${days}d${Math.floor((s % 86400) / 3600)}h`;
  }
  if (s >= 3600) return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  if (s >= 60) return `${Math.floor(s / 60)}m`;
  return `${s}s`;
}

/** The rail's second line: `20 ticks · 4h12m`. */
export function runFacts(run: AgentRunRow, nowSec: number): string {
  const ticks = `${run.tick_count} tick${run.tick_count === 1 ? "" : "s"}`;
  const duration = formatDuration(runDurationSec(run, nowSec));
  return duration ? `${ticks} · ${duration}` : ticks;
}

/**
 * Whether this run's money was ever actually priced.
 *
 * Most runs never traded, and the money pipeline answers for them with a full
 * set of zeros — which the KPI strip then renders as eight `+$0.00` tiles. That
 * is not a fact about the run, it is the absence of one, and printing it is
 * what makes every agent surface today read as a flat ledger. So: no priced
 * money, no strip.
 *
 * Deliberately generous about what counts as priced — a real session that
 * genuinely broke even still has volume, fees, closes or an open position — so
 * the only thing filtered out is the all-zero answer.
 */
export function hasPricedMoney(
  perf:
    | {
        total_pnl?: number;
        realized_pnl?: number;
        unrealized_pnl?: number;
        volume?: number;
        fees?: number;
        trade_count?: number;
        open_count?: number;
      }
    | null
    | undefined,
): boolean {
  if (!perf) return false;
  return [
    perf.total_pnl,
    perf.realized_pnl,
    perf.unrealized_pnl,
    perf.volume,
    perf.fees,
    perf.trade_count,
    perf.open_count,
  ].some((n) => typeof n === "number" && n !== 0);
}

// ── The beat ──

/**
 * What one tick's beat says.
 *
 * - `failed` — a deed on this tick came back not ok.
 * - `ok` — deeds ran and every one of them worked.
 * - `idle` — nothing was done, and the run keeps a log that would have said so.
 * - `unlogged` — this run has no action log at all, so nothing is known about
 *   what the tick did. Every session written before FEAT-097 lands here, and
 *   painting them `idle` would be an assertion the data does not support.
 */
export type BeatState = "failed" | "ok" | "idle" | "unlogged";

/** Deeds grouped by the tick they happened on — the spine's join. */
export function actionsByTick(rows: readonly AgentActionRow[]): Map<number, AgentActionRow[]> {
  const byTick = new Map<number, AgentActionRow[]>();
  for (const row of rows) {
    const bucket = byTick.get(row.tick);
    if (bucket) bucket.push(row);
    else byTick.set(row.tick, [row]);
  }
  return byTick;
}

/**
 * The beat rule, in the order the design states it.
 *
 * `journalActions` is the journal's own `actions=N` for the tick, which comes
 * from the same fold that writes `actions.jsonl` — so the two can only disagree
 * when the log is missing, and that is exactly the case the last line catches.
 */
export function beatState(input: {
  actions: readonly AgentActionRow[];
  journalActions: number;
  hasActionsLog: boolean;
}): BeatState {
  if (input.actions.some((a) => !a.ok)) return "failed";
  if (input.actions.length > 0) return "ok";
  if (input.hasActionsLog && input.journalActions === 0) return "idle";
  return "unlogged";
}

/** What hovering a beat says, when the beat itself cannot say it. */
export const BEAT_TITLES: Record<BeatState, string> = {
  failed: "an action failed on this tick",
  ok: "actions ran and succeeded",
  idle: "no actions on this tick",
  unlogged: "no action log for this run",
};
