/**
 * Credentials gate exactly one capability, and nothing before the answer arrives
 * (ARCH-272).
 *
 * `GET /market/venues` now spans every venue Condor can chart, not just the ones
 * the account holds keys on, so most of the trade panel's list is readable and
 * unexecutable. Two things have to hold for that to be an improvement rather than
 * a regression: `canTrade` must be the *only* capability that moves with
 * `credentialed`, and the unknown-venue answer must be tradable — it is what
 * every venue looks like until the query resolves, and a `false` there would blur
 * the Execute panel on every page load.
 */

import { describe, expect, it } from "vitest";

import {
  connectorCapabilities,
  orderBookVenues,
  type VenueTraits,
} from "./connector-capabilities";

function venue(over: Partial<VenueTraits> & { name: string }): VenueTraits {
  return {
    hummingbotMarketData: true,
    clmmLp: false,
    credentialed: true,
    ...over,
  };
}

const HYPERLIQUID = venue({ name: "hyperliquid_perpetual" });
const BINANCE = venue({ name: "binance", credentialed: false });
const SOLANA = venue({ name: "solana-mainnet-beta", hummingbotMarketData: false, clmmLp: true });

const VENUES = [HYPERLIQUID, BINANCE, SOLANA];

describe("connectorCapabilities canTrade", () => {
  it("is true on a venue the account holds keys on", () => {
    expect(connectorCapabilities("hyperliquid_perpetual", VENUES).canTrade).toBe(true);
  });

  it("is false on a venue that reached the list on its market data alone", () => {
    expect(connectorCapabilities("binance", VENUES).canTrade).toBe(false);
  });

  it("is true for an unknown venue, so the pending render never blurs", () => {
    // Every venue looks like this between the first paint and the venues query
    // resolving — including the one that is about to come back credentialed.
    expect(connectorCapabilities("binance", []).canTrade).toBe(true);
  });

  it("leaves every other capability alone on a view-only venue", () => {
    // A view-only venue has a real book, real rules and real strategies. What it
    // lacks is an account, so only canTrade may differ from its credentialed twin.
    const viewOnly = connectorCapabilities("binance", VENUES);
    const credentialed = connectorCapabilities("hyperliquid_perpetual", VENUES);
    expect(viewOnly.hasOrderBook).toBe(true);
    expect(viewOnly.hasTradingRules).toBe(true);
    expect(viewOnly.hasRestPrice).toBe(true);
    expect(viewOnly.orderStrategies).toEqual(credentialed.orderStrategies);
    expect(viewOnly.executorTypes).toEqual(credentialed.executorTypes);
  });

  it("does not put a Gateway network into the read-only state", () => {
    // The LP and DexPool pages read the same capabilities; ARCH-271 reports
    // Gateway networks as credentialed precisely so they never go view-only.
    const caps = connectorCapabilities("solana-mainnet-beta", VENUES);
    expect(caps.canTrade).toBe(true);
    expect(caps.supportsLp).toBe(true);
  });
});

describe("orderBookVenues", () => {
  it("drops venues with no Hummingbot market feed", () => {
    expect(orderBookVenues(VENUES)).not.toContain("solana-mainnet-beta");
  });

  it("lists credentialed venues first, so the reset fallback stays tradable", () => {
    // The server happens to report the view-only venue first; allConnectors[0] is
    // the panel's reset fallback and must still land somewhere tradable.
    const ordered = orderBookVenues([BINANCE, HYPERLIQUID]);
    expect(ordered).toEqual(["hyperliquid_perpetual", "binance"]);
  });

  it("keeps the server's order within each group", () => {
    const kraken = venue({ name: "kraken" });
    const okx = venue({ name: "okx", credentialed: false });
    expect(orderBookVenues([okx, HYPERLIQUID, BINANCE, kraken])).toEqual([
      "hyperliquid_perpetual",
      "kraken",
      "okx",
      "binance",
    ]);
  });
});
