import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ExecutorInfo } from "@/lib/api";
import { OnchainEvidence } from "./OnchainEvidence";

describe("on-chain execution evidence", () => {
  it("shows a risk rejection even when the chain simulation passed", () => {
    const executor = { config: { commit: false }, custom_info: {
      simulation_passed: true, error: { reason: "gas_unpriced" },
    }} as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("Action stopped");
    expect(html).not.toContain("Simulation only — no funds moved");
  });
  it("preserves exact token precision beyond JavaScript number range", () => {
    const executor = { config: { commit: false }, custom_info: {
      balance_changes: [
        { amount: "123456789012345678901234567890", decimals: 18 },
        { amount: "1", decimals: 6 }, { amount: "1000000" },
      ],
    }} as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("123456789012.34567890123456789");
    expect(html).toContain("0.000001");
    expect(html).toContain("1000000 raw units");
  });
  it("does not portray a successful simulation as executed funds", () => {
    const executor = { config: { commit: false }, custom_info: {
      simulation_passed: true, approvals: [{ unlimited: true, asset: "token", spender: "pool" }],
    }} as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("Simulation only — no funds moved");
    expect(html).toContain("Unlimited allowance");
    expect(html).toContain("Not priced");
    expect(html).not.toContain("Transaction confirmed");
  });
});
