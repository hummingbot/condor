import type { QueryClient } from "@tanstack/react-query";

import { poolLabel } from "@/components/dex/format";
import { getDisplayCurrency } from "@/hooks/useDisplayCurrency";
import type {
  AgentDetail,
  BotDetail,
  BotsPageResponse,
  ConsolidatedPositionsResponse,
  ExecutorInfo,
  PoolSummary,
  PortfolioHistoryResponse,
  PortfolioResponse,
  ReportGroup,
  RoutineInfo,
  RoutineInstance,
} from "./api";
import {
  formatAge,
  formatCurrency,
  formatCurrencyPnl,
  formatCurrencyVolume,
  formatPct,
  formatPriceSig,
  isExecutorActive,
  toMs,
} from "./formatters";
import { formatWithRate, type RateTable } from "./rates";
import type { ViewFacts } from "./viewFacts";

/**
 * URL → what screen this is, what it is about, and what it currently says.
 *
 * The route baseline for the chat's page context: a flat table over the
 * routes in `App.tsx`, contributing a label and a URL-derived subject through
 * the same `useViewFacts` seam every richer contributor uses.
 *
 * With a `QueryClient` in hand it also fills in `onScreen` (FEAT-060) by
 * reading the very cache each page renders from — one lookup, in the file that
 * already owns route→label, instead of eight page components each handing
 * their state over and each a chance to go stale. Query keys are already
 * load-bearing shared contract here (`AgentChatTab` reuses `["agents"]` so
 * react-query dedupes against the fleet tab), so this adds another consumer of
 * a coupling that exists, not a new kind of it. `pageFacts.onScreen.test.ts`
 * seeds the real keys, so a rename breaks a test rather than an answer.
 *
 * Called at *send* time, never at render time: a cache read costs nothing
 * while the user is only looking at the page, and is current at the moment
 * they ask — including data that arrived after the bubble was opened.
 */
export function routeFacts(
  pathname: string,
  search: string,
  qc?: QueryClient,
): ViewFacts | null {
  // The workspace already *is* the chat; telling the agent "the user is
  // looking at a chat with you" is noise.
  if (pathname === "/") return null;

  const params = new URLSearchParams(search);
  const tab = params.get("tab") || "";

  for (const { pattern, facts, onScreen } of ROUTES) {
    const m = pathname.match(pattern);
    if (!m) continue;
    const parts = m.slice(1).map(decode);
    const base = facts(parts, tab);
    if (!qc || !onScreen) return base;
    // A half-loaded cache must leave the block partial, never empty and never
    // thrown: the user asking mid-load still deserves the label and subject.
    try {
      const shown = prune(onScreen(parts, tab, qc));
      return shown ? { ...base, onScreen: shown } : base;
    } catch {
      return base;
    }
  }
  return null;
}

function decode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/** Drop the fields that did not resolve; `undefined` when none did. */
function prune(
  shown: ViewFacts["onScreen"],
): ViewFacts["onScreen"] | undefined {
  if (!shown) return undefined;
  const kept = Object.entries(shown).filter(
    ([, v]) => v !== null && v !== undefined && v !== "",
  );
  return kept.length > 0 ? Object.fromEntries(kept) : undefined;
}

// ── Reading the cache the page renders from ──

/**
 * The freshest cached value under a key prefix.
 *
 * Every page-level key is server-scoped (`["portfolio", server]`) and the route
 * says nothing about which server is selected, so the prefix is matched rather
 * than the whole key and the most recently updated entry wins — which is the
 * one the screen in front of the user is rendering. `where` narrows to the
 * entry a route parameter names (a bot id, a pool address).
 */
function fresh<T>(
  qc: QueryClient,
  prefix: unknown[],
  where?: (key: readonly unknown[]) => boolean,
): T | undefined {
  let best: T | undefined;
  let bestAt = -1;
  for (const query of qc.getQueryCache().findAll({ queryKey: prefix })) {
    const { data, dataUpdatedAt } = query.state;
    if (data === undefined) continue;
    if (where && !where(query.queryKey)) continue;
    if (dataUpdatedAt > bestAt) {
      bestAt = dataUpdatedAt;
      best = data as T;
    }
  }
  return best;
}

/** The key of the freshest cached entry under a prefix — a page's own tab and
 *  network live in the key of the listing it asked for, not in its payload. */
function freshKey(
  qc: QueryClient,
  prefix: unknown[],
): readonly unknown[] | undefined {
  let best: readonly unknown[] | undefined;
  let bestAt = -1;
  for (const query of qc.getQueryCache().findAll({ queryKey: prefix })) {
    if (query.state.data === undefined) continue;
    if (query.state.dataUpdatedAt > bestAt) {
      bestAt = query.state.dataUpdatedAt;
      best = query.queryKey;
    }
  }
  return best;
}

