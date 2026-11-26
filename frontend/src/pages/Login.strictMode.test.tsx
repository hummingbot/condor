/**
 * Local mode signs in by itself (FEAT-049), including under StrictMode in dev.
 *
 * StrictMode mounts, unmounts and remounts the page before the /auth/mode
 * answer comes back. The probe used to latch on the first mount, so the
 * remount skipped it and the first answer was dropped as cancelled: the page
 * sat on "Signing in..." with nothing in flight. Each case resolves the probe
 * only after that teardown, which is the order that hung.
 *
 * @vitest-environment jsdom
 */

import { act, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const loginLocal = vi.fn(() => Promise.resolve());
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    isAuthenticated: false,
    loginWithToken: vi.fn(),
    loginLocal,
  }),
}));

const { Login } = await import("./Login");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

type Pending = { resolve: (r: Response) => void; reject: (e: Error) => void };
let pending: Pending[];
let host: HTMLDivElement;
let root: Root;

const modeResponse = (mode: string) =>
  ({ ok: true, json: () => Promise.resolve({ mode }) }) as unknown as Response;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  loginLocal.mockClear();
  pending = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve, reject) => {
          pending.push({ resolve, reject });
        }),
    ),
  );
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => {
    root.render(
      <StrictMode>
        <MemoryRouter>
          <Login />
        </MemoryRouter>
      </StrictMode>,
    );
  });
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it("signs in once after StrictMode tears down the first probe", async () => {
  // Both mounts asked; neither has an answer yet.
  expect(pending.length).toBe(2);
  await act(async () => {
    for (const p of pending) p.resolve(modeResponse("local"));
  });
  expect(loginLocal).toHaveBeenCalledOnce();
});

it("asks again from Retry after a probe that got no answer", async () => {
  await act(async () => {
    for (const p of pending) p.reject(new Error("Failed to fetch"));
  });
  expect(host.textContent).toContain("could not reach Condor");
  expect(loginLocal).not.toHaveBeenCalled();

  const before = pending.length;
  const retry = [...host.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Retry")!;
  act(() => retry.click());
  expect(pending.length).toBe(before + 1);

  await act(async () => {
    pending[pending.length - 1].resolve(modeResponse("local"));
  });
  expect(loginLocal).toHaveBeenCalledOnce();
});
