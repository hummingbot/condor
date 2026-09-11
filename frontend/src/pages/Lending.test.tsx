/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ServerContext } from "@/hooks/useServer";

const createExecutor = vi.fn();
const getExecutor = vi.fn();
vi.mock("@/lib/api", () => ({ api: { createExecutor: (...a: unknown[]) => createExecutor(...a), getExecutor: (...a: unknown[]) => getExecutor(...a) } }));
vi.mock("@/components/executor/LendingPositions", () => ({ LendingPositions: () => null }));
vi.mock("@/components/executor/OnchainEvidence", () => ({ OnchainEvidence: () => null }));
const { Lending } = await import("./Lending");
let root: Root;
let host: HTMLDivElement;
let query: QueryClient;
beforeEach(async () => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  createExecutor.mockReset(); getExecutor.mockReset();
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host); query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => root.render(<QueryClientProvider client={query}><MemoryRouter><ServerContext.Provider value={{server:"test", setServer:()=>{}}}><Lending /></ServerContext.Provider></MemoryRouter></QueryClientProvider>));
});
afterEach(async () => { await act(async () => root.unmount()); query.clear(); host.remove(); });
const button = (text: string) => [...host.querySelectorAll("button")].find(b => b.textContent === text);
async function click(text: string) { await act(async () => { button(text)!.click(); }); }
async function fill(index: number, value: string) {
  await act(async () => {
    const input = host.querySelectorAll("input")[index];
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function preview() {
  getExecutor.mockImplementation(async (_server, id) => id === "preview" ? {
    status: "terminated", close_type: "completed", config: {commit:false},
    custom_info: {simulation_passed:true, build_expires_at:Date.now()/1000+300},
  } : {status:"terminated", config:{commit:true}, custom_info:{committed:true, commit_attempted:true}});
  createExecutor.mockResolvedValueOnce({executor_id:"preview"});
  await fill(0,"0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266"); await fill(1,"1");
  await click("Preview movements and approval");
  await vi.waitFor(async () => { await act(async () => new Promise(r => setTimeout(r,10))); expect(button("Confirm supply")?.disabled).toBe(false); });
}
it("can start a new action after a confirmed commit", async () => {
  await preview(); createExecutor.mockResolvedValueOnce({executor_id:"confirmed"});
  await click("Confirm supply");
  await vi.waitFor(async () => { await act(async () => new Promise(r => setTimeout(r,10))); expect(button("Start another action")).toBeDefined(); });
  await click("Start another action");
  expect(host.querySelector("fieldset")!.disabled).toBe(false);
  expect(button("Preview movements and approval")).toBeDefined();
  expect(createExecutor).toHaveBeenCalledTimes(2);
});
it("keeps an ambiguous accepted request locked instead of enabling retry", async () => {
  await preview(); createExecutor.mockRejectedValueOnce(new Error("connection lost"));
  await click("Confirm supply");
  expect(host.textContent).toContain("request may already have been accepted");
  expect(button("Start another action")).toBeUndefined();
  expect(host.querySelector("fieldset")!.disabled).toBe(true);
  expect(createExecutor).toHaveBeenCalledTimes(2);
});
