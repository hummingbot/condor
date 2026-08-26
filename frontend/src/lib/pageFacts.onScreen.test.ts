/**
 * What each route says is on screen (FEAT-060).
 *
 * The point of this file is query-key drift: `routeFacts` reads the numbers
 * out of the same react-query cache each page renders from, and a renamed key
 * would silently drop that page's facts rather than fail. So every case seeds
 * a real `QueryClient` under the key the page actually uses and asserts the
 * block names the value — a rename breaks a test, not a user's answer.
 *
 * The last block is the other half of the contract: with nothing in the cache
 * the block must still render label and subject, and no reader may throw.
 *
 * @vitest-environment jsdom
 */

import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { routeFacts } from "./pageFacts";
import { renderViewBlock, VIEW_BLOCK_MAX_CHARS } from "./viewFacts";

const SRV = "main";

let qc: QueryClient;
beforeEach(() => {
  qc = new QueryClient();
});

/** The `On screen:` line of the block, or "" when the route contributed none. */
function onScreenLine(pathname: string, search = ""): string {
  const facts = routeFacts(pathname, search, qc);
  const block = renderViewBlock(facts ? [facts] : [], pathname + search);
  return block.split("\n").find((l) => l.startsWith("On screen:")) ?? "";
}

describe("/portfolio", () => {
  it("names the total, the change over the window on screen and the holdings", () => {
    qc.setQueryData(["portfolio", SRV], {
      server: SRV,
      total_usd: 125_430.5,
      connectors: [
        { connector: "binance", total_usd: 100, balances: [{}, {}, {}] },
        { connector: "kucoin", total_usd: 25, balances: [{}] },
      ],
    });
    qc.setQueryData(["portfolio-history", SRV, "1W"], {
      server: SRV,
      interval: "1h",
      points: [{ timestamp: 1, total_usd: 120_000 }, { timestamp: 2, total_usd: 125_430.5 }],
    });
    qc.setQueryData(["consolidated-positions", SRV], {
      executor_positions: [{}, {}],
      bot_positions: [{}],
    });

    const line = onScreenLine("/portfolio");
    expect(line).toContain("total $125.4K");
    expect(line).toContain("change (1W) +$5,430.50");
    expect(line).toContain("assets 4");
    expect(line).toContain("open positions 3");
    expect(line).toContain("tab assets");
  });

  it("reads the tab from the URL", () => {
    qc.setQueryData(["portfolio", SRV], { server: SRV, total_usd: 1, connectors: [] });
    expect(onScreenLine("/portfolio", "?tab=positions")).toContain("tab positions");
  });
});

describe("/bots", () => {
  it("counts the fleet that is running", () => {
    qc.setQueryData(["bots", SRV], {
      bots: [
        { bot_name: "a", status: "running", num_controllers: 2 },
        { bot_name: "b", status: "stopped", num_controllers: 1 },
      ],
      controllers: [{ bot_name: "a" }, { bot_name: "a" }, { bot_name: "b" }],
      total_pnl: 0,
      total_volume: 0,
    });
    const line = onScreenLine("/bots");
    expect(line).toContain("bots 1 running / 2");
    expect(line).toContain("controllers 3");
  });
});

describe("/bots/:id", () => {
  beforeEach(() => {
    qc.setQueryData(["bot", SRV, "42"], {
      bot: {
        id: "42",
        name: "backpack-mm-3",
        status: "running",
        connector: "backpack",
        trading_pair: "SOL-USDC",
        pnl: -412.2971,
        uptime: 0,
        controller_type: "market_making",
      },
      config: {},
      performance: {},
    });
    qc.setQueryData(["bots", SRV], {
      bots: [
        {
          bot_name: "backpack-mm-3",
          status: "running",
          num_controllers: 3,
          deployed_at: new Date(Date.now() - 90 * 60_000).toISOString(),
        },
      ],
      controllers: [{ bot_name: "backpack-mm-3" }],
      total_pnl: 0,
      total_volume: 0,
    });
  });

  it("names the bot, its PNL and its controller count", () => {
    const line = onScreenLine("/bots/42");
    expect(line).toContain("bot backpack-mm-3");
    expect(line).toContain("status running");
    // Formatted the way the page formats money, not `-412.2971`.
    expect(line).toContain("pnl -$412.30");
    expect(line).toContain("controllers 3");
    expect(line).toContain("uptime 1h 30m");
  });

  it("reads the bot the URL names, not whichever bot is cached", () => {
    qc.setQueryData(["bot", SRV, "43"], {
      bot: { id: "43", name: "other-bot", status: "stopped", pnl: 0, trading_pair: "" },
      config: {},
      performance: {},
    });
    expect(onScreenLine("/bots/42")).toContain("bot backpack-mm-3");
    expect(onScreenLine("/bots/43")).toContain("bot other-bot");
  });
});

