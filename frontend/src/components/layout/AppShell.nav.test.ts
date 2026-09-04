/**
 * The shell's two lists, as contract rather than as markup.
 *
 * A page is only reachable if it is in the nav, and it only lays itself out
 * correctly if the shell knows it is full bleed — two facts that live in two
 * arrays a hundred lines apart and that a rendered test would need the whole
 * chat provider, the socket and the credentials query to reach. Read directly
 * instead: they are data, and `/fleet` is the case that shows why both have to
 * be edited together — it spent FEAT-104 as a query parameter on the home,
 * with no nav entry to reach it by and its padding decided by a list of home
 * views rather than by the shell's own array.
 *
 * Both live in `lib/nav` rather than beside the shell that renders them: a
 * module may not export a component and plain data both (the lint gate's
 * `react-refresh/only-export-components`), and having them in a leaf module is
 * what lets this file read them with a static import and no DOM at all.
 */

import { describe, expect, it } from "vitest";

import { FULL_BLEED_ROUTES, NAV_ITEMS } from "@/lib/nav";

describe("the nav", () => {
  it("has a door to the fleet, between the agents and the floor", () => {
    const paths = NAV_ITEMS.map((item) => item.to);
    expect(paths).toContain("/fleet");
    expect(paths.indexOf("/fleet")).toBe(paths.indexOf("/") + 1);
    expect(paths.indexOf("/floor")).toBe(paths.indexOf("/fleet") + 1);
    expect(NAV_ITEMS.find((item) => item.to === "/fleet")?.label).toBe("Fleet");
    expect(NAV_ITEMS.find((item) => item.to === "/floor")?.label).toBe("Floor");
  });

  it("gives every nav entry a distinct address and label", () => {
    expect(new Set(NAV_ITEMS.map((i) => i.to)).size).toBe(NAV_ITEMS.length);
    expect(new Set(NAV_ITEMS.map((i) => i.label)).size).toBe(NAV_ITEMS.length);
  });
});

describe("full bleed", () => {
  it("includes the floor, which owns its own scrolling under a sticky strip", () => {
    expect(FULL_BLEED_ROUTES).toContain("/floor");
  });

  it("includes the home and the fleet, one body each again", () => {
    // Both were answered by `FULL_BLEED_HOME_VIEWS` while `/` mounted two
    // screens. The chat scrolls its transcript and the fleet is a screen-tall
    // list that scrolls itself; neither wants `main`'s padding.
    expect(FULL_BLEED_ROUTES).toContain("/");
    expect(FULL_BLEED_ROUTES).toContain("/fleet");
  });
});
