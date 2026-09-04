/**
 * What the strategy actually put into the world.
 *
 * The workbench could describe the *loop* down to the second and say nothing
 * at all about the controllers it deployed — the only part of it spending
 * money. The one route to those was a button that navigated to `/bots`, which
 * is to say the answer cost you the strategy you were reading.
 *
 * Two promises are load-bearing here:
 *
 * 1. **Only this strategy's rows.** The narrowing *is* the feature: a reader on
 *    a strategy is asking about their own scope, and it is decided by
 *    `attributionOf` — the same rule the fleet browser's tree applies — so the
 *    two surfaces can never disagree about who owns what.
 * 2. **An empty list says which kind of empty it is.** A run whose ownership
 *    claim never landed (every run before the ACP arguments fix) has a fleet
 *    trading on unattributed, and printing "nothing deployed" over that is the
 *    lie that sends a reader hunting a frontend bug for an hour.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { FleetOwner } from "@/lib/agent-attribution";
import type { ControllerInfo } from "@/lib/api";
import { DeployedFleet } from "./DeployedFleet";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/** The display formatting the real `formatWithRate` produces, in miniature. */
const money = (value: number) =>
  `$${Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;

/** What `useFleetData` hands back, swapped per test. */
let FLEET: {
  controllers: ControllerInfo[];
  owners: FleetOwner[];
  isLoading: boolean;
};

vi.mock("@/lib/api", () => ({
  api: {
    claimBot: vi.fn(async () => ({ claimed: "", session: "", owned: [] })),
    stopControllers: vi.fn(async () => ({})),
    startControllers: vi.fn(async () => ({})),
  },
}));

vi.mock("@/hooks/useFleetData", () => ({
  useFleetData: () => ({
    ...FLEET,
    bots: [],
    executors: [],
    paging: {},
    snapshots: [],
    truncated: false,
    runs: [],
    terminatedControllers: [],
    deeds: null,
    // The real `ConvertFn` returns `{ value, converted }`, and the real
    // formatters mark an unconverted figure. Mirrored rather than stubbed to a
    // number: a mock that returns a bare number let the component read `.value`
    // off it and render NaN, which `tsc --noEmit` over the test never sees.
    convert: (value: number) => ({ value, converted: true }),
    currencySymbol: "$",
    rateFormatPnl: money,
    rateFormatValue: money,
    rateFormatDetailed: money,
    error: null,
    serverOnline: true,
  }),
}));

function controller(over: Partial<ControllerInfo> = {}): ControllerInfo {
  return {
    controller_name: "pmm_simple",
    controller_type: "market_making",
    controller_id: "c1",
    bot_name: "brigado-fleet_op-20260903-181000",
    status: "running",
    connector: "binance",
    trading_pair: "BTC-BRL",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    global_pnl_pct: 0,
    volume_traded: 0,
    close_type_counts: {},
    positions_summary: [],
    deployed_at: null,
    config: {},
    ...over,
  };
}

/** The fleet map's claim: this namespace belongs to this run key. */
function owner(over: Partial<FleetOwner> = {}): FleetOwner {
  return {
    runKey: "brigado.fleet_op",
    agentSlug: "brigado",
    agentName: "Brigado",
    strategySlug: "fleet_op",
    strategyName: "Fleet Operator",
    namespace: "brigado-fleet_op",
    declaredBots: [],
    agentIds: [],
    live: null,
    ...over,
  };
}

let host: HTMLDivElement;
let root: Root;

async function render(node: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

const panel = () => (
  <DeployedFleet slug="brigado" sslug="fleet_op" serverName="brigado_2" />
);

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  FLEET = { controllers: [], owners: [], isLoading: false };
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("the rows it shows", () => {
  it("shows the controllers this strategy owns", async () => {
    FLEET = {
      controllers: [
        controller({ controller_id: "c1", trading_pair: "BTC-BRL" }),
        controller({ controller_id: "c2", trading_pair: "ETH-BRL" }),
      ],
      owners: [owner()],
      isLoading: false,
    };
    await render(panel());

    expect(host.textContent).toContain("BTC-BRL");
    expect(host.textContent).toContain("ETH-BRL");
    expect(host.textContent).toContain("brigado-fleet_op-20260903-181000");
  });

  it("leaves out controllers belonging to another run", async () => {
    // The narrowing is the whole point: everything else on the server is noise
    // to a reader who came here to ask about their own scope.
    FLEET = {
      controllers: [
        controller({ controller_id: "mine", trading_pair: "BTC-BRL" }),
        controller({
          controller_id: "theirs",
          bot_name: "someone-else-20260903-181000",
          trading_pair: "SOL-USDC",
        }),
      ],
      owners: [owner(), owner({ runKey: "other.thing", namespace: "someone-else" })],
      isLoading: false,
    };
    await render(panel());

    expect(host.textContent).toContain("BTC-BRL");
    expect(host.textContent).not.toContain("SOL-USDC");
    // And it says how much of the server it is leaving out, so the number is
    // never mistaken for the whole fleet.
    expect(host.textContent).toContain("1 of 2");
  });

  it("adds up the money across the rows it kept", async () => {
    FLEET = {
      controllers: [
        controller({ controller_id: "c1", global_pnl_quote: 40, volume_traded: 1000 }),
        controller({ controller_id: "c2", global_pnl_quote: 24.5, volume_traded: 500 }),
        controller({
          controller_id: "not-mine",
          bot_name: "stranger",
          global_pnl_quote: 9999,
          volume_traded: 9999,
        }),
      ],
      owners: [owner()],
      isLoading: false,
    };
    await render(panel());

    expect(host.textContent).toContain("+$64.50");
    expect(host.textContent).toContain("$1,500.00");
    expect(host.textContent).not.toContain("9,999");
  });

  it("reads the kill switch rather than the status field", async () => {
    // Upstream hardcodes a controller's `status` to "running"; what actually
    // says whether it is quoting is `manual_kill_switch` in its config.
    FLEET = {
      controllers: [
        controller({
          controller_id: "off",
          status: "running",
          config: { manual_kill_switch: true },
        }),
      ],
      owners: [owner()],
      isLoading: false,
    };
    await render(panel());

    const dot = host.querySelector('[title="Stopped (kill switch on)"]');
    expect(dot).not.toBeNull();
  });

  it("pauses and starts a controller from the row, without leaving the strategy", async () => {
    // The same control the execution dock and the fleet browser carry: a
    // reader who found their strategy's controllers here should not have to go
    // and find them a second time somewhere else to pause one.
    FLEET = {
      controllers: [
        controller({ controller_id: "quoting" }),
        controller({ controller_id: "off", config: { manual_kill_switch: true } }),
      ],
      owners: [owner()],
      isLoading: false,
    };
    await render(panel());

    const toggles = [
      ...host.querySelectorAll<HTMLButtonElement>("[data-controller-toggle]"),
    ];
    expect(toggles).toHaveLength(2);

    await act(async () => {
      toggles[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(api.stopControllers).toHaveBeenCalledWith(
      "brigado_2",
      "brigado-fleet_op-20260903-181000",
      ["quoting"],
    );

    await act(async () => {
      toggles[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(api.startControllers).toHaveBeenCalledWith(
      "brigado_2",
      "brigado-fleet_op-20260903-181000",
      ["off"],
    );
  });
});

describe("when there is nothing to show", () => {
  it("says the fleet is still being read rather than that it is empty", async () => {
    FLEET = { controllers: [], owners: [], isLoading: true };
    await render(panel());
    expect(host.textContent).toContain("Reading the fleet");
  });

  it("says so plainly when the server really is empty", async () => {
    await render(panel());
    expect(host.textContent).toContain("brigado_2");
    expect(host.textContent).not.toContain("Before the ledger");
  });

  it("points at the unattributed rows when the server is not empty", async () => {
    // The signature of a lost ownership claim: controllers running under
    // nobody while this strategy has run. The reader is told that, rather than
    // being left to conclude their agent did nothing.
    FLEET = {
      controllers: [
        controller({ controller_id: "orphan", bot_name: "pmm-king-btcbrl-20260903-181000" }),
      ],
      owners: [],
      isLoading: false,
    };
    await render(panel());

    expect(host.textContent).toContain("1 controller is running there");
    expect(host.textContent).toContain("Before the ledger");
  });

  it("says the real reason when no server is pinned at all", async () => {
    await render(
      <DeployedFleet slug="brigado" sslug="fleet_op" serverName="" />,
    );
    expect(host.textContent).toContain("no server pinned");
  });
});

describe("claiming a bot whose ownership never landed", () => {
  it("offers the repair beside the diagnosis", async () => {
    FLEET = {
      controllers: [
        controller({
          controller_id: "orphan",
          bot_name: "pmm-king-btcbrl-20260903-181000",
        }),
      ],
      owners: [],
      isLoading: false,
    };
    await render(panel());

    expect(host.textContent).toContain("pmm-king-btcbrl-20260903-181000");
    const claim = [...host.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "Claim",
    );
    expect(claim).toBeDefined();
  });

  it("claims from the bot's deploy time, not from the click", async () => {
    // The whole value of the back-fill: the ledger slices PnL over the window
    // it owns the bot for, so claiming at "now" would credit the strategy with
    // nothing it has already made.
    const deployedAt = "2026-09-03T18:10:00Z";
    FLEET = {
      controllers: [
        controller({
          controller_id: "orphan",
          bot_name: "pmm-king-btcbrl-20260903-181000",
          deployed_at: deployedAt,
        }),
      ],
      owners: [],
      isLoading: false,
    };
    await render(panel());

    await act(async () => {
      [...host.querySelectorAll("button")]
        .find((b) => b.textContent?.trim() === "Claim")
        ?.click();
    });

    expect(api.claimBot).toHaveBeenCalledWith(
      "brigado",
      "fleet_op",
      "pmm-king-btcbrl-20260903-181000",
      Date.parse(deployedAt) / 1000,
    );
  });

  it("takes the earliest deploy across a bot's controllers", async () => {
    // A bot is up when its first controller went up; a later sibling must not
    // shorten the window the claim opens.
    FLEET = {
      controllers: [
        controller({
          controller_id: "a",
          bot_name: "fleet",
          deployed_at: "2026-09-03T20:00:00Z",
        }),
        controller({
          controller_id: "b",
          bot_name: "fleet",
          deployed_at: "2026-09-03T18:10:00Z",
        }),
      ],
      owners: [],
      isLoading: false,
    };
    await render(panel());

    await act(async () => {
      [...host.querySelectorAll("button")]
        .find((b) => b.textContent?.trim() === "Claim")
        ?.click();
    });

    expect(api.claimBot).toHaveBeenCalledWith(
      "brigado",
      "fleet_op",
      "fleet",
      Date.parse("2026-09-03T18:10:00Z") / 1000,
    );
  });

  it("never lets an unknown deploy time beat a real one", async () => {
    // A missing timestamp reads as 0, and 0 winning would claim the bot from
    // 1970 — slicing in trading that predates this strategy entirely.
    FLEET = {
      controllers: [
        controller({ controller_id: "a", bot_name: "fleet", deployed_at: null }),
        controller({
          controller_id: "b",
          bot_name: "fleet",
          deployed_at: "2026-09-03T18:10:00Z",
        }),
      ],
      owners: [],
      isLoading: false,
    };
    await render(panel());

    await act(async () => {
      [...host.querySelectorAll("button")]
        .find((b) => b.textContent?.trim() === "Claim")
        ?.click();
    });

    expect(api.claimBot).toHaveBeenCalledWith(
      "brigado",
      "fleet_op",
      "fleet",
      Date.parse("2026-09-03T18:10:00Z") / 1000,
    );
  });

  it("offers no claim for a bot another run already owns", async () => {
    FLEET = {
      controllers: [
        controller({ controller_id: "theirs", bot_name: "someone-else" }),
      ],
      owners: [owner({ runKey: "other.thing", namespace: "someone-else" })],
      isLoading: false,
    };
    await render(panel());

    const claim = [...host.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "Claim",
    );
    // Claiming another run's trading is a mis-attribution that moves money
    // between two agents' books; it is never offered.
    expect(claim).toBeUndefined();
  });
});