/**
 * Money formatted the way the page formats it.
 *
 * Reads the same `["rates", server, currency, …]` cache `useRates` fills and
 * runs it through the same `lib/rates` rule — same conversion, same symbol,
 * same `⚠` marker — so the block says `$-412.30` where the screen says
 * `$-412.30`, in the display currency the user picked rather than in raw quote
 * units. Sharing the rule is the point: the copy that used to live here had
 * drifted on the symbol for an unconvertible quote (ARCH-228).
 */
function money(qc: QueryClient) {
  const currency = getDisplayCurrency();
  const table = fresh<RateTable>(qc, ["rates"], (key) => key[2] === currency);

  // A missing number is left out of the block entirely — better no fact than a
  // confident `$0.00` the screen is not showing.
  const format = (fmt: (val: number, symbol?: string) => string) => {
    const formatted = formatWithRate(fmt, table, currency);
    return (val: number | null | undefined, quote?: string): string | undefined =>
      val == null || !Number.isFinite(val) ? undefined : formatted(val, quote);
  };

  return {
    value: format(formatCurrency),
    pnl: format(formatCurrencyPnl),
    volume: format(formatCurrencyVolume),
    currency,
  };
}

/** `3 running / 5` — the shape a user says out loud about a list. */
function ratio(running: number, total: number): string {
  return `${running} running / ${total}`;
}

// ── The table ──

type Reader = (
  parts: string[],
  tab: string,
  qc: QueryClient,
) => ViewFacts["onScreen"];

