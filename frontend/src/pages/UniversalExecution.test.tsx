/** @vitest-environment jsdom */
import { focusManager, onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ServerContext } from "@/hooks/useServer";

const createExecutor = vi.fn(), getExecutor = vi.fn(), prepareOnchain = vi.fn();
vi.mock("@/lib/api", () => ({ api: { createExecutor: (...a: unknown[]) => createExecutor(...a),
  getExecutor: (...a: unknown[]) => getExecutor(...a), prepareOnchain: (...a: unknown[]) => prepareOnchain(...a) } }));
vi.mock("@/components/executor/OnchainEvidence", () => ({ OnchainEvidence: () => null }));
const { UniversalExecution } = await import("./UniversalExecution");
let root: Root, host: HTMLDivElement, query: QueryClient;
const instructions = [{ instructions: [{ program_id: "program", data_base64: "AA==", accounts: [] }] }];
const response = () => ({ wallet: "wallet", cluster: "mainnet-beta", result: {
  wallet: "wallet", cluster: "mainnet-beta", local_mirror: true, expires_at: Date.now() / 1000 + 120,
  venues: [{ id: "jupiter-lend", name: "Jupiter Lend", example_market: "market", example_label: "Example" }],
  market: { address: "market", label: "Lend", asset: "asset", decimals: 6, share_mint: "share", program_id: "program" },
  position: { shares_raw: "0" }, instructions,
} });
beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  createExecutor.mockReset(); getExecutor.mockReset(); prepareOnchain.mockReset();
  prepareOnchain.mockImplementation(async () => response());
  getExecutor.mockResolvedValue({ status: "terminated", close_type: "completed", config: { commit: false },
    custom_info: { simulation_passed: true, svm_plan_hash: "a".repeat(64), build_expires_at: Date.now() / 1000 + 300 } });
  createExecutor.mockResolvedValue({ executor_id: "preview" });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root.render(<QueryClientProvider client={query}><MemoryRouter>
    <ServerContext.Provider value={{ server: "test", setServer: () => {} }}><UniversalExecution /></ServerContext.Provider>
  </MemoryRouter></QueryClientProvider>));
});
afterEach(async () => { await act(async () => root.unmount()); query.clear(); host.remove(); });
const button = (label: string) => [...host.querySelectorAll("button")].find(b => b.textContent === label);
async function click(label: string) { await act(async () => { button(label)!.click(); }); }
async function waitFor(test: () => void) { await vi.waitFor(async () => { await act(async () => new Promise(r => setTimeout(r, 10))); test(); }); }
async function inspect() {
  await waitFor(() => expect(button("Inspect market")?.disabled).toBe(false));
  await click("Inspect market");
  await waitFor(() => expect(button("Prepare and preview")?.disabled).toBe(false));
}
async function preview() {
  await inspect(); await click("Prepare and preview");
  await waitFor(() => expect(button("Confirm execution")?.disabled).toBe(false));
}

it("prepares with exact raw units then binds confirmation to reviewed instructions", async () => {
  await preview();
  expect(prepareOnchain).toHaveBeenCalledWith("test", "prepare", {
    venue: "jupiter-lend", market: "market", action: "deposit", amount_raw: "2000000", slippage_bps: 50,
  });
  expect(createExecutor.mock.calls[0][1].config).toMatchObject({ instructions, commit: false, max_svm_network_fee_lamports: 10000 });
  expect(createExecutor.mock.calls[0][1].config.svm_spending_policy).toEqual({ wallet: "wallet", market: "market",
    protocol_program: "program", allowed_programs: ["program"], max_debits_raw: { native: "30000000", asset: "2100000" } });
  expect(host.querySelector("fieldset")!.disabled).toBe(true);
  createExecutor.mockResolvedValueOnce({ executor_id: "commit" });
  await click("Confirm execution");
  expect(createExecutor.mock.calls[1][1].config).toMatchObject({ instructions, commit: true, reviewed_svm_plan_hash: "a".repeat(64) });
  expect(createExecutor.mock.calls[1][1].config.svm_spending_policy).toEqual(createExecutor.mock.calls[0][1].config.svm_spending_policy);
  expect(host.textContent).toContain("Local Solana mirror");
});

it("does not enable confirmation for a passing simulation rejected by a limit", async () => {
  getExecutor.mockResolvedValue({ status: "terminated", close_type: "failed", config: { commit: false },
    custom_info: { simulation_passed: true, svm_plan_hash: "a".repeat(64), error: { reason: "network_fee_over_budget" } } });
  await inspect(); await click("Prepare and preview");
  await waitFor(() => expect(button("Confirm execution")?.disabled).toBe(true));
  await click("Confirm execution"); expect(createExecutor).toHaveBeenCalledTimes(1);
});

it("refuses a wallet switch between inspection and preparation", async () => {
  await inspect();
  prepareOnchain.mockResolvedValueOnce({ ...response(), wallet: "other" });
  await click("Prepare and preview");
  expect(createExecutor).not.toHaveBeenCalled();
  expect(host.textContent).toContain("Wallet or network changed");
});

it("keeps uncertain confirmation locked instead of enabling another submission", async () => {
  await preview(); createExecutor.mockRejectedValueOnce(new Error("connection lost"));
  await click("Confirm execution");
  expect(host.textContent).toContain("may have been accepted");
  expect(host.querySelector("fieldset")!.disabled).toBe(true);
  expect(button("Confirm execution")).toBeUndefined();
  expect(createExecutor).toHaveBeenCalledTimes(2);
});

it("rejects an expired prepared action even if the executor preview remains fresh", async () => {
  await inspect();
  const expired = response(); expired.result.expires_at = Date.now() / 1000 - 1;
  prepareOnchain.mockResolvedValueOnce(expired);
  await click("Prepare and preview");
  await waitFor(() => expect(button("Confirm execution")?.disabled).toBe(false));
  await click("Confirm execution");
  expect(host.textContent).toContain("Preview expired");
  expect(createExecutor).toHaveBeenCalledTimes(1);
});


it("does not refetch prepared market reads on window focus or reconnect", async () => {
  await preview();
  const calls = prepareOnchain.mock.calls.length;
  await act(async () => { focusManager.setFocused(false); onlineManager.setOnline(false); });
  await act(async () => { focusManager.setFocused(true); onlineManager.setOnline(true);
    await new Promise(resolve => setTimeout(resolve, 30)); });
  expect(prepareOnchain).toHaveBeenCalledTimes(calls);
  await click("Confirm execution");
  getExecutor.mockResolvedValue({ status: "terminated", custom_info: { committed: true } });
  await act(async () => { await query.invalidateQueries({ queryKey: ["aomi-execution"] }); });
  await waitFor(() => expect(prepareOnchain.mock.calls.some(call => call[1] === "position")).toBe(true));
  const afterPosition = prepareOnchain.mock.calls.length;
  await act(async () => { focusManager.setFocused(false); onlineManager.setOnline(false); });
  await act(async () => { focusManager.setFocused(true); onlineManager.setOnline(true);
    await new Promise(resolve => setTimeout(resolve, 30)); });
  expect(prepareOnchain).toHaveBeenCalledTimes(afterPosition);
  focusManager.setFocused(undefined);
});
