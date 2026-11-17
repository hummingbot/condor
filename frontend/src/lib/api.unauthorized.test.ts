/**
 * What `apiFetch` does with a 401 (FD-01).
 *
 * A tab left open past the token's lifetime used to fail every request
 * silently. The wrapper now reports a 401 to the handler `useAuthState`
 * registers — once, however many requests fail together — and retries once
 * when the handler says the session was renewed.
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, errorStatus, setOnUnauthorized } from "./api";
import { TOKEN_KEY } from "./auth-token";

const json = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

let unregister: (() => void) | null = null;

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "old");
});

afterEach(() => {
  unregister?.();
  unregister = null;
  vi.unstubAllGlobals();
});

describe("apiFetch on 401", () => {
  it("runs the handler once for concurrent 401s and rejects each request", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "Invalid token" })));
    const handler = vi.fn(async () => {
      await Promise.resolve();
      localStorage.removeItem(TOKEN_KEY);
      return false;
    });
    unregister = setOnUnauthorized(handler);

    const results = await Promise.allSettled([
      api.getServers(),
      api.getServers(),
      api.getPortfolio("prod"),
    ]);

    expect(handler).toHaveBeenCalledTimes(1);
    for (const r of results) {
      expect(r.status).toBe("rejected");
      expect(errorStatus((r as PromiseRejectedResult).reason)).toBe(401);
    }
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("retries once with the new token when the handler renewed the session", async () => {
    const fetchMock = vi.fn(async (_path: string, init?: RequestInit) => {
      const auth = (init?.headers as Record<string, string>).Authorization;
      return auth === "Bearer new" ? json(200, [{ name: "prod" }]) : json(401);
    });
    vi.stubGlobal("fetch", fetchMock);
    unregister = setOnUnauthorized(async () => {
      localStorage.setItem(TOKEN_KEY, "new");
      return true;
    });

    await expect(api.getServers()).resolves.toEqual([{ name: "prod" }]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("does not loop when the retry is refused too", async () => {
    const fetchMock = vi.fn(async () => json(401));
    vi.stubGlobal("fetch", fetchMock);
    const handler = vi.fn(async () => true);
    unregister = setOnUnauthorized(handler);

    await expect(api.getServers()).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("leaves a request that carried no token alone", async () => {
    localStorage.removeItem(TOKEN_KEY);
    vi.stubGlobal("fetch", vi.fn(async () => json(401)));
    const handler = vi.fn(async () => false);
    unregister = setOnUnauthorized(handler);

    await expect(api.getServers()).rejects.toMatchObject({ status: 401 });
    expect(handler).not.toHaveBeenCalled();
  });

  it("does not report other failures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(403, { detail: "Access denied" })));
    const handler = vi.fn(async () => false);
    unregister = setOnUnauthorized(handler);

    await expect(api.getServers()).rejects.toThrow("Access denied");
    expect(handler).not.toHaveBeenCalled();
  });

  it("throws the 401 as before when no handler is registered", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "Invalid token" })));
    await expect(api.getServers()).rejects.toThrow("Invalid token");
  });
});
