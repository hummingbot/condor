/**
 * @vitest-environment jsdom
 *
 * What a strategy-config save puts on the wire, and what it reads back
 * (FEAT-142).
 *
 * The route existed with no client at all, so nothing held it to its own shape:
 * CORR-732 changed the response once and no caller would have noticed if it had
 * broken it. Asserted here rather than through the card, because the card mocks
 * this module — a URL typo or a body renamed to `settings` would pass every
 * component test and reach the server as a 422 forever (READ-333's failure).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

/** Record the single request the call makes, and answer the route's shape. */
function serve() {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return {
        ok: true,
        json: async () => ({
          updated: true,
          config: { frequency_sec: 60 },
          applies_to_running: false,
          live_engines: 2,
          message: "Saved; restart 2 sessions to apply.",
        }),
      } as unknown as Response;
    }),
  );
  return calls;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("updateStrategyConfig", () => {
  it("PUTs the whole mapping under `config` to the strategy's config route", async () => {
    const calls = serve();

    await api.updateStrategyConfig("brigado", "pmm_king", {
      frequency_sec: 60,
      risk_limits: { max_leverage: 3 },
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/v1/agents/brigado/strategies/pmm_king/config");
    expect(calls[0].init.method).toBe("PUT");
    // `UpdateConfigRequest` is a single `config` mapping — nested blocks ride
    // inside it whole, which is what keeps the route's shallow merge from
    // dropping a key of `risk_limits` (SEC-715).
    expect(JSON.parse(calls[0].init.body as string)).toEqual({
      config: { frequency_sec: 60, risk_limits: { max_leverage: 3 } },
    });
  });

  it("escapes both slugs rather than pasting them into the path", async () => {
    const calls = serve();

    await api.updateStrategyConfig("a/b", "c d", {});

    expect(calls[0].url).toBe("/api/v1/agents/a%2Fb/strategies/c%20d/config");
  });

  it("reads back all five fields the route answers, `message` included", async () => {
    serve();

    const res = await api.updateStrategyConfig("brigado", "pmm_king", {});

    // The count and the sentence are the whole of CORR-732: `updated: true`
    // alone was true of the file and silent about the running session.
    expect(res).toEqual({
      updated: true,
      config: { frequency_sec: 60 },
      applies_to_running: false,
      live_engines: 2,
      message: "Saved; restart 2 sessions to apply.",
    });
  });

  it("surfaces a gate's refusal as the error's message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 403,
        json: async () => ({ detail: "You have no access to server brigado_2" }),
      }) as unknown as Response),
    );

    await expect(
      api.updateStrategyConfig("brigado", "pmm_king", { server_name: "brigado_2" }),
    ).rejects.toThrow("You have no access to server brigado_2");
  });
});
