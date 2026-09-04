/**
 * Moving the workspace's URL — the half `views.test.ts` does not cover.
 *
 * These rules used to be nine hand-written `setParams({…})` calls in the page,
 * so "moving the strategy drops the run and the tick" was true because six
 * callers remembered it. Now it is true because this file says so, which is
 * what lets a second host (the chat's pane, FEAT-117) spend the same grammar
 * without spelling the cascades out again.
 */

import { describe, expect, it } from "vitest";

import {
  applyWorkspacePatch,
  clearWorkspaceSearch,
  patchReplaces,
  workspaceSearch,
  WORKSPACE_PARAMS,
} from "./workspaceUrl";

const q = (s: string) => new URLSearchParams(s);
/** What a patch leaves behind, as a plain string, for readable expectations. */
const after = (search: string, patch: Parameters<typeof applyWorkspacePatch>[1]) =>
  applyWorkspacePatch(q(search), patch).toString();

describe("applyWorkspacePatch", () => {
  it("writes only the keys it is given", () => {
    expect(after("?view=money", { strategy: "brl_mm" })).toBe(
      "view=money&strategy=brl_mm",
    );
  });

  it("leaves every parameter that is not the workspace's alone", () => {
    // The pane spends this grammar on the *home's* query string, where
    // `?panel=`, `?who=` and `?desk=` are somebody else's state.
    const next = applyWorkspacePatch(
      q("?panel=agent&who=brigado&desk=execution"),
      { view: "money" },
    );
    expect(next.get("panel")).toBe("agent");
    expect(next.get("who")).toBe("brigado");
    expect(next.get("desk")).toBe("execution");
    expect(next.get("view")).toBe("money");
  });

  it("does not mutate the params it was handed", () => {
    const before = q("?view=money");
    applyWorkspacePatch(before, { view: "fleet" });
    expect(before.get("view")).toBe("money");
  });

  it("never spells out the default view", () => {
    // The shortest URL that lands somewhere is the one people paste.
    expect(after("?view=money", { view: "now" })).toBe("");
    expect(after("", { view: "now" })).toBe("");
  });

  it("retires the legacy `?tab=` on any write", () => {
    // `parseWorkspace` still reads it; nothing writes it back.
    expect(after("?tab=skills", { view: "money" })).toBe("view=money");
  });

  it("clears a key given as null, and treats an empty string the same", () => {
    expect(after("?strategy=brl_mm", { strategy: null })).toBe("");
    expect(after("?strategy=brl_mm", { strategy: "" })).toBe("");
  });

  describe("the cascades", () => {
    it("drops the run and the tick when the strategy moves", () => {
      expect(
        after("?view=runs&strategy=old&run=s:3&tick=40", { strategy: "new" }),
      ).toBe("view=runs&strategy=new");
    });

    it("drops them when the strategy is cleared, too", () => {
      expect(after("?strategy=old&run=s:3&tick=40", { strategy: null })).toBe("");
    });

    it("drops the tick when the run moves, and keeps the scope", () => {
      expect(
        after("?view=runs&strategy=brl_mm&run=s:3&tick=40", { run: "s:4" }),
      ).toBe("view=runs&strategy=brl_mm&run=s%3A4");
    });

    it("keeps the run when the caller names one with the strategy", () => {
      // Opening a run from the rail is "this loop, and this run of it" — one
      // call, not a scope change that immediately undoes itself.
      expect(
        after("?strategy=old&run=s:3&tick=40", {
          strategy: "new",
          run: "s:9",
        }),
      ).toBe("strategy=new&run=s%3A9");
    });

    it("keeps a tick the caller names alongside a run", () => {
      expect(after("?run=s:3&tick=40", { run: "s:4", tick: 7 })).toBe(
        "run=s%3A4&tick=7",
      );
    });

    it("moves a tick on its own without disturbing the run", () => {
      expect(after("?view=runs&run=s:3&tick=40", { tick: 41, view: "tick" })).toBe(
        "view=tick&run=s%3A3&tick=41",
      );
    });

    it("clears the tick without clearing the run", () => {
      expect(after("?view=tick&run=s:3&tick=40", { tick: null, view: "runs" })).toBe(
        "view=runs&run=s%3A3",
      );
    });
  });
});

describe("patchReplaces", () => {
  it("replaces for a bare section change", () => {
    // Reading down the spine is not nine history entries to press Back through.
    expect(patchReplaces({ view: "money" })).toBe(true);
  });

  it("pushes for a scope, a run or a tick", () => {
    expect(patchReplaces({ strategy: "brl_mm" })).toBe(false);
    expect(patchReplaces({ run: "s:3" })).toBe(false);
    expect(patchReplaces({ tick: 40 })).toBe(false);
    // A section change that also moves the scope is a step, not a correction.
    expect(patchReplaces({ view: "playbook", strategy: "brl_mm" })).toBe(false);
  });
});

describe("workspaceSearch", () => {
  it("takes the four keys and leaves the host's own behind", () => {
    expect(
      workspaceSearch(
        q("?panel=agent&who=brigado&view=money&strategy=brl_mm&run=s:3&tick=40"),
      ).toString(),
    ).toBe("view=money&strategy=brl_mm&run=s%3A3&tick=40");
  });

  it("hands back the exact `?run=` spelling it was given", () => {
    // `s3` is the Lab's older form and still parses; a trip through the
    // full-screen door must not rewrite what somebody pasted.
    expect(workspaceSearch(q("?run=s3")).get("run")).toBe("s3");
  });

  it("is empty for a bare home", () => {
    expect(workspaceSearch(q("?panel=agent")).toString()).toBe("");
  });
});

describe("clearWorkspaceSearch", () => {
  it("removes every trace of a workspace and nothing else", () => {
    const next = clearWorkspaceSearch(
      q("?panel=routines&view=money&strategy=brl_mm&run=s:3&tick=40&tab=skills"),
    );
    expect(next.toString()).toBe("panel=routines");
    for (const key of WORKSPACE_PARAMS) expect(next.get(key)).toBeNull();
  });
});
