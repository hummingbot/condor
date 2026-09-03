/**
 * The chat's pane has an address now (FEAT-103).
 *
 * Four panels that never touched the URL: Escape was the only way out of any of
 * them, browser Back did not close one, and none could be sent to anyone. This
 * pins the grammar that fixes it — including the two things deliberately *not*
 * in the URL, because a parameter per click is a history stack nobody can press
 * Back through.
 */

import { describe, expect, it } from "vitest";

import { readPane, writePane, type PaneView } from "./paneUrl";

const q = (search: string) => new URLSearchParams(search);
const round = (pane: PaneView, focus = {}) =>
  readPane(writePane(q(""), pane), focus);

describe("reading the pane off the URL", () => {
  it("opens the panel the URL names", () => {
    expect(readPane(q("?panel=agent"), {})).toEqual({ kind: "agent" });
    expect(readPane(q("?panel=desk"), {})).toEqual({ kind: "desk" });
  });

  it("closes for anything it does not have", () => {
    // A hand-typed panel is not an error page, it is a closed pane.
    expect(readPane(q(""), {})).toBeNull();
    expect(readPane(q("?panel=nonsense"), {})).toBeNull();
  });

  it("reads a strategy sheet's whole address", () => {
    expect(readPane(q("?panel=strategy&loop=brigado/brl_mm"), {})).toEqual({
      kind: "strategy",
      agentSlug: "brigado",
      strategySlug: "brl_mm",
    });
  });

  it("closes rather than opening a strategy sheet with no strategy", () => {
    for (const bad of ["", "&loop=", "&loop=brigado", "&loop=/brl_mm", "&loop=brigado/"]) {
      expect(readPane(q(`?panel=strategy${bad}`), {})).toBeNull();
    }
  });

  it("takes the library's focus from beside the URL, not from it", () => {
    // A report or a run is set by the library's own navigation and changes
    // several times a minute; a pasted `?panel=routines` opens it unfocused,
    // which is where the reader would have started anyway.
    expect(readPane(q("?panel=routines"), { source: "arb_check" })).toEqual({
      kind: "routines",
      focus: { source: "arb_check" },
    });
    expect(readPane(q("?panel=routines"), {})).toEqual({
      kind: "routines",
      focus: {},
    });
  });
});

describe("writing the pane into the URL", () => {
  it("round-trips every panel", () => {
    expect(round({ kind: "agent" })).toEqual({ kind: "agent" });
    expect(round({ kind: "desk" })).toEqual({ kind: "desk" });
    expect(
      round({ kind: "strategy", agentSlug: "brigado", strategySlug: "brl_mm" }),
    ).toEqual({ kind: "strategy", agentSlug: "brigado", strategySlug: "brl_mm" });
  });

  it("leaves no trace when the pane closes", () => {
    const open = writePane(q("?agent=brigado"), {
      kind: "strategy",
      agentSlug: "brigado",
      strategySlug: "brl_mm",
    });
    expect(open.get("panel")).toBe("strategy");
    expect(open.get("loop")).toBe("brigado/brl_mm");

    const closed = writePane(open, null);
    expect(closed.get("panel")).toBeNull();
    expect(closed.get("loop")).toBeNull();
    // And it keeps whatever else was in the query string.
    expect(closed.get("agent")).toBe("brigado");
  });

  it("drops a stale loop when another panel takes the pane", () => {
    const next = writePane(q("?panel=strategy&loop=brigado/brl_mm"), {
      kind: "agent",
    });
    expect(next.get("panel")).toBe("agent");
    expect(next.get("loop")).toBeNull();
  });
});
