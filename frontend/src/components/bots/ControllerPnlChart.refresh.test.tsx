/**
 * What a chart actually puts on the wire when it refreshes (PERF-239).
 *
 * The policy is unit-tested in `lib/history-refresh.test.ts`; this file pins the
 * thing the item is about, which is only observable at the request level: the
 * first load walks the history from the deploy, and every refresh after it asks
 * for the gap and nothing else. Asserted against real query strings through a
 * real `QueryClient`, because the whole mechanism hangs off the query *key* —
 * the previous series is read back under it, and a key that changed shape (or a
 * refresh that read the wrong entry) would still render a plausible chart while
 * quietly downloading the history again.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControllerPerformanceSnapshot } from "@/lib/api";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

// The pixels are PnlEvolutionChart's business (ARCH-242) and recharts needs a
// layout jsdom does not have; only the requests matter here.
vi.mock("./PnlEvolutionChart", async () => {
  const { createElement: h } = await import("react");
  return { PnlEvolutionChart: () => h("div", { "data-chart": "" }) };
});

const { ControllerPnlChart } = await import("./ControllerPnlChart");

const SERVER = "prod";
const BOT = "bot-a";
const CTRL = "ctrl-1";
const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;

/**
 * Fixture instants are anchored to the real clock, not to a written-out date.
 *
 * `samplingIntervalSince` reads `Date.now()` to decide the resolution, so a
 * hard-coded deploy time would pick a finer interval this month and a coarser
 * one next month, and these assertions would start failing on a calendar rather
 * than on a change.
 */
const NOW = Date.now();

function snap(timestamp: string): ControllerPerformanceSnapshot {
  return {
    timestamp,
    bot_name: BOT,
    controller_id: CTRL,
    controller_name: "pmm",
    connector: "binance",
    trading_pair: "SOL-USDC",
    realized_pnl_quote: 0,
    unrealized_pnl_quote: 0,
    global_pnl_quote: 0,
    global_pnl_pct: 0,
    volume_traded: 0,
    positions_summary: [],
  };
}

/** `n` snapshots one minute apart ending at `end`. */
function series(end: number, n: number): ControllerPerformanceSnapshot[] {
  return Array.from({ length: n }, (_, i) => snap(new Date(end - (n - 1 - i) * MINUTE).toISOString()));
}

/** Serve a scripted list of history pages and record every URL requested. */
function serve(bodies: { snapshots: ControllerPerformanceSnapshot[]; next_cursor?: string | null }[]) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      const body = bodies[urls.length - 1];
      if (!body) throw new Error(`no scripted response ${urls.length} (${url})`);
      return {
        ok: true,
        json: async () => ({ next_cursor: null, interval: "5m", ...body }),
      } as unknown as Response;
    }),
  );
  return urls;
}

function params(url: string): URLSearchParams {
  return new URLSearchParams(url.slice(url.indexOf("?") + 1));
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  client.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function mount(deployedAt: string) {
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(ControllerPnlChart, {
          server: SERVER,
          controllerId: CTRL,
          botName: BOT,
          deployedAt,
        }),
      ),
    );
  });
}

