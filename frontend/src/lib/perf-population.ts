// ── The live fleet, as leaves (FEAT-109) ──
//
// `PerfBrowser` built the running population inside its own body, which was
// fine while it was the only thing that folded a fleet. FEAT-109 adds a second
// reader — the workspace's Money view, which has to report *the same number*
// `/bots` reports at `?scope=agent:{runKey}` — and a second reader means the
// construction has to be shared rather than copied. A copy would make the two
// screens agree by coincidence and drift the first time a leaf gains a field.
//
// The terminated population followed for the same reason: the execution dock
// credits each agent with what its stopped bots and closed executors earned, and
// that has to be the number `/bots?population=terminated` folds for the same
// agent — so `terminatedLeaves` is the browser's construction, lifted, not a
// second one written beside it.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

import { attributionIndex, type DeedIndex, type FleetOwner } from "@/lib/agent-attribution";
import type { BotRunInfo, ControllerInfo, ExecutorInfo } from "@/lib/api";
import { isExecutorActive, toMs } from "@/lib/formatters";
import {
  UNATTACHED_BOT,
  leafFromController,
  leafFromExecutor,
  leafFromTerminatedController,
  type ConvertQuote,
  type PerfLeaf,
} from "@/lib/perf-tree";
import type { ConvertFn } from "@/lib/rates";
import { buildAttributor, runWindows, type Attributor } from "@/lib/run-attribution";

/**
 * `useRates`'s converter, in the shape the fold takes.
 *
 * Two adaptations in one place rather than one per host: a leaf is denominated
 * in its **pair's quote** and `useRates` is asked about a currency, and the
 * fold wants a number where the rate answers with a number and whether it could
 * convert at all. FEAT-109's whole premise is that the headline and `/bots`
 * report one quantity — they would not, by an FX rate, if either host wrote its
 * own adapter and the two ever disagreed about the fallback quote.
 */
export function quoteConverter(convert: ConvertFn): ConvertQuote {
  return (value: number, pair: string) => convert(value, pair?.split("-")[1] || "USDT").value;
}

/**
 * Which bot each controller id is running, or `null` where two bots share it.
 *
 * An `ExecutorInfo` carries no `bot_name`, so the bot has to come from the
 * controller it hangs under. A config id is shared by every bot running that
 * config, which is normally one; where it is not, the answer is `null` and the
 * executor is left unattached rather than credited to whichever bot was seen
 * first.
 */
export function botsByController(
  controllers: readonly ControllerInfo[],
): Map<string, string | null> {
  const owners = new Map<string, string | null>();
  for (const c of controllers) {
    const id = c.controller_id || c.controller_name;
    if (!id) continue;
    const known = owners.get(id);
    owners.set(id, known === undefined || known === c.bot_name ? c.bot_name : null);
  }
  return owners;
}

/**
 * Everything trading right now, in the browser's one vocabulary.
 *
 * Every live controller, plus the executors currently working — including the
 * standalone ones an agent created, whose `controller_id` *is* their session's
 * agent id and which therefore belong to no controller row at all.
 *
 * **Attribution is `attributionOf` and nothing else**: the bot's namespace, the
 * name a strategy declared, the session id a standalone executor is tagged
 * with, and only then the record Condor kept of its own deeds (FEAT-096/106).
 * Bot name first, because a controller is attributed through its bot and an
 * executor working under one inherits that answer — which leaves the
 * `controller_id` fallback to the executor nobody claims.
 */
export function runningLeaves({
  controllers,
  executors,
  owners,
  deeds,
  botByController = botsByController(controllers),
}: {
  controllers: readonly ControllerInfo[];
  executors: readonly ExecutorInfo[];
  owners: FleetOwner[];
  deeds: DeedIndex | null;
  /** Passed in by a caller that already has one; built here otherwise. */
  botByController?: Map<string, string | null>;
}): PerfLeaf[] {
  const all: PerfLeaf[] = [];
  // Prepared once for the whole fold, not once per record: `owners` cannot
  // change while this runs, and both loops below ask it the same two
  // loop-invariant questions (PERF-331).
  const agentOf = attributionIndex(owners, deeds);
  for (const c of controllers) {
    const att = agentOf(c.bot_name, "");
    all.push(leafFromController(c, att.runKey, att.how));
  }
  for (const ex of executors) {
    if (!isExecutorActive(ex.status)) continue;
    const bot = botByController.get(ex.controller_id) ?? UNATTACHED_BOT;
    const att = agentOf(bot, ex.controller_id);
    all.push(leafFromExecutor(ex, bot, att.runKey, att.how));
  }
  return all;
}

