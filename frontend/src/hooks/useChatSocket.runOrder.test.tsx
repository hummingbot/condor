/**
 * The order a turn's reasoning and tool calls happened in (ARCH-330).
 *
 * `thought` and `toolCalls` say what a run held; they cannot say how it went.
 * A turn that thinks, calls a tool, thinks again and calls a second one is one
 * merged string beside a flat list in those two fields, and the four steps are
 * unrecoverable from them — which is why the recorder now keeps the order and
 * this hook carries it, both from the record and as the frames arrive.
 *
 * The two paths are pinned against *each other* on purpose: the acceptance the
 * item was filed for is that expanding a live run and expanding the same run
 * after a reload show the same sequence.
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

/** The assistant turn's run, as the transcript state holds it. */
const run = () => messages().find((m) => m.role === "assistant")?.events;

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

describe("a run hydrated from the record", () => {
  it("comes back with think, call, think, call in that order", async () => {
    stored({
      role: "assistant",
      text: "You have $50.",
      thought: "Check the book. Now the balances.",
      ts: 1,
      tool_calls: [
        { id: "t1", title: "get_prices", status: "completed" },
        { id: "t2", title: "get_portfolio", status: "completed" },
      ],
      events: [
        { type: "thought", text: "Check the book. " },
        { type: "tool", id: "t1" },
        { type: "thought", text: "Now the balances." },
        { type: "tool", id: "t2" },
      ],
    });

    await open();

    expect(run()).toEqual([
      { type: "thought", text: "Check the book. " },
      { type: "tool", id: "t1" },
      { type: "thought", text: "Now the balances." },
      { type: "tool", id: "t2" },
    ]);
  });

  it("drops a step naming a call the turn does not hold", async () => {
    // The two fields can only ever be joined by id, so a step that names
    // nothing is a row with no name and no status. It is dropped rather than
    // drawn — the call it points at is not in the record to draw.
    stored({
      role: "assistant",
      text: "done",
      thought: "thinking",
      ts: 1,
      tool_calls: [{ id: "t1", title: "get_prices", status: "completed" }],
      events: [
        { type: "tool", id: "ghost" },
        { type: "thought", text: "thinking" },
        { type: "tool", id: "t1" },
      ],
    });

    await open();

    expect(run()).toEqual([
      { type: "thought", text: "thinking" },
      { type: "tool", id: "t1" },
    ]);
  });

  it("has no run at all for a turn recorded before the order was kept", async () => {
    // Not an empty list: "nobody wrote the order down" and "the run was empty"
    // are different answers, and only the first falls back to the flat fields.
    stored({
      role: "assistant",
      text: "done",
      thought: "thinking",
      ts: 1,
      tool_calls: [{ id: "t1", title: "get_prices", status: "completed" }],
    });

    await open();

    expect(run()).toBeUndefined();
    expect(messages()[0].thought).toBe("thinking");
    expect(messages()[0].toolCalls).toHaveLength(1);
  });
});

describe("a run watched live", () => {
  it("records each step where it actually arrived", async () => {
    await open();

    frame({ event: "thought_chunk", text: "Check " });
    frame({ event: "thought_chunk", text: "the book. " });
    frame({
      event: "tool_call",
      tool_call_id: "t1",
      title: "get_prices",
      status: "pending",
    });
    frame({ event: "thought_chunk", text: "Now the balances." });
    frame({
      event: "tool_call",
      tool_call_id: "t2",
      title: "get_portfolio",
      status: "pending",
    });
    frame({ event: "text_chunk", text: "You have $50." });
    frame({ event: "prompt_done" });

    expect(run()).toEqual([
      { type: "thought", text: "Check the book. " },
      { type: "tool", id: "t1" },
      { type: "thought", text: "Now the balances." },
      { type: "tool", id: "t2" },
    ]);
  });

  it("agrees with the same turn after a reload", async () => {
    await open();

    frame({ event: "thought_chunk", text: "Check the book. " });
    frame({
      event: "tool_call",
      tool_call_id: "t1",
      title: "get_prices",
      status: "pending",
    });
    frame({ event: "thought_chunk", text: "Now the balances." });
    frame({
      event: "tool_call",
      tool_call_id: "t2",
      title: "get_portfolio",
      status: "pending",
    });
    frame({ event: "prompt_done" });

    const live = run();

    // What `Recorder.flush` writes for exactly those frames, read back cold.
    act(() => root.unmount());
    stored({
      role: "assistant",
      text: "",
      thought: "Check the book. Now the balances.",
      ts: 1,
      tool_calls: [
        { id: "t1", title: "get_prices", status: "completed" },
        { id: "t2", title: "get_portfolio", status: "completed" },
      ],
      events: [
        { type: "thought", text: "Check the book. " },
        { type: "tool", id: "t1" },
        { type: "thought", text: "Now the balances." },
        { type: "tool", id: "t2" },
      ],
    });
    root = createRoot(container);
    await open();

    expect(run()).toEqual(live);
  });
});
