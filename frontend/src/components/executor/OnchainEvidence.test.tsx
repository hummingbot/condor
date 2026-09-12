import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ExecutorInfo } from "@/lib/api";
import { OnchainEvidence } from "./OnchainEvidence";

describe("on-chain execution evidence", () => {
  it("makes uncertain post-commit submission prominent and preserves reconciliation evidence", () => {
    const executor = { config: { commit: true }, custom_info: {
      commit_attempted: true, committed: false, simulation_passed: true,
      tx_hashes: ["submitted-signature"],
      error: { message: "Transaction receipt retrieval failed" },
    }} as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("Confirmation unavailable</h3>");
    expect(html).toContain("Transaction submission may have occurred");
    expect(html).toContain("Do not retry until this execution is reconciled");
    expect(html).toContain("Transaction receipt retrieval failed");
    expect(html).toContain("submitted-signature");
    expect(html).toContain("Transaction identifiers");
    expect(html).not.toContain("Action stopped");
    expect(html).not.toContain("Transaction receipts");
    expect(html).not.toContain("Simulation only — no funds moved");
  });
  it("distinguishes a pending commit from execution that has not started", () => {
    const executor = { custom_info: { commit_attempted: true, committed: false } } as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("Awaiting transaction confirmation");
    expect(html).not.toContain("Awaiting execution");
  });
  it("keeps authoritative confirmation ahead of an earlier attempt or error", () => {
    const executor = { custom_info: {
      commit_attempted: true, committed: true, error: { message: "Earlier retrieval error" },
      tx_hashes: ["confirmed-signature"],
    }} as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("Transaction confirmed");
    expect(html).toContain("Transaction receipts");
    expect(html).not.toContain("Confirmation unavailable");
    expect(html).not.toContain("Do not retry");
  });
  it("shows Solana fees and limits in SOL with their actual scope", () => {
    const executor = { config: { commit: false }, custom_info: {
      chain: "svm", chain_id: 1, cluster: "mainnet-beta", simulation_passed: true,
      estimated_svm_network_fee_lamports: "5000", max_svm_network_fee_lamports: "4999",
      error: { reason: "network_fee_over_budget" },
    }} as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("0.000005 SOL");
    expect(html).toContain("0.000004999");
    expect(html).toContain("mainnet-beta");
    expect(html).toContain("exclude account rent, protocol charges and signing-provider costs");
    expect(html).toContain("Action stopped");
  });
  it("does not turn an incomplete Solana fee estimate into zero", () => {
    const executor = { custom_info: { chain: "svm", estimated_svm_network_fee_lamports: null }} as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("Complete estimate unavailable");
    expect(html).not.toContain(">0 SOL<");
  });
  it("shows the estimate independently of incurred executor fees", () => {
    const executor = { config: { commit: false }, cum_fees_quote: "0", custom_info: {
      estimated_gas_quote: "0.063", fees_quote_source: "unavailable",
    }} as unknown as ExecutorInfo;
    const html = renderToStaticMarkup(<OnchainEvidence executor={executor} />);
    expect(html).toContain("0.063 USDT");
    expect(html).not.toContain(">0 USDT<");
  });
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

it("separates wallet movements from pool credits and retains unknown owners", () => {
  const executor = { custom_info: { chain: "svm", wallet_address: "wallet", balance_changes: [
    { account: "wallet", asset: "USDC", amount: "2000000", decimals: 6, direction: "out" },
    { account: "pool", asset: "USDC", amount: "2000000", decimals: 6, direction: "in" },
    { asset: "other", amount: "1", decimals: 0, direction: "out" },
  ] } } as unknown as ExecutorInfo;
  const html = renderToStaticMarkup(<OnchainEvidence executor={executor} walletOnly assetLabels={{ USDC: "USDC" }} />);
  expect(html).toContain("Simulated wallet movements");
  expect(html).toContain("Out: 2 USDC");
  expect(html).not.toContain("In: 2 USDC");
  expect(html).toContain("Account: Unknown");
  expect(html).not.toContain("Estimated gas");
});
