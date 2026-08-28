/**
 * The bubble picks up the agent's newest conversation when none is live
 * (CORR-257).
 *
 * The repro: the panel is left open on `/agents/X` and the page is reloaded.
 * Every live slot dies with the reload and the shell prewarms only at `/`, so
 * the bubble came back showing its empty hero over a conversation with X that
 * was sitting one API call away — and the first message minted a durable
 * second one, against the bubble's own "one conversation per bound agent"
 * invariant and against the session budget FEAT-059 rations.
 *
 * Pinned here is the whole gate, not just the happy path: the read only
 * happens with the panel open, only on an agent's page, only when there is
 * nothing live to adopt (CORR-255 still wins), and it never moves the
 * workspace's focus. An agent with no history still spawns on first send.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, ChatSlot, SlotInfo } from "@/hooks/useChatSocket";
import type { ConversationMeta } from "@/lib/api";

const chat = {
  slots: [] as ChatSlot[],
  resolveSlotId: (id: string) => id,
  isSlotStreaming: () => false,
  isSlotQueued: () => false,
  permissionFor: () => null,
  resolvePermission: vi.fn(),
  startSession: vi.fn(() => "spawned"),
  // Mirrors the real one closely enough for the bubble: the tab and its
  // transcript exist on this frame, the spawn happens behind them.
  resumeConversation: vi.fn(
    (id: string, meta?: Partial<SlotInfo>) => {
      chat.slots = [
        ...chat.slots,
        {
          info: {
            slot_id: id,
            agent_key: "",
            agent_slug: meta?.agent_slug || "",
          },
          messages: [message("the conversation from the server")],
        },
      ];
    },
  ),
  sendMessage: vi.fn(),
  abortPrompt: vi.fn(),
  setActiveSlotId: vi.fn(),
};

const listConversations = vi.fn();

vi.mock("@/hooks/useChat", () => ({
  useChat: () => chat,
  useSessionOptions: () => ({ defaultAgent: "claude", agents: [] }),
}));
vi.mock("@/hooks/useServer", () => ({ useServer: () => ({ server: "srv" }) }));
vi.mock("@/hooks/useStarters", () => ({ useStarters: () => [] }));
// Only the two reads the bubble makes are faked; the rest of the module is
// real, so `CHAT_SLUG` and the types keep their actual values.
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getAgents: () => Promise.resolve([]),
      listConversations: (...args: unknown[]) => listConversations(...args),
    },
  };
});

const { ChatBubble } = await import("./ChatBubble");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function message(text: string): ChatMessage {
  return { id: text, role: "assistant", text, toolCalls: [] };
}

function slot(slotId: string, agentSlug: string, text: string): ChatSlot {
  return {
    info: { slot_id: slotId, agent_key: "", agent_slug: agentSlug },
    messages: [message(text)],
  };
}

/** A conversation record as `/api/v1/conversations` returns it. */
function conversation(id: string, agentSlug: string): ConversationMeta {
  return {
    id,
    user_id: 1,
    surface: "web",
    title: id,
    agent_key: "claude-code",
    agent_slug: agentSlug,
    server_name: "srv",
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    turn_count: 2,
    last_snippet: "",
  };
}

let container: HTMLDivElement;
let root: Root;

async function render(pathname: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[pathname]}>
          <ChatBubble />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  // Let the query settle: react-query resolves off the microtask queue and
  // then re-renders, so one flush is not reliably enough.
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function send(text: string) {
  const input = container.querySelector("textarea");
  if (!input) throw new Error("no composer on screen");
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!;
    setter.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  act(() => {
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom implements neither, and the transcript autoscrolls on mount.
  Element.prototype.scrollIntoView = () => {};
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  }) as typeof requestAnimationFrame;
  // The panel starts open — the state a reload restores.
  localStorage.setItem("condor_bubble_open", "1");
  chat.slots = [];
  chat.startSession.mockClear();
  chat.resumeConversation.mockClear();
  chat.sendMessage.mockClear();
  chat.setActiveSlotId.mockClear();
  listConversations.mockReset();
  listConversations.mockResolvedValue([]);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
});

