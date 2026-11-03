/**
 * A tab whose session was reaped while the socket was down (CORR-265).
 *
 * The roster replaces the tab strip rather than merging into it, on purpose: a
 * conversation killed from Telegram or the REST API has to disappear here, with
 * no cross-talk. That makes the roster the only thing keeping a tab on screen —
 * so when the backend stopped listing a slot whose subprocess had been reaped,
 * the user's open conversation went with it, messages and all.
 *
 * These pin the client half of the fix: a slot listed with `alive: false` is
 * still a tab, it still hydrates, and typing into it goes out on the wire so the
 * backend can reattach — while a slot that is simply absent still goes away.
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

/** The slot under test, as the backend lists it while its session is up. */
const LIVE = {
  slot_id: "s1",
  conversation_id: "c1",
  agent_key: "k",
  alive: true,
};

/** The same slot after the idle sweep took its subprocess. */
const REAPED = { ...LIVE, alive: false };

const slot = () => chat().slots.find((s) => s.info.slot_id === "s1");
const messages = () => slot()?.messages ?? [];

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
  act(() => {
    sock().deliver({ event: "sessions_list", sessions: [LIVE] });
  });
  await settle();
}

/** Drop the socket and bring it back, exactly as a woken laptop would. */
function reconnect() {
  act(() => {
    sock().readyState = FakeSocket.CLOSED;
    sock().onclose?.();
  });
  act(() => {
    chat().connect();
    sock().onopen?.();
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.last = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  listConversations.mockResolvedValue([]);
  getConversation.mockResolvedValue({
    meta: {},
    turns: [
      {
        role: "user",
        text: "my favourite pair is SOL-USDC",
        ts: "1",
        tool_calls: [],
      },
    ],
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("a slot the backend reaped", () => {
  it("keeps its tab and its messages across the reconnect", async () => {
    await arrive();
    expect(messages().map((m) => m.text)).toEqual([
      "my favourite pair is SOL-USDC",
    ]);

    reconnect();
    act(() => {
      sock().deliver({ event: "sessions_list", sessions: [REAPED] });
    });
    await settle();

    expect(slot()).toBeTruthy();
    expect(slot()!.info.alive).toBe(false);
    expect(messages().map((m) => m.text)).toEqual([
      "my favourite pair is SOL-USDC",
    ]);
    // Still the tab in front of the user, not a stranded background one.
    expect(chat().activeSlotId).toBe("s1");
  });

  it("sends the next message so the backend can reattach it", async () => {
    await arrive();
    reconnect();
    act(() => {
      sock().deliver({ event: "sessions_list", sessions: [REAPED] });
    });
    await settle();

    act(() => {
      chat().sendMessage("s1", "what was it?");
    });
    await settle();

    // Sent, not swallowed: a dead subprocess is the backend's to respawn, and
    // it answers this very message on the conversation the tab already shows.
    const sends = sock().sent.filter((f) => f.action === "send_message");
    expect(sends).toHaveLength(1);
    expect(sends[0].slot_id).toBe("s1");
    expect(sends[0].text).toBe("what was it?");
    // No second conversation asked for beside the one that is right there.
    expect(sock().sent.some((f) => f.action === "start_session")).toBe(false);

    // ...and the reattach lands back on the same tab, keeping its scrollback.
    act(() => {
      sock().deliver({
        event: "session_started",
        slot_id: "s1",
        conversation_id: "c1",
        agent_key: "k",
        restored: true,
      });
    });
    await settle();

    expect(chat().slots).toHaveLength(1);
    expect(messages().map((m) => m.text)).toEqual([
      "my favourite pair is SOL-USDC",
      "what was it?",
    ]);
    // A `session_started` is a live subprocess by definition, and the reattach
    // path emits it with no `alive` key — so the slot has to be marked alive
    // here, or the tab's dot stays detached until the next roster (CORR-295).
    expect(slot()!.info.alive).toBe(true);
  });

  it("still lets go of a slot the backend has stopped listing", async () => {
    await arrive();

    // Destroyed elsewhere — Telegram, the REST API — rather than reaped, so the
    // runtime keeps no memory of it and leaves it off the roster. The roster
    // replaces rather than merges precisely so this tab goes away.
    reconnect();
    act(() => {
      sock().deliver({
        event: "sessions_list",
        sessions: [
          { slot_id: "s2", conversation_id: "c2", agent_key: "k", alive: true },
        ],
      });
    });
    await settle();

    expect(chat().slots.map((s) => s.info.slot_id)).toEqual(["s2"]);
  });
});