describe("/dex", () => {
  it("takes the chain and source tab from the listing's own key", () => {
    qc.setQueryData(
      ["dex-pools", SRV, "gecko", "trending", "solana", "", "", 1],
      { pools: [{ network: "solana" }, { network: "solana" }], has_more: false },
    );
    const line = onScreenLine("/dex");
    expect(line).toContain("network solana");
    expect(line).toContain("source trending");
    expect(line).toContain("pools listed 2");
  });
});

describe("/dex/:network/:address", () => {
  it("names the pair, dex, price and TVL", () => {
    qc.setQueryData(["dex-pool-by-address", SRV, "solana", "7qbRF6"], {
      address: "7qbRF6",
      name: "SOL / USDC",
      dex_id: "meteora",
      network: "solana",
      base_symbol: "SOL",
      quote_symbol: "USDC",
      trading_pair: "So111-USDC",
      current_price: 182.4471,
      reserve_usd: 4_210_000,
      volume_24h: 1_250_000,
      price_change_24h: 3.5,
      lp_supported: true,
    });
    qc.setQueryData(["dex-lp-executors", SRV], [
      { id: "1", status: "active", config: { pool_address: "7qbRF6" } },
      { id: "2", status: "active", config: { pool_address: "other" } },
      { id: "3", status: "completed", config: { pool_address: "7qbRF6" } },
    ]);

    const line = onScreenLine("/dex/solana/7qbRF6");
    expect(line).toContain("pair SOL-USDC");
    expect(line).toContain("dex meteora");
    expect(line).toContain("price 182.4471");
    expect(line).toContain("tvl $4.21M");
    expect(line).toContain("24h volume $1.3M");
    expect(line).toContain("24h change +3.50%");
    // Only the open ranges in *this* pool.
    expect(line).toContain("your lp positions 1");
  });
});

describe("/executors", () => {
  it("counts what is active across the loaded pages", () => {
    qc.setQueryData(["executors-infinite", SRV], {
      pageParams: [""],
      pages: [
        {
          executors: [
            { id: "1", status: "active", pnl: 12.5, trading_pair: "SOL-USDT" },
            { id: "2", status: "active", pnl: 7.5, trading_pair: "SOL-USDT" },
            { id: "3", status: "completed", pnl: -3, trading_pair: "SOL-USDT" },
          ],
          next_cursor: null,
        },
      ],
    });
    const line = onScreenLine("/executors");
    expect(line).toContain("active 2");
    expect(line).toContain("loaded 3");
    expect(line).toContain("active pnl +$20.00");
  });
});

describe("/routines", () => {
  it("counts the routines and what is live", () => {
    qc.setQueryData(["routines"], [{ name: "a" }, { name: "b" }, { name: "c" }]);
    qc.setQueryData(["routine-instances"], [
      { instance_id: "1", status: "running" },
      { instance_id: "2", status: "scheduled" },
      { instance_id: "3", status: "idle" },
    ]);
    const line = onScreenLine("/routines");
    expect(line).toContain("routines 3");
    expect(line).toContain("instances 2 running / 3");
  });

  it("counts reports on the reports tab", () => {
    qc.setQueryData(["reports-grouped"], [
      { source_name: "a", total_count: 4, latest_report: {}, all_tags: [] },
      { source_name: "b", total_count: 2, latest_report: {}, all_tags: [] },
    ]);
    const line = onScreenLine("/routines", "?tab=reports");
    expect(line).toContain("report sources 2");
    expect(line).toContain("reports 6");
  });
});

describe("/agents/:slug", () => {
  it("names the agent and how many of its strategies are running", () => {
    qc.setQueryData(["agent", "orca-lp-expert"], {
      slug: "orca-lp-expert",
      name: "Orca LP Expert",
      server_name: SRV,
      strategies: [
        { slug: "sol-lp", instances: [{ agent_id: "x" }] },
        { slug: "eth-lp", instances: [] },
      ],
    });
    const line = onScreenLine("/agents/orca-lp-expert");
    expect(line).toContain("agent Orca LP Expert");
    expect(line).toContain("strategies 1 running / 2");
    expect(line).toContain(`server ${SRV}`);
  });
});

