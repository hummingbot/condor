/**
 * The approval prompt must read as a paused command, not a warning.
 *
 * Users left agents "running" while they sat blocked on an approval that
 * looked like a notice — often right after typing "confirm" in chat. These pin
 * the parts that make it unmistakable: it previews the call, counts down to the
 * automatic deny, says a chat reply is not an answer, and answers by id.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PermissionRequest } from "@/hooks/useChatSocket";
import { ApprovalPrompt } from "./ApprovalPrompt";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

const NOW = 1_700_000_000_000;

function request(partial: Partial<PermissionRequest> = {}): PermissionRequest {
  return {
    request_id: "abc123",
    summary: "BUY 1 SOL-USDC (MARKET) on binance",
    origin: "brigado on moneymaker",
    tool: "place_order",
    input: { trading_pair: "SOL-USDC", amount: 1 },
    deadline: NOW / 1000 + 90,
    ...partial,
  };
}

function render(req: PermissionRequest, onResolve = vi.fn()) {
  act(() => root.render(<ApprovalPrompt request={req} onResolve={onResolve} />));
  return onResolve;
}

function button(name: string): HTMLButtonElement {
  const match = [...container.querySelectorAll("button")].find(
    (b) => b.textContent === name,
  );
  if (!match) throw new Error(`no "${name}" button`);
  return match;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("ApprovalPrompt", () => {
  it("says the agent is paused and previews the call it would run", () => {
    render(request());

    const text = container.textContent ?? "";
    expect(text).toContain("Paused — waiting for your approval");
    expect(text).toContain("brigado on moneymaker wants to run:");
    expect(text).toContain("BUY 1 SOL-USDC (MARKET) on binance");
    expect(container.querySelector("pre")?.textContent).toContain('"amount": 1');
  });

  it("says a chat reply is not the answer", () => {
    render(request());

    expect(container.textContent).toContain("sending one cancels this call");
  });

  it("answers by request id", () => {
    const onResolve = render(request());

    act(() => button("Allow").click());
    expect(onResolve).toHaveBeenLastCalledWith("abc123", true);

    act(() => button("Deny").click());
    expect(onResolve).toHaveBeenLastCalledWith("abc123", false);
  });

  it("counts down to the automatic deny", () => {
    render(request());
    expect(container.textContent).toContain("1:30");

    act(() => vi.advanceTimersByTime(31_000));
    expect(container.textContent).toContain("0:59");
  });

  it("stops offering Allow once the deadline has passed", () => {
    const onResolve = render(request({ deadline: NOW / 1000 + 2 }));

    act(() => vi.advanceTimersByTime(3_000));

    expect(container.textContent).toContain("Approval timed out");
    expect(() => button("Allow")).toThrow();
    act(() => button("Dismiss").click());
    expect(onResolve).toHaveBeenLastCalledWith("abc123", false);
  });

  it("renders an older backend's summary-only request without inventing a preview", () => {
    render(request({ tool: undefined, input: undefined, deadline: undefined, origin: "" }));

    expect(container.textContent).toContain("The agent wants to run:");
    expect(container.querySelector("pre")).toBeNull();
    expect(button("Allow")).toBeTruthy();
  });
});
