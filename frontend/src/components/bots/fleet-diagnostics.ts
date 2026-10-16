import {
  attributionIndex,
  countdown,
  ownerOf,
  type DeedIndex,
  type FleetOwner,
  type LiveLoop,
} from "@/lib/agent-attribution";
import type {
  ApiLag,
  BotSummary,
  CallerVolume,
  ExecutorHygiene,
  FleetDiagnostics as FleetDiagnosticsData,
  HostLoad,
  VenueCondition,
} from "@/lib/api";

/**
 * Which of my bots is in trouble, and why — from signals rather than from logs
 * (FEAT-141).
 *
 * The folding half, apart from the drawing half (`FleetDiagnostics.tsx`) for
 * the reason `lib/agent-attribution` is apart from the tree it feeds: nothing
 * here fetches or renders, so every threshold and every verdict below is
 * reachable from a test.
 *
 * GitHub issue [#64](https://github.com/hummingbot/condor/issues/64) asked for
 * a log viewer and got one (`LogsSection`, in the drawer beside this). What the
 * race's comment on that issue added is the list logs cannot answer, and that
 * is what this draws: the five signal groups, in the order an operator triages
 * them. The case that cost one entry 4h45m is the first of them — a container
 * reporting `running`, no controller inside it, and nothing successful for an
 * hour — and no single column says it, which is why they are shown together.
 *
 * ## Every signal has three states, and unknown is one of them
 *
 * `ok`, `flagged`, `unknown`. Unknown is explicit, labelled, and never styled
 * as ok: a diagnostics view that paints a missing reading green reproduces the
 * exact defect it exists to surface ([[CORR-725]], where no reading at all read
 * as *running*).
 *
 * ## It costs no poll
 *
 * Nothing here fetches. Every row is folded from payloads the fleet view
 * already has — the bots page, and the fleet map the page polls every five
 * seconds — because the view's constraint is that it adds no polling cost to a
 * dashboard that already polls per server.
 */

// ── Thresholds ──
//
// They live here, with the view that draws them, because each is a judgement an
// operator has to be able to second-guess — so the number and the band it came
// from are rendered next to the reading rather than hidden in a backend.

/**
 * The share of calls failing that flags a venue or an agent.
 *
 * Set from the race's measured bands rather than invented: volume strategies
 * failing 0.01–0.4% of orders were fine, and one DEX path at 93% went
 * unnoticed for hours. 5% is an order of magnitude above the top of the normal
 * band and an order of magnitude below the missed case, so neither end is a
 * close call.
 */
export const FAILURE_RATE_FLAG = 0.05;

/**
 * Attempts a rate needs before it is a rate at all.
 *
 * One refused call out of one is 100% and means nothing. A venue that is
 * genuinely refusing everything is reported by the throttling group on its
 * `condition`, which needs no sample size, so nothing is lost by refusing to
 * judge a small one here.
 */
export const MIN_ATTEMPTS_FOR_RATE = 20;

/** The stated band, rendered beside the rate so a borderline reading can be judged. */
export const FAILURE_BAND =
  "0.01–0.4% was normal at the race; one DEX path at 93% went unnoticed for hours";

/**
 * Load per vCPU that flags a host.
 *
 * 1.0x is the point at which runnable work exceeds the cores to run it, and the
 * race's oversubscribed case — a 2-vCPU host above 1.0x running high-frequency
 * makers — showed up as slow ticks and timeouts rather than as a crash, so it
 * was read as a strategy bug.
 */
export const LOAD_PER_VCPU_FLAG = 1.0;

/** How long without a successful action before a loop is flagged, in seconds. */
export const IDLE_SECONDS = 3600;

/**
 * How long a refusal keeps a caller reading as throttled, in seconds.
 *
 * `condor/agents/throttle.py:THROTTLED_FOR_SECONDS`, which is where the venue
 * half of these same counters gets its `condition` — the backend sends the
 * venue rows pre-judged and the caller rows as raw counts, so the window has
 * to be spelled once on this side for the caller row to be judged the same way.
 */
