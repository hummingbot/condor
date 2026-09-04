/**
 * The one rule left over from the home's `?view=`.
 *
 * FEAT-104 put a fleet overview on `/?view=fleet`; it is `/fleet` now. What
 * this file pins is the forwarding, because the old spelling is in bookmarks
 * and in notification payloads and those cannot be rewritten — in particular
 * that everything riding along beside `view` survives the trip, which is the
 * difference between a bookmark that still works and one that lands on a
 * subtly wrong page.
 */

import { describe, expect, it } from "vitest";

import { legacyFleetPath } from "./homeView";

describe("legacyFleetPath", () => {
  it("forwards the old overview URL to its own route", () => {
    expect(legacyFleetPath("?view=fleet")).toBe("/fleet");
    expect(legacyFleetPath(new URLSearchParams("view=fleet"))).toBe("/fleet");
  });

  it("carries every other parameter across, dropping only `view`", () => {
    expect(legacyFleetPath("?view=fleet&server=brigado")).toBe(
      "/fleet?server=brigado",
    );
    expect(legacyFleetPath("?agent=brigado&view=fleet&ask=hi")).toBe(
      "/fleet?agent=brigado&ask=hi",
    );
  });

  it("leaves every other URL on the home", () => {
    // `?view=chat` asked for what `/` already is; `?view=now` is the agent
    // workspace's grammar on the wrong path. Neither is the overview.
    expect(legacyFleetPath("")).toBeNull();
    expect(legacyFleetPath("?view=chat")).toBeNull();
    expect(legacyFleetPath("?view=now")).toBeNull();
    expect(legacyFleetPath("?view=")).toBeNull();
    expect(legacyFleetPath("?agent=brigado&ask=how%20are%20we")).toBeNull();
    expect(legacyFleetPath("?conversation=7f3a")).toBeNull();
  });
});
