/**
 * @vitest-environment jsdom
 *
 * The saved defaults are merged onto DCA_DEFAULTS, and only the persisted keys
 * come from storage — so `prices` arrives as the constant's own array unless the
 * loader copies it. It must: the resize that matches prices to the saved level
 * count would otherwise rewrite the exported constant, and every later load
 * (logout clears the key, so the `!raw` branch is one click away) would hand out
 * 3 amounts against N prices, which the validation rejects and no level edit can
 * clear.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { DCA_DEFAULTS, loadSavedDefaults } from "./dca-config";
import { DCA_DEFAULTS_KEY } from "@/lib/sessionState";

describe("loadSavedDefaults", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("leaves DCA_DEFAULTS.prices alone when the saved levels outnumber it", () => {
    localStorage.setItem(DCA_DEFAULTS_KEY, JSON.stringify({ amounts_quote: [1, 2, 3, 4, 5] }));

    const loaded = loadSavedDefaults();

    expect(loaded.amounts_quote).toEqual([1, 2, 3, 4, 5]);
    expect(loaded.prices).toEqual([0, 0, 0, 0, 0]);
    expect(DCA_DEFAULTS.prices).toEqual([0, 0, 0]);
    expect(DCA_DEFAULTS.amounts_quote).toEqual([100, 100, 150]);
  });

  it("leaves DCA_DEFAULTS.prices alone when the saved levels are fewer", () => {
    localStorage.setItem(DCA_DEFAULTS_KEY, JSON.stringify({ amounts_quote: [50] }));

    const loaded = loadSavedDefaults();

    expect(loaded.prices).toHaveLength(1);
    expect(DCA_DEFAULTS.prices).toEqual([0, 0, 0]);
  });

  it("still hands the untouched constant back once a load has resized its own copy", () => {
    // The second call is the one the bug reached the user through: after a
    // logout clears the key, the `!raw` branch returns DCA_DEFAULTS as-is.
    localStorage.setItem(DCA_DEFAULTS_KEY, JSON.stringify({ amounts_quote: [1, 2, 3, 4, 5] }));
    loadSavedDefaults();
    localStorage.clear();

    const afterLogout = loadSavedDefaults();

    expect(afterLogout.prices).toHaveLength(afterLogout.amounts_quote.length);
  });
});
