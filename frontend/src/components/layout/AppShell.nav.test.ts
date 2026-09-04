/**
 * The shell's two lists, as contract rather than as markup.
 *
 * A page is only reachable if it is in the nav, and it only lays itself out
 * correctly if the shell knows it is full bleed — two facts that live in two
 * arrays a hundred lines apart and that a rendered test would need the whole
 * chat provider, the socket and the credentials query to reach. Read directly
 * instead: they are data, and `/floor` (FEAT-112) is the case that shows why
 * both have to be edited together.
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
  it("has a door to the floor, between the agents and the portfolio", () => {
    const paths = NAV_ITEMS.map((item) => item.to);
    expect(paths).toContain("/floor");
    expect(paths.indexOf("/floor")).toBe(paths.indexOf("/") + 1);
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
});
