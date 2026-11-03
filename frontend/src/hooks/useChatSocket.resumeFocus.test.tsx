/**
 * Resuming a conversation from a background surface (CORR-257).
 *
 * `resumeConversation` was written for the workspace's rail, where "open this
 * conversation" and "make it the active tab" are the same gesture, so it set
 * `activeSlotId` unconditionally. The bubble resumes from a page that is not
 * the chat — it reattaches to the agent's newest conversation on `/agents/X`
 * — and there that focus is theft: it decides what the workspace at `/` shows
 * next, without the user ever asking. `startSession` already carried the
 * `{ focus: false }` escape hatch for exactly this; these pin the matching one
 * on the resume, including the `session_started` that lands a beat later with
 * no `client_ref` to recognise it by.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: {
    listConversations: () => Promise.resolve([]),
    getSessionOptions: () => Promise.resolve({ default_agent: "claude-code" }),
    getConversation: () => Promise.resolve({ meta: {}, turns: [] }),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ token: "jwt", user: { id: 7 } }),
}));

const { useChatSocket } = await import("./useChatSocket");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let sent: Record<string, unknown>[] = [];
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
  send(raw: string) {
    sent.push(JSON.parse(raw));
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

/** Connected, with whatever sessions the server says are already live. */
function connect(sessions: Record<string, unknown>[] = []) {
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <Harness />
      </QueryClientProvider>,
    );
  });
  act(() => {
    chat().connect();
    sock().onopen?.();
  });
  act(() => {
    sock().deliver({ event: "sessions_list", sessions });
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("WebSocket", FakeSocket);
  sent = [];
  FakeSocket.last = null;
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

describe("resumeConversation({ focus: false })", () => {
  it("attaches the conversation without making it the active tab", () => {
    connect();

    act(() => {
      chat().resumeConversation("conv-1", { agent_slug: "x" }, { focus: false });
    });

    // The tab exists and the spawn is on the wire — only the focus is withheld.
    expect(chat().slots.map((s) => s.info.slot_id)).toEqual(["conv-1"]);
    expect(sent).toContainEqual({
      action: "resume_conversation",
      conversation_id: "conv-1",
    });
    expect(chat().activeSlotId).toBeNull();
  });

  it("keeps the focus when the spawn reports back", () => {
    connect();

    act(() => {
      chat().resumeConversation("conv-1", { agent_slug: "x" }, { focus: false });
    });
    act(() => {
      // A resume's session_started carries no client_ref, so the conversation
      // id is the only thing that can mark it unfocused.
      sock().deliver({
        event: "session_started",
        slot_id: "conv-1",
        conversation_id: "conv-1",
        agent_key: "claude-code",
        agent_slug: "x",
        restored: true,
      });
    });

    expect(chat().activeSlotId).toBeNull();
  });

  it("does not disturb the tab the workspace is already on", () => {
    connect([
      { slot_id: "s1", agent_key: "k", last_prompt_at: "2026-08-01T00:00:00Z" },
      { slot_id: "s2", agent_key: "k", last_prompt_at: "2026-08-02T00:00:00Z" },
    ]);
    expect(chat().activeSlotId).toBe("s2");

    act(() => {
      chat().resumeConversation("s1", {}, { focus: false });
    });

    expect(chat().activeSlotId).toBe("s2");
  });
});

describe("resumeConversation by default", () => {
  it("still focuses what it opens, the way the rail expects", () => {
    connect();

    act(() => {
      chat().resumeConversation("conv-1", { agent_slug: "x" });
    });
    expect(chat().activeSlotId).toBe("conv-1");

    act(() => {
      sock().deliver({
        event: "session_started",
        slot_id: "conv-1",
        conversation_id: "conv-1",
        agent_key: "claude-code",
        agent_slug: "x",
        restored: true,
      });
    });
    expect(chat().activeSlotId).toBe("conv-1");
  });

  it("adopts an unfocused-then-refocused conversation only once", () => {
    // The set must not grow for the life of the tab: `delete` is the
    // membership test, so a second resume of the same conversation is a
    // normal, focusing one.
    connect();

    act(() => {
      chat().resumeConversation("conv-1", {}, { focus: false });
    });
    act(() => {
      sock().deliver({
        event: "session_started",
        slot_id: "conv-1",
        conversation_id: "conv-1",
        agent_key: "claude-code",
        restored: true,
      });
    });
    expect(chat().activeSlotId).toBeNull();

    // "Back to chat", or the rail: the same conversation, asked for properly.
    act(() => {
      chat().resumeConversation("conv-1", {});
    });
    expect(chat().activeSlotId).toBe("conv-1");
  });
});
