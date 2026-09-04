/**
 * The shell's two lists, as contract rather than as markup.
 *
 * A page is only reachable if it is in the nav, and it only lays itself out
 * correctly if the shell knows it is full bleed — two facts that live in two
 * arrays a hundred lines apart and that a rendered test would need the whole
 * chat provider, the socket and the credentials query to reach. Read directly
 * instead: they are data, and `/fleet` is the case that shows why both have to
 * be edited together — it spent FEAT-104 as a query parameter on the home, got
 * a page and a nav entry, and lost both again in FEAT-114 when what every agent
 * is doing became a panel of the home's own rail. An entry left in either array
 * for a route that no longer renders a page is a door onto a redirect.
 *
 * Both live in `lib/nav` rather than beside the shell that renders them: a
 * module may not export a component and plain data both (the lint gate's
 * `react-refresh/only-export-components`), and having them in a leaf module is
 * what lets this file read them with a static import and no DOM at all.
 */

import { describe, expect, it } from "vitest";

import { FULL_BLEED_ROUTES, NAV_ITEMS } from "@/lib/nav";

describe("the nav", () => {
  it("has no fleet entry: the fleet is a panel of the home now", () => {
    // FEAT-114. `/fleet` still resolves — it redirects into the panel — but a
    // nav entry onto a redirect is a door that opens onto another door.
    const paths: string[] = NAV_ITEMS.map((item) => item.to);
    expect(paths).not.toContain("/fleet");
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

  it("includes the home, which scrolls its own transcript", () => {
    // It was answered by `FULL_BLEED_HOME_VIEWS` while `/` mounted two screens.
    // The chat scrolls its transcript and does not want `main`'s padding.
    expect(FULL_BLEED_ROUTES).toContain("/");
    // And the fleet is not a route with a body any more (FEAT-114), so a
    // layout rule for it would be a rule about a redirect.
    expect(FULL_BLEED_ROUTES as readonly string[]).not.toContain("/fleet");
  });
});
