/**
 * The same vector and the same digest as `tests/test_vault_config.py`.
 *
 * These two files are the contract: a config's hash is a commitment a vault
 * carries on chain for its whole life, and it has to be computable in the
 * browser that signs it and in the backend that stores it. If this expected
 * value ever has to change, every vault already deployed becomes unverifiable
 * — so it changes only alongside a migration, never to make a test pass.
 */
import { describe, expect, it } from "vitest";

import { canonicalJson, configHash, NotCanonical } from "./canonical";

const VECTOR = {
  pair: "SOL-USDC",
  bands: [
    { width_bps: 50, side: "both" },
    { width_bps: 120, side: "buy" },
  ],
  amount_lamports: 12_500_000_000,
  label: "café ☕",
  advanced: { z: 1, a: { nested: true }, m: "x" },
  empty: {},
};

const VECTOR_BYTES =
  '{"advanced":{"a":{"nested":true},"m":"x","z":1},' +
  '"amount_lamports":12500000000,' +
  '"bands":[{"side":"both","width_bps":50},{"side":"buy","width_bps":120}],' +
  '"empty":{},' +
  '"label":"café ☕",' +
  '"pair":"SOL-USDC"}';

const VECTOR_HASH = "0d391584d44d093fb6cd08844b4551b08e3faa2e5ab1c231735b69ae7cae2ad7";

describe("the canonical config encoding", () => {
  it("produces the bytes the backend produces", () => {
    expect(new TextDecoder().decode(canonicalJson(VECTOR))).toBe(VECTOR_BYTES);
  });

  it("produces the digest the backend produces", async () => {
    expect(await configHash(VECTOR)).toBe(VECTOR_HASH);
  });

  it("does not depend on the order the keys were written in", async () => {
    const reordered = Object.fromEntries(Object.entries(VECTOR).reverse());
    expect(await configHash(reordered)).toBe(VECTOR_HASH);
  });

  it("does depend on the order of an array, which is data", async () => {
    const swapped = { ...VECTOR, bands: [...VECTOR.bands].reverse() };
    expect(await configHash(swapped)).not.toBe(VECTOR_HASH);
  });

  it("refuses a non-integer rather than rounding it", () => {
    expect(() => canonicalJson({ spread: 0.1 })).toThrow(NotCanonical);
    expect(() => canonicalJson({ spread: 0.1 })).toThrow(/spread/);
  });

  it("refuses null, because leaving the key out means the same thing", () => {
    expect(() => canonicalJson({ outer: { inner: null } })).toThrow(/outer\.inner/);
  });
});
