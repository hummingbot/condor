/**
 * The desk has an address (FEAT-114).
 *
 * `/fleet` was a page and is now a redirect into the Execution section of this
 * conversation's desk — which only works if a URL can say *which sections*, not
 * merely that the desk is up. Without `?desk=`, the redirect would land on
 * whatever this browser happened to have recorded, so somebody following a link
 * to the fleet would get the portfolio.
 *
 * The parser is the whole rule, so it is what is pinned: the same treatment
 * `parseGrouping` gives its axes — unknown ids dropped, repeats collapsed, a
 * value that names nothing falling back to storage rather than opening an empty
 * desk.
 */

import { describe, expect, it } from "vitest";

import { DESK_PARAM, EXECUTION_PATH, parseDesk } from "./accountPanels";
import { PANEL_PARAM } from "./paneUrl";

describe("parseDesk", () => {
  it("reads the sections a link names", () => {
    expect(parseDesk("execution")).toEqual(["execution"]);
    expect(parseDesk("portfolio.execution")).toEqual(["portfolio", "execution"]);
  });

  it("draws them in the panel's own order, not the link's", () => {
    // The sections are two panes of one panel with a fixed vertical order; a
    // URL that could reorder them would be a second layout to reason about.
    expect(parseDesk("execution.portfolio")).toEqual(["portfolio", "execution"]);
  });

  it("drops what it does not have, and keeps what it does", () => {
    expect(parseDesk("execution.nonsense")).toEqual(["execution"]);
    expect(parseDesk("execution.execution")).toEqual(["execution"]);
    expect(parseDesk(" execution ")).toEqual(["execution"]);
  });

  it("falls back to storage rather than opening an empty desk", () => {
    // `null` is *nothing was named*, which the hook reads as "use what this
    // browser had open" — a bare `?panel=desk` still restores the reader's own
    // desk instead of a blank one.
    expect(parseDesk(null)).toBeNull();
    expect(parseDesk("")).toBeNull();
    expect(parseDesk("  ")).toBeNull();
    expect(parseDesk("nonsense")).toBeNull();
  });
});

describe("the fleet's address", () => {
  it("names both the pane and the section", () => {
    const params = new URLSearchParams(EXECUTION_PATH.slice(2));
    // One without the other is half an answer: the pane would open on the
    // wrong section, or the section would be recorded with nothing on screen.
    expect(params.get(PANEL_PARAM)).toBe("desk");
    expect(parseDesk(params.get(DESK_PARAM))).toEqual(["execution"]);
  });
});