export const THROTTLED_SECONDS = 900;

// ── What "unknown" is, as an input ──
//
// The three defaults a caller reaches for when a payload did not carry a
// signal. They live here rather than with the wire types because *unknown* is
// this module's vocabulary: the whole rule is that a reading nobody took is
// drawn apart from a reading that came back clean.

/** No host reading at all — what an older backend's fleet map reads as. */
export const UNKNOWN_HOST_LOAD: HostLoad = {
  available: false,
  load_1m: 0,
  vcpus: 0,
  load_per_vcpu: 0,
};

/** No hygiene reading at all — what an older backend's bots page reads as. */
export const UNKNOWN_EXECUTOR_HYGIENE: ExecutorHygiene = {
  available: false,
  counted: 0,
  running: 0,
  terminated: 0,
  truncated: false,
  orphaned_running: [],
};

/** A fleet map that failed or has not landed: every signal unknown. */
export const UNKNOWN_DIAGNOSTICS: FleetDiagnosticsData = {
  host: UNKNOWN_HOST_LOAD,
  api_lag: [],
  venues: [],
  callers: [],
};

// ── Signals ──

export type SignalState = "ok" | "flagged" | "unknown";

/** One reading: what it is, what it says, and the facts behind the verdict. */
export interface Signal {
  label: string;
  state: SignalState;
  /** The reading itself, or `"unknown"` when there is none. */
  value: string;
  /** Why it reads that way. Every fact the verdict rests on, named. */
  facts: string[];
  /** The band or rule the verdict applied, when one is worth stating. */
  note?: string;
}

const UNKNOWN_VALUE = "unknown";

function unknown(label: string, why: string, note?: string): Signal {
  return { label, state: "unknown", value: UNKNOWN_VALUE, facts: [why], note };
}

/** `0.93`, or `null` when the counters cannot support a rate. */
export function failureRate(counts: {
  attempts: number;
  refusals: number;
  errors: number;
}): number | null {
  const attempts = Number(counts.attempts) || 0;
  if (attempts < MIN_ATTEMPTS_FOR_RATE) return null;
  const failed = (Number(counts.refusals) || 0) + (Number(counts.errors) || 0);
  return failed / attempts;
}

function pct(rate: number): string {
  if (rate > 0 && rate < 0.001) return "<0.1%";
  return `${(rate * 100).toFixed(rate < 0.1 ? 2 : 0)}%`;
}

/**
 * One call-failure rate, three-stated.
 *
 * `unknown` below {@link MIN_ATTEMPTS_FOR_RATE} attempts — deliberately not
 * `ok`, because "nothing has been tried" is not "nothing is wrong".
 */
export function rateSignal(
  label: string,
  counts: { attempts: number; successes: number; refusals: number; errors: number },
): Signal {
  const rate = failureRate(counts);
  const attempts = Number(counts.attempts) || 0;
  if (rate === null) {
    return unknown(
      label,
      `${attempts} attempt${attempts === 1 ? "" : "s"} — fewer than ${MIN_ATTEMPTS_FOR_RATE}, too few to rate`,
      FAILURE_BAND,
    );
  }
  return {
    label,
    state: rate >= FAILURE_RATE_FLAG ? "flagged" : "ok",
    value: pct(rate),
    facts: [
      `${attempts} attempts`,
      `${counts.successes} ok`,
      `${counts.refusals} refused`,
      `${counts.errors} errored`,
    ],
    note: FAILURE_BAND,
  };
}

/** `"4m 20s ago"`, or `"never"` for a stamp of 0 — never 1970. */
function age(at: number, nowMs: number): string {
  return at > 0 ? `${countdown(nowMs / 1000 - at)} ago` : "never";
}

// The two folds of the throttle counters are labelled in one place each, so the
// Activity row and the Throttling row for the same counters carry the same name
// and an operator can see they are one reading seen twice.
function venueLabel(venue: VenueCondition): string {
  return `${venue.venue || "unknown venue"} @ ${venue.host || "unknown host"}`;
}

