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

/**
 * The four kinds of work a run can be (FEAT-111).
 *
 * The first two are a sequence of ticks under a strategy. The other two are one
 * stretch of work each, they belong to no strategy, and they are why an agent
 * that is only ever chatted with — Condor included — used to have an empty rail.
 */
export const RUN_KINDS = ["session", "experiment", "delegation", "conversation"] as const;

export type RunKind = (typeof RUN_KINDS)[number];

/** The letter each kind takes in a `?run=` value. Mirrors `all_runs.py`. */
const KIND_LETTERS: Record<RunKind, string> = {
  session: "s",
  experiment: "e",
  delegation: "d",
  conversation: "c",
};

const KIND_FOR_LETTER: Record<string, RunKind> = {
  s: "session",
  e: "experiment",
  d: "delegation",
  c: "conversation",
};

/** Whether this kind is a loop's run — a strategy, an ordinal and ticks. */
export function isLoopRun(kind: string): boolean {
  return kind === "session" || kind === "experiment";
}

/**
 * A run's identity in the URL: `s:3` is `session_3`, `c:7f3a` is a conversation.
 *
 * `number` is the ordinal for the two kinds that have one and `0` for the two
 * that do not; `id` is the opaque half, and is the only one of the two that
 * every kind can be matched on.
 */
export interface RunRef {
  kind: RunKind;
  number: number;
  id: string;
}

/**
 * Parse a `?run=` value. `null` for anything that is not one.
 *
 * Two forms, one written. `s:3` is the grammar since FEAT-111 — a letter, a
 * colon and an id that does not have to be a number, because a conversation's
 * is a uuid. `s3` is the form the Lab wrote before that, and it keeps parsing
 * forever: those links are in bookmarks and in notification payloads, exactly
 * as `?tab=` keeps parsing as a synonym for `?view=`.
 */
export function parseRunId(value: string | null | undefined): RunRef | null {
  if (!value) return null;
  const raw = value.trim();

  const scoped = /^([sedc]):(.+)$/.exec(raw);
  if (scoped) {
    const kind = KIND_FOR_LETTER[scoped[1]];
    const id = scoped[2];
    if (!isLoopRun(kind)) return { kind, number: 0, id };
    const number = Number(id);
    if (!/^\d+$/.test(id) || !Number.isInteger(number) || number <= 0) return null;
    return { kind, number, id };
  }

  const legacy = /^([se])(\d+)$/.exec(raw);
  if (!legacy) return null;
  const number = Number(legacy[2]);
  if (!Number.isInteger(number) || number <= 0) return null;
  return { kind: KIND_FOR_LETTER[legacy[1]], number, id: legacy[2] };
}

/** The inverse of `parseRunId` — what goes back into the URL, always `kind:id`. */
export function formatRunId(ref: RunRef): string {
  return `${KIND_LETTERS[ref.kind]}:${ref.id || ref.number}`;
}

/**
 * The short badge on a rail row: `S3`, `D1`, `R1`, `C`, `T`.
 *
 * A dry run and a single real tick both land in `dry_runs/` and only one of them
 * can lose money, so they are named apart here the same way `MODE_STYLES` colours
 * them apart. The two kinds with no ordinal get a bare letter: `C` for a chat
 * and `T` for a background task — `D` was already a dry run's, and one letter
 * meaning two things is how a rail stops being readable.
 */
export function runLabel(run: Pick<AgentRunRow, "kind" | "number" | "execution_mode">): string {
  if (run.kind === "conversation") return "C";
  if (run.kind === "delegation") return "T";
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

/**
 * The rail's second line: `20 ticks · 4h12m`.
 *
 * A tick is a loop concept, so the two kinds that have none say only how long
 * they lasted. Printing `0 ticks` beside a chat would be an assertion about the
 * chat rather than a fact about it — the same honesty rule the beat states are
 * built on.
 */
export function runFacts(run: AgentRunRow, nowSec: number): string {
  const duration = formatDuration(runDurationSec(run, nowSec));
  if (!isLoopRun(run.kind)) return duration;
  const ticks = `${run.tick_count} tick${run.tick_count === 1 ? "" : "s"}`;
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
