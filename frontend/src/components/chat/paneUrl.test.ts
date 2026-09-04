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

import {
  AGENT_PARAM,
  TAB_PARAM,
  readPane,
  writePane,
  type PaneView,
} from "./paneUrl";

const q = (search: string) => new URLSearchParams(search);
const round = (pane: PaneView, focus = {}) =>
  readPane(writePane(q(""), pane), focus);

describe("reading the pane off the URL", () => {
  it("opens the panel the URL names", () => {
    expect(readPane(q("?panel=agent"), {})).toEqual({ kind: "agent" });
    expect(readPane(q("?panel=desk"), {})).toEqual({ kind: "desk" });
  });

  it("reads whose agent panel it is, and defaults to the conversation's", () => {
    // FEAT-114: an Execution row opens a *different* agent than the one being
    // talked to, so the pane's subject had to become part of its address.
    expect(readPane(q(`?panel=agent&${AGENT_PARAM}=brigado`), {})).toEqual({
      kind: "agent",
      slug: "brigado",
    });
    // A bare one is still the conversation's, which is what every link already
    // written to `?panel=agent` means, and an empty slug is not a slug.
    expect(readPane(q("?panel=agent"), {})).toEqual({ kind: "agent" });
    expect(readPane(q(`?panel=agent&${AGENT_PARAM}=`), {})).toEqual({
      kind: "agent",
    });
  });

  it("keeps its own spelling clear of the home's `?agent=`", () => {
    // `/` reads `?agent=<slug>` as *start or focus a conversation with this
    // agent* and strips it a tick later, so the pane cannot write its subject
    // there: it would spawn a chat and then erase itself.
    expect(AGENT_PARAM).not.toBe("agent");
    expect(readPane(q("?panel=agent&agent=brigado"), {})).toEqual({
      kind: "agent",
    });
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

  it("round-trips another agent's panel, and writes nothing for its own", () => {
    expect(round({ kind: "agent", slug: "brigado" })).toEqual({
      kind: "agent",
      slug: "brigado",
    });
    // No parameter at all for the conversation's own agent, so the shortest
    // URL keeps being the common case.
    expect(writePane(q(""), { kind: "agent" }).get(AGENT_PARAM)).toBeNull();
  });

  it("drops a stale subject when another panel takes the pane", () => {
    const next = writePane(q(`?panel=agent&${AGENT_PARAM}=brigado`), {
      kind: "desk",
    });
    expect(next.get(AGENT_PARAM)).toBeNull();
    const closed = writePane(q(`?panel=agent&${AGENT_PARAM}=brigado`), null);
    expect(closed.get(AGENT_PARAM)).toBeNull();
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

  describe("the section the panel is open on (FEAT-118)", () => {
    it("round-trips a section, and writes nothing for Brain", () => {
      expect(round({ kind: "agent", tab: "tools" })).toEqual({
        kind: "agent",
        tab: "tools",
      });
      // Brain is where a panel with nothing said opens, so the shortest URL
      // keeps being the common case.
      expect(writePane(q(""), { kind: "agent" }).get(TAB_PARAM)).toBeNull();
    });

    it("keeps the section while the pane goes on showing the same agent", () => {
      const open = `?panel=agent&${AGENT_PARAM}=brigado&${TAB_PARAM}=skills`;
      expect(
        writePane(q(open), { kind: "agent", slug: "brigado" }).get(TAB_PARAM),
      ).toBe("skills");
    });

    it("drops it when the pane opens a different agent", () => {
      // Brigado's Skills are not Quiet's, and a Back through them would
      // restore a pane nobody asked for.
      const open = `?panel=agent&${AGENT_PARAM}=brigado&${TAB_PARAM}=skills`;
      const next = writePane(q(open), { kind: "agent", slug: "quiet" });
      expect(next.get(AGENT_PARAM)).toBe("quiet");
      expect(next.get(TAB_PARAM)).toBeNull();
    });

    it("drops it when another panel takes the pane, and when it closes", () => {
      const open = `?panel=agent&${TAB_PARAM}=memories`;
      expect(writePane(q(open), { kind: "desk" }).get(TAB_PARAM)).toBeNull();
      expect(writePane(q(open), null).get(TAB_PARAM)).toBeNull();
    });

    it("reads a section nobody has as a panel open on Brain", () => {
      // A hand-typed `?tab=` is not an error page: the link still asked for
      // this agent's panel and that is what it gets.
      expect(readPane(q(`?panel=agent&${TAB_PARAM}=nonsense`), {})).toEqual({
        kind: "agent",
      });
    });

    it("spends none of the workspace's four parameters", () => {
      // The pane stopped being the workspace (FEAT-118), so `?view=`,
      // `?strategy=`, `?run=` and `?tick=` are none of its business — it
      // neither writes them nor clears somebody else's.
      const next = writePane(q("?view=money&strategy=brl_mm&run=s:3&tick=40"), {
        kind: "agent",
        tab: "strategies",
      });
      expect(next.get("view")).toBe("money");
      expect(next.get("strategy")).toBe("brl_mm");
      expect(next.get("run")).toBe("s:3");
      expect(next.get("tick")).toBe("40");
    });
  });
});