describe("ChatBubble with no live slot on an agent's page", () => {
  it("reattaches to that agent's newest conversation and shows it", async () => {
    listConversations.mockResolvedValue([
      conversation("conv-new", "x"),
      conversation("conv-old", "x"),
    ]);

    await render("/agents/x");

    expect(chat.resumeConversation).toHaveBeenCalledWith(
      "conv-new",
      { agent_key: "claude-code", server_name: "srv", agent_slug: "x" },
      { focus: false },
    );
    expect(container.textContent).toContain("the conversation from the server");
    expect(container.textContent).not.toContain("about this page");
  });

  it("appends to it on send rather than spawning a second", async () => {
    listConversations.mockResolvedValue([conversation("conv-x", "x")]);

    await render("/agents/x");
    send("and what about fees?");

    expect(chat.startSession).not.toHaveBeenCalled();
    expect(chat.sendMessage).toHaveBeenCalledWith(
      "conv-x",
      "and what about fees?",
    );
  });

  it("resumes without taking the workspace's focus", async () => {
    listConversations.mockResolvedValue([conversation("conv-x", "x")]);

    await render("/agents/x");
    send("hi");

    // "Back to chat" is the only gesture allowed to move `activeSlotId`.
    expect(chat.setActiveSlotId).not.toHaveBeenCalled();
  });

  it("matches Condor's own page through the registry's spelling", async () => {
    // `/agents/condor` normalizes to the empty slug, and a record written
    // before the two spellings were reconciled still says "condor".
    listConversations.mockResolvedValue([conversation("conv-c", "condor")]);

    await render("/agents/condor");

    expect(chat.resumeConversation).toHaveBeenCalledWith(
      "conv-c",
      expect.objectContaining({ agent_slug: "condor" }),
      { focus: false },
    );
  });

  it("ignores another agent's conversations and still spawns on send", async () => {
    listConversations.mockResolvedValue([conversation("conv-y", "y")]);

    await render("/agents/x");

    expect(chat.resumeConversation).not.toHaveBeenCalled();
    send("first question");
    expect(chat.startSession).toHaveBeenCalledTimes(1);
  });

  it("still spawns for an agent with no history at all", async () => {
    listConversations.mockResolvedValue([]);

    await render("/agents/x");

    expect(chat.resumeConversation).not.toHaveBeenCalled();
    send("first question");
    expect(chat.startSession).toHaveBeenCalledTimes(1);
  });
});

describe("ChatBubble stays the cheap surface", () => {
  it("reads nothing and starts nothing while the panel is closed", async () => {
    localStorage.setItem("condor_bubble_open", "0");
    listConversations.mockResolvedValue([conversation("conv-x", "x")]);

    await render("/agents/x");

    expect(listConversations).not.toHaveBeenCalled();
    expect(chat.resumeConversation).not.toHaveBeenCalled();
    expect(chat.startSession).not.toHaveBeenCalled();
  });

  it("does not read off an agent's page", async () => {
    listConversations.mockResolvedValue([conversation("conv-c", "")]);

    await render("/bots");

    // FEAT-059's rule: a quick question from /bots must not land in whatever
    // conversation was last open at `/`.
    expect(listConversations).not.toHaveBeenCalled();
    send("quick question");
    expect(chat.startSession).toHaveBeenCalledTimes(1);
  });

  it("prefers the live slot it can adopt over the server's record", async () => {
    chat.slots = [slot("slot-x", "x", "the workspace transcript")];
    listConversations.mockResolvedValue([conversation("conv-x", "x")]);

    await render("/agents/x");

    expect(listConversations).not.toHaveBeenCalled();
    expect(chat.resumeConversation).not.toHaveBeenCalled();
    expect(container.textContent).toContain("the workspace transcript");
  });
});