describe("ControllerPnlChart refresh traffic", () => {
  it("loads the whole history once, then asks only for the tail", async () => {
    const now = NOW;
    const deployedAt = new Date(now - 60 * MINUTE).toISOString();
    const urls = serve([
      { snapshots: series(now, 4) },
      { snapshots: [snap(new Date(now + MINUTE).toISOString())] },
    ]);

    await mount(deployedAt);

    // First load: the full window, from the deploy.
    expect(urls).toHaveLength(1);
    expect(params(urls[0]).get("start_time")).toBe(deployedAt);

    await act(async () => {
      await client.refetchQueries();
    });

    // The refresh is one request — not a re-walk — and it starts at the newest
    // point already cached, backed off by one sampling bucket so the seam
    // bucket cannot be lost.
    expect(urls).toHaveLength(2);
    const refreshed = params(urls[1]);
    expect(refreshed.get("start_time")).toBe(new Date(now - 5 * MINUTE).toISOString());
    expect(refreshed.get("interval")).toBe("5m");
    expect(refreshed.get("cursor")).toBeNull();

    // Nothing older than the newest cached snapshot was re-requested.
    expect(Date.parse(refreshed.get("start_time")!)).toBeGreaterThan(Date.parse(deployedAt));

    // And the cache grew by exactly the new point, with no duplicates.
    const merged = client.getQueryData<{ snapshots: ControllerPerformanceSnapshot[] }>([
      "controller-perf-history",
      SERVER,
      BOT,
      CTRL,
      deployedAt,
      "5m",
    ]);
    expect(merged?.snapshots).toHaveLength(5);
    expect(new Set(merged?.snapshots.map((s) => s.timestamp)).size).toBe(5);
  });

  it("backfills the whole window after a socket gap, in one request", async () => {
    // A connection that dropped an hour ago left the cache ending an hour ago;
    // the refresh that repairs it asks for that hour and gets several buckets
    // back, overlapping the seam.
    const now = NOW;
    const deployedAt = new Date(now - 600 * MINUTE).toISOString();
    const gapEnd = now - 60 * MINUTE;
    const urls = serve([
      { snapshots: series(gapEnd, 3) },
      // The seam bucket comes back again, plus the hour that was missed.
      { snapshots: [snap(new Date(gapEnd).toISOString()), ...series(now, 12)] },
    ]);

    await mount(deployedAt);
    await act(async () => {
      await client.refetchQueries();
    });

    expect(urls).toHaveLength(2);
    expect(params(urls[1]).get("start_time")).toBe(new Date(gapEnd - 5 * MINUTE).toISOString());

    const merged = client.getQueryData<{ snapshots: ControllerPerformanceSnapshot[] }>([
      "controller-perf-history",
      SERVER,
      BOT,
      CTRL,
      deployedAt,
      "5m",
    ]);
    // 3 cached + 12 new; the repeated seam snapshot folds away.
    expect(merged?.snapshots).toHaveLength(15);
    expect(new Set(merged?.snapshots.map((s) => s.timestamp)).size).toBe(15);
  });

  it("re-walks a series a failed walk left truncated, rather than appending to it", async () => {
    const now = NOW;
    const deployedAt = new Date(now - 60 * MINUTE).toISOString();
    const urls = serve([{ snapshots: series(now, 4) }, { snapshots: series(now, 6) }]);

    await mount(deployedAt);

    const key = ["controller-perf-history", SERVER, BOT, CTRL, deployedAt, "5m"];
    // What `collectCursorPages` leaves behind when a later page fails: real
    // rows, of an extent nothing can vouch for.
    client.setQueryData(key, (old: Record<string, unknown> | undefined) => ({
      ...old,
      truncated: true,
      outcome: "error",
    }));

    await act(async () => {
      await client.refetchQueries();
    });

    expect(urls).toHaveLength(2);
    // Back to the deploy — the whole window, not the tail.
    expect(params(urls[1]).get("start_time")).toBe(deployedAt);
    expect(client.getQueryData<{ truncated: boolean }>(key)?.truncated).toBe(false);
  });

  it("never extends a series sampled at another resolution", async () => {
    // The interval is the last element of the key (PERF-238), so it is part of
    // the identity a refresh resumes from. A month-old controller charts at
    // "1h"; a five-minutely series cached for the same controller is a
    // different entry and must not be tailed into this one.
    const now = NOW;
    // 30 days back lands on "1h" (PERF-238's ladder: ~20d → 30m, ~41d → 1h).
    const deployedAt = new Date(now - 30 * DAY).toISOString();
    const urls = serve([{ snapshots: series(now, 4) }]);

    client.setQueryData(["controller-perf-history", SERVER, BOT, CTRL, deployedAt, "5m"], {
      snapshots: series(now, 3),
      next_cursor: null,
      interval: "5m",
      pages: 1,
      truncated: false,
      outcome: "complete",
    });

    await mount(deployedAt);

    expect(urls).toHaveLength(1);
    const first = params(urls[0]);
    expect(first.get("interval")).toBe("1h");
    expect(first.get("start_time")).toBe(deployedAt);
  });
});
