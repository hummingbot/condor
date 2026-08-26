/**
 * The session boundary is only as good as its key list, and the list is a
 * judgment call: form state goes, device preferences stay (SEC-231). Two of the
 * families are prefix-matched — one entry per routine, one per DEX connector —
 * which is the part a fixed list of names cannot cover and the part that breaks
 * silently if someone iterates `localStorage` while removing from it.
 *
 * Needs a real `localStorage`, so this file opts into jsdom; the rest of the
 * suite stays on vitest's `node` default (see vite.config.ts).
 *
 * @vitest-environment jsdom
 */

import { beforeEach, describe, expect, it } from "vitest";

import { clearSessionState } from "@/lib/sessionState";

/** Everything the outgoing user typed or picked. */
const SESSION = [
  "condor_order_defaults",
  "condor_lp_defaults",
  "condor_position_defaults",
  "condor_dca_defaults",
  "condor_grid_defaults",
  "condor_last_market",
  "condor_market_favorites",
  "condor_dex_favorites",
];

/** How this device renders the app — none of it names the outgoing user. */
const DEVICE = [
  "condor_theme",
  "condor_display_currency",
  "condor.dock.open",
  "condor_bubble_open",
  "condor_sheet_zen",
  "condor_trade_bottom_pane",
  "routines_view_mode",
  "condor.dex.network",
  "condor.dex.depth-collapsed",
];

beforeEach(() => {
  localStorage.clear();
});

describe("clearSessionState", () => {
  it("removes every fixed session key", () => {
    for (const key of SESSION) localStorage.setItem(key, "x");
    clearSessionState();
    for (const key of SESSION) expect(localStorage.getItem(key)).toBeNull();
  });

  it("leaves the device preferences alone", () => {
    for (const key of DEVICE) localStorage.setItem(key, "x");
    clearSessionState();
    for (const key of DEVICE) expect(localStorage.getItem(key)).toBe("x");
  });

  it("removes every routine_config: entry, not just the first", () => {
    // Removing while walking `key(i)` re-indexes the store and skips every
    // other match — with three entries a naive loop leaves one behind.
    localStorage.setItem("routine_config:arb_check", "{}");
    localStorage.setItem("routine_config:price_monitor", "{}");
    localStorage.setItem("routine_config:pnl_report", "{}");
    clearSessionState();
    const left = Object.keys(localStorage).filter((k) =>
      k.startsWith("routine_config:"),
    );
    expect(left).toEqual([]);
  });

  it("removes every condor_dex_pairs: entry", () => {
    localStorage.setItem("condor_dex_pairs:jupiter", "[]");
    localStorage.setItem("condor_dex_pairs:uniswap", "[]");
    localStorage.setItem("condor_dex_pairs:meteora", "[]");
    clearSessionState();
    const left = Object.keys(localStorage).filter((k) =>
      k.startsWith("condor_dex_pairs:"),
    );
    expect(left).toEqual([]);
  });

  it("clears both prefix families and keeps preferences in one pass", () => {
    for (const key of [...SESSION, ...DEVICE]) localStorage.setItem(key, "x");
    localStorage.setItem("routine_config:arb_check", "{}");
    localStorage.setItem("condor_dex_pairs:jupiter", "[]");

    clearSessionState();

    expect(Object.keys(localStorage).sort()).toEqual([...DEVICE].sort());
  });

  it("does not touch a key that merely contains a cleared prefix", () => {
    // Prefix-matched, not substring-matched: the boundary must not eat a
    // future key whose name happens to embed one of these.
    localStorage.setItem("my_routine_config:x", "keep");
    clearSessionState();
    expect(localStorage.getItem("my_routine_config:x")).toBe("keep");
  });
});
