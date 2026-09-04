// ── The live fleet, as leaves (FEAT-109) ──
//
// `PerfBrowser` built the running population inside its own body, which was
// fine while it was the only thing that folded a fleet. FEAT-109 adds a second
// reader — the workspace's Money view, which has to report *the same number*
// `/bots` reports at `?scope=agent:{runKey}` — and a second reader means the
// construction has to be shared rather than copied. A copy would make the two
// screens agree by coincidence and drift the first time a leaf gains a field.
//
// The terminated population deliberately stays in the browser: it needs the
// run-window attributor, the period cutoff and the finished-controller records,
// none of which the Money view has or wants. What is lifted is the half that
// two hosts genuinely share.
//
// Nothing here fetches and nothing here renders (the ARCH-300 split).

import { attributionOf, type DeedIndex, type FleetOwner } from "@/lib/agent-attribution";
import type { ControllerInfo, ExecutorInfo } from "@/lib/api";
import { isExecutorActive } from "@/lib/formatters";
import {
  UNATTACHED_BOT,
  leafFromController,
  leafFromExecutor,
  type PerfLeaf,
} from "@/lib/perf-tree";

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
  for (const c of controllers) {
    const att = attributionOf(owners, deeds, c.bot_name, "");
    all.push(leafFromController(c, att.runKey, att.how));
  }
  for (const ex of executors) {
    if (!isExecutorActive(ex.status)) continue;
    const bot = botByController.get(ex.controller_id) ?? UNATTACHED_BOT;
    const att = attributionOf(owners, deeds, bot, ex.controller_id);
    all.push(leafFromExecutor(ex, bot, att.runKey, att.how));
  }
  return all;
}
