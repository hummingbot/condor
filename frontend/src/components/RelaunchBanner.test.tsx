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
  useRelaunch: () => ({ data, isSuccess: data !== undefined }),
}));

const relaunch = vi.fn(async () => ({ relaunching: true }));
vi.mock("@/lib/updates-api", () => ({
  updatesApi: { relaunch: () => relaunch() },
}));
vi.mock("@/lib/admin-api", () => ({ isForbidden: () => false }));

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

describe("a tab that did not ask for the relaunch", () => {
  it("reloads when the successor answers, though it never ran the countdown", async () => {
    data = { required: true };
    await render();
    expect(reload).not.toHaveBeenCalled();

    // Somebody else applied it: the other admin's tab, Telegram's button, or a
    // `make restart` on the host. This tab only ever sees the flag go out.
    data = { required: false };
    await render();

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("does not reload on a flag that was false the whole time", async () => {
    data = { required: false };
    await render();
    await render();
    expect(reload).not.toHaveBeenCalled();
  });
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