/**
 * The run behind each bot name, for the two things a controller record cannot
 * say: when its bot stopped, and which archive it left.
 *
 * A bot name carries its own deploy timestamp, so it is unique per run in
 * practice; where it is not, the most recent run wins, which is the one the
 * finished controllers on screen belong to.
 */
export function latestRunByBot(runs: readonly BotRunInfo[]): Map<string, BotRunInfo> {
  const latest = new Map<string, BotRunInfo>();
  for (const run of runs) {
    const seen = latest.get(run.bot_name);
    if (!seen || (run.created_at ?? "") > (seen.created_at ?? "")) latest.set(run.bot_name, run);
  }
  return latest;
}

/**
 * Everything that has finished, in the browser's one vocabulary: every closed
 * executor, plus the controllers the finished runs left behind.
 *
 * A closed executor is filed under the run that **opened** it, asked through
 * the run-window attributor at its own start time — a position that outlived
 * its bot's stop still belongs to the run that opened it. The live fleet is the
 * fallback for an executor of a bot that is still deployed (it has no closed
 * window to sit in), and `(unattached)` for one that belongs to no run at all,
 * which on a real server is every hand-opened position.
 *
 * `windowCutoff` (epoch ms, `0` for no window) drops what finished before it:
 * a period called "the last week" is the trading that *stopped* in it.
 *
 * `attribute` and `runByBot` are taken from a caller that already memoised
 * them and built from `runs` otherwise.
 */
export function terminatedLeaves({
  executors,
  terminatedControllers,
  runs,
  owners,
  deeds,
  botByController,
  windowCutoff = 0,
  attribute = buildAttributor(runWindows(runs)),
  runByBot = latestRunByBot(runs),
}: {
  executors: readonly ExecutorInfo[];
  terminatedControllers: readonly ControllerInfo[];
  runs: readonly BotRunInfo[];
  owners: FleetOwner[];
  deeds: DeedIndex | null;
  /** The live fleet's controller → bot map — see {@link botsByController}. */
  botByController: Map<string, string | null>;
  windowCutoff?: number;
  attribute?: Attributor;
  runByBot?: Map<string, BotRunInfo>;
}): PerfLeaf[] {
  const all: PerfLeaf[] = [];
  // One index for the whole walk (PERF-331): this population walks every
  // executor the fleet has ever had.
  const agentOf = attributionIndex(owners, deeds);
  const closedBotOf = (ex: ExecutorInfo, startedAt: number | null) => {
    if (!ex.controller_id) return UNATTACHED_BOT;
    const at = startedAt ?? (ex.close_timestamp > 0 ? toMs(ex.close_timestamp) : null);
    const owner = at === null ? null : attribute(ex.controller_id, at);
    return owner ?? botByController.get(ex.controller_id) ?? UNATTACHED_BOT;
  };
  for (const ex of executors) {
    if (isExecutorActive(ex.status)) continue;
    const started = ex.timestamp > 0 ? toMs(ex.timestamp) : null;
    const bot = closedBotOf(ex, started);
    const att = agentOf(bot, ex.controller_id);
    const leaf = leafFromExecutor(ex, bot, att.runKey, att.how);
    if (windowCutoff && leaf.endedAt !== null && leaf.endedAt < windowCutoff) continue;
    all.push(leaf);
  }
  // The spine of the terminated tree, exactly as the live controllers are the
  // spine of the running one: a controller record covers every executor it
  // ever ran, including the ones that closed long before the bounded executor
  // walk reaches.
  for (const ctrl of terminatedControllers) {
    const att = agentOf(ctrl.bot_name, "");
    const leaf = leafFromTerminatedController(
      ctrl,
      runByBot.get(ctrl.bot_name),
      att.runKey,
      att.how,
    );
    if (windowCutoff && leaf.endedAt !== null && leaf.endedAt < windowCutoff) continue;
    all.push(leaf);
  }
  return all;
}