describe("money in the display currency", () => {
  it("converts through the rates cache and labels it with that symbol", async () => {
    // The currency store reads localStorage once at module load, so the whole
    // module graph is re-imported with the choice already made — the same way
    // a reload reaches a user who picked EUR last session.
    localStorage.setItem("condor_display_currency", "EUR");
    vi.resetModules();
    const [{ routeFacts: fresh }, { renderViewBlock: render }] = await Promise.all([
      import("./pageFacts"),
      import("./viewFacts"),
    ]);

    const client = new QueryClient();
    client.setQueryData(["portfolio", SRV], {
      server: SRV,
      total_usd: 125_430.5,
      connectors: [],
    });
    // The key `useRates` fills: EUR priced against the quotes it was asked for.
    client.setQueryData(["rates", SRV, "EUR", "USDT"], { USDT: 1.1 });

    const block = render([fresh("/portfolio", "", client)!], "/portfolio");
    expect(block).toContain("total \u20AC114.0K");
    expect(block).toContain("currency EUR");

    localStorage.removeItem("condor_display_currency");
    vi.resetModules();
  });

  it("keeps the $ symbol until the rate lands, so number and label agree", async () => {
    localStorage.setItem("condor_display_currency", "EUR");
    vi.resetModules();
    const [{ routeFacts: fresh }, { renderViewBlock: render }] = await Promise.all([
      import("./pageFacts"),
      import("./viewFacts"),
    ]);

    const client = new QueryClient();
    client.setQueryData(["portfolio", SRV], {
      server: SRV,
      total_usd: 125_430.5,
      connectors: [],
    });

    const block = render([fresh("/portfolio", "", client)!], "/portfolio");
    // Unconverted, and marked as such exactly like `useRates` marks it.
    expect(block).toContain("total $125.4K \u26A0");

    localStorage.removeItem("condor_display_currency");
    vi.resetModules();
  });
});

describe("an empty cache", () => {
  it("still renders label and subject, and no reader throws", () => {
    for (const [path, search] of [
      ["/portfolio", ""],
      ["/bots", ""],
      ["/bots/42", ""],
      ["/trade", ""],
      ["/dex", ""],
      ["/dex/solana/7qbRF6", ""],
      ["/executors", ""],
      ["/routines", ""],
      ["/routines", "?tab=reports"],
      ["/agents/orca-lp-expert", ""],
      ["/settings", ""],
    ] as const) {
      const facts = routeFacts(path, search, qc);
      expect(facts).not.toBeNull();
      expect(facts!.label).toBeTruthy();
      expect(facts!.onScreen).toBeUndefined();
      const block = renderViewBlock([facts!], path + search);
      expect(block).toContain(`Screen: ${facts!.label}`);
      expect(block).not.toContain("On screen:");
    }
  });

  it("degrades to the baseline when a cached payload is the wrong shape", () => {
    // A key that survives a backend change but whose payload does not: the
    // reader throws on `.filter` of a non-array and the block must stay whole.
    qc.setQueryData(["bots", SRV], { bots: "not an array", controllers: null });
    const facts = routeFacts("/bots", "", qc);
    expect(facts).toEqual({ label: "Bots" });
  });
});

describe("the block's budget", () => {
  it("stays inside the cap on the busiest page", () => {
    qc.setQueryData(["portfolio", SRV], {
      server: SRV,
      total_usd: 125_430.5,
      connectors: Array.from({ length: 12 }, () => ({
        connector: "binance",
        total_usd: 1,
        balances: Array.from({ length: 40 }, () => ({})),
      })),
    });
    qc.setQueryData(["portfolio-history", SRV, "1W"], {
      points: [{ timestamp: 1, total_usd: 1 }, { timestamp: 2, total_usd: 2 }],
    });
    qc.setQueryData(["consolidated-positions", SRV], {
      executor_positions: Array.from({ length: 30 }, () => ({})),
      bot_positions: [],
    });
    const facts = routeFacts("/portfolio", "", qc)!;
    const block = renderViewBlock([facts], "/portfolio");
    expect(block.length).toBeLessThan(VIEW_BLOCK_MAX_CHARS);
    expect(block).not.toContain("…");
  });
});
