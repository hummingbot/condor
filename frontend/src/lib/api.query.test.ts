/**
 * @vitest-environment jsdom
 *
 * The URLs the optional-parameter endpoints actually put on the wire (READ-333).
 *
 * Each of these used to spell its parameter names twice — once in its
 * TypeScript type and once in a `qs.set` line — so a field added to the type
 * and forgotten in the setter compiled cleanly and was silently dropped from
 * the request: a filter that looks applied and is not. They now share one
 * `query()` builder, and this file pins what that builder emits for a mixed set
 * of present, empty-string and undefined parameters, plus the bare path with no
 * `?` when nothing is set.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

const SERVER = "prod";

/** Answer every request with `body` and record the URLs requested. */
function serve(body: unknown = {}) {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      return { ok: true, json: async () => body } as unknown as Response;
    }),
  );
  return urls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("getBotRuns", () => {
  it("keeps set parameters and drops undefined and empty ones", async () => {
    const urls = serve({ bot_runs: [], total: 0 });
    await api.getBotRuns(SERVER, {
      bot_name: "bot-a",
      run_status: "",
      deployment_status: undefined,
      limit: 50,
    });
    expect(urls[0]).toBe(
      "/api/v1/servers/prod/bot-runs?bot_name=bot-a&limit=50",
    );
  });

  it("omits the ? entirely when nothing is set", async () => {
    const urls = serve({ bot_runs: [], total: 0 });
    await api.getBotRuns(SERVER);
    expect(urls[0]).toBe("/api/v1/servers/prod/bot-runs");
  });
});

describe("getExecutors", () => {
  it("keeps set parameters and drops undefined and empty ones", async () => {
    const urls = serve([]);
    await api.getExecutors(SERVER, {
      executor_type: "position_executor",
      trading_pair: "",
      status: undefined,
      controller_id: "ctrl-1",
      limit: 200,
    });
    expect(urls[0]).toBe(
      "/api/v1/servers/prod/executors?executor_type=position_executor&controller_id=ctrl-1&limit=200",
    );
  });

  it("omits the ? entirely when called with no parameters", async () => {
    const urls = serve([]);
    await api.getExecutors(SERVER);
    expect(urls[0]).toBe("/api/v1/servers/prod/executors");
  });
});

describe("getExecutorsPage", () => {
  it("always carries a limit, defaulting to 50", async () => {
    const urls = serve({ executors: [], next_cursor: null });
    await api.getExecutorsPage(SERVER, { status: "", trading_pair: "SOL-USDC" });
    expect(urls[0]).toBe(
      "/api/v1/servers/prod/executors/page?trading_pair=SOL-USDC&limit=50",
    );
  });

  it("carries the cursor and an explicit limit when given them", async () => {
    const urls = serve({ executors: [], next_cursor: null });
    await api.getExecutorsPage(SERVER, {
      cursor: "cur-1",
      limit: 200,
      controller_id: undefined,
    });
    expect(urls[0]).toBe(
      "/api/v1/servers/prod/executors/page?cursor=cur-1&limit=200",
    );
  });
});

describe("getReports", () => {
  it("keeps set parameters and drops undefined and empty ones", async () => {
    const urls = serve({ reports: [], total: 0 });
    await api.getReports({
      source_type: "routine",
      tag: "",
      search: undefined,
      offset: 20,
    });
    expect(urls[0]).toBe("/api/v1/reports?source_type=routine&offset=20");
  });

  it("omits the ? entirely when called with no parameters", async () => {
    const urls = serve({ reports: [], total: 0 });
    await api.getReports();
    expect(urls[0]).toBe("/api/v1/reports");
  });
});

describe("controller performance history page", () => {
  it("builds its query from the walk's parameters, page size included", async () => {
    const urls = serve({ snapshots: [], next_cursor: null, interval: "1h" });
    await api.getControllerPerformanceHistoryAll(
      SERVER,
      {
        bot_name: "bot-a",
        controller_id: "",
        start_time: "2026-07-01T00:00:00Z",
        interval: "1h",
      },
      { pageSize: 3, maxRows: 100 },
    );
    expect(urls[0]).toBe(
      "/api/v1/servers/prod/controller-performance/history" +
        "?bot_name=bot-a&start_time=2026-07-01T00%3A00%3A00Z&interval=1h&limit=3",
    );
  });
});

describe("performance history page", () => {
  it("builds its query from the walk's parameters, page size included", async () => {
    const urls = serve({ snapshots: [], next_cursor: null, interval: "1h" });
    await api.getPerformanceHistory(
      SERVER,
      { subject: "controller", bot_name: "bot-a", executor_id: "", interval: "1h" },
      { pageSize: 3, maxRows: 100 },
    );
    expect(urls[0]).toBe(
      "/api/v1/servers/prod/performance/history" +
        "?subject=controller&bot_name=bot-a&interval=1h&limit=3",
    );
  });
});
