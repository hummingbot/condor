/**
 * What arriving at the chat workspace spawns.
 *
 * The workspace is warm on arrival or the first message pays for the spawn,
 * and which of the two happens is decided in one place — `prewarmLatest`. Its
 * two branches are easy to break silently: nothing on screen says whether the
 * empty composer is backed by a session, so a regression only shows up as the
 * first message taking seconds. These pin both branches to the frame they put
 * on the wire.
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

const listConversations = vi.fn();
const getSessionOptions = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listConversations: (...args: unknown[]) => listConversations(...args),
    getSessionOptions: () => getSessionOptions(),
    // A resumed conversation hydrates its transcript over HTTP; empty is fine.
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

/** Every frame the hook put on the wire, parsed. */
let sent: Record<string, unknown>[] = [];
class FakeSocket {
  /** The most recently constructed one, which is the hook's. */
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
  /** Deliver a server frame the way the socket would. */
  deliver(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

/** The socket the hook opened, so the test can play the server. */
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

/** Let the prewarm's fetches settle — it awaits two promises at most. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

/**
 * Arrive at the workspace: connect, learn the roster is empty, and say this
 * surface is a chat — the exact order `AppShell` and `AgentChatTab` produce.
 */
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
    sock().deliver({ event: "sessions_list", sessions: [] });
  });
  act(() => {
    chat().enablePrewarm();
  });
  await settle();
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

describe("prewarm on arrival at the workspace", () => {
  it("resumes the thread the user was last in", async () => {
    listConversations.mockResolvedValue([
      {
        id: "conv-1",
        agent_key: "claude-code",
        server_name: "brigado",
        agent_slug: "backpack_mm",
      },
    ]);

    await arrive();

    expect(sent).toContainEqual({
      action: "resume_conversation",
      conversation_id: "conv-1",
    });
    expect(sent.some((f) => f.action === "start_session")).toBe(false);
    expect(getSessionOptions).not.toHaveBeenCalled();
  });

  it("spawns an empty chat when there is nothing to resume", async () => {
    listConversations.mockResolvedValue([]);
    getSessionOptions.mockResolvedValue({
      agents: [],
      custom_providers: [],
      agent_bindings: [],
      default_agent: "claude-code:opus",
    });

    await arrive();

    const start = sent.find((f) => f.action === "start_session");
    expect(start).toBeDefined();
    // The user's own default brain, not the backend's — an empty `agent_key`
    // would silently resolve to DEFAULT_AGENT instead of what they last picked.
    expect(start!.agent_key).toBe("claude-code:opus");
    // Born on the selected trading server, like a chat the composer starts.
    expect(start!.server_name).toBe("moneymaker");
    // Unbound: the specialist is the rail's question, and nobody asked it yet.
    expect(start!.agent_slug).toBeUndefined();
  });

  it("leaves a live session alone", async () => {
    listConversations.mockResolvedValue([]);
    getSessionOptions.mockResolvedValue({ default_agent: "claude-code" });

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
      chat().enablePrewarm();
    });
    act(() => {
      sock().deliver({
        event: "sessions_list",
        sessions: [{ slot_id: "s1", conversation_id: "c1", agent_key: "k" }],
      });
    });
    await settle();

    expect(sent.some((f) => f.action === "start_session")).toBe(false);
    expect(sent.some((f) => f.action === "resume_conversation")).toBe(false);
    expect(listConversations).not.toHaveBeenCalled();
  });

  it("does not spawn for a surface that is not a chat", async () => {
    listConversations.mockResolvedValue([]);
    getSessionOptions.mockResolvedValue({ default_agent: "claude-code" });

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
      sock().deliver({ event: "sessions_list", sessions: [] });
    });
    await settle();

    // `enablePrewarm` was never called: the shell holds this socket open on
    // /portfolio too, and a user who only opened that gets no subprocess.
    expect(sent.some((f) => f.action === "start_session")).toBe(false);
  });
});
