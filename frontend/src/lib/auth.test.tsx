/**
 * When a session ends (FD-01).
 *
 * The mount check used to log out on any non-OK `/auth/me`, so reloading
 * behind a reverse proxy while Condor restarted (502) wiped the session and
 * everything `logout` clears with it. Only `/auth/me` refusing the token ends
 * a session now — at mount, and when a later request reports a 401.
 *
 * @vitest-environment jsdom
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";
import {
  AUTH_PROBE_TIMEOUT_MS,
  type AuthState,
  resolveUnauthorized,
  useAuthState,
} from "./auth";
import { TOKEN_KEY } from "./auth-token";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const USER = { id: 7, username: "fede", first_name: "Fede", role: "admin" };

const json = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

/** `fetch` answering per path; anything unlisted is a 404. */
function stubFetch(routes: Record<string, () => Response>) {
  const mock = vi.fn(async (path: string) => (routes[path] ?? (() => json(404)))());
  vi.stubGlobal("fetch", mock);
  return mock;
}

let container: HTMLDivElement;
let root: Root;
let state: AuthState;

function Probe() {
  const value = useAuthState();
  useEffect(() => {
    state = value;
  });
  return null;
}

async function mount() {
  await act(async () => {
    root.render(<Probe />);
  });
  await act(async () => {
    for (let i = 0; i < 10; i++) await Promise.resolve();
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  localStorage.setItem(TOKEN_KEY, "old");
  localStorage.setItem("condor_user", JSON.stringify(USER));
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("token check on mount", () => {
  it.each([500, 502, 503])("keeps the session when /auth/me answers %s", async (status) => {
    stubFetch({ "/api/v1/auth/me": () => json(status) });
    await mount();
    expect(localStorage.getItem(TOKEN_KEY)).toBe("old");
    expect(state.isAuthenticated).toBe(true);
  });

  it("keeps the session when the server is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await mount();
    expect(localStorage.getItem(TOKEN_KEY)).toBe("old");
    expect(state.isAuthenticated).toBe(true);
  });

  it.each([401, 403])("ends the session when /auth/me answers %s", async (status) => {
    stubFetch({
      "/api/v1/auth/me": () => json(status),
      "/api/v1/auth/mode": () => json(200, { mode: "telegram" }),
    });
    await mount();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(state.isAuthenticated).toBe(false);
  });
});

describe("a 401 on a later request", () => {
  it("logs out once the session is confirmed dead", async () => {
    let meStatus = 200;
    stubFetch({
      "/api/v1/auth/me": () => json(meStatus, USER),
      "/api/v1/auth/mode": () => json(200, { mode: "telegram" }),
      "/api/v1/servers": () => json(401, { detail: "Invalid token" }),
    });
    await mount();
    expect(state.isAuthenticated).toBe(true);

    meStatus = 401;
    await act(async () => {
      await Promise.allSettled([api.getServers(), api.getServers()]);
    });
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(state.isAuthenticated).toBe(false);
  });

  it("keeps the session when the 401 was not about it", async () => {
    // e.g. the hub routes passing Botcamp's own 401 through.
    stubFetch({
      "/api/v1/auth/me": () => json(200, USER),
      "/api/v1/servers": () => json(401, { detail: "Botcamp token revoked" }),
    });
    await mount();
    await act(async () => {
      await expect(api.getServers()).rejects.toThrow("Botcamp token revoked");
    });
    expect(localStorage.getItem(TOKEN_KEY)).toBe("old");
    expect(state.isAuthenticated).toBe(true);
  });

  it("claims a fresh session in local mode and retries the request", async () => {
    let meStatus = 200;
    const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
      const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
      if (path === "/api/v1/auth/me") return json(meStatus, USER);
      if (path === "/api/v1/auth/mode") return json(200, { mode: "local" });
      if (path === "/api/v1/auth/local-login") return json(200, { token: "new", user: USER });
      if (path === "/api/v1/servers") return auth === "Bearer new" ? json(200, []) : json(401);
      return json(404);
    });
    vi.stubGlobal("fetch", fetchMock);
    await mount();

    meStatus = 401;
    await act(async () => {
      await expect(api.getServers()).resolves.toEqual([]);
    });
    expect(localStorage.getItem(TOKEN_KEY)).toBe("new");
    expect(state.token).toBe("new");
    expect(state.isAuthenticated).toBe(true);
  });
});

describe("resolveUnauthorized", () => {
  it("logs out when the local re-login fails", async () => {
    stubFetch({
      "/api/v1/auth/me": () => json(401),
      "/api/v1/auth/mode": () => json(200, { mode: "local" }),
    });
    const logout = vi.fn();
    const loginLocal = vi.fn(async () => {
      throw new Error("Local login failed");
    });
    await expect(resolveUnauthorized({ loginLocal, logout })).resolves.toBe(false);
    expect(logout).toHaveBeenCalledTimes(1);
  });

  /** `fetch` that never answers until its signal aborts it. */
  function hangUntilAborted(_path: string, init?: RequestInit): Promise<Response> {
    return new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () =>
        reject(new DOMException("aborted", "AbortError")),
      );
    });
  }

  it("keeps the session when /auth/me never answers", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("fetch", vi.fn(hangUntilAborted));
      const logout = vi.fn();
      const loginLocal = vi.fn(async () => {});
      const outcome = resolveUnauthorized({ loginLocal, logout });
      await vi.advanceTimersByTimeAsync(AUTH_PROBE_TIMEOUT_MS);
      await expect(outcome).resolves.toBe(false);
      expect(logout).not.toHaveBeenCalled();
      expect(loginLocal).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("ends a refused session when /auth/mode never answers", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn((path: string, init?: RequestInit) =>
          path === "/api/v1/auth/me"
            ? Promise.resolve(json(401))
            : hangUntilAborted(path, init),
        ),
      );
      const logout = vi.fn();
      const loginLocal = vi.fn(async () => {});
      const outcome = resolveUnauthorized({ loginLocal, logout });
      await vi.advanceTimersByTimeAsync(AUTH_PROBE_TIMEOUT_MS);
      await expect(outcome).resolves.toBe(false);
      expect(logout).toHaveBeenCalledTimes(1);
      expect(loginLocal).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
