/**
 * Who owns a closed executor, pinned (FEAT-089).
 *
 * Every case here is a way attribution can report trading under the wrong
 * name: an executor credited to a run that had already stopped, one credited
 * to whichever of two concurrent runs was seen first, one silently dropped
 * because its run is still live and has no end, and a manual position credited
 * to a bot because a lookup answered a question nobody could answer.
 */

import { describe, expect, it } from "vitest";

import type { BotRunInfo } from "./api";
import { buildAttributor, runWindows, type RunWindow } from "./run-attribution";

const HOUR = 3_600_000;
const T0 = Date.parse("2026-08-01T00:00:00Z");

function win(over: Partial<RunWindow> = {}): RunWindow {
  return {
    bot: "alpha",
    deployedAt: T0,
    stoppedAt: T0 + 10 * HOUR,
    controllerIds: ["pmm_1"],
    ...over,
  };
}

function botRun(over: Partial<BotRunInfo> = {}): BotRunInfo {
  return {
    bot_name: "alpha",
    bot_run_id: 1,
    account_name: "master",
    strategy_type: "controller",
    strategy_name: "v2_with_controllers",
    run_status: "STOPPED",
    deployment_status: "ARCHIVED",
    created_at: new Date(T0).toISOString(),
    stopped_at: new Date(T0 + 10 * HOUR).toISOString(),
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    volume_traded: 0,
    num_controllers: 1,
    archive_db_path: null,
    controller_ids: ["pmm_1"],
    is_live: false,
    ...over,
  };
}

describe("runWindows", () => {
  it("keeps a run that declared controllers and can bound a window", () => {
    expect(runWindows([botRun()])).toEqual([
      { bot: "alpha", deployedAt: T0, stoppedAt: T0 + 10 * HOUR, controllerIds: ["pmm_1"] },
    ]);
  });

  it("leaves a live run's window open-ended", () => {
    const [w] = runWindows([botRun({ is_live: true, stopped_at: null })]);
    expect(w.stoppedAt).toBeNull();
  });

  // A window with no start contains every instant, so it would swallow every
  // executor on the server rather than the handful it actually ran.
  it("drops a run that cannot say when it started", () => {
    expect(runWindows([botRun({ created_at: null })])).toEqual([]);
    expect(runWindows([botRun({ created_at: "not a date" })])).toEqual([]);
  });

  it("drops a run that declared no controllers", () => {
    expect(runWindows([botRun({ controller_ids: [] })])).toEqual([]);
  });

  // Ending the window where the parse failed would orphan everything the run
  // traded after that point; open-ended keeps it attributed.
  it("treats an unreadable stop time as still open rather than as an end", () => {
    const [w] = runWindows([botRun({ stopped_at: "nonsense" })]);
    expect(w.stoppedAt).toBeNull();
  });
});

describe("buildAttributor", () => {
  it("names the run whose window contains the instant", () => {
    const at = buildAttributor([win()]);
    expect(at("pmm_1", T0 + 5 * HOUR)).toBe("alpha");
  });

  it("includes both ends of the window", () => {
    const at = buildAttributor([win()]);
    expect(at("pmm_1", T0)).toBe("alpha");
    expect(at("pmm_1", T0 + 10 * HOUR)).toBe("alpha");
  });

  it("attributes nothing before the run started or after it stopped", () => {
    const at = buildAttributor([win()]);
    expect(at("pmm_1", T0 - 1)).toBeNull();
    expect(at("pmm_1", T0 + 10 * HOUR + 1)).toBeNull();
  });

  it("keeps attributing to a run that has not stopped", () => {
    const at = buildAttributor([win({ stoppedAt: null })]);
    expect(at("pmm_1", T0 + 10_000 * HOUR)).toBe("alpha");
  });

  // The whole reason attribution is by window and not by id: `main` is the
  // default config id, so the same id names a different bot in every run.
  it("tells two runs of the same shared config id apart by when", () => {
    const at = buildAttributor([
      win({ bot: "alpha", deployedAt: T0, stoppedAt: T0 + 5 * HOUR, controllerIds: ["main"] }),
      win({
        bot: "beta",
        deployedAt: T0 + 6 * HOUR,
        stoppedAt: T0 + 12 * HOUR,
        controllerIds: ["main"],
      }),
    ]);
    expect(at("main", T0 + 2 * HOUR)).toBe("alpha");
    expect(at("main", T0 + 8 * HOUR)).toBe("beta");
    // The gap between them belongs to neither.
    expect(at("main", T0 + 5.5 * HOUR)).toBeNull();
  });

  // Crediting the trading to whichever run was indexed first would file one
  // bot's PnL under another's name, which is worse than declining to answer.
  it("refuses to choose between two concurrent runs claiming the instant", () => {
    const at = buildAttributor([
      win({ bot: "alpha", controllerIds: ["main"] }),
      win({ bot: "beta", controllerIds: ["main"] }),
    ]);
    expect(at("main", T0 + 5 * HOUR)).toBeNull();
  });

  it("is not confused by the same run appearing twice", () => {
    const at = buildAttributor([win({ controllerIds: ["main"] }), win({ controllerIds: ["main"] })]);
    expect(at("main", T0 + 5 * HOUR)).toBe("alpha");
  });

  // Windows are ordered by start, so a long early run sits behind shorter ones
  // that started later. The scan has to reach back past them.
  it("finds a long early run behind later, shorter ones", () => {
    const at = buildAttributor([
      win({ bot: "long", deployedAt: T0, stoppedAt: T0 + 100 * HOUR, controllerIds: ["a"] }),
      win({ bot: "short", deployedAt: T0 + 10 * HOUR, stoppedAt: T0 + 11 * HOUR, controllerIds: ["b"] }),
      win({ bot: "short2", deployedAt: T0 + 20 * HOUR, stoppedAt: T0 + 21 * HOUR, controllerIds: ["b"] }),
    ]);
    expect(at("a", T0 + 50 * HOUR)).toBe("long");
  });

  it("attributes nothing for a controller no run ever declared", () => {
    const at = buildAttributor([win()]);
    // The real shape of every executor table measured: `main` is Condor's own
    // default for a hand-opened position, and no deployment declares it.
    expect(at("main", T0 + 5 * HOUR)).toBeNull();
    expect(at("", T0 + 5 * HOUR)).toBeNull();
  });

  it("attributes nothing for an instant it cannot read", () => {
    const at = buildAttributor([win()]);
    expect(at("pmm_1", NaN)).toBeNull();
  });

  it("answers null for every id when there are no runs at all", () => {
    expect(buildAttributor([])("pmm_1", T0)).toBeNull();
  });
});
