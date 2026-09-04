/**
 * A turn that only ever produced reasoning survives a reload (ARCH-328).
 *
 * "Is this turn worth showing" was written twice: once in `Recorder.flush`
 * (condor/runtime/conversations.py), which writes an assistant turn when there
 * is text **or** tools **or** reasoning, and once in `turnsToMessages`, which
 * omitted the reasoning clause. So a turn the user stopped while the model was
 * still thinking was rendered live, written to disk, and replayed into the
 * resumed session's context — and then dropped the next time the page loaded.
 * The transcript silently lost a turn the user had just been reading. These
 * pin the two predicates to the same answer.
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

/** The transcript of the slot under test, as rendered. */
const messages = () =>
  chat().slots.find((s) => s.info.slot_id === "s1")?.messages ?? [];

/** What the server has on disk for this conversation. */
function stored(...turns: Record<string, unknown>[]) {
  getConversation.mockResolvedValue({ meta: {}, turns });
}

/** Open the page cold, exactly as a reload does, and hydrate from the record. */
async function reload() {
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

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubGlobal("WebSocket", FakeSocket);
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

describe("a turn that produced only reasoning", () => {
  it("comes back on reload, as it was on screen live", async () => {
    // Stopped while the model was still thinking: no answer text, no tools,
    // but the reasoning the user was reading. The recorder wrote it, because
    // `flush` writes a turn for `text or tools or thought`.
    stored(
      { role: "user", text: "should I widen the range?", ts: 1, tool_calls: [] },
      {
        role: "assistant",
        text: "",
        thought: "The book is thin on the bid side, so widening would...",
        ts: 2,
        tool_calls: [],
        stop_reason: "cancelled",
      },
    );

    await reload();

    expect(messages()).toHaveLength(2);
    const turn = messages()[1];
    expect(turn.role).toBe("assistant");
    expect(turn.text).toBe("");
    expect(turn.thought).toContain("The book is thin");
    // It was cut short, and it says so — the same seam the live frame drew.
    expect(turn.interrupted).toBe(true);
  });

  it("is kept whether or not the stream reported an ending", async () => {
    stored({ role: "assistant", text: "", thought: "Checking the fleet.", ts: 1, tool_calls: [] });

    await reload();

    expect(messages()).toHaveLength(1);
    expect(messages()[0].thought).toBe("Checking the fleet.");
  });
});

describe("a turn holding nothing at all", () => {
  it("is still skipped — an empty bubble is noise, not a lost turn", async () => {
    stored(
      { role: "user", text: "audit the fleet", ts: 1, tool_calls: [] },
      { role: "assistant", text: "", thought: "", ts: 2, tool_calls: [] },
    );

    await reload();

    expect(messages().map((m) => m.text)).toEqual(["audit the fleet"]);
  });
});
