/**
 * The home's grammar, and the one line in it that is a habit change.
 *
 * `DEFAULT_HOME_VIEW` is the whole risk of FEAT-104: every link, notification
 * and muscle memory that means "the chat" means `/`. Steps 1 and 2 mount the
 * overview and build it; step 3 flips this constant. So the first test here is
 * not a formality — it is the guard that says the flip has not happened yet,
 * and it is meant to be *changed*, deliberately, in the commit that flips it.
 */

import { describe, expect, it } from "vitest";

import {
  DEFAULT_HOME_VIEW,
  FULL_BLEED_HOME_VIEWS,
  homePath,
  homeView,
  isHomeView,
} from "./homeView";

describe("the default home view", () => {
  it("is still the chat — step 3 of FEAT-104 is what flips it", () => {
    expect(DEFAULT_HOME_VIEW).toBe("chat");
    expect(homeView("")).toBe("chat");
    expect(homeView("?agent=brigado&ask=how%20are%20we")).toBe("chat");
  });
});

describe("homeView", () => {
  it("reads the view the URL names", () => {
    expect(homeView("?view=fleet")).toBe("fleet");
    expect(homeView("?view=chat")).toBe("chat");
    expect(homeView(new URLSearchParams("view=fleet"))).toBe("fleet");
  });

  it("falls back to the default rather than erroring on a foreign view", () => {
    // `?view=now` is the agent workspace's grammar pasted onto the wrong path.
    // The home is where people land, and landing nowhere is worse.
    expect(homeView("?view=now")).toBe("chat");
    expect(homeView("?view=")).toBe("chat");
  });
});

describe("homePath", () => {
  it("never spells out the default, so the pasted URL is the short one", () => {
    expect(homePath("chat")).toBe("/");
    expect(homePath("fleet")).toBe("/?view=fleet");
  });

  it("round-trips through homeView", () => {
    for (const view of ["chat", "fleet"] as const) {
      const path = homePath(view);
      const search = path.includes("?") ? path.slice(path.indexOf("?")) : "";
      expect(homeView(search)).toBe(view);
    }
  });
});

describe("isHomeView", () => {
  it("accepts only the two the home owns", () => {
    expect(isHomeView("chat")).toBe(true);
    expect(isHomeView("fleet")).toBe(true);
    expect(isHomeView("now")).toBe(false);
    expect(isHomeView(null)).toBe(false);
    expect(isHomeView("")).toBe(false);
  });
});

describe("the full-bleed rule", () => {
  it("covers both views — each owns its own scrolling", () => {
    expect([...FULL_BLEED_HOME_VIEWS].sort()).toEqual(["chat", "fleet"]);
  });
});
