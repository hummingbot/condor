/**
 * A chat is answering from the moment its message goes out until its turn ends,
 * by whatever end: the answer finishing, Stop, an error. It used to be read off
 * the transcript ("the last message is the user's"), so a turn stopped before
 * its first token kept the rail saying "Answering" for ever.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";

const getConversation = vi.fn();
const listConversations = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listConversations: (...args: unknown[]) => listConversations(...args),
    getSessionOptions: () => Promise.resolve({ default_agent: "claude-code" }),
    getConversation: (...args: unknown[]) => getConversation(...args),
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
  });
}

const ONE = { slot_id: "s1", conversation_id: "c1", agent_key: "k", alive: true };

function deliver(frame: Record<string, unknown>) {
  act(() => {
    sock().deliver(frame);
  });
}

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
  deliver({ event: "sessions_list", sessions: [ONE] });
  await settle();
}

async function ask(text = "how is the book?") {
  act(() => {
    chat().sendMessage("s1", text);
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.last = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  listConversations.mockResolvedValue([]);
  getConversation.mockResolvedValue({ meta: {}, turns: [] });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("a sent message", () => {
  it("is awaiting from the send until the answer starts", async () => {
    await arrive();
    expect(chat().isSlotAwaiting("s1")).toBe(false);
    await ask();
    expect(chat().isSlotAwaiting("s1")).toBe(true);

    deliver({ event: "text_chunk", slot_id: "s1", text: "Thin." });
    expect(chat().isSlotAwaiting("s1")).toBe(false);
    expect(chat().isSlotStreaming("s1")).toBe(true);
  });

  it("stops awaiting when stopped before its first token", async () => {
    await arrive();
    await ask();
    act(() => chat().abortPrompt("s1"));
    expect(chat().isSlotAwaiting("s1")).toBe(false);
    // The transcript still ends on the user's words; that no longer matters.
    expect(chat().slots[0].messages.at(-1)?.role).toBe("user");
  });

  it("stops awaiting when the turn ends without a word", async () => {
    await arrive();
    await ask();
    deliver({ event: "prompt_done", slot_id: "s1" });
    expect(chat().isSlotAwaiting("s1")).toBe(false);
  });

  it("stops awaiting when the turn fails", async () => {
    await arrive();
    await ask();
    deliver({ event: "error", slot_id: "s1", message: "boom" });
    expect(chat().isSlotAwaiting("s1")).toBe(false);
  });
});
