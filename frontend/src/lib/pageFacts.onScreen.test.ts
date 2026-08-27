/**
 * What each route says is on screen (FEAT-060, widened by FEAT-072).
 *
 * The point of this file is query-key drift: `routeFacts` reads the numbers
 * out of the same react-query cache each page renders from, and a renamed key
 * would silently drop that page's facts rather than fail. So every case seeds
 * a real `QueryClient` under the key the page actually uses and asserts the
 * block names the value — a rename breaks a test, not a user's answer.
 *
 * FEAT-072 turned the block from an orientation label into an answer, so the
 * cases below are also about the doctrine: the exception is named beside the
 * count (R1), a total comes with its rate (R2), a slice says it is one (R3),
 * the notable rows are there by name (R4/R5) and a stale poll is stamped (R6).
 *
 * The last blocks are the other half of the contract: with nothing in the cache
 * the block must still render label and subject, no reader may throw, and every
 * route stays inside the wire cap with a full one.
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
  beforeEach(() => {
    qc.setQueryData(["portfolio", SRV], {
      server: SRV,
      total_usd: 125_430.5,
      connectors: [
        {
          connector: "binance",
          total_usd: 100_430.5,
          balances: [
            { token: "SOL", total: 400, available: 400, usd_value: 62_000 },
            { token: "USDC", total: 30_000, available: 30_000, usd_value: 30_000 },
            { token: "ETH", total: 2, available: 2, usd_value: 8_430.5 },
          ],
        },
        {
          connector: "kucoin",
          total_usd: 25_000,
          balances: [{ token: "SOL", total: 160, available: 160, usd_value: 25_000 }],
        },
      ],
    });
    qc.setQueryData(["portfolio-history", SRV, "1W"], {
      server: SRV,
      interval: "1h",
      points: [
        { timestamp: 1, total_usd: 120_000, tokens: { SOL: 84_000, USDC: 30_000, ETH: 6_000 } },
        { timestamp: 2, total_usd: 125_430.5, tokens: { SOL: 87_000, USDC: 30_000, ETH: 8_430.5 } },
      ],
    });
    qc.setQueryData(["consolidated-positions", SRV], {
      executor_positions: [
        {
          trading_pair: "SOL-USDC",
          position_side: "LONG",
          notional_value: 41_000,
          unrealized_pnl: 1_200,
        },
        {
          trading_pair: "ETH-USDT",
          position_side: "SHORT",
          notional_value: 9_000,
          unrealized_pnl: -300,
        },
      ],
      bot_positions: [
        {
          trading_pair: "BTC-USDT",
          position_side: "LONG",
          notional_value: 3_000,
          unrealized_pnl: 50,
        },
      ],
    });
  });

  it("names the total, the change over the window on screen and the holdings", () => {
    const line = onScreenLine("/portfolio");
    expect(line).toContain("total $125.4K");
    expect(line).toContain("change (1W) +$5,430.50");
    expect(line).toContain("assets 4");
    expect(line).toContain("open positions 3");
    expect(line).toContain("tab assets");
  });

  it("names the venues and the biggest holdings, summed across venues (R4)", () => {
    const line = onScreenLine("/portfolio");
    expect(line).toContain("venues binance $100.4K, kucoin $25.0K");
    // SOL is held on both venues; the user holds one SOL position, not two.
    expect(line).toContain("SOL $87.0K (69%)");
    expect(line).toContain("USDC $30.0K (24%)");
  });

  it("names what moved the wrong way over the window on screen", () => {
    // ETH and SOL both rose over the window; nothing fell, so nothing is named.
    expect(onScreenLine("/portfolio")).not.toContain("worst mover");

    qc.setQueryData(["portfolio-history", SRV, "1W"], {
      server: SRV,
      interval: "1h",
      points: [
        { timestamp: 1, total_usd: 130_000, tokens: { SOL: 94_000, ETH: 6_000 } },
        { timestamp: 2, total_usd: 125_430.5, tokens: { SOL: 87_000, ETH: 8_430.5 } },
      ],
    });
    expect(onScreenLine("/portfolio")).toContain("worst mover (1W) SOL -$7,000.00");
  });

  it("reads the tab from the URL, and the positions tab brings its own facts", () => {
    const line = onScreenLine("/portfolio", "?tab=positions");
    expect(line).toContain("tab positions");
    expect(line).toContain("unrealized +$950.00");
    expect(line).toContain("largest position SOL-USDC LONG $41.0K");
    // Position facts belong to the tab that shows them.
    expect(onScreenLine("/portfolio")).not.toContain("largest position");
  });
});

describe("/bots", () => {
  beforeEach(() => {
    qc.setQueryData(["bots", SRV], {
      bots: [
        { bot_name: "backpack-mm-3", status: "running", num_controllers: 2 },
        { bot_name: "grid-eth-1", status: "stopped", num_controllers: 1 },
        { bot_name: "mm-sol-2", status: "stopped", num_controllers: 1 },
      ],
      controllers: [
        { bot_name: "backpack-mm-3", global_pnl_quote: 900 },
        { bot_name: "backpack-mm-3", global_pnl_quote: 320 },
        { bot_name: "grid-eth-1", global_pnl_quote: -75 },
        { bot_name: "mm-sol-2", global_pnl_quote: -410 },
      ],
      total_pnl: 735,
      total_volume: 2_549_843,
    });
  });

  it("counts the fleet that is running", () => {
    const line = onScreenLine("/bots");
    expect(line).toContain("bots 1 running / 3");
    expect(line).toContain("controllers 4");
  });

  it("names the ones that are not running (R1)", () => {
    expect(onScreenLine("/bots")).toContain("stopped grid-eth-1, mm-sol-2");
  });

  it("carries the server-side totals the payload already had", () => {
    const line = onScreenLine("/bots");
    expect(line).toContain("total pnl +$735.00");
    expect(line).toContain("total volume $2.5M");
  });

  it("names the best and the worst bot by summed controller PNL (R4/R5)", () => {
    const line = onScreenLine("/bots");
    // backpack-mm-3 is 900 + 320 across two controllers.
    expect(line).toContain("best backpack-mm-3 +$1,220.00");
    expect(line).toContain("worst mm-sol-2 -$410.00");
  });

  it("describes the tab the user is actually on, not the live fleet", () => {
    // Five tabs, five tables. Reading the fleet under "Bot runs" would report a
    // screen the user is not looking at.
    qc.setQueryData(["bot-runs", SRV], {
      total: 312,
      runs: [
        { bot_name: "backpack-mm-3", run_status: "STOPPED", global_pnl_quote: 1_220 },
        { bot_name: "mm-sol-2", run_status: "STOPPED", global_pnl_quote: -410 },
        { bot_name: "grid-eth-1", run_status: "RUNNING", global_pnl_quote: -75 },
      ],
    });
    const runs = onScreenLine("/bots", "?tab=runs");
    expect(runs).toContain("runs 3 of 312");
    expect(runs).toContain("by status STOPPED 2, RUNNING 1");
    expect(runs).toContain("best backpack-mm-3 +$1,220.00");
    expect(runs).not.toContain("total volume");

    qc.setQueryData(["archived-bots", SRV], [
      { bot_name: "old-mm-1", db_path: "a", total_trades: 400 },
      { bot_name: "old-mm-2", db_path: "b", total_trades: 112 },
    ]);
    const archived = onScreenLine("/bots", "?tab=archived");
    expect(archived).toContain("archived bots 2");
    expect(archived).toContain("bots old-mm-1, old-mm-2");
    expect(archived).toContain("trades 512");

    // Backtest and the editor hold nothing readable; the label says enough.
    expect(onScreenLine("/bots", "?tab=backtest")).toBe("");
    expect(onScreenLine("/bots", "?tab=editor")).toBe("");
  });

  it("caps the exception list and says how many it left out", () => {
    qc.setQueryData(["bots", SRV], {
      bots: Array.from({ length: 6 }, (_, i) => ({
        bot_name: `bot-${i}`,
        status: "stopped",
        num_controllers: 0,
      })),
      controllers: [],
      total_pnl: 0,
      total_volume: 0,
    });
    expect(onScreenLine("/bots")).toContain("stopped bot-0, bot-1, bot-2 +3 more");
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
      controllers: [
        {
          bot_name: "backpack-mm-3",
          controller_id: "mm_sol_a",
          realized_pnl_quote: -500,
          unrealized_pnl_quote: 87.7,
          volume_traded: 412_000,
          config: {},
        },
        {
          bot_name: "backpack-mm-3",
          controller_id: "mm_sol_b",
          realized_pnl_quote: 0,
          unrealized_pnl_quote: 0,
          volume_traded: 0,
          config: { manual_kill_switch: true },
        },
      ],
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

  it("puts the bot's name in the subject, not its id (R5)", () => {
    expect(routeFacts("/bots/42", "", qc)?.subject).toBe('bot "backpack-mm-3" (id 42)');
    // Nothing cached: the URL is all there is, and the id is honest.
    expect(routeFacts("/bots/42", "", new QueryClient())?.subject).toBe("bot id 42");
  });

  it("splits realized from unrealized and totals the volume", () => {
    const line = onScreenLine("/bots/42");
    expect(line).toContain("realized -$500.00");
    expect(line).toContain("unrealized +$87.70");
    expect(line).toContain("volume $412.0K");
  });

  it("turns the total into a rate off real elapsed hours (R2)", () => {
    // -412.2971 over 1.5h extrapolates to about -$6.6K/day. Never "per day" by
    // dividing a 90-minute run by one day. Matched loosely on the cents: the
    // elapsed span grows while the test runs.
    expect(onScreenLine("/bots/42")).toMatch(/pnl rate -\$6,59\d\.\d\d\/day/);
  });

  it("names the controllers that are actually stopped (R1)", () => {
    // `status` in this payload is a hardcoded "running"; the kill switch is
    // what the fleet table greys a row out on.
    expect(onScreenLine("/bots/42")).toContain("stopped controllers mm_sol_b");
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
  it("takes the chain, source tab and search from the listing's own key", () => {
    qc.setQueryData(
      ["dex-pools", SRV, "gecko", "trending", "solana", "bonk", "orca,meteora", 1],
      {
        pools: [
          {
            address: "a",
            network: "solana",
            dex_id: "orca",
            base_symbol: "SOL",
            quote_symbol: "USDC",
            reserve_usd: 4_210_000,
            volume_24h: 1_250_000,
          },
          {
            address: "b",
            network: "solana",
            dex_id: "meteora",
            base_symbol: "BONK",
            quote_symbol: "SOL",
            reserve_usd: 90_000,
            volume_24h: 12_000,
          },
        ],
        has_more: false,
      },
    );
    const line = onScreenLine("/dex");
    expect(line).toContain("network solana");
    expect(line).toContain("source trending");
    expect(line).toContain("pools listed 2");
    // R3: the box the user typed in and the venue chips they ticked are what
    // make this a slice rather than "the pools".
    expect(line).toContain("search bonk");
    expect(line).toContain("dexes orca,meteora");
    // R4, biggest first.
    expect(line).toContain("top pools SOL-USDC tvl $4.21M vol $1.3M");
  });
});

describe("/dex/:network/:address", () => {
  beforeEach(() => {
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
      {
        id: "1",
        status: "active",
        connector: "meteora/clmm",
        trading_pair: "So111-USDC",
        config: { pool_address: "7qbRF6", connector_name: "meteora/clmm" },
        custom_info: { state: "IN_RANGE", total_value_quote: 4_200, fees_earned_quote: 31.5 },
      },
      {
        id: "2",
        status: "active",
        connector: "meteora/clmm",
        trading_pair: "So111-USDC",
        config: { pool_address: "other", connector_name: "meteora/clmm" },
        custom_info: { state: "IN_RANGE", total_value_quote: 9_999, fees_earned_quote: 99 },
      },
      {
        id: "3",
        status: "active",
        connector: "meteora/clmm",
        trading_pair: "So111-USDC",
        config: { pool_address: "7qbRF6", connector_name: "meteora/clmm" },
        custom_info: { state: "OUT_OF_RANGE", total_value_quote: 800, fees_earned_quote: 4.5 },
      },
      {
        id: "4",
        status: "completed",
        connector: "meteora/clmm",
        trading_pair: "So111-USDC",
        config: { pool_address: "7qbRF6", connector_name: "meteora/clmm" },
        custom_info: { state: "IN_RANGE", total_value_quote: 1_000, fees_earned_quote: 7 },
      },
    ]);
  });

  it("names the pair, dex, price and TVL", () => {
    const line = onScreenLine("/dex/solana/7qbRF6");
    expect(line).toContain("pair SOL-USDC");
    expect(line).toContain("dex meteora");
    expect(line).toContain("price 182.4471");
    expect(line).toContain("tvl $4.21M");
    expect(line).toContain("24h volume $1.3M");
    expect(line).toContain("24h change +3.50%");
  });

  it("says how much of what Condor holds here is still earning", () => {
    const line = onScreenLine("/dex/solana/7qbRF6");
    // Only the open ranges in *this* pool, and out-of-range is the thing worth
    // spotting: a range you are outside of is earning nothing.
    expect(line).toContain("your lp positions 2 (1 in range, 1 out)");
    expect(line).toContain("your lp value $5,000.00");
    expect(line).toContain("fees earned $36.00");
  });

  it("names the pool in the subject rather than its address (R5)", () => {
    expect(routeFacts("/dex/solana/7qbRF6", "", qc)?.subject).toBe(
      "the SOL-USDC pool on meteora (solana)",
    );
  });
});

describe("/executors", () => {
  function seed(next_cursor: string | null) {
    qc.setQueryData(["executors-infinite", SRV], {
      pageParams: [""],
      pages: [
        {
          executors: [
            { id: "1", type: "grid_executor", status: "active", pnl: 12.5, trading_pair: "SOL-USDT" },
            { id: "2", type: "grid_executor", status: "active", pnl: 7.5, trading_pair: "SOL-USDT" },
            { id: "3", type: "position_executor", status: "completed", pnl: -3, trading_pair: "SOL-USDT" },
            { id: "4", type: "position_executor", status: "completed", pnl: -41, trading_pair: "ETH-USDT" },
          ],
          next_cursor,
        },
      ],
    });
  }

  it("counts what is active across the loaded pages", () => {
    seed(null);
    const line = onScreenLine("/executors");
    expect(line).toContain("active 2");
    expect(line).toContain("active pnl +$20.00");
    expect(line).toContain("by type grid_executor 2, position_executor 2");
  });

  it("says whether what is loaded is all of it (R3)", () => {
    seed(null);
    expect(onScreenLine("/executors")).toContain("loaded 4 of 4");
    // The page endpoint answers with a cursor, not a count: a walk that stopped
    // must say so rather than invite an aggregate over the whole history.
    seed("sds:2000");
    expect(onScreenLine("/executors")).toContain("loaded 4 of more — not all loaded");
  });

  it("names the notable rows by market, not by tied row (R4/R5)", () => {
    seed(null);
    const line = onScreenLine("/executors");
    // The two SOL-USDT grids are one line, summed: an executor has no name, so
    // ranking rows individually would print the same label twice and say
    // nothing the user could act on.
    expect(line).toContain("best SOL-USDT grid_executor +$20.00");
    expect(line).toContain("worst ETH-USDT position_executor -$41.00");
  });

  it("leaves flat rows out of both ends, and keeps each row in its own quote", () => {
    // A real screen: 113 executors, most of which never made or lost anything,
    // across three different quote assets.
    qc.setQueryData(["executors-infinite", SRV], {
      pageParams: [""],
      pages: [
        {
          executors: [
            { id: "1", type: "grid", status: "completed", pnl: 337.71, trading_pair: "BTC-BRL" },
            { id: "2", type: "grid", status: "completed", pnl: 3.16, trading_pair: "ETH-USDT" },
            { id: "3", type: "order", status: "completed", pnl: 0, trading_pair: "ETH-USDC" },
            { id: "4", type: "order", status: "completed", pnl: 0, trading_pair: "USDC-USDT" },
            { id: "5", type: "grid", status: "completed", pnl: -12.4, trading_pair: "SOL-USDT" },
          ],
          next_cursor: null,
        },
      ],
    });
    // BRL has no path to the display currency, so it keeps the quote's own
    // symbol and the `⚠` — the same string the screen shows.
    qc.setQueryData(["rates", SRV, "USDT", "BRL"], { BRL: null });
    const line = onScreenLine("/executors");
    expect(line).toContain("best BTC-BRL grid +R$337.71");
    expect(line).toContain("worst SOL-USDT grid -$12.40");
    // Nothing that stayed at zero is named at either end.
    expect(line).not.toContain("+$0.00,");
    expect(line).not.toContain("ETH-USDC");
    expect(line).not.toContain("USDC-USDT");
  });

  it("merges the page's own sort and filters into the same screen", () => {
    seed(null);
    // What `Executors.tsx` contributes through `useViewFacts`: the selection,
    // which no cache holds. Route entry and page contributor share a label, so
    // the block is one screen with both halves.
    const block = renderViewBlock(
      [
        routeFacts("/executors", "", qc)!,
        {
          label: "Executors",
          onScreen: {
            sort: "pnl desc",
            filters: 'pair ~ "SOL", type grid_executor',
            showing: 2,
            "kpi period": "1M",
          },
        },
      ],
      "/executors",
    );
    expect(block.match(/^Screen: /gm)).toHaveLength(1);
    expect(block).toContain("loaded 4 of 4");
    expect(block).toContain("sort pnl desc");
    expect(block).toContain('filters pair ~ "SOL", type grid_executor');
    expect(block).toContain("showing 2");
    expect(block).toContain("kpi period 1M");
  });
});

describe("/routines", () => {
  it("counts the routines and names what is live and what broke", () => {
    qc.setQueryData(["routines"], [{ name: "a" }, { name: "b" }, { name: "c" }]);
    qc.setQueryData(["routine-instances"], [
      {
        instance_id: "1",
        routine_name: "funding_watch",
        status: "running",
        last_run_at: Date.now() / 1000 - 5 * 60,
      },
      {
        instance_id: "2",
        routine_name: "daily_report",
        status: "scheduled",
        schedule: { type: "interval", interval_sec: 3600 },
      },
      {
        instance_id: "3",
        routine_name: "pool_scan",
        status: "idle",
        error: "ConnectionError: gateway refused\n  at line 4",
      },
    ]);
    const line = onScreenLine("/routines");
    expect(line).toContain("routines 3");
    expect(line).toContain("instances 2 running / 3");
    expect(line).toContain("running funding_watch (last run 5m ago)");
    expect(line).toContain("scheduled daily_report");
    // The run that broke is the one worth naming — first line only.
    expect(line).toContain("last failure pool_scan: ConnectionError: gateway refused");
    expect(line).not.toContain("at line 4");
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
  beforeEach(() => {
    qc.setQueryData(["agent", "orca-lp-expert"], {
      slug: "orca-lp-expert",
      name: "Orca LP Expert",
      agent_key: "claude-fable-5",
      server_name: SRV,
      strategies: [
        {
          slug: "sol-lp",
          name: "SOL range keeper",
          status: "active",
          daily_pnl: 120.5,
          total_pnl: 1_430,
          open_positions: 2,
          instances: [{ agent_id: "x", tick_count: 314, agent_key: "claude-fable-5" }],
        },
        {
          slug: "eth-lp",
          name: "ETH range keeper",
          status: "idle",
          daily_pnl: -20.5,
          total_pnl: -130,
          open_positions: 0,
          instances: [],
        },
      ],
    });
  });

  it("names the agent, its model and how many of its strategies are running", () => {
    const line = onScreenLine("/agents/orca-lp-expert");
    expect(line).toContain("agent Orca LP Expert");
    expect(line).toContain("model claude-fable-5");
    expect(line).toContain("strategies 1 running / 2");
    expect(line).toContain(`server ${SRV}`);
  });

  it("names the running strategies and totals what they made (R1/R4)", () => {
    const line = onScreenLine("/agents/orca-lp-expert");
    expect(line).toContain("running SOL range keeper (tick 314)");
    expect(line).toContain("daily pnl +$100.00");
    expect(line).toContain("total pnl +$1,300.00");
    expect(line).toContain("open positions 2");
  });

  it("counts the libraries once the knowledge panel has loaded them", () => {
    expect(onScreenLine("/agents/orca-lp-expert")).not.toContain("skills");
    qc.setQueryData(["agent-brain", "orca-lp-expert"], {
      slug: "orca-lp-expert",
      skills: [{ slug: "a" }, { slug: "b" }],
      memories: [{ slug: "m" }],
      routines: [],
      strategies: [],
      tools: [],
    });
    const line = onScreenLine("/agents/orca-lp-expert");
    expect(line).toContain("skills 2");
    expect(line).toContain("memories 1");
  });
});

describe("/agents/:slug/strategies/:sslug", () => {
  beforeEach(() => {
    qc.setQueryData(["strategy", "orca-lp-expert", "sol-lp"], {
      slug: "sol-lp",
      agent_slug: "orca-lp-expert",
      name: "SOL range keeper",
      status: "active",
      sessions: [{ number: 1 }, { number: 2 }],
      experiments: [{ id: "e1" }],
      instances: [
        {
          agent_id: "x",
          session_num: 2,
          tick_count: 314,
          agent_key: "claude-fable-5",
          execution_mode: "loop",
          daily_pnl: 120.5,
          total_pnl: 1_430,
          open_count: 2,
        },
      ],
    });
    qc.setQueryData(["agent", "orca-lp-expert"], {
      slug: "orca-lp-expert",
      name: "Orca LP Expert",
      strategies: [
        {
          slug: "sol-lp",
          name: "SOL range keeper",
          status: "active",
          daily_pnl: 120.5,
          total_pnl: 1_430,
          open_positions: 2,
          instances: [],
        },
      ],
    });
  });

  it("is no longer label-only", () => {
    const line = onScreenLine("/agents/orca-lp-expert/strategies/sol-lp");
    expect(line).toContain("status active");
    expect(line).toContain("running session 2, tick 314 (loop)");
    expect(line).toContain("model claude-fable-5");
    expect(line).toContain("daily pnl +$120.50");
    expect(line).toContain("total pnl +$1,430.00");
    expect(line).toContain("open positions 2");
    expect(line).toContain("sessions 2");
    expect(line).toContain("experiments 1");
  });

  it("says so when nothing is running, rather than leaving it open", () => {
    qc.setQueryData(["strategy", "orca-lp-expert", "sol-lp"], {
      slug: "sol-lp",
      name: "SOL range keeper",
      status: "idle",
      sessions: [],
      experiments: [],
      instances: [],
    });
    expect(onScreenLine("/agents/orca-lp-expert/strategies/sol-lp")).toContain(
      "running no live instance",
    );
  });

  it("names the strategy in the subject once it is cached", () => {
    expect(routeFacts("/agents/orca-lp-expert/strategies/sol-lp", "", qc)?.subject).toBe(
      'strategy "SOL range keeper" of agent "orca-lp-expert"',
    );
  });
});

describe("freshness (R6)", () => {
  const fleet = {
    bots: [{ bot_name: "a", status: "running", num_controllers: 1 }],
    controllers: [{ bot_name: "a", global_pnl_quote: 1 }],
    total_pnl: 1,
    total_volume: 1,
  };

  it("says nothing while the poll behind the page is current", () => {
    qc.setQueryData(["bots", SRV], fleet);
    expect(onScreenLine("/bots")).not.toContain("as of");
  });

  it("stamps a poll that has gone quiet", () => {
    qc.setQueryData(["bots", SRV], fleet, { updatedAt: Date.now() - 4 * 60_000 });
    expect(onScreenLine("/bots")).toContain("as of 4m ago");
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

  /**
   * The parity the block's docstring promises, checked against the hook the
   * pages actually render with rather than against a second opinion.
   *
   * The interesting case is a quote with no rate path: the number stays in
   * quote units, so it must keep the *quote's* symbol on both surfaces. The
   * block used to relabel it with the display currency's — telling the agent a
   * BRL loss was in euros while the screen said `R$` (ARCH-228).
   */
  it("hands the agent the exact string the page renders for an unconvertible quote", async () => {
    localStorage.setItem("condor_display_currency", "EUR");
    vi.resetModules();
    const [
      { routeFacts: facts },
      { renderViewBlock: render },
      { useRates },
      { ServerContext },
      { QueryClient: Client, QueryClientProvider },
      React,
      { createRoot },
    ] = await Promise.all([
      import("./pageFacts"),
      import("./viewFacts"),
      import("@/hooks/useRates"),
      import("@/hooks/useServer"),
      import("@tanstack/react-query"),
      import("react"),
      import("react-dom/client"),
    ]);

    const PNL = -412.2971;
    const client = new Client();
    client.setQueryData(["bot", SRV, "42"], {
      bot: { id: "42", name: "brl-mm", status: "running", pnl: PNL, trading_pair: "BTC-BRL" },
      config: {},
      performance: {},
    });
    // EUR priced against BRL, and the server has no path: a real answer.
    client.setQueryData(["rates", SRV, "EUR", "BRL"], { BRL: null });

    const holder: { onScreen?: string } = {};
    function Harness() {
      const { formatPnlValue } = useRates(["BRL"]);
      React.useEffect(() => {
        holder.onScreen = formatPnlValue(PNL, "BRL");
      });
      return null;
    }

    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await React.act(async () => {
      root.render(
        React.createElement(
          QueryClientProvider,
          { client },
          React.createElement(
            ServerContext.Provider,
            { value: { server: SRV, setServer: () => {} } },
            React.createElement(Harness),
          ),
        ),
      );
    });
    await React.act(async () => root.unmount());
    container.remove();

    // The screen keeps the quote's symbol and marks the value unconverted...
    expect(holder.onScreen).toContain("R$");
    expect(holder.onScreen).toContain("\u26A0");
    expect(holder.onScreen).not.toContain("\u20AC");
    // ...and the block quotes that string character for character.
    const block = render([facts("/bots/42", "", client)!], "/bots/42");
    expect(block).toContain(`pnl ${holder.onScreen}`);

    localStorage.removeItem("condor_display_currency");
    vi.resetModules();
  });
});

