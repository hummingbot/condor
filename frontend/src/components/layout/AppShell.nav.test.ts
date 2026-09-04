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
 * The module reaches `localStorage` at import time (the display-currency
 * preference, through `pageFacts`), so this file takes a DOM even though it
 * renders nothing.
 *
 * @vitest-environment jsdom
 */

import { describe, expect, it, vi } from "vitest";

// `useTheme` reads the media query at module scope and jsdom has no
// `matchMedia`. Stubbed before the import below, which is why that import is
// dynamic: a static one is hoisted above this line.
vi.stubGlobal(
  "matchMedia",
  () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
);

const { FULL_BLEED_ROUTES, NAV_ITEMS } = await import("./AppShell");

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
