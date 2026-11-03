/**
 * A cut-off answer still reads as cut off after a reload.
 *
 * Live, the `prompt_interrupted` frame marks the partial so it does not read
 * as a finished answer that trailed off. That mark used to die with the page:
 * the transcript records *why* the stream ended (`TurnEntry.stop_reason`), and
 * hydration threw it away, so the worst case — an answer the user watched get
 * cut off — came back looking like the agent's considered reply. These pin the
 * replay side of that seam.
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

describe("a truncated answer, after a reload", () => {
  it("still says it was interrupted", async () => {
    // The user hit stop half a sentence in. The recorder wrote why.
    stored(
      { role: "user", text: "audit the fleet", ts: 1, tool_calls: [] },
      {
        role: "assistant",
        text: "The fleet looks",
        ts: 2,
        tool_calls: [],
        stop_reason: "cancelled",
      },
    );

    await reload();

    expect(messages().map((m) => m.text)).toEqual([
      "audit the fleet",
      "The fleet looks",
    ]);
    expect(messages()[1].interrupted).toBe(true);
  });

  it.each(["timeout", "error", "disconnected"])(
    "says so for a stream the backend ended with %s too",
    async (reason) => {
      stored({
        role: "assistant",
        text: "half an answer",
        ts: 1,
        tool_calls: [],
        stop_reason: reason,
      });

      await reload();

      expect(messages()[0].interrupted).toBe(true);
    },
  );

  it("leaves a finished answer unmarked", async () => {
    stored({
      role: "assistant",
      text: "the whole answer",
      ts: 1,
      tool_calls: [],
      stop_reason: "end_turn",
    });

    await reload();

    expect(messages()[0].interrupted).toBeUndefined();
  });

  it("does not guess when the stream never reported an ending", async () => {
    // `""` is the abandoned generator and every turn written before the field
    // existed. Unknown is not truncated, and it is not finished either — so it
    // gets no seam rather than a fabricated one.
    stored(
      { role: "assistant", text: "recorded before the field", ts: 1, tool_calls: [] },
      {
        role: "assistant",
        text: "generator abandoned",
        ts: 2,
        tool_calls: [],
        stop_reason: "",
      },
    );

    await reload();

    expect(messages().map((m) => m.interrupted)).toEqual([undefined, undefined]);
  });

  it("never marks a system divider", async () => {
    // `stop_reason` describes a model stream; a divider has none to describe,
    // and the seam belongs to a bubble.
    stored({
      role: "system",
      text: "switched to fable",
      kind: "switch",
      ts: 1,
      tool_calls: [],
      stop_reason: "cancelled",
    });

    await reload();

    expect(messages()[0].interrupted).toBeUndefined();
  });
});
