import { describe, expect, it } from "vitest";
import { BASE_USDC, lendingConfig } from "./lending";

const wallet = "0x1111111111111111111111111111111111111111";

describe("operator lending inputs", () => {
  it("preserves amounts beyond JavaScript's safe integer range", () => {
    const config = lendingConfig(wallet, "123456789012345678.123456", "supply", "1");
    expect(config.lending.amount).toBe("123456789012345678123456");
    expect(config.lending.pool).toBe(BASE_USDC.pool);
    expect(config.lending.asset).toBe(BASE_USDC.asset);
    expect(config).not.toHaveProperty("notional_quote");
  });
  it("does not round fractional raw units or accept negative and unbounded amounts", () => {
    for (const amount of ["1.0000001", "0", "-1", "1e3", "Infinity", (2n ** 256n).toString()]) {
      expect(() => lendingConfig(wallet, amount, "supply", "1")).toThrow();
    }
  });
  it("requires a signer and a finite positive fee estimate limit", () => {
    expect(() => lendingConfig("wrong", "1", "supply", "1")).toThrow();
    for (const gas of ["0", "-1", "NaN", "Infinity", "1e10"]) {
      expect(() => lendingConfig(wallet, "1", "supply", gas)).toThrow();
    }
  });
  it("withdraws an explicit exact amount to the same wallet", () => {
    expect(lendingConfig(wallet, "0.000001", "withdraw", "0.5").lending).toEqual({
      ...BASE_USDC, wallet, action: "withdraw", amount: "1",
    });
  });
});
