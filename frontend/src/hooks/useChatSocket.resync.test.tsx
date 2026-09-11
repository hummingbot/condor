/**
 * What a reconnect re-reads.
 *
 * The chat socket is the only delivery channel for an out-of-band note — a
 * delegation's outcome, a routine's result — and it is a fire-and-forget
 * broadcast: no queue, no replay, no ack. A tab whose socket was down for the
 * second one was pushed lost it, and nothing ever asked for it again, because
 * the transcript was read exactly once per slot per page load. These pin the
 * recovery path: a reconnect re-reads, and re-reading neither duplicates what
 * is on screen nor drops what arrived while the read was in flight.
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
  });
}

/** The one session the server knows about in these tests. */
const ROSTER = [{ slot_id: "s1", conversation_id: "c1", agent_key: "k" }];

const roster = () => ({ event: "sessions_list", sessions: ROSTER });

/** The transcript of the slot under test, as rendered. */
const messages = () =>
  chat().slots.find((s) => s.info.slot_id === "s1")?.messages ?? [];

/** Open the page and let the first roster hydrate the one live session. */
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
    sock().deliver(roster());
  });
  await settle();
}

/** Drop the socket and bring it back, exactly as the browser would. */
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
  getConversation.mockResolvedValue({
    meta: {},
    turns: [{ role: "user", text: "run the audit", ts: "1", tool_calls: [] }],
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("re-reading the transcript on a reconnect", () => {
  it("shows a note that landed while the socket was down", async () => {
    await arrive();
    expect(messages().map((m) => m.text)).toEqual(["run the audit"]);

    // While nobody was listening, the delegation finished and wrote its
    // outcome into the transcript. The push went to a socket that was gone.
    getConversation.mockResolvedValue({
      meta: {},
      turns: [
        { role: "user", text: "run the audit", ts: "1", tool_calls: [] },
        {
          role: "system",
          text: "the audit finished",
          kind: "delegation",
          ts: "2",
          tool_calls: [],
        },
      ],
    });

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual([
      "run the audit",
      "the audit finished",
    ]);
    expect(messages()[1].kind).toBe("delegation");
  });

  it("does not duplicate what a live push already put on screen", async () => {
    await arrive();

    // The note arrived over the wire this time, so it is on screen as a live
    // message *and* recorded server-side. A reconnect must not show it twice.
    act(() => {
      sock().deliver({
        event: "system_note",
        slot_id: "s1",
        text: "the audit finished",
        kind: "delegation",
      });
    });
    getConversation.mockResolvedValue({
      meta: {},
      turns: [
        { role: "user", text: "run the audit", ts: "1", tool_calls: [] },
        {
          role: "system",
          text: "the audit finished",
          kind: "delegation",
          ts: "2",
          tool_calls: [],
        },
      ],
    });

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual([
      "run the audit",
      "the audit finished",
    ]);
  });

  it("keeps a note that streamed in while the re-read was in flight", async () => {
    await arrive();

    let release: (v: unknown) => void = () => {};
    getConversation.mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    // The re-read is out but not back, and the wire delivers a fresh note.
    act(() => {
      sock().deliver({
        event: "system_note",
        slot_id: "s1",
        text: "and the deploy is green",
        kind: "routine",
      });
    });
    await act(async () => {
      release({
        meta: {},
        turns: [{ role: "user", text: "run the audit", ts: "1", tool_calls: [] }],
      });
      await Promise.resolve();
      await Promise.resolve();
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual([
      "run the audit",
      "and the deploy is green",
    ]);
  });

  it("reads the transcript exactly once on the first connect", async () => {
    await arrive();

    expect(getConversation).toHaveBeenCalledTimes(1);
    expect(getConversation).toHaveBeenCalledWith("c1");

    // A roster the client asked for itself is not a reconnect: no re-read.
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(getConversation).toHaveBeenCalledTimes(1);
  });

  it("leaves the transcript alone when the re-read fails", async () => {
    await arrive();

    getConversation.mockRejectedValue(new Error("gateway is down"));

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual(["run the audit"]);

    // And the chat still works: the next reconnect tries again rather than
    // treating the slot as never-hydrated and re-prepending the whole thing.
    getConversation.mockResolvedValue({
      meta: {},
      turns: [
        { role: "user", text: "run the audit", ts: "1", tool_calls: [] },
        { role: "system", text: "back up", kind: "delegation", ts: "2", tool_calls: [] },
      ],
    });
    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(messages().map((m) => m.text)).toEqual(["run the audit", "back up"]);
  });
});
