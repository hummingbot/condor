/**
 * The launcher shows a dead socket (FU-03).
 *
 * On every page but the workspace the closed launcher is all there is of the
 * chat, so it is where "offline" has to show before the panel is opened.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatSlot } from "@/hooks/useChatSocket";
import { CONNECTION_LOST_DELAY_MS } from "@/hooks/useConnectionLost";

const chat = {
  isConnected: true,
  slots: [] as ChatSlot[],
  resolveSlotId: (id: string) => id,
  isSlotStreaming: () => false,
  isSlotQueued: () => false,
  permissionFor: () => null,
  resolvePermission: vi.fn(),
  activeSlotId: null as string | null,
};

vi.mock("@/hooks/useChat", () => ({
  useChat: () => chat,
  useSessionOptions: () => ({ defaultAgent: "claude", agents: [] }),
}));
vi.mock("@/hooks/useServer", () => ({ useServer: () => ({ server: "srv" }) }));
vi.mock("@/hooks/useStarters", () => ({ useStarters: () => [] }));
vi.mock("@tanstack/react-query", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-query")>()),
  useQuery: () => ({ data: [] }),
}));

const { ChatBubble } = await import("./ChatBubble");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function render(isConnected: boolean) {
  chat.isConnected = isConnected;
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/portfolio"]}>
        <ChatBubble />
      </MemoryRouter>,
    );
  });
}

const dot = () => container.querySelector('[data-testid="bubble-offline"]');

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("the closed launcher (FU-03)", () => {
  it("marks a socket that has stayed down, and clears when it is back", () => {
    render(true);
    expect(dot()).toBeNull();

    render(false);
    expect(dot()).toBeNull();
    act(() => vi.advanceTimersByTime(CONNECTION_LOST_DELAY_MS));
    expect(dot()).not.toBeNull();

    render(true);
    expect(dot()).toBeNull();
  });
});
