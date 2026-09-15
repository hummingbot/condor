/**
 * @vitest-environment jsdom
 *
 * The one merge every executor panel's defaults come back through (ARCH-346).
 *
 * What it has to get right is what the five copies of it each had to: only the
 * whitelisted fields are restored, a missing or unusable payload is not an
 * error, and the object handed back shares nothing with the exported constant
 * it was made from — which is the property CORR-308 was the absence of, and the
 * one a caller cannot check for itself.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { loadPersistedDefaults, savePersistedDefaults } from "./persisted-defaults";

const KEY = "condor_test_defaults";

interface TestState {
  side: number;
  amount: number;
  levels: number[];
  activePickField: string | null;
}

const DEFAULTS: TestState = {
  side: 1,
  amount: 0,
  levels: [0, 0, 0],
  activePickField: null,
};

const FIELDS: (keyof TestState)[] = ["side", "amount"];

describe("loadPersistedDefaults", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("restores the whitelisted fields and ignores every other stored key", () => {
    localStorage.setItem(
      KEY,
      JSON.stringify({ side: 2, amount: 500, activePickField: "price", nonsense: 7 }),
    );

    const loaded = loadPersistedDefaults(KEY, DEFAULTS, FIELDS);

    expect(loaded.side).toBe(2);
    expect(loaded.amount).toBe(500);
    // Not on the whitelist: the panel's own transient state never comes back.
    expect(loaded.activePickField).toBeNull();
    expect(loaded).not.toHaveProperty("nonsense");
  });

  it("falls back to the default for a whitelisted key stored as undefined", () => {
    localStorage.setItem(KEY, JSON.stringify({ side: 2, amount: undefined }));

    const loaded = loadPersistedDefaults(KEY, DEFAULTS, FIELDS);

    expect(loaded.side).toBe(2);
    expect(loaded.amount).toBe(DEFAULTS.amount);
  });

  it("returns the defaults when nothing is stored", () => {
    expect(loadPersistedDefaults(KEY, DEFAULTS, FIELDS)).toEqual(DEFAULTS);
  });

  it("returns the defaults for a corrupt blob", () => {
    localStorage.setItem(KEY, "{not json");

    expect(loadPersistedDefaults(KEY, DEFAULTS, FIELDS)).toEqual(DEFAULTS);
  });

  it("returns the defaults for a payload that is not an object", () => {
    localStorage.setItem(KEY, "null");

    expect(loadPersistedDefaults(KEY, DEFAULTS, FIELDS)).toEqual(DEFAULTS);
  });

  it("hands back a copy whose nested values do not alias the defaults", () => {
    localStorage.setItem(KEY, JSON.stringify({ side: 2 }));

    const loaded = loadPersistedDefaults(KEY, DEFAULTS, FIELDS);
    loaded.levels.push(99);

    expect(DEFAULTS.levels).toEqual([0, 0, 0]);
  });

  it("copies the nested values on the empty and the corrupt paths too", () => {
    loadPersistedDefaults(KEY, DEFAULTS, FIELDS).levels.push(1);
    localStorage.setItem(KEY, "{not json");
    loadPersistedDefaults(KEY, DEFAULTS, FIELDS).levels.push(2);

    expect(DEFAULTS.levels).toEqual([0, 0, 0]);
  });
});

describe("savePersistedDefaults", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("writes only the whitelisted fields, and round-trips them", () => {
    const state: TestState = { side: 2, amount: 250, levels: [1, 2], activePickField: "price" };

    savePersistedDefaults(KEY, state, FIELDS);

    expect(JSON.parse(localStorage.getItem(KEY)!)).toEqual({ side: 2, amount: 250 });
    expect(loadPersistedDefaults(KEY, DEFAULTS, FIELDS)).toEqual({
      ...DEFAULTS,
      side: 2,
      amount: 250,
    });
  });
});