function callerLabel(caller: CallerVolume): string {
  return `${caller.agent || "chat seat"} → ${caller.host || "unknown host"}`;
}

/**
 * One bot's liveness, and the 4h45m case it exists for.
 *
 * Flagged when the container says `running` and nothing inside it is: no
 * controller, or no successful action for {@link IDLE_SECONDS}. All three facts
 * are named whichever one fired, because the diagnosis is the conjunction — the
 * race's entry showed `running`, and that was true.
 *
 * `unknown` when no agent owns the bot, so there is no session whose last
 * successful action could be read — or when the session that owns it has not
 * acted yet, which is the same absence seen from the other side. A bot nobody
 * owns may be perfectly healthy; this view cannot say, and says so.
 */
export function livenessSignal(
  bot: BotSummary,
  live: LiveLoop | null | undefined,
  nowMs: number,
): Signal {
  const status = `status ${bot.status || "unknown"}`;
  const controllers = `${bot.num_controllers} controller${bot.num_controllers === 1 ? "" : "s"}`;
  if (!live) {
    return {
      label: bot.bot_name,
      state: bot.running_without_controllers ? "flagged" : "unknown",
      value: bot.running_without_controllers ? "running, nothing trading" : UNKNOWN_VALUE,
      facts: [
        status,
        controllers,
        "no agent session owns this bot — last successful action unknown",
      ],
    };
  }
  // A session that has not acted *yet* has no idle duration, not an infinite
  // one. Read as Infinity it was flagged "nothing worked for 1h" from its first
  // second — the restart case especially, where a fresh session adopts a
  // perfectly healthy bot and has nothing of its own to have done. There is no
  // session start stamp on this row to date the silence against, so the leg
  // says unknown until the session has acted once, which is this module's rule
  // everywhere else: a reading nobody took is drawn apart from a clean one.
  const acted = live.lastSuccessfulActionAt > 0;
  const idle = acted && nowMs / 1000 - live.lastSuccessfulActionAt >= IDLE_SECONDS;
  const facts = [
    status,
    controllers,
    `read ${age(live.lastReadingAt, nowMs)}`,
    `acted ${age(live.lastSuccessfulActionAt, nowMs)}`,
  ];
  if (!bot.running_without_controllers && !idle) {
    return acted
      ? { label: bot.bot_name, state: "ok", value: "live", facts }
      : {
          label: bot.bot_name,
          state: "unknown",
          value: UNKNOWN_VALUE,
          facts: [...facts, "this session has not acted yet — nothing to date the silence from"],
        };
  }
  const why: string[] = [];
  if (bot.running_without_controllers) why.push("nothing trading");
  if (idle) why.push(`nothing worked for ${countdown(IDLE_SECONDS)}`);
  return {
    label: bot.bot_name,
    state: "flagged",
    value: why.join(", "),
    facts,
    note: `flagged after ${countdown(IDLE_SECONDS)} without a successful action`,
  };
}

/** The host's load against its cores, three-stated. */
export function hostLoadSignal(host: HostLoad): Signal {
  if (!host.available) {
    return unknown(
      "Load per vCPU",
      "this platform reports no load average, or no CPU count",
    );
  }
  return {
    label: "Load per vCPU",
    state: host.load_per_vcpu > LOAD_PER_VCPU_FLAG ? "flagged" : "ok",
    value: `${host.load_per_vcpu.toFixed(2)}x`,
    facts: [`load ${host.load_1m.toFixed(2)}`, `${host.vcpus} vCPU`],
    note: `oversubscribed above ${LOAD_PER_VCPU_FLAG.toFixed(1)}x — slow ticks and timeouts, not crashes`,
  };
}

