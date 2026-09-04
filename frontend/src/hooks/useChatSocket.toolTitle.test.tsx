/**
 * The name of a tool that is only named on its update (CORR-327).
 *
 * The ACP adapter announces a call before it knows what it is: the `tool_call`
 * frame can carry no title at all and the real one arrives on the following
 * `tool_call_update`, beside the arguments. The recorder folds that late title
 * into the transcript, so the row reads correctly once the page is reloaded —
 * but the live handler merged only `status`, so the same row read the generic
 * "tool" while it ran. A view that only heals on reload is the worst shape a
 * disagreement can have, and it is the shape CORR-323 and CORR-325 were filed
 * for too.
 *
 * So the two paths are pinned against each other here, the way the run-order
 * tests pin theirs: watching a call live and re-reading the same turn cold must
 * name it the same thing. The asymmetry is pinned as well — an update patches
 * the name only when it carries one, so a blank or placeholder title can never
 * overwrite a name the announcement already got right.
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
import { formatToolName } from "@/lib/formatters";

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

/** What the row actually renders — the only claim the reader ever sees. */
const rendered = () => calls().map((tc) => formatToolName(tc.title));

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

describe("a tool named only on its update", () => {
  it("reads its real name live, without a reload", async () => {
    await open();

    // The announcement the ACP bridge actually sends for a call whose
    // `rawInput` and title land late: an id and a status, nothing else.
    frame({ event: "tool_call", tool_call_id: "t1", status: "pending" });
    expect(rendered()).toEqual(["tool"]);

    frame({
      event: "tool_call_update",
      tool_call_id: "t1",
      title: "mcp__condor__run_code",
      status: "in_progress",
    });

    expect(rendered()).toEqual(["run code"]);
    expect(calls()[0].status).toBe("in_progress");
  });

  it("agrees with the same turn after a reload", async () => {
    await open();

    frame({ event: "tool_call", tool_call_id: "t1", status: "pending" });
    frame({
      event: "tool_call_update",
      tool_call_id: "t1",
      title: "mcp__condor__run_code",
      status: "completed",
    });
    frame({ event: "prompt_done" });

    const live = calls();

    // What the recorder writes for exactly those frames: `fold_tool_call_event`
    // patches the name from the update, so the record holds the real one.
    act(() => root.unmount());
    stored({
      role: "assistant",
      text: "",
      ts: 1,
      tool_calls: [{ id: "t1", title: "mcp__condor__run_code", status: "completed" }],
    });
    root = createRoot(container);
    await open();

    expect(calls()).toEqual(live);
    expect(rendered()).toEqual(["run code"]);
  });

  it("keeps a name that arrived late across the settle at the end of the turn", async () => {
    await open();

    frame({ event: "tool_call", tool_call_id: "t1", status: "pending" });
    frame({
      event: "tool_call_update",
      tool_call_id: "t1",
      title: "get_prices",
    });

    // An update carrying a name but no status says nothing about progress, so
    // the call is still in flight and the settle pass is what finishes it —
    // the refusal contract that pass enforces is untouched by any of this.
    expect(calls()[0].status).toBe("pending");

    frame({ event: "prompt_done" });

    expect(rendered()).toEqual(["get prices"]);
    expect(calls()[0].status).toBe("completed");
  });
});

describe("an update that names nothing", () => {
  it("leaves the announced name standing when it carries no title", async () => {
    await open();

    frame({
      event: "tool_call",
      tool_call_id: "t1",
      title: "mcp__condor__manage_routines",
      status: "pending",
    });
    frame({ event: "tool_call_update", tool_call_id: "t1", status: "completed" });

    expect(rendered()).toEqual(["manage routines"]);
  });

  it("does not clobber a good name with a placeholder", async () => {
    await open();

    frame({
      event: "tool_call",
      tool_call_id: "t1",
      title: "mcp__condor__manage_routines",
      status: "pending",
    });

    // Every spelling of "no name" a real adapter has sent: blank, whitespace, a
    // JSON-encoded JavaScript `undefined` (a real transcript holds five), and a
    // field that was never a string. None of them is an instruction to forget
    // the name the announcement already got right.
    for (const title of ["", "   ", "undefined", '"undefined"', null, 42, { a: 1 }]) {
      frame({
        event: "tool_call_update",
        tool_call_id: "t1",
        title,
        status: "in_progress",
      });
      expect(rendered()).toEqual(["manage routines"]);
    }
  });

  it("still reads as 'tool' when neither frame ever named it", async () => {
    await open();

    frame({ event: "tool_call", tool_call_id: "t1", status: "pending" });
    frame({ event: "tool_call_update", tool_call_id: "t1", status: "completed" });

    expect(rendered()).toEqual(["tool"]);
    expect(calls()[0].status).toBe("completed");
  });
});
