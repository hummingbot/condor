/**
 * A slot knows what its conversation has cost (FEAT-120).
 *
 * The stored total is the truth — it survives a reload and it includes turns
 * answered from Telegram or another tab — so a slot is seeded from it whenever
 * the transcript is read, and each `prompt_done` adds the turn it closes. These
 * pin both halves, and that a reconnect's re-read re-seeds rather than keeping
 * a figure only this tab believed.
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

/** The token total of the slot under test. */
const usageOf = () => chat().slots.find((s) => s.info.slot_id === "s1")?.usage;

const TURNS = [{ role: "user", text: "run the audit", ts: "1", tool_calls: [] }];

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
    meta: { usage: { input_tokens: 1000, output_tokens: 50, cost_usd: 0.1 } },
    turns: TURNS,
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("a conversation's token total", () => {
  it("is seeded from the stored total when the transcript hydrates", async () => {
    await arrive();

    expect(usageOf()?.total_tokens).toBe(1050);
    expect(usageOf()?.cost_usd).toBeCloseTo(0.1);
  });

  it("advances by what each turn cost", async () => {
    await arrive();

    act(() => {
      sock().deliver({
        event: "prompt_done",
        slot_id: "s1",
        stop_reason: "end_turn",
        usage: {
          input_tokens: 200,
          output_tokens: 10,
          cost_usd: 0.05,
          context_used: 5000,
          context_size: 200000,
        },
      });
    });

    expect(usageOf()?.total_tokens).toBe(1260);
    expect(usageOf()?.cost_usd).toBeCloseTo(0.15);
    expect(usageOf()?.context_used).toBe(5000);
    expect(usageOf()?.context_size).toBe(200000);
  });

  it("stays put on a prompt_done that carries no usage", async () => {
    await arrive();

    act(() => {
      sock().deliver({ event: "prompt_done", slot_id: "s1", stop_reason: "cancelled" });
    });

    expect(usageOf()?.total_tokens).toBe(1050);
  });

  it("re-seeds on a reconnect, so a turn answered elsewhere converges", async () => {
    await arrive();
    // Telegram answered a turn while this tab's socket was down.
    getConversation.mockResolvedValue({
      meta: { usage: { input_tokens: 5000, output_tokens: 100 } },
      turns: TURNS,
    });

    reconnect();
    act(() => {
      sock().deliver(roster());
    });
    await settle();

    expect(usageOf()?.total_tokens).toBe(5100);
  });

  it("shows nothing for a conversation older than the measurement", async () => {
    getConversation.mockResolvedValue({ meta: {}, turns: TURNS });

    await arrive();

    expect(usageOf()).toBeUndefined();
  });
});
