/**
 * What the chat does with a dead socket (FU-03).
 *
 * The hook queued every frame written while the socket was down and replayed
 * all of them on the next `onopen`, with no expiry — so "close my position"
 * typed before a laptop slept reached the agent hours later, looking exactly
 * like a message that had been sent at the time. Stop was worse: it queued
 * the frame and then drew the turn as stopped, while the agent ran on.
 *
 * What is pinned: a bubble written offline says it is queued and goes out on a
 * prompt reconnect; one that waited past the bound is not replayed and says it
 * was not sent, and survives the transcript re-read that follows a reconnect;
 * Stop while offline changes nothing on screen and is sent once, however late;
 * a stale approval is dropped rather than applied.
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
    getPendingConfirmations: () => Promise.resolve([]),
    attachmentUrl: (conv: string, id: string) => `/api/conversations/${conv}/a/${id}`,
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ token: "jwt", user: { id: 7 } }),
}));

const { useChatSocket, QUEUED_FRAME_TTL_MS } = await import("./useChatSocket");

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
  /** Every frame this socket was asked to carry. */
  sent: Record<string, unknown>[] = [];

  constructor() {
    FakeSocket.last = this;
  }
  send(raw: string) {
    this.sent.push(JSON.parse(raw));
  }
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
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

/** The wire goes away, as a sleep or a network drop takes it. */
function drop() {
  const dead = sock();
  act(() => {
    dead.readyState = FakeSocket.CLOSED;
    dead.onclose?.();
  });
  return dead;
}

/** The reconnect: a new socket, opened. */
function reconnect() {
  act(() => {
    chat().connect();
  });
  const fresh = sock();
  act(() => {
    fresh.onopen?.();
  });
  return fresh;
}

const actions = (s: FakeSocket) => s.sent.map((f) => f.action);
const userBubble = () => messages().find((m) => m.role === "user");

let now = 1_800_000_000_000;

describe("the chat with its socket down (FU-03)", () => {
  beforeEach(() => {
    now = 1_800_000_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
  });

  it("reports the connection, down and back", async () => {
    await open();
    expect(chat().isConnected).toBe(true);

    drop();
    expect(chat().isConnected).toBe(false);

    reconnect();
    expect(chat().isConnected).toBe(true);
  });

  it("flags a message written offline as queued, and sends it on a prompt reconnect", async () => {
    await open();
    const dead = drop();

    act(() => chat().sendMessage("s1", "what is SOL doing"));
    await settle();

    expect(userBubble()?.delivery).toBe("queued");
    expect(actions(dead)).not.toContain("send_message");

    now += 5_000;
    const fresh = reconnect();

    expect(fresh.sent).toContainEqual(
      expect.objectContaining({ action: "send_message", text: "what is SOL doing" }),
    );
    expect(userBubble()?.delivery).toBeUndefined();
  });

  it("does not replay a message that waited past the bound, and says so", async () => {
    await open();
    drop();

    act(() => chat().sendMessage("s1", "close my position"));
    await settle();
    expect(userBubble()?.delivery).toBe("queued");

    now += QUEUED_FRAME_TTL_MS + 1;
    const fresh = reconnect();

    expect(actions(fresh)).not.toContain("send_message");
    expect(userBubble()?.delivery).toBe("unsent");

    // The reconnect re-reads the transcript and replaces what was on screen.
    // The unsent bubble is in no transcript, and has to outlive that.
    stored({ role: "assistant", text: "an earlier answer", ts: 1 });
    act(() => {
      fresh.deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual([
      "an earlier answer",
      "close my position",
    ]);
    expect(userBubble()?.delivery).toBe("unsent");
  });

  it("leaves a message sent on a live socket unflagged", async () => {
    await open();

    act(() => chat().sendMessage("s1", "hello"));
    await settle();

    expect(actions(sock())).toContain("send_message");
    expect(userBubble()?.delivery).toBeUndefined();
  });

  it("does not show a turn as stopped while Stop cannot reach the agent", async () => {
    await open();
    frame({ event: "tool_call", tool_call_id: "t1", title: "a", status: "in_progress" });
    expect(chat().isSlotStreaming("s1")).toBe(true);
    drop();

    act(() => chat().abortPrompt("s1"));
    act(() => chat().abortPrompt("s1"));

    // Nothing was stopped, so nothing reads as stopped.
    expect(chat().isSlotStreaming("s1")).toBe(true);
    expect(calls()[0].status).toBe("in_progress");

    // Stop is still wanted however late it lands — once.
    now += QUEUED_FRAME_TTL_MS * 10;
    const fresh = reconnect();
    expect(actions(fresh).filter((a) => a === "abort_prompt")).toHaveLength(1);
  });

  it("stops at once on a live socket, as before", async () => {
    await open();
    frame({ event: "tool_call", tool_call_id: "t1", title: "a", status: "in_progress" });

    act(() => chat().abortPrompt("s1"));

    expect(actions(sock())).toContain("abort_prompt");
    expect(chat().isSlotStreaming("s1")).toBe(false);
    expect(calls()[0].status).toBe("interrupted");
  });

  it("drops an approval that went stale in the queue, and sends a fresh one", async () => {
    await open();
    drop();

    act(() => chat().resolvePermission("r-old", true));
    now += QUEUED_FRAME_TTL_MS + 1;
    act(() => chat().resolvePermission("r-new", false));
    const fresh = reconnect();

    const resolved = fresh.sent.filter((f) => f.action === "resolve_permission");
    expect(resolved).toEqual([
      { action: "resolve_permission", request_id: "r-new", approved: false },
    ]);
  });
});
