import { describe, expect, it } from "vitest";
import { displayUnits, networkFeeLimit, rawUnits } from "./universal";

describe("universal execution amounts", () => {
  it("retains exact integer precision instead of rounding through Number", () => {
    expect(rawUnits("9007199254740993.000001", 6)).toBe("9007199254740993000001");
    expect(displayUnits("9007199254740993000001", 6)).toBe("9007199254740993.000001");
    expect(networkFeeLimit("0.000005")).toBe(5000);
    expect(networkFeeLimit("0")).toBe(0);
  });
  it("rejects negative, exponent and excess-precision input", () => {
    for (const value of ["-1", "1e6", "0.0000001", "NaN", "01"]) expect(() => rawUnits(value, 6)).toThrow();
    expect(() => networkFeeLimit("9007199254740993")).toThrow();
  });
});

it("binds venue identity and keeps native and wrapped-token budgets distinct", async () => {
  const { spendingPolicy } = await import("./universal");
  const market = { address: "pool", label: "Pool", asset: "USDC", decimals: 6, share_mint: "LP", share_decimals: 9,
    base_asset: "So11111111111111111111111111111111111111112", base_decimals: 9, program_id: "pool-program" };
  const response = { wallet: "wallet", cluster: "mainnet-beta", result: { market, position: { shares_raw: "9007199254740993" },
    instructions: [{ instructions: [{ program_id: "token" }, { program_id: "pool-program" }, { program_id: "token" }] }] } };
  expect(spendingPolicy(response, "deposit", { asset: "2.1", native: "0.03", base: "0" })).toEqual({
    wallet: "wallet", market: "pool", protocol_program: "pool-program", allowed_programs: ["token", "pool-program"],
    max_debits_raw: { native: "30000000", USDC: "2100000", So11111111111111111111111111111111111111112: "0" },
  });
  expect(spendingPolicy(response, "withdraw", { asset: "0", native: "0.01", base: "0" }).max_debits_raw)
    .toEqual({ native: "10000000", LP: "9007199254740993" });
  expect(() => spendingPolicy(response, "deposit", { asset: "18446744073709551616", native: "0", base: "0" })).toThrow();
});


it("uses exact wallet-held shares independently of fractional farm valuation", async () => {
  const { spendingPolicy } = await import("./universal");
  const market = { address: "vault", label: "Vault", asset: "USDC", decimals: 6,
    share_mint: "shares", share_decimals: 6, program_id: "vault-program" };
  const result = { market, instructions: [{ instructions: [{ program_id: "vault-program" }] }],
    position: { shares: "1.8897249999477933", staked_shares: "1.8897249999477933", unstaked_shares: "0", wallet_shares_raw: "0" } };
  const response = { wallet: "wallet", cluster: "mainnet-beta", result };
  const budgets = { asset: "0", base: "0", native: "0.01" };
  expect(spendingPolicy(response, "withdraw", budgets).max_debits_raw).toEqual({ native: "10000000", shares: "0" });
  response.result.position.wallet_shares_raw = "9007199254740993";
  expect(spendingPolicy(response, "withdraw", budgets).max_debits_raw.shares).toBe("9007199254740993");
  const missing = { ...response, result: { ...result, position: { shares: "1.8897249999477933" } } };
  expect(() => spendingPolicy(missing, "withdraw", budgets)).toThrow("Exact wallet share balance is unavailable");
  const integralEconomicValue = { ...missing, result: { ...missing.result, position: { shares: "2" } } };
  expect(() => spendingPolicy(integralEconomicValue, "withdraw", budgets)).toThrow("Exact wallet share balance is unavailable");
});
