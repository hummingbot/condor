/**
 * A `start_error` note's own resolution, through a retry from its picker.
 *
 * The note used to be write-once: a spawn that never came up left it sitting
 * on screen as a live failure forever, even after `switchBrain` quietly fixed
 * the slot underneath it — "Use Sonnet" worked, the chat answered, and the
 * note above it kept reading exactly as alarming as it had a moment earlier.
 * `switchBrain`'s optional `startErrorMessageId` closes that loop: it marks
 * the one note the retry came from `retrying` before the request, then either
 * `resolved` (naming who answered) or back to its plain failure shape with
 * the new error — never left both stale and unresolved at once.
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
const switchSession = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listConversations: (...args: unknown[]) => listConversations(...args),
    getSessionOptions: () => Promise.resolve({ default_agent: "claude-code" }),
    getConversation: (...args: unknown[]) => getConversation(...args),
    switchSession: (...args: unknown[]) => switchSession(...args),
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

const ONE = {
  slot_id: "s1",
  conversation_id: "c1",
  agent_key: "openrouter:x/y",
  agent_slug: "backpack_mm",
  alive: true,
};

const slot = () => chat().slots.find((s) => s.info.slot_id === "s1")!;
const messages = () => slot().messages;
const startErrorNote = () => messages().find((m) => m.kind === "start_error")!;

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

/** The spawn that was about to use an unconfigured provider dies at start. */
async function failStart() {
  deliver({
    event: "error",
    slot_id: "s1",
    message: "OPENROUTER_API_KEY is not set.",
    phase: "start",
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
  switchSession.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("a start_error note's own retry", () => {
  it("stamps what it was attempting at creation, from the slot's own attempt", async () => {
    await arrive();
    await failStart();

    expect(startErrorNote().attemptedAgentKey).toBe("openrouter:x/y");
    expect(startErrorNote().attemptedAgentSlug).toBe("backpack_mm");
  });

  it("marks the note retrying the instant the retry starts, naming the target", async () => {
    await arrive();
    await failStart();
    const noteId = startErrorNote().id;

    let resolveSwitch!: (v: { session: Record<string, unknown> }) => void;
    switchSession.mockReturnValue(
      new Promise((resolve) => {
        resolveSwitch = resolve;
      }),
    );

    let switched!: Promise<void>;
    act(() => {
      switched = chat().switchBrain("s1", { agentKey: "claude-sonnet-5" }, noteId);
    });

    const note = startErrorNote();
    expect(note.retrying).toBe(true);
    expect(note.retryTargetKey).toBe("claude-sonnet-5");
    // The failure text is still there underneath — only the render layer
    // hides it — but nothing about the note claims success yet.
    expect(note.resolved).toBeFalsy();

    resolveSwitch({
      session: {
        agent_key: "claude-sonnet-5",
        agent_slug: "backpack_mm",
        label: "Backpack MM · Sonnet",
        server_pinned: false,
      },
    });
    await act(() => switched);
  });

  it("resolves the note onto the new identity on a successful retry", async () => {
    await arrive();
    await failStart();
    const noteId = startErrorNote().id;

    switchSession.mockResolvedValue({
      session: {
        agent_key: "claude-sonnet-5",
        agent_slug: "backpack_mm",
        label: "Backpack MM · Sonnet",
        server_pinned: false,
      },
    });

    await act(async () => {
      await chat().switchBrain("s1", { agentKey: "claude-sonnet-5" }, noteId);
    });

    const note = startErrorNote();
    expect(note.resolved).toBe(true);
    expect(note.resolvedAgentKey).toBe("claude-sonnet-5");
    expect(note.resolvedAgentSlug).toBe("backpack_mm");
    expect(note.retrying).toBe(false);
    // The failed attempt's own words are untouched — the render layer folds
    // them away, but they are not rewritten or dropped.
    expect(note.text).toBe("OPENROUTER_API_KEY is not set.");
    // The slot itself moved too, same as any other switch.
    expect(slot().info.agent_key).toBe("claude-sonnet-5");

    // No generic "Could not switch" path was taken.
    expect(chat().slots.length).toBe(1);
  });

  it("falls back to the plain failure shape, with the new error, when the retry fails too", async () => {
    await arrive();
    await failStart();
    const noteId = startErrorNote().id;

    switchSession.mockRejectedValue(new Error("claude-sonnet-5 is also not configured"));

    await act(async () => {
      await expect(
        chat().switchBrain("s1", { agentKey: "claude-sonnet-5" }, noteId),
      ).rejects.toThrow();
    });

    const note = startErrorNote();
    expect(note.resolved).toBe(false);
    expect(note.resolvedAgentKey).toBeUndefined();
    expect(note.retrying).toBe(false);
    expect(note.text).toBe("claude-sonnet-5 is also not configured");
    // The note now names the model that *this* attempt just failed on, not
    // the original one — a second switch from here must offer to retry that.
    expect(note.attemptedAgentKey).toBe("claude-sonnet-5");
    // The identity never changed in this retry (only the model did), so the
    // bound Agent it belongs to is carried forward rather than dropped.
    expect(note.attemptedAgentSlug).toBe("backpack_mm");
  });

  it("does not append a message for a model-only retry — it edits the one note in place", async () => {
    await arrive();
    await failStart();
    const before = messages().length;
    const noteId = startErrorNote().id;

    switchSession.mockResolvedValue({
      session: {
        agent_key: "claude-sonnet-5",
        agent_slug: "backpack_mm",
        label: "Backpack MM · Sonnet",
        server_pinned: false,
      },
    });

    await act(async () => {
      await chat().switchBrain("s1", { agentKey: "claude-sonnet-5" }, noteId);
    });

    expect(messages().length).toBe(before);
  });
});
