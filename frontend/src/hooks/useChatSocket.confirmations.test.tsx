/**
 * What a socket open re-reads about approvals (READ-597).
 *
 * `permission_request` is a fire-and-forget push. A reload mid-approval killed
 * the socket it was addressed to and nothing re-sent it, so the agent sat
 * waiting behind a page that showed no prompt until its TTL denied the tool
 * call. These pin the recovery path: every open asks the registry what is
 * still pending, files it under the conversation that asked, and never
 * disturbs an approval already on screen.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";

const getPendingConfirmations = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listConversations: () => Promise.resolve([]),
    getSessionOptions: () => Promise.resolve({ default_agent: "claude-code" }),
    getConversation: () => Promise.resolve({ meta: {}, turns: [] }),
    getPendingConfirmations: () => getPendingConfirmations(),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ token: "jwt", user: { id: 7 } }),
}));

const { useChatSocket } = await import("./useChatSocket");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

class FakeSocket {
  static last: FakeSocket | null = null;

  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = FakeSocket.OPEN;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;

  constructor() {
    FakeSocket.last = this;
  }
  send() {}
  close() {
    this.readyState = FakeSocket.CLOSED;
  }
  deliver(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

const sock = () => FakeSocket.last!;

const holder: { current: ReturnType<typeof useChatSocket> | null } = {
  current: null,
};
const chat = () => holder.current!;

function Harness() {
  const state = useChatSocket();
  useEffect(() => {
    holder.current = state;
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Open the page and bring the socket up, the way the browser would. */
async function arrive() {
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <ServerContext value={{ server: "moneymaker", setServer: () => {} }}>
          <Harness />
        </ServerContext>
      </QueryClientProvider>,
    );
  });
  act(() => {
    chat().connect();
    sock().onopen?.();
  });
  await settle();
}

/** One approval, as `GET /api/v1/confirmations` lists it. */
const stranded = {
  id: "abc123",
  slot_id: "s1",
  summary: "Place a 1 SOL order?",
  origin: "brigado on moneymaker",
  expires_at: 0,
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.last = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getPendingConfirmations.mockResolvedValue([]);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("re-reading pending approvals on a socket open", () => {
  it("re-renders a prompt the reload stranded, in the slot that asked", async () => {
    getPendingConfirmations.mockResolvedValue([stranded]);

    await arrive();

    expect(chat().permissionRequests.s1).toEqual({
      request_id: "abc123",
      summary: "Place a 1 SOL order?",
      origin: "brigado on moneymaker",
    });
  });

  it("carries the call and a local deadline, so the prompt can preview and count down", async () => {
    getPendingConfirmations.mockResolvedValue([
      { ...stranded, tool: "place_order", input: { amount: 1 }, expires_in: 90 },
    ]);
    const before = Date.now() / 1000;

    await arrive();

    const req = chat().permissionRequests.s1;
    expect(req.tool).toBe("place_order");
    expect(req.input).toEqual({ amount: 1 });
    expect(req.deadline).toBeGreaterThanOrEqual(before + 90);
    expect(req.deadline).toBeLessThanOrEqual(Date.now() / 1000 + 90);
  });

  it("files an approval with no slot where an unaddressed one goes", async () => {
    getPendingConfirmations.mockResolvedValue([{ ...stranded, slot_id: "" }]);

    await arrive();

    expect(chat().permissionRequests[""].request_id).toBe("abc123");
  });

  it("leaves an approval this session is already showing alone", async () => {
    await arrive();

    // The live push arrives first and is what the user is looking at.
    act(() => {
      sock().deliver({
        event: "permission_request",
        slot_id: "s1",
        request_id: "live-1",
        summary: "the one on screen",
        origin: "",
      });
    });

    // A later open re-reads and finds the registry's own view of that slot.
    getPendingConfirmations.mockResolvedValue([stranded]);
    act(() => {
      sock().readyState = FakeSocket.CLOSED;
      sock().onclose?.();
    });
    act(() => {
      chat().connect();
      sock().onopen?.();
    });
    await settle();

    expect(chat().permissionRequests.s1.request_id).toBe("live-1");
  });

  it("says nothing when there is nothing pending", async () => {
    await arrive();

    expect(getPendingConfirmations).toHaveBeenCalled();
    expect(chat().permissionRequests).toEqual({});
  });

  it("keeps the chat working when the read fails", async () => {
    getPendingConfirmations.mockRejectedValue(new Error("gateway is down"));

    await arrive();

    expect(chat().permissionRequests).toEqual({});
    expect(chat().isConnected).toBe(true);
  });
});