const ROUTES: {
  pattern: RegExp;
  facts: (parts: string[], tab: string) => ViewFacts;
  onScreen?: Reader;
}[] = [
  {
    pattern: /^\/portfolio$/,
    facts: () => ({ label: "Portfolio" }),
    onScreen: (_p, tab, qc) => {
      const m = money(qc);
      const data = fresh<PortfolioResponse>(qc, ["portfolio"]);
      const history = fresh<PortfolioHistoryResponse>(qc, ["portfolio-history"]);
      // The window is the one the period selector is on, and it lives in the
      // key — captioning it "24h" would put a label on screen that is not.
      const window = String(freshKey(qc, ["portfolio-history"])?.[2] ?? "");
      const points = history?.points ?? [];
      const change =
        points.length >= 2
          ? points[points.length - 1].total_usd - points[0].total_usd
          : null;
      const held = fresh<ConsolidatedPositionsResponse>(qc, [
        "consolidated-positions",
      ]);
      const holds =
        held && (held.executor_positions ?? []).length + (held.bot_positions ?? []).length;
      // Nothing fetched yet is a blank screen: naming the tab and the currency
      // of numbers that are not there would describe a page the user cannot
      // read. Label and subject alone are the honest answer until data lands.
      if (!data && !history && !held) return undefined;
      return {
        total: m.value(data?.total_usd),
        [window ? `change (${window})` : "change"]: m.pnl(change),
        currency: data ? m.currency : undefined,
        assets: data?.connectors.reduce((n, c) => n + c.balances.length, 0),
        "open positions": holds,
        tab: tab === "positions" ? "positions" : "assets",
      };
    },
  },
  {
    pattern: /^\/bots$/,
    facts: (_p, tab) => ({
      label:
        tab === "backtest"
          ? "Backtests"
          : tab === "archived"
            ? "Archived bots"
            : "Bots",
    }),
    onScreen: (_p, tab, qc) => {
      const data = fresh<BotsPageResponse>(qc, ["bots"]);
      if (!data) return undefined;
      const bots = data.bots ?? [];
      return {
        bots: ratio(bots.filter((b) => b.status === "running").length, bots.length),
        controllers: (data.controllers ?? []).length,
        tab: tab || "active",
      };
    },
  },
  {
    pattern: /^\/bots\/([^/]+)$/,
    facts: ([id]) => ({ label: "Bot detail", subject: `bot id ${id}` }),
    onScreen: ([id], _tab, qc) => {
      const detail = fresh<BotDetail>(qc, ["bot"], (key) => key[2] === id);
      const bot = detail?.bot;
      if (!bot) return undefined;
      const m = money(qc);
      const quote = bot.trading_pair?.split("-")[1] || "USDT";
      // The bot's own controllers, from the fleet listing the /bots page
      // already holds — the detail payload counts none.
      const fleet = fresh<BotsPageResponse>(qc, ["bots"]);
      const summary = fleet?.bots?.find((b) => b.bot_name === bot.name);
      const controllers = (fleet?.controllers ?? []).filter(
        (c) => c.bot_name === bot.name,
      );
      return {
        bot: bot.name,
        status: bot.status,
        pnl: m.pnl(bot.pnl, quote),
        pair: bot.trading_pair,
        controllers: summary?.num_controllers ?? (controllers.length || undefined),
        uptime: summary?.deployed_at
          ? formatAge(toMs(summary.deployed_at) / 1000)
          : undefined,
      };
    },
  },
  // `/trade`'s form is local component state, so `CreateExecutor` registers it
  // through `useViewFacts` — no cache holds what the user has typed.
  { pattern: /^\/trade$/, facts: () => ({ label: "Trade — create executor" }) },
  {
    pattern: /^\/dex$/,
    facts: () => ({ label: "DEX pools" }),
    onScreen: (_p, _tab, qc) => {
      // The browser's chain and source tab are local state, but the listing it
      // asked for carries both in its key: ["dex-pools", server, kind, view,
      // network, query, dexes, page].
      const key = freshKey(qc, ["dex-pools"]);
      const page = fresh<{ pools: PoolSummary[] }>(qc, ["dex-pools"]);
      if (!key || !page) return undefined;
      const source = String(key[3] || key[2] || "");
      return {
        network: String(key[4] || page.pools[0]?.network || ""),
        source,
        "pools listed": page.pools.length,
        page: Number(key[7]) > 1 ? Number(key[7]) : undefined,
      };
    },
  },
  {
    pattern: /^\/dex\/([^/]+)\/([^/]+)$/,
    facts: ([network, address]) => ({
      label: "DEX pool",
      subject: `pool ${address} on ${network}`,
    }),
    onScreen: ([network, address], _tab, qc) => {
      const pool = fresh<PoolSummary | null>(
        qc,
        ["dex-pool-by-address"],
        (key) => key[2] === network && key[3] === address,
      );
      if (!pool) return undefined;
      const m = money(qc);
      // The ranges Condor holds *in this pool* — the LP executors the pool
      // page's own position panel reads, filtered to this address.
      const lp = fresh<ExecutorInfo[]>(qc, ["dex-lp-executors"]) ?? [];
      const mine = lp.filter(
        (ex) =>
          isExecutorActive(ex.status) &&
          String(ex.config?.pool_address ?? "") === pool.address,
      ).length;
      return {
        pair: poolLabel(pool),
        dex: pool.dex_id,
        price: pool.current_price != null ? formatPriceSig(pool.current_price) : undefined,
        tvl: m.value(pool.reserve_usd),
        "24h volume": m.volume(pool.volume_24h),
        "24h change": pool.price_change_24h != null ? formatPct(pool.price_change_24h / 100) : undefined,
        "your lp positions": mine || undefined,
      };
    },
  },
  {
    pattern: /^\/executors$/,
    facts: () => ({ label: "Executors" }),
    onScreen: (_p, _tab, qc) => {
      const paged = fresh<{ pages: { executors: ExecutorInfo[] }[] }>(qc, [
        "executors-infinite",
      ]);
      if (!paged?.pages) return undefined;
      const all = paged.pages.flatMap((p) => p.executors ?? []);
      const active = all.filter((ex) => isExecutorActive(ex.status));
      const m = money(qc);
      const sum = (rows: ExecutorInfo[]) =>
        rows.reduce((total, ex) => total + (ex.pnl ?? 0), 0);
      return {
        active: active.length,
        loaded: all.length,
        // Per-quote conversion is what the KPI strip does; the quotes on one
        // screen are near-always the same, so summing after conversion here
        // reproduces it without a second pass over the rows.
        "active pnl": m.pnl(sum(active), active[0]?.trading_pair?.split("-")[1]),
      };
    },
  },
  {
    pattern: /^\/routines$/,
    facts: (_p, tab) => ({
      label: tab === "reports" ? "Routine reports" : "Routines",
    }),
    onScreen: (_p, tab, qc) => {
      const routines = fresh<RoutineInfo[]>(qc, ["routines"]);
      const instances = fresh<RoutineInstance[]>(qc, ["routine-instances"]) ?? [];
      const groups = fresh<ReportGroup[]>(qc, ["reports-grouped"]);
      if (!routines && !groups) return undefined;
      const live = instances.filter(
        (i) => i.status === "running" || i.status === "scheduled",
      ).length;
      return tab === "reports"
        ? {
            "report sources": groups?.length,
            reports: groups?.reduce((n, g) => n + g.total_count, 0),
          }
        : {
            routines: routines?.length,
            instances: ratio(live, instances.length),
          };
    },
  },
  {
    pattern: /^\/agents\/([^/]+)\/strategies\/([^/]+)$/,
    facts: ([slug, sslug]) => ({
      label: "Strategy detail",
      subject: `strategy "${sslug}" of agent "${slug}"`,
    }),
  },
  {
    pattern: /^\/agents\/([^/]+)$/,
    facts: ([slug]) => ({ label: "Agent page", subject: `agent "${slug}"` }),
    onScreen: ([slug], _tab, qc) => {
      const agent = fresh<AgentDetail>(qc, ["agent"], (key) => key[1] === slug);
      if (!agent) return undefined;
      const strategies = agent.strategies ?? [];
      return {
        agent: agent.name,
        strategies: ratio(
          strategies.filter((s) => s.instances.length > 0).length,
          strategies.length,
        ),
        server: agent.server_name || undefined,
      };
    },
  },
  { pattern: /^\/settings$/, facts: () => ({ label: "Settings" }) },
];
