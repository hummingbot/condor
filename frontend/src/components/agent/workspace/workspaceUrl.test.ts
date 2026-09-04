/**
 * Moving the run screen's URL — the half `views.test.ts` does not cover.
 *
 * These rules used to be nine hand-written `setParams({…})` calls in the page,
 * so "moving the strategy drops the run and the tick" was true because six
 * callers remembered it. It is true because this file says so now, which is
 * what keeps it true when the next control is grown.
 */

import { describe, expect, it } from "vitest";

import { applyWorkspacePatch, patchReplaces } from "./workspaceUrl";

const q = (s: string) => new URLSearchParams(s);
/** What a patch leaves behind, as a plain string, for readable expectations. */
const after = (search: string, patch: Parameters<typeof applyWorkspacePatch>[1]) =>
  applyWorkspacePatch(q(search), patch).toString();

describe("applyWorkspacePatch", () => {
  it("writes only the keys it is given", () => {
    expect(after("?open=money", { strategy: "brl_mm" })).toBe(
      "open=money&strategy=brl_mm",
    );
  });

  it("leaves every parameter that is not the screen's alone", () => {
    // The disclosures spend this same string: `?fscope=` is the fleet browser's
    // and `?population=` is `/bots`' filter, and a scope change from the loop
    // bar has no business resetting either.
    const next = applyWorkspacePatch(q("?fscope=bot%3Ax&population=running"), {
      open: "fleet",
    });
    expect(next.get("fscope")).toBe("bot:x");
    expect(next.get("population")).toBe("running");
    expect(next.get("open")).toBe("fleet");
  });

  it("does not mutate the params it was handed", () => {
    const before = q("?open=money");
    applyWorkspacePatch(before, { open: "fleet" });
    expect(before.get("open")).toBe("money");
  });

  it("drops `?open=` entirely when nothing is left open", () => {
    // The shortest URL that lands somewhere is the one people paste — and an
    // `?open=` on its own would say "nothing", which is not the same as
    // "whatever this browser had", the thing an absent one means.
    expect(after("?open=money", { open: null })).toBe("");
    expect(after("?open=money", { open: "" })).toBe("");
  });

  it("retires a legacy `?view=` or `?tab=` on any write", () => {
    // Nothing reads them any more but the page's redirect guard, and carrying
    // one along would put a dead parameter back on a live URL.
    expect(after("?view=money&tab=skills", { open: "money" })).toBe("open=money");
  });

  it("clears a key given as null, and treats an empty string the same", () => {
    expect(after("?strategy=brl_mm", { strategy: null })).toBe("");
    expect(after("?strategy=brl_mm", { strategy: "" })).toBe("");
  });

  describe("the cascades", () => {
    it("drops the run and the tick when the strategy moves", () => {
      expect(
        after("?open=runs&strategy=old&run=s:3&tick=40", { strategy: "new" }),
      ).toBe("open=runs&strategy=new");
    });

    it("drops them when the strategy is cleared, too", () => {
      expect(after("?strategy=old&run=s:3&tick=40", { strategy: null })).toBe("");
    });

    it("drops the tick when the run moves, and keeps the scope", () => {
      expect(
        after("?open=runs&strategy=brl_mm&run=s:3&tick=40", { run: "s:4" }),
      ).toBe("open=runs&strategy=brl_mm&run=s%3A4");
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
      expect(after("?open=runs&run=s:3&tick=40", { tick: 41 })).toBe(
        "open=runs&run=s%3A3&tick=41",
      );
    });

    it("clears the tick without clearing the run or what is open", () => {
      // Closing the overlay is the whole of this move: the reader comes back to
      // the same screen with the same disclosures open under it.
      expect(after("?open=runs&run=s:3&tick=40", { tick: null })).toBe(
        "open=runs&run=s%3A3",
      );
    });
  });
});

describe("patchReplaces", () => {
  it("replaces for a disclosure opening or shutting", () => {
    // Reading down a page is not five history entries to press Back through.
    expect(patchReplaces({ open: "money" })).toBe(true);
    expect(patchReplaces({ open: null })).toBe(true);
  });

  it("pushes for a scope, a run or a tick", () => {
    expect(patchReplaces({ strategy: "brl_mm" })).toBe(false);
    expect(patchReplaces({ run: "s:3" })).toBe(false);
    // A tick pushes, which is what makes Back a way out of the overlay.
    expect(patchReplaces({ tick: 40 })).toBe(false);
    // A disclosure that also moves the scope is a step, not a correction.
    expect(patchReplaces({ open: "playbook", strategy: "brl_mm" })).toBe(false);
  });
});
