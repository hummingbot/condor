/**
 * @vitest-environment jsdom
 *
 * What a routine run says about who asked for it (FEAT-077).
 *
 * `session_key` is the whole of the provenance: the route resolves it into a
 * conversation, stamps the instance with it — which is what puts the run in
 * that conversation's dock — and posts the outcome back into the chat when it
 * lands. Asserted on the wire rather than through the caller, because the field
 * is only ever read server-side: a rename that dropped it would break nothing
 * visible here, and every run started from a chat would quietly become nobody's
 * again.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

/** Record the body of the single request the call makes. */
function serve() {
  const bodies: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      bodies.push(JSON.parse(init.body as string));
      return {
        ok: true,
        json: async () => ({ instance_id: "i1" }),
      } as unknown as Response;
    }),
  );
  return bodies;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("runRoutine", () => {
  it("carries the conversation that asked for the run", async () => {
    const bodies = serve();

    await api.runRoutine("chat-server", "backtest_chart", { pair: "SOL-USDC" }, {
      sessionKey: "web:42:main",
      attributeTo: "scout",
    });

    expect(bodies[0]).toEqual({
      routine_name: "backtest_chart",
      server_name: "chat-server",
      config: { pair: "SOL-USDC" },
      session_key: "web:42:main",
      attribute_to: "scout",
    });
  });

  it("omits it when there is no conversation behind the run", async () => {
    const bodies = serve();

    await api.runRoutine("dashboard-server", "backtest_chart");

    // Not `session_key: ""` — the dashboard's own runs are nobody's, and the
    // route reads a present-but-empty key the same way. Sending it anyway would
    // still be a lie about what this call knows.
    expect(bodies[0]).toEqual({
      routine_name: "backtest_chart",
      server_name: "dashboard-server",
      config: {},
    });
  });

  it("omits it for a conversation the browser was opened without", async () => {
    const bodies = serve();

    await api.runRoutine("dashboard-server", "backtest_chart", {}, {});

    expect(bodies[0]).not.toHaveProperty("session_key");
    expect(bodies[0]).not.toHaveProperty("attribute_to");
  });
});