describe("an empty cache", () => {
  it("still renders label and subject, and no reader throws", () => {
    for (const [path, search] of [
      ["/portfolio", ""],
      ["/bots", ""],
      ["/bots", "?tab=runs"],
      ["/bots", "?tab=editor"],
      ["/bots/42", ""],
      ["/trade", ""],
      ["/dex", ""],
      ["/dex/solana/7qbRF6", ""],
      ["/executors", ""],
      ["/routines", ""],
      ["/routines", "?tab=reports"],
      ["/agents/orca-lp-expert", ""],
      ["/agents/orca-lp-expert/strategies/sol-lp", ""],
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

  it("gathers nothing at all on /settings (R9)", () => {
    // Credentials are typed on this page. `secrets.redact` guards the wire, but
    // a block that never collects one cannot leak one.
    qc.setQueryData(["servers"], [{ name: SRV, host: "h", port: 1 }]);
    expect(routeFacts("/settings", "", qc)).toEqual({ label: "Settings" });
  });
});

describe("the block's budget", () => {
  /** Every route, each with as full a cache as it can have. */
  function seedEverything() {
    const stale = { updatedAt: Date.now() - 4 * 60_000 };
    qc.setQueryData(
      ["portfolio", SRV],
      {
        server: SRV,
        total_usd: 125_430.5,
        connectors: Array.from({ length: 12 }, (_, c) => ({
          connector: `connector-with-a-long-name-${c}`,
          total_usd: 10_000 + c,
          balances: Array.from({ length: 40 }, (_, b) => ({
            token: `TOKEN${c}${b}`,
            total: 1,
            available: 1,
            usd_value: 1_000 - b,
          })),
        })),
      },
      stale,
    );
    qc.setQueryData(
      ["portfolio-history", SRV, "1W"],
      {
        points: [
          { timestamp: 1, total_usd: 1, tokens: Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`TOKEN0${i}`, 100])) },
          { timestamp: 2, total_usd: 2, tokens: Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`TOKEN0${i}`, 50])) },
        ],
      },
      stale,
    );
    qc.setQueryData(
      ["consolidated-positions", SRV],
      {
        executor_positions: Array.from({ length: 30 }, (_, i) => ({
          trading_pair: `PAIR${i}-USDC`,
          position_side: "LONG",
          notional_value: 1_000 + i,
          unrealized_pnl: -i,
        })),
        bot_positions: [],
      },
      stale,
    );
    qc.setQueryData(
      ["bots", SRV],
      {
        bots: Array.from({ length: 20 }, (_, i) => ({
          bot_name: `a-rather-long-bot-name-${i}`,
          status: i === 0 ? "running" : "stopped",
          num_controllers: 2,
          deployed_at: new Date(Date.now() - 90 * 60_000).toISOString(),
        })),
        controllers: Array.from({ length: 40 }, (_, i) => ({
          bot_name: `a-rather-long-bot-name-${i % 20}`,
          controller_id: `a-rather-long-controller-id-${i}`,
          global_pnl_quote: i * 10 - 200,
          realized_pnl_quote: i,
          unrealized_pnl_quote: -i,
          volume_traded: 10_000 * i,
          config: { manual_kill_switch: i % 2 === 0 },
        })),
        total_pnl: 735,
        total_volume: 2_549_843,
      },
      stale,
    );
    qc.setQueryData(
      ["bot-runs", SRV],
      {
        total: 3_142,
        runs: Array.from({ length: 200 }, (_, i) => ({
          bot_name: `a-rather-long-bot-name-${i}`,
          run_status: `A_LONG_STATUS_${i % 5}`,
          global_pnl_quote: i * 10 - 1_000,
        })),
      },
      stale,
    );
    qc.setQueryData(
      ["archived-bots", SRV],
      Array.from({ length: 40 }, (_, i) => ({
        bot_name: `an-archived-bot-with-a-long-name-${i}`,
        db_path: `/data/${i}.sqlite`,
        total_trades: 100 + i,
      })),
      stale,
    );
    qc.setQueryData(
      ["bot", SRV, "42"],
      {
        bot: {
          id: "42",
          name: "a-rather-long-bot-name-0",
          status: "running",
          trading_pair: "SOL-USDC",
          pnl: -412.2971,
        },
        config: {},
        performance: {},
      },
      stale,
    );
    qc.setQueryData(
      ["dex-pools", SRV, "gecko", "trending", "solana", "a-long-search-term", "orca,meteora,raydium", 3],
      {
        pools: Array.from({ length: 50 }, (_, i) => ({
          address: `pool-${i}`,
          network: "solana",
          dex_id: "meteora",
          base_symbol: `LONGBASE${i}`,
          quote_symbol: "USDC",
          reserve_usd: 1_000_000 - i,
          volume_24h: 100_000 - i,
        })),
      },
      stale,
    );
    qc.setQueryData(
      ["dex-pool-by-address", SRV, "solana", "7qbRF6"],
      {
        address: "7qbRF6",
        dex_id: "meteora",
        network: "solana",
        base_symbol: "SOL",
        quote_symbol: "USDC",
        current_price: 182.4471,
        reserve_usd: 4_210_000,
        volume_24h: 1_250_000,
        price_change_24h: 3.5,
      },
      stale,
    );
    qc.setQueryData(
      ["dex-lp-executors", SRV],
      Array.from({ length: 25 }, (_, i) => ({
        id: `${i}`,
        status: "active",
        connector: "meteora/clmm",
        trading_pair: "So111-USDC",
        config: { pool_address: "7qbRF6", connector_name: "meteora/clmm" },
        custom_info: {
          state: i % 2 ? "IN_RANGE" : "OUT_OF_RANGE",
          total_value_quote: 100 * i,
          fees_earned_quote: i,
        },
      })),
      stale,
    );
    qc.setQueryData(
      ["executors-infinite", SRV],
      {
        pageParams: [""],
        pages: [
          {
            executors: Array.from({ length: 500 }, (_, i) => ({
              id: `${i}`,
              type: `a_long_executor_type_${i % 6}`,
              status: i % 3 ? "completed" : "active",
              pnl: i - 250,
              trading_pair: `LONGPAIR${i % 9}-USDT`,
            })),
            next_cursor: "sds:500",
          },
        ],
      },
      stale,
    );
    qc.setQueryData(
      ["routines"],
      Array.from({ length: 30 }, (_, i) => ({ name: `routine_${i}` })),
      stale,
    );
    qc.setQueryData(
      ["routine-instances"],
      Array.from({ length: 20 }, (_, i) => ({
        instance_id: `${i}`,
        routine_name: `a_rather_long_routine_name_${i}`,
        status: i % 2 ? "running" : "scheduled",
        last_run_at: Date.now() / 1000 - 300,
        error: i === 3 ? "x".repeat(500) : null,
      })),
      stale,
    );
    qc.setQueryData(
      ["reports-grouped"],
      Array.from({ length: 20 }, (_, i) => ({ source_name: `s${i}`, total_count: i })),
      stale,
    );
    const strategies = Array.from({ length: 12 }, (_, i) => ({
      slug: `strategy-${i}`,
      name: `A Rather Long Strategy Name ${i}`,
      status: "active",
      daily_pnl: 10 * i,
      total_pnl: 100 * i,
      open_positions: i,
      instances: [
        { agent_id: `x${i}`, session_num: i, tick_count: 300 + i, agent_key: "claude-fable-5", execution_mode: "loop", daily_pnl: 10 * i, total_pnl: 100 * i, open_count: i },
      ],
    }));
    qc.setQueryData(
      ["agent", "orca-lp-expert"],
      {
        slug: "orca-lp-expert",
        name: "Orca LP Expert",
        agent_key: "claude-fable-5",
        server_name: SRV,
        strategies,
      },
      stale,
    );
    qc.setQueryData(
      ["agent-brain", "orca-lp-expert"],
      { slug: "orca-lp-expert", skills: Array.from({ length: 18 }, () => ({})), memories: Array.from({ length: 40 }, () => ({})) },
      stale,
    );
    qc.setQueryData(
      ["strategy", "orca-lp-expert", "strategy-0"],
      {
        slug: "strategy-0",
        name: "A Rather Long Strategy Name 0",
        status: "active",
        sessions: Array.from({ length: 40 }, (_, i) => ({ number: i })),
        experiments: Array.from({ length: 12 }, () => ({})),
        instances: strategies[0].instances,
      },
      stale,
    );
  }

  it("stays inside the cap on every route, with a full cache", () => {
    seedEverything();
    for (const [path, search] of [
      ["/portfolio", ""],
      ["/portfolio", "?tab=positions"],
      ["/bots", ""],
      ["/bots", "?tab=runs"],
      ["/bots", "?tab=archived"],
      ["/bots/42", ""],
      ["/dex", ""],
      ["/dex/solana/7qbRF6", ""],
      ["/executors", ""],
      ["/routines", ""],
      ["/routines", "?tab=reports"],
      ["/agents/orca-lp-expert", ""],
      ["/agents/orca-lp-expert/strategies/strategy-0", ""],
    ] as const) {
      const facts = routeFacts(path, search, qc)!;
      expect(facts, path + search).not.toBeNull();
      const block = renderViewBlock([facts], path + search);
      expect(block.length, `${path}${search} → ${block.length} chars`).toBeLessThan(
        VIEW_BLOCK_MAX_CHARS,
      );
      expect(block, path + search).not.toContain("\u2026");
    }
  });

  it("keeps /executors inside the cap once the page adds its selection", () => {
    seedEverything();
    const block = renderViewBlock(
      [
        routeFacts("/executors", "", qc)!,
        {
          label: "Executors",
          onScreen: {
            sort: "pnl desc",
            filters: 'pair ~ "LONGPAIR", type a_long_executor_type_0/a_long_executor_type_1, controller 4 selected',
            showing: 120,
            "kpi period": "3M",
          },
        },
      ],
      "/executors",
    );
    expect(block.length).toBeLessThan(VIEW_BLOCK_MAX_CHARS);
  });
});
