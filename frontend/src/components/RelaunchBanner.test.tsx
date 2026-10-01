/**
 * What actually makes an open tab pick up the rebuilt bundle.
 *
 * The update run rebuilds `frontend/dist` and swaps it in while the old process
 * is still serving, so every open tab is left holding a bundle whose chunks
 * have moved. Two separate things have to happen for that to resolve, and both
 * used to be wrong in a way no test would have caught.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Data = { required: boolean; from_commit?: string; target_commit?: string };

let data: Data | undefined;

vi.mock("@/hooks/useRelaunch", () => ({
  useReloadAfterRelaunch: () => ({ data, isSuccess: data !== undefined }),
}));

const relaunch = vi.fn(async () => ({ relaunching: true }));
vi.mock("@/lib/updates-api", () => ({
  updatesApi: { relaunch: () => relaunch() },
}));

/** Flipped per test: a 403 is the one rejection that means "and it never will". */
let forbidden = false;
vi.mock("@/lib/admin-api", () => ({ isForbidden: () => forbidden }));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let reload: ReturnType<typeof vi.fn>;
let fetchMock: ReturnType<typeof vi.fn>;

/** `reloaded` is module state, so each test needs its own copy of the module. */
async function render() {
  const { RelaunchBanner } = await import("./RelaunchBanner");
  await act(async () => {
    root.render(<RelaunchBanner />);
  });
}

/** Let the 5s countdown elapse, which is what fires the relaunch. */
async function runCountdown() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
  }
}

function answers(body: Data) {
  fetchMock.mockResolvedValue({ ok: true, json: async () => body });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.resetModules();
  vi.useFakeTimers();
  relaunch.mockClear();
  forbidden = false;
  reload = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { reload },
  });
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the tab that did ask", () => {
  it("does not reload while the process it asked to restart is still answering", async () => {
    data = { required: true };
    await render();

    // SIGTERM only starts the shutdown. Through the whole drain and teardown
    // the dying process answers 200 — and it is still the one that owes the
    // relaunch, so it still says required.
    answers({ required: true });
    await runCountdown();
    expect(relaunch).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(fetchMock).toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
  });

  it("reloads once the successor is the one answering", async () => {
    data = { required: true };
    await render();

    answers({ required: true });
    await runCountdown();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(reload).not.toHaveBeenCalled();

    answers({ required: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

describe("a seat that may not restart", () => {
  it("says so once, and does not poll or reload after the refusal", async () => {
    // Without this the 403 looked like any other dropped connection: poll,
    // find the same server still answering, reload, count down, post again —
    // a reload loop on a seat that can never end it.
    forbidden = true;
    relaunch.mockRejectedValueOnce(new Error("Forbidden"));
    data = { required: true };
    await render();

    answers({ required: true });
    await runCountdown();
    expect(relaunch).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
    expect(container.textContent).toContain("cannot restart Condor");
  });

  it("offers no Restart now button, which would be refused the same way", async () => {
    forbidden = true;
    relaunch.mockRejectedValueOnce(new Error("Forbidden"));
    data = { required: true };
    await render();
    await runCountdown();

    const labels = [...container.querySelectorAll("button")].map(
      (b) => b.textContent,
    );
    expect(labels).not.toContain("Restart now");
  });
});
