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
  parseSections,
  sectionForView,
  serializeSections,
} from "./sections";

describe("parseSections", () => {
  it("reads a dot-joined list", () => {
    expect(parseSections("runs.money")).toEqual(["runs", "money"]);
  });

  it("drops ids that name no section, rather than failing", () => {
    // A stale or hand-edited parameter should open what it does name: the
    // reader gets the sections they asked for, not an error page.
    expect(parseSections("runs.lab.money")).toEqual(["runs", "money"]);
    expect(parseSections("lab")).toEqual([]);
  });

  it("collapses repeats", () => {
    expect(parseSections("money.money")).toEqual(["money"]);
  });

  it("returns them in the order the screen draws them", () => {
    // Whatever order they were clicked in: the disclosures are a stack, and a
    // URL that reordered them would render a different page from the clicks.
    expect(parseSections("playbook.runs")).toEqual(["runs", "playbook"]);
  });

  it("tells a URL that says nothing from one that says nothing is open", () => {
    // The difference is load-bearing: absent falls back to what this browser
    // had open, empty is a reader who closed everything.
    expect(parseSections(null)).toBeNull();
    expect(parseSections(undefined)).toBeNull();
    expect(parseSections("")).toEqual([]);
    expect(parseSections("   ")).toEqual([]);
  });
});

describe("serializeSections", () => {
  it("round-trips through the parser", () => {
    expect(parseSections(serializeSections(["money", "runs"]))).toEqual([
      "runs",
      "money",
    ]);
  });

  it("writes an empty string for nothing open, which clears the key", () => {
    expect(serializeSections([])).toBe("");
  });
});

describe("sectionForView — where a retired ?view= lands", () => {
  it("sends the four Doing views to their disclosure", () => {
    expect(sectionForView("runs")).toBe("runs");
    expect(sectionForView("money")).toBe("money");
    expect(sectionForView("fleet")).toBe("fleet");
    expect(sectionForView("playbook")).toBe("playbook");
  });

  it("sends Now to no disclosure — the answer stack is the screen", () => {
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

  it("covers every disclosure the screen has", () => {
    // The four Doing views map onto four of the five; `detail` is new here and
    // was `runs`' lower half, so no retired address can name it.
    const landed = new Set(
      ["runs", "money", "fleet", "playbook"].map(sectionForView),
    );
    for (const id of SECTIONS) {
      if (id !== "detail") expect(landed.has(id)).toBe(true);
    }
  });
});
