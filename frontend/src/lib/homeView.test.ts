/**
 * The home's grammar, and the one line in it that was a habit change.
 *
 * `DEFAULT_HOME_VIEW` was the whole risk of FEAT-104: every link, notification
 * and muscle memory that meant "the chat" meant `/`. Steps 1 and 2 mounted the
 * overview and built it; step 3 flipped this constant, and the first test here
 * was written to be changed in exactly that commit — it now pins the flip
 * rather than its absence, so an accidental revert is as loud as the flip was.
 *
 * The rest of the risk is paid for by the handover rule: the links that were
 * written when `/` meant the conversation still reach it, which is why the
 * cases below are the ones that would have broken.
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
  it("is the fleet overview — step 3 of FEAT-104 flipped it, deliberately", () => {
    expect(DEFAULT_HOME_VIEW).toBe("fleet");
    expect(homeView("")).toBe("fleet");
  });

  it("is still the chat for the links written when `/` meant the chat", () => {
    // These are in bookmarks and in notification payloads. The flip may not
    // reach them, so the parameters themselves name the view.
    expect(homeView("?agent=brigado&ask=how%20are%20we")).toBe("chat");
    expect(homeView("?agent=brigado")).toBe("chat");
    expect(homeView("?ask=how%20are%20we")).toBe("chat");
    expect(homeView("?conversation=7f3a")).toBe("chat");
  });

  it("lets an explicit view win over a handover parameter", () => {
    expect(homeView("?view=fleet&agent=brigado")).toBe("fleet");
  });

  it("does not read an empty handover parameter as a request", () => {
    expect(homeView("?agent=")).toBe("fleet");
    expect(homeView("?conversation=")).toBe("fleet");
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
    expect(homeView("?view=now")).toBe("fleet");
    expect(homeView("?view=")).toBe("fleet");
  });
});

describe("homePath", () => {
  it("never spells out the default, so the pasted URL is the short one", () => {
    expect(homePath("fleet")).toBe("/");
    expect(homePath("chat")).toBe("/?view=chat");
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
