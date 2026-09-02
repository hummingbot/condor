/**
 * What is running right now, read beside the conversation (FEAT-094).
 *
 * Three things this panel can only get wrong once, because they are the three
 * places the live fleet is not what the payload literally says:
 *
 *  - a controller's `status` is a hardcoded `"running"`, so the kill switch is
 *    the only thing that says whether it is trading;
 *  - `controller_id` is the *config* id, so one config on two bots is two
 *    controllers and has to be two rows with two different scope links
 *    (CORR-241);
 *  - an executor carries no bot, so one whose controller is not in the live
 *    fleet belongs under Unattached rather than under a guess.
 *
 * And that every row's link is the `?scope=` id the perf browser reads, built
 * through `controllerNodeId` rather than formatted here — the single point of
 * change that keeps the two from drifting (FEAT-084, FEAT-086).
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControllerInfo, ExecutorInfo } from "@/lib/api";

const getBots = vi.fn();
const getExecutors = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getBots: (...a: unknown[]) => getBots(...a),
    getExecutors: (...a: unknown[]) => getExecutors(...a),
    getRates: () => Promise.resolve({ rates: {} }),
  },
}));

vi.mock("@/hooks/useWebSocket", () => ({ useCondorWebSocket: () => ({}) }));

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return { ...actual, useNavigate: () => navigate };
});

const { DockExecution } = await import("./DockExecution");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SERVER = "brigado_2";

function controller(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "pmm_dynamic",
    controller_id: "pmm_v2",
    bot_name: "backpack-mm-3",
    status: "running",
    connector: "backpack",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 412,
    global_pnl_pct: 1.2,
    volume_traded: 0,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: "2026-09-01T10:00:00Z",
    config: {},
    ...over,
  };
}

function executor(over: Partial<ExecutorInfo> = {}): ExecutorInfo {
  return {
    id: "e1",
    type: "position_executor",
    connector: "backpack",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "active",
    close_type: "",
    pnl: 1,
    volume: 10,
    timestamp: 1_756_000_000,
    controller_id: "pmm_v2",
    cum_fees_quote: 0,
    net_pnl_pct: 0,
    entry_price: 1,
    current_price: 1,
    close_timestamp: 0,
    custom_info: {},
    config: {},
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;

async function render() {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <DockExecution server={SERVER} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

const rows = () => [...container.querySelectorAll("[data-controller-row]")];
/** The bot group headers, one per bot with anything live under it. */
const botHeaders = () => [...container.querySelectorAll("[data-bot-group]")];
const rowFor = (title: string) =>
  container.querySelector<HTMLElement>(`[title="${title}"]`)!;
const counts = () =>
  container.querySelector('[data-testid="execution-counts"]')!.textContent ?? "";
const text = () => container.textContent ?? "";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  getBots.mockResolvedValue({
    controllers: [controller()],
    bots: [],
    total_pnl: 0,
    total_volume: 0,
  });
  getExecutors.mockResolvedValue([executor()]);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("the execution panel", () => {
  it("links a controller row to the scope the browser opens it on", async () => {
    await render();

    await click(rows()[0] as HTMLElement);
    expect(navigate).toHaveBeenCalledWith("/bots?scope=ctrl:backpack-mm-3:pmm_v2");

    // The group header above it is the bot's whole branch, not this row's.
    expect(botHeaders()).toHaveLength(1);
    await click(rowFor("Everything backpack-mm-3 is running"));
    expect(navigate).toHaveBeenLastCalledWith("/bots?scope=bot:backpack-mm-3");
  });

  it("leaves out a controller whose kill switch is on", async () => {
    getBots.mockResolvedValue({
      controllers: [
        controller(),
        // `status` still says "running" — it always does. The switch is what
        // decides, and this panel's whole job is that distinction.
        controller({
          controller_id: "grid_v1",
          bot_name: "brigado-2",
          config: { manual_kill_switch: true },
        }),
      ],
      bots: [],
      total_pnl: 0,
      total_volume: 0,
    });

    await render();

    expect(text()).not.toContain("grid_v1");
    expect(rows()).toHaveLength(1);
    expect(counts()).toContain("1 controller");
  });

  it("gives one config on two bots two rows and two scopes", async () => {
    getBots.mockResolvedValue({
      controllers: [controller(), controller({ bot_name: "brigado-2" })],
      bots: [],
      total_pnl: 0,
      total_volume: 0,
    });

    await render();

    expect(rows()).toHaveLength(2);
    // Two bots, so two groups: the config id alone would have merged them.
    expect(botHeaders()).toHaveLength(2);
    await click(rowFor("pmm_v2 on backpack-mm-3"));
    expect(navigate).toHaveBeenLastCalledWith("/bots?scope=ctrl:backpack-mm-3:pmm_v2");
    await click(rowFor("pmm_v2 on brigado-2"));
    expect(navigate).toHaveBeenLastCalledWith("/bots?scope=ctrl:brigado-2:pmm_v2");
  });

  it("files an executor no live controller claims under Unattached", async () => {
    getExecutors.mockResolvedValue([
      executor(),
      executor({ id: "e2", controller_id: "main" }),
      // Closed executors are not the live fleet, whatever they are filed under.
      executor({ id: "e3", controller_id: "main", status: "terminated" }),
    ]);

    await render();

    expect(text()).toContain("(unattached)");
    expect(text()).toContain("1 executor");
    // Counted where it belongs: the controller keeps only the one that is its.
    // The count is a cell in the Exec column now, not a phrase in the row.
    expect(rows()[0].querySelectorAll("td")[2].textContent).toBe("1");
    expect(counts()).toContain("2 executors");

    await click(container.querySelector<HTMLButtonElement>(
      'button[title="Live executors no running controller claims"]',
    )!);
    expect(navigate).toHaveBeenLastCalledWith("/bots?population=running");
  });

  it("says the fleet is empty rather than drawing an empty table", async () => {
    getBots.mockResolvedValue({
      controllers: [],
      bots: [],
      total_pnl: 0,
      total_volume: 0,
    });
    getExecutors.mockResolvedValue([]);

    await render();

    expect(text()).toContain(`No controllers running on ${SERVER}`);
    expect(text()).toContain("Open execution");
  });
});
