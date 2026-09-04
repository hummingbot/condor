/**
 * Every messages-only update goes through one helper (READ-329).
 *
 * `handleEvent` used to write out `slots.map(s => s.info.slot_id === id ? …)`
 * once per event, so "which conversation does this belong to" and "what does a
 * system divider look like" were decided eight times over. These pin the two
 * facts that consolidation must not change, and that a hand-rolled copy could
 * quietly get wrong again: an event touches the slot it names and no other, and
 * a reload, a routine's note and a secret notice produce the *same* shaped
 * entry — differing only in their words and their kind.
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

/** Two conversations open at once — the whole point of keying by slot. */
const ONE = { slot_id: "s1", conversation_id: "c1", agent_key: "k", alive: true };
const TWO = { slot_id: "s2", conversation_id: "c2", agent_key: "k", alive: true };

const slot = (id: string) => chat().slots.find((s) => s.info.slot_id === id)!;
const messages = (id: string) => slot(id).messages;

/** Deliver a frame and let React commit it. */
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
  deliver({ event: "sessions_list", sessions: [ONE, TWO] });
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
  // An empty transcript never wipes the screen, so both slots start blank and
  // every message below is one this test put there.
  getConversation.mockResolvedValue({ meta: {}, turns: [] });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("a system note, whatever raised it", () => {
  it("has one shape: only the words and the kind differ", async () => {
    await arrive();

    deliver({ event: "reload", slot_id: "s1", parts: ["model", "tools"] });
    deliver({
      event: "system_note",
      slot_id: "s1",
      text: "Routine finished",
      kind: "routine",
    });
    deliver({ event: "secret_notice", slot_id: "s1", kind: "mnemonic" });

    const notes = messages("s1");
    expect(notes).toHaveLength(3);
    expect(notes.map((m) => m.kind)).toEqual([
      "reload",
      "routine",
      "secret_notice",
    ]);
    expect(notes[0].text).toBe(
      "Reloaded to apply configuration changes (model, tools)",
    );
    expect(notes[1].text).toBe("Routine finished");
    expect(notes[2].text).toContain("A recovery phrase was removed");
    for (const m of notes) {
      expect(m.role).toBe("system");
      expect(m.toolCalls).toEqual([]);
      expect(typeof m.ts).toBe("number");
      expect(m.open).toBeUndefined();
    }
    // Distinct ids, or React would key two dividers the same.
    expect(new Set(notes.map((m) => m.id)).size).toBe(3);
    // The other conversation heard nothing.
    expect(messages("s2")).toEqual([]);
  });

  it("is what a stream error becomes too, not an assistant bubble (CORR-325)", async () => {
    await arrive();
    const boom = "Connection to the agent subprocess was lost.\nNothing was sent.";
    deliver({ event: "error", slot_id: "s1", message: boom });

    const [note] = messages("s1");
    // The recorder writes exactly this shape for a prompt that failed before
    // producing anything, so the live frame and the reloaded turn are one
    // rendering rather than a `⚠️` bubble that becomes a divider on reload.
    expect(note.role).toBe("system");
    expect(note.kind).toBe("error");
    // The backend's own words, unadorned: the glyph and the label belong to
    // the renderer, and the transcript on disk carries no prefix.
    expect(note.text).toBe(boom);
    expect(messages("s2")).toEqual([]);
  });

  it("carries no kind when the backend named none", async () => {
    await arrive();
    deliver({ event: "system_note", slot_id: "s1", text: "Something happened" });
    expect(messages("s1")).toHaveLength(1);
    expect(messages("s1")[0].kind).toBeUndefined();
  });

  it("is not written at all for an empty note or an unknown secret kind", async () => {
    await arrive();
    deliver({ event: "system_note", slot_id: "s1", text: "" });
    deliver({ event: "secret_notice", slot_id: "s1", kind: "not-a-kind" });
    expect(messages("s1")).toEqual([]);
  });

  it("goes nowhere when the slot it names is not open", async () => {
    await arrive();
    deliver({ event: "reload", slot_id: "ghost", parts: ["model"] });
    expect(messages("s1")).toEqual([]);
    expect(messages("s2")).toEqual([]);
  });
});

/** Put one streamed bubble with a live tool call into `slotId`. */
function streamWithPendingCall(slotId: string, callId: string) {
  deliver({ event: "text_chunk", slot_id: slotId, text: "working" });
  deliver({
    event: "tool_call",
    slot_id: slotId,
    tool_call_id: callId,
    title: "run_code",
    status: "in_progress",
  });
}

describe("a per-slot transcript update", () => {
  it("settles only the conversation whose prompt ended", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");
    streamWithPendingCall("s2", "t2");

    deliver({ event: "prompt_done", slot_id: "s1" });

    expect(messages("s1")[0].toolCalls[0].status).toBe("completed");
    // s2 is still mid-answer; its spinner must keep spinning.
    expect(messages("s2")[0].toolCalls[0].status).toBe("in_progress");
    expect(chat().isSlotStreaming("s1")).toBe(false);
    expect(chat().isSlotStreaming("s2")).toBe(true);
  });

  it("leaves a refused call refused when the prompt ends (CORR-324)", async () => {
    await arrive();
    deliver({ event: "text_chunk", slot_id: "s1", text: "working" });
    // What the permission gate emits when it says no. No further update for
    // this id ever arrives — the bridge `continue`s past the call.
    deliver({
      event: "tool_call",
      slot_id: "s1",
      tool_call_id: "t1",
      title: "create_lp_executor",
      status: "blocked",
    });

    deliver({ event: "prompt_done", slot_id: "s1" });

    // The settle pass used to rewrite this to "completed", telling the user a
    // tool ran that they had explicitly refused.
    expect(messages("s1")[0].toolCalls[0].status).toBe("blocked");
  });

  it("routes a tool status to the call that owns it, in its own slot", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");
    streamWithPendingCall("s2", "t2");

    deliver({
      event: "tool_call_update",
      slot_id: "s1",
      tool_call_id: "t1",
      status: "failed",
    });

    expect(messages("s1")[0].toolCalls[0].status).toBe("failed");
    expect(messages("s2")[0].toolCalls[0].status).toBe("in_progress");
  });

  it("marks the interrupted answer in one conversation only", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");
    streamWithPendingCall("s2", "t2");

    deliver({ event: "prompt_interrupted", slot_id: "s1" });

    expect(messages("s1")[0].interrupted).toBe(true);
    expect(messages("s1")[0].toolCalls[0].status).toBe("completed");
    expect(messages("s2")[0].interrupted).toBeUndefined();
    expect(messages("s2")[0].toolCalls[0].status).toBe("in_progress");
  });

  it("settles the slot the user aborted, and no other", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");
    streamWithPendingCall("s2", "t2");

    act(() => {
      chat().abortPrompt("s2");
    });

    expect(messages("s2")[0].toolCalls[0].status).toBe("completed");
    expect(messages("s1")[0].toolCalls[0].status).toBe("in_progress");
  });

  it("stamps a streamed bubble with the slot's own agent", async () => {
    await arrive();
    deliver({
      event: "session_started",
      slot_id: "s1",
      conversation_id: "c1",
      agent_key: "k",
      agent_slug: "brigado",
    });
    streamWithPendingCall("s1", "t1");
    streamWithPendingCall("s2", "t2");

    // The updater reads the slot it is writing into, not whichever one is
    // active — the two bubbles must not be credited to the same agent.
    expect(messages("s1")[0].agentSlug).toBe("brigado");
    expect(messages("s2")[0].agentSlug).toBe("");
  });
});