/** One live loop's last completed tick duration, against its own frequency. */
export function tickDurationSignal(
  runKey: string,
  live: LiveLoop,
): Signal {
  if (live.lastTickDuration <= 0) {
    return unknown(`${runKey} tick`, "no tick has completed yet");
  }
  const budget = Math.max(1, live.frequencySec);
  return {
    label: `${runKey} tick`,
    state: live.lastTickDuration > budget ? "flagged" : "ok",
    value: `${live.lastTickDuration.toFixed(1)}s`,
    facts: [`every ${budget}s`, `session ${live.sessionNum}`, `tick ${live.tickCount}`],
    note: "a tick longer than its own interval cannot keep up",
  };
}

/** One server's round-trip, as the order-placement lag it bounds. */
export function lagSignal(row: ApiLag): Signal {
  if (row.last_latency_ms <= 0) {
    return unknown(`${row.host} placement lag`, "no round-trip measured yet");
  }
  return {
    label: `${row.host} placement lag`,
    state: row.status === "online" && row.consecutive_failures === 0 ? "ok" : "flagged",
    value: `${row.last_latency_ms.toFixed(0)} ms`,
    facts: [
      `server ${row.status}`,
      `${row.consecutive_failures} consecutive failure${row.consecutive_failures === 1 ? "" : "s"}`,
    ],
    note: "a floor: an order cannot be placed faster than the API answers",
  };
}

/** This host's executor hygiene: accumulated terminal rows, and orphans. */
export function hygieneSignals(hygiene: ExecutorHygiene): Signal[] {
  if (!hygiene.available) {
    return [
      unknown(
        "Terminated executors",
        "no executor rows cached for this host, and this view does not fetch",
      ),
      unknown(
        "Orphaned running",
        "no executor rows cached for this host, and this view does not fetch",
      ),
    ];
  }
  const floor = hygiene.truncated ? " or more" : "";
  const orphans = hygiene.orphaned_running;
  return [
    {
      label: "Terminated executors",
      state: "ok",
      value: `${hygiene.terminated}${floor}`,
      facts: [
        `${hygiene.running} running`,
        `${hygiene.counted} rows read`,
        hygiene.truncated ? "newest page only — the count is a floor" : "whole cached page",
      ],
      note: "nothing upstream prunes a terminal row",
    },
    {
      label: "Orphaned running",
      state: orphans.length > 0 ? "flagged" : "ok",
      value: String(orphans.length),
      facts:
        orphans.length === 0
          ? ["every running executor has a live session"]
          : orphans.map(
              (o) =>
                `${o.executor_id} (${o.kind || "executor"} ${o.pair || "?"}) left by ${o.agent_slug}.${o.strategy_slug} session ${o.session_num}`,
            ),
      note:
        orphans.length > 0
          ? "it keeps trading and polling until stopped"
          : undefined,
    },
  ];
}

/** Refusals per venue, and per agent — the two folds of one set of counters. */
export function throttleSignals(
  venues: readonly VenueCondition[],
  callers: readonly CallerVolume[],
  nowMs: number,
): Signal[] {
  if (venues.length === 0 && callers.length === 0) {
    return [
      unknown(
        "Refusals",
        "no call has been accounted yet — a venue that never refused and a venue never called look the same",
      ),
    ];
  }
  const out: Signal[] = [];
  for (const venue of venues) {
    out.push({
      label: venueLabel(venue),
      state: venue.condition === "throttled" ? "flagged" : "ok",
      value: `${venue.refusals} refused`,
      facts: [
        `${venue.attempts} attempts`,
        `${venue.successes} ok`,
        `last refusal ${age(venue.last_refusal_at, nowMs)}`,
        venue.last_reason || "no reason recorded",
      ],
      note: "a venue condition, never an agent error",
    });
  }
  for (const caller of callers) {
    // `refusals` is a lifetime count, so flagging on it left the row — and the
    // group's badge — red for the rest of the process after a single refusal,
    // hours of clean calls and a finished session included, while the venue row
    // beside it cleared after the window. The count is the history; what an
    // operator can act on is whether a refusal is recent, which is the same
    // judgement `venue.condition` already carries.
    const refusedRecently =
      caller.refusals > 0 && nowMs / 1000 - caller.last_refusal_at <= THROTTLED_SECONDS;
    out.push({
      label: callerLabel(caller),
      state: refusedRecently ? "flagged" : "ok",
      value: `${caller.refusals} refused`,
      facts: [
        `${caller.attempts} attempts`,
        `${caller.successes} ok`,
        `${caller.errors} errored`,
        `last refusal ${age(caller.last_refusal_at, nowMs)}`,
        caller.agent_id ? `session ${caller.agent_id}` : "no session label",
      ],
      note: "whose call volume spent this host's budget",
    });
  }
  return out;
}

