/**
 * The failure contract of the two respawn-backed switches (CORR-225).
 *
 * The server switch used to be wired straight into the socket hook from the
 * identity strip — a floating promise. `api.switchSession` throws on any
 * non-2xx (403 on a server the session may not use, 400 when the respawn dies
 * after the old session is already destroyed), so a refused move closed the
 * menu, left the chip naming the old trading account, and dropped the reason on
 * the floor as an unhandled rejection. These cases pin both switches to the one
 * error surface the thread already renders.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const chat = {
  activeSlotId: "slot-1" as string | null,
  switchBrain: vi.fn(),
  switchServer: vi.fn(),
};

vi.mock("@/hooks/useChat", () => ({ useChat: () => chat }));

const { useBrainSwitch } = await import("./useBrainSwitch");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/**
 * The hook's state, readable from outside the render. Published from an effect
 * rather than assigned during render — the same purity rule the production code
 * follows.
 */
const holder: { current: ReturnType<typeof useBrainSwitch> | null } = {
  current: null,
};
const latest = () => holder.current!;

function Harness() {
  const state = useBrainSwitch();
  useEffect(() => {
    holder.current = state;
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(<Harness />);
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
  chat.activeSlotId = "slot-1";
});

/** Let the switch's own promise settle before reading the state it wrote. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("useBrainSwitch", () => {
  it("surfaces a refused server switch instead of leaving it unhandled", async () => {
    chat.switchServer.mockRejectedValue(new Error("Access denied to server prod"));

    act(() => latest().switchServer("slot-1", "prod"));
    await settle();

    expect(chat.switchServer).toHaveBeenCalledWith("slot-1", "prod");
    expect(latest().switchError).toBe("Access denied to server prod");
  });

  it("falls back to naming the server when the rejection carries no message", async () => {
    chat.switchServer.mockRejectedValue(new Error(""));

    act(() => latest().switchServer("slot-1", "prod"));
    await settle();

    expect(latest().switchError).toBe("Could not switch to server prod");
  });

  it("shows nothing after a server switch that worked", async () => {
    chat.switchServer.mockResolvedValue(undefined);

    act(() => latest().switchServer("slot-1", "prod"));
    await settle();

    expect(latest().switchError).toBeNull();
  });

  it("clears a previous failure when the next switch starts, and on dismiss", async () => {
    chat.switchServer.mockRejectedValue(new Error("Access denied to server prod"));
    act(() => latest().switchServer("slot-1", "prod"));
    await settle();
    expect(latest().switchError).toBe("Access denied to server prod");

    act(() => latest().dismissSwitchError());
    expect(latest().switchError).toBeNull();

    act(() => latest().switchServer("slot-1", "prod"));
    await settle();
    chat.switchServer.mockResolvedValue(undefined);
    act(() => latest().switchServer("slot-1", "staging"));
    await settle();
    expect(latest().switchError).toBeNull();
  });

  it("still routes a refused brain switch to the same banner", async () => {
    chat.switchBrain.mockRejectedValue(new Error("Model not configured"));

    act(() => latest().switchBrain({ agentKey: "claude-code" } as never));
    await settle();

    expect(latest().switchError).toBe("Model not configured");
  });

  it("does not try to switch the brain when there is no session on screen", () => {
    chat.activeSlotId = null;

    act(() => latest().switchBrain({ agentKey: "claude-code" } as never));

    expect(chat.switchBrain).not.toHaveBeenCalled();
  });
});
