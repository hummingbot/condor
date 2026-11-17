/**
 * A stopped turn does not claim its tool calls ran (FU-06).
 *
 * Stop and a mid-turn message both settle whatever was still in flight, and
 * the settle used to rewrite every such call to `completed` — a green check on
 * a `create_position_executor` whose real outcome nobody on this side knows.
 * The same rewrite was already wrong once, for refused calls (CORR-324).
 *
 * What is pinned: a call cut off by Stop, by a redirect, or by the user's next
 * message reads `interrupted`, never `completed`; a late non-terminal update
 * cannot put it back in flight for the closing settle to tick green; the
 * server naming the real outcome afterwards still wins; and a turn that simply
 * ended settles the way it always did.
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
import { toolCallState } from "@/lib/formatters";

const getConversation = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listConversations: () => Promise.resolve([]),
    getSessionOptions: () => Promise.resolve({ default_agent: "claude-code" }),
    getConversation: (...args: unknown[]) => getConversation(...args),
    attachmentUrl: (conv: string, id: string) => `/api/conversations/${conv}/a/${id}`,
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
    await Promise.resolve();
  });
}

const roster = () => ({
  event: "sessions_list",
  sessions: [{ slot_id: "s1", conversation_id: "c1", agent_key: "k" }],
});

const messages = () =>
  chat().slots.find((s) => s.info.slot_id === "s1")?.messages ?? [];

/** The turn's tool calls, as the transcript state holds them. */
const calls = () => messages().find((m) => m.role === "assistant")?.toolCalls ?? [];

function stored(...turns: Record<string, unknown>[]) {
  getConversation.mockResolvedValue({ meta: {}, turns });
}

async function open() {
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
  act(() => {
    sock().deliver(roster());
  });
  await settle();
}

/** One frame, delivered as the socket would deliver it. */
function frame(f: Record<string, unknown>) {
  act(() => {
    sock().deliver({ ...f, slot_id: "s1" });
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.last = null;
  stored();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("tool calls of a turn that was cut short (FU-06)", () => {
  async function inFlight() {
    await open();
    frame({ event: "tool_call", tool_call_id: "t1", title: "a", status: "completed" });
    frame({ event: "tool_call", tool_call_id: "t2", title: "b", status: "in_progress" });
    frame({ event: "tool_call", tool_call_id: "t3", title: "c", status: "blocked" });
  }
  const statuses = () => calls().map((tc) => tc.status);

  it("Stop settles the in-flight call to interrupted, not completed", async () => {
    await inFlight();

    act(() => chat().abortPrompt("s1"));

    // The finished and the refused call are left exactly as reported.
    expect(statuses()).toEqual(["completed", "interrupted", "blocked"]);
    expect(toolCallState(calls()[1].status)).toBe("interrupted");
  });

  it("stays interrupted through the prompt_done that follows the abort", async () => {
    await inFlight();
    act(() => chat().abortPrompt("s1"));

    // A straggler from before the abort landed, then the turn's own ending.
    frame({ event: "tool_call_update", tool_call_id: "t2", status: "in_progress" });
    frame({ event: "prompt_done" });

    expect(statuses()).toEqual(["completed", "interrupted", "blocked"]);
  });

  it("takes the real outcome when the server does name one", async () => {
    await inFlight();
    act(() => chat().abortPrompt("s1"));

    frame({ event: "tool_call_update", tool_call_id: "t2", status: "completed" });

    expect(statuses()).toEqual(["completed", "completed", "blocked"]);
  });

  it("a redirect and the user's next message settle the same way", async () => {
    await inFlight();

    act(() => chat().sendMessage("s1", "actually, stop"));
    expect(statuses()).toEqual(["completed", "interrupted", "blocked"]);

    frame({ event: "tool_call", tool_call_id: "t4", title: "d", status: "pending" });
    frame({ event: "prompt_interrupted" });
    const all = messages().flatMap((m) => m.toolCalls.map((tc) => tc.status));
    expect(all).toEqual(["completed", "interrupted", "blocked", "interrupted"]);
  });

  it("a turn that simply ended still settles to completed", async () => {
    await inFlight();

    frame({ event: "prompt_done" });

    expect(statuses()).toEqual(["completed", "completed", "blocked"]);
  });
});