export interface FleetDiagnosticsProps {
  /** The fleet's bots, for the liveness group. */
  bots: readonly BotSummary[];
  /** The attribution join, so a bot can be asked which session owns it. */
  owners: readonly FleetOwner[];
  deeds: DeedIndex | null;
  /** Host, lag and throttle signals, off the fleet map (FEAT-141). */
  diagnostics: FleetDiagnosticsData;
  /** This host's executor hygiene, off the bots payload. */
  hygiene: ExecutorHygiene;
  /**
   * The clock, injected. Every age on screen is relative to it, and a view
   * that reads `Date.now()` itself cannot be tested at a known moment.
   */
  nowMs: number;
}

/** One titled group of signals, in the order an operator triages them. */
export interface SignalGroup {
  title: string;
  signals: Signal[];
}

/**
 * Every group, folded from the payloads the page already holds.
 *
 * Pure and exported so the same fold answers two questions: what the drawer
 * draws, and how many signals are flagged — which the button that opens the
 * drawer has to say *without* the drawer being open. The race's symptoms went
 * unnoticed for hours because they were somewhere you had to go and look, so a
 * count nobody can see would repeat the mistake.
 */
export function buildSignalGroups({
  bots,
  owners,
  deeds,
  diagnostics,
  hygiene,
  nowMs,
}: FleetDiagnosticsProps): SignalGroup[] {
  // One index above the loop, not one per bot: `attributionOf` builds a whole
  // index per call, which is what PERF-331 took out of the leaf folds.
  const attribute = attributionIndex([...owners], deeds);

  const liveness = bots.map((bot) =>
    livenessSignal(bot, ownerOf(owners, attribute(bot.bot_name).runKey)?.live, nowMs),
  );

  const activity: Signal[] = [];
  for (const venue of diagnostics.venues) {
    activity.push(rateSignal(venueLabel(venue), venue));
  }
  for (const caller of diagnostics.callers) {
    activity.push(rateSignal(callerLabel(caller), caller));
  }
  if (activity.length === 0) {
    activity.push(unknown("Call failure rate", "no call has been accounted yet", FAILURE_BAND));
  }

  // All three readings every time, each three-stated. A group that simply
  // omits the lag row when nothing measured one leaves the reader to notice an
  // absence, which is the one thing this view exists not to ask of them.
  const host: Signal[] = [hostLoadSignal(diagnostics.host)];
  const live = owners.filter((owner) => owner.live);
  if (live.length === 0) {
    host.push(unknown("Tick duration", "no loop is running on this host"));
  }
  for (const owner of live) {
    host.push(tickDurationSignal(owner.runKey, owner.live!));
  }
  if (diagnostics.api_lag.length === 0) {
    host.push(unknown("Placement lag", "no server has been polled yet"));
  }
  for (const row of diagnostics.api_lag) host.push(lagSignal(row));

  return [
    { title: "Liveness", signals: liveness },
    { title: "Activity", signals: activity },
    { title: "Host", signals: host },
    { title: "Executors", signals: hygieneSignals(hygiene) },
    {
      title: "Throttling",
      signals: throttleSignals(diagnostics.venues, diagnostics.callers, nowMs),
    },
  ];
}

/** How many signals are flagged, for the button that opens the drawer. */
export function flaggedCount(groups: readonly SignalGroup[]): number {
  return groups.reduce(
    (total, group) => total + group.signals.filter((s) => s.state === "flagged").length,
    0,
  );
}
