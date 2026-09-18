/**
 * The run screen's disclosure grammar, and the redirect table beside it
 * (FEAT-119).
 *
 * Two things are pinned here and they are pinned for different reasons. The
 * parse rules are `parseDesk`'s, copied id for id, so this file is the proof
 * that the copy behaves like the original rather than like a second grammar
 * that happens to use dots. And `sectionForView` is the whole compatibility
 * surface of the feature: `?view=` is in notification payloads, in the chat's
 * route facts and in bookmarks, and every value it can take has to land
 * somewhere — which is a table, and a table is exactly the thing a test can
 * hold to.
 */

import { describe, expect, it } from "vitest";

import {
  SECTIONS,
  openForPaneSection,
  pageSection,
  parseSections,
  sectionForView,
} from "./sections";

describe("parseSections", () => {
  it("reads a dot-joined list", () => {
    expect(parseSections("runs.fleet")).toEqual(["runs", "fleet"]);
  });

  it("drops ids that name no section, rather than failing", () => {
    // A stale or hand-edited parameter should open what it does name: the
    // reader gets the sections they asked for, not an error page.
    expect(parseSections("runs.lab.fleet")).toEqual(["runs", "fleet"]);
    expect(parseSections("lab")).toEqual([]);
  });

  it("collapses repeats", () => {
    expect(parseSections("fleet.fleet")).toEqual(["fleet"]);
  });

  it("drops the retired Money band like any other unknown id", () => {
    expect(parseSections("money.playbook")).toEqual(["playbook"]);
  });

  it("returns them in the order the screen draws them", () => {
    // A pre-tabs `?open=` set lands on its first section in drawing order,
    // whatever order its links were written in.
    expect(parseSections("playbook.runs")).toEqual(["runs", "playbook"]);
  });

  it("tells a URL that says nothing from one that says nothing is open", () => {
    expect(parseSections(null)).toBeNull();
    expect(parseSections(undefined)).toBeNull();
    expect(parseSections("")).toEqual([]);
    expect(parseSections("   ")).toEqual([]);
  });
});

describe("sectionForView — where a retired ?view= lands", () => {
  it("sends the four Doing views to their tab", () => {
    expect(sectionForView("runs")).toBe("runs");
    // Money is gone; Fleet charts the same fold.
    expect(sectionForView("money")).toBe("fleet");
    expect(sectionForView("fleet")).toBe("fleet");
    expect(sectionForView("playbook")).toBe("playbook");
  });

  it("sends Now to no section — it is the first tab", () => {
    expect(sectionForView("now")).toBeNull();
  });

  it("sends a tick to none either: `?tick=` opens it as an overlay", () => {
    expect(sectionForView("tick")).toBeNull();
  });

  it("answers for a Being section and for nonsense alike", () => {
    // The page redirects the seven Being sections to the chat's panel before it
    // asks this, so reaching here with one is a hand-typed address: it lands on
    // the screen with nothing open, which is a page and not an error.
    expect(sectionForView("skills")).toBeNull();
    expect(sectionForView("nonsense")).toBeNull();
    expect(sectionForView(null)).toBeNull();
  });

  it("covers every section the screen has", () => {
    // `detail` is new here and was `runs`' lower half, so no retired address
    // can name it.
    const landed = new Set(
      ["runs", "money", "fleet", "playbook"].map(sectionForView),
    );
    for (const id of SECTIONS) {
      if (id !== "detail") expect(landed.has(id)).toBe(true);
    }
  });
});

describe("the page's tab", () => {
  it("is the first section `?open=` names, else Now", () => {
    expect(pageSection(null)).toBe("now");
    expect(pageSection("")).toBe("now");
    expect(pageSection("fleet")).toBe("fleet");
    // A pre-tabs set: the first one in drawing order.
    expect(pageSection("playbook.runs")).toBe("runs");
    expect(pageSection("money")).toBe("now");
  });

  it("round-trips through `?open=`, Now clearing it", () => {
    expect(openForPaneSection("now")).toBe("");
    for (const id of SECTIONS) expect(pageSection(openForPaneSection(id))).toBe(id);
  });
});
