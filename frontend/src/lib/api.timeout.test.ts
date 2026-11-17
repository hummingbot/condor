/**
 * The fetch wrapper's default timeout (FD-05).
 *
 * A `fetch` that never answers, under fake timers: reads are abandoned at
 * 30 s, writes only at a ceiling long enough for the slow ones, and a caller's
 * own signal still cancels.
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, errorStatus } from "./api";

let signals: AbortSignal[] = [];

/** Never answers; rejects the way `fetch` does when its signal aborts. */
function hangingFetch() {
  return vi.fn(
    (_path: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        const signal = init!.signal!;
        signals.push(signal);
        signal.addEventListener("abort", () =>
          reject(signal.reason ?? new DOMException("Aborted", "AbortError")),
        );
      }),
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  signals = [];
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Settle state of a promise without awaiting it. */
function track<T>(p: Promise<T>) {
  const state: { error?: unknown; done: boolean } = { done: false };
  p.then(
    () => (state.done = true),
    (e) => {
      state.done = true;
      state.error = e;
    },
  );
  return state;
}

describe("apiFetch timeout", () => {
  it("abandons a read after 30 s", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    const state = track(api.getServers());

    await vi.advanceTimersByTimeAsync(29_999);
    expect(state.done).toBe(false);
    await vi.advanceTimersByTimeAsync(1);

    expect(state.done).toBe(true);
    expect((state.error as Error).name).toBe("TimeoutError");
    expect((state.error as Error).message).toBe("Request timed out after 30s");
    // No status: the query layer treats it like a network failure and retries.
    expect(errorStatus(state.error)).toBeUndefined();
    expect(signals[0].aborted).toBe(true);
  });

  it("gives a write far longer, and says it may still have been applied", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    const state = track(api.stopExecutor("prod", "abc"));

    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(state.done).toBe(false);
    await vi.advanceTimersByTimeAsync(5 * 60_000);

    expect((state.error as Error).name).toBe("TimeoutError");
    expect((state.error as Error).message).toContain("may still have been applied");
  });

  it("leaves no timer behind once the request is answered", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("[]", { headers: { "Content-Type": "application/json" } })),
    );
    await expect(api.getServers()).resolves.toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("still honours the caller's own signal, as an abort rather than a timeout", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    const controller = new AbortController();
    const state = track(
      api.getControllerPerformanceHistoryAll("prod", {}, { signal: controller.signal }),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(state.done).toBe(false);

    controller.abort();
    await vi.advanceTimersByTimeAsync(0);
    expect(state.done).toBe(true);
    expect((state.error as Error).name).not.toBe("TimeoutError");
    expect(vi.getTimerCount()).toBe(0);
  });
});
