/**
 * The bubble binds to the conversation already live with this page's agent
 * (CORR-255).
 *
 * The repro: a chat with agent X is open in the workspace, the user follows
 * the workspace's own "Knowledge" link to `/agents/X`, opens the bubble — and
 * got an empty hero, because the bubble resolved its conversation from a map
 * only its own `ask()` ever wrote to. Sending then minted a *durable* second
 * conversation server-side, so the rail grew a duplicate thread with the same
 * agent. Both halves are one defect: slot resolution.
 *
 * What is pinned here is the whole rule, not just the happy path — the
 * adoption is scoped to agent pages (FEAT-059 still wants a question from
 * /bots kept out of a deep specialist chat), it must not steal the
 * workspace's focus, and `/agents/condor` has to work even though it
 * normalizes to the same empty slug `/bots` produces.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, ChatSlot } from "@/hooks/useChatSocket";

const chat = {
  slots: [] as ChatSlot[],
  resolveSlotId: (id: string) => id,
  isSlotStreaming: () => false,
  isSlotQueued: () => false,
  permissionFor: () => null,
  resolvePermission: vi.fn(),
  startSession: vi.fn(() => "spawned"),
  sendMessage: vi.fn(),
  abortPrompt: vi.fn(),
  setActiveSlotId: vi.fn(),
  activeSlotId: null as string | null,
};

vi.mock("@/hooks/useChat", () => ({
  useChat: () => chat,
  useSessionOptions: () => ({ defaultAgent: "claude", agents: [] }),
}));
vi.mock("@/hooks/useServer", () => ({ useServer: () => ({ server: "srv" }) }));
vi.mock("@/hooks/useStarters", () => ({ useStarters: () => [] }));
// The bubble's only network read. Real react-query would still resolve it
// asynchronously; an empty roster is all these cases need, and `CHAT_SLUG`
// has to survive the mock because `@/lib/agentSlug` reads it.
vi.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: [] }) }));

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

let container: HTMLDivElement;
let root: Root;

function render(pathname: string) {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[pathname]}>
        <ChatBubble />
      </MemoryRouter>,
    );
  });
}

/** The bubble's composer, which only exists once the panel is open. */
function composer(): HTMLTextAreaElement {
  const el = container.querySelector("textarea");
  if (!el) throw new Error("no composer on screen");
  return el as HTMLTextAreaElement;
}

function send(text: string) {
  const input = composer();
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
  // The panel starts open, so a test never has to click its way in.
  localStorage.setItem("condor_bubble_open", "1");
  chat.slots = [];
  chat.activeSlotId = null;
  chat.startSession.mockClear();
  chat.sendMessage.mockClear();
  chat.setActiveSlotId.mockClear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
});

describe("ChatBubble on an agent's page", () => {
  it("renders the live conversation with that agent instead of its hero", () => {
    chat.slots = [slot("slot-x", "x", "the workspace transcript")];
    render("/agents/x");

    expect(container.textContent).toContain("the workspace transcript");
    expect(container.textContent).not.toContain("about this page");
  });

  it("continues that conversation on send rather than spawning a second", () => {
    chat.slots = [slot("slot-x", "x", "the workspace transcript")];
    render("/agents/x");

    send("and what about fees?");

    expect(chat.startSession).not.toHaveBeenCalled();
    expect(chat.sendMessage).toHaveBeenCalledWith(
      "slot-x",
      "and what about fees?",
    );
  });

  it("adopts without taking the workspace's focus", () => {
    chat.slots = [slot("slot-x", "x", "the workspace transcript")];
    render("/agents/x");
    send("hi");

    // "Back to chat" is the only gesture allowed to move `activeSlotId`.
    expect(chat.setActiveSlotId).not.toHaveBeenCalled();
  });

  it("adopts the newest conversation when the agent has several", () => {
    chat.slots = [
      slot("slot-old", "x", "an older thread"),
      slot("slot-new", "x", "the one just opened"),
    ];
    render("/agents/x");

    expect(container.textContent).toContain("the one just opened");
    expect(container.textContent).not.toContain("an older thread");
  });

  it("adopts the conversation the workspace has focused, not the newest", () => {
    // The repro: open an older thread from the rail — `resumeConversation`
    // appends it, so array order stops meaning "most recently in" — then
    // follow Knowledge to the agent's page. The bubble showed the *other*
    // conversation, and the tab the user had just been reading was nowhere.
    chat.slots = [
      slot("slot-read", "x", "the thread I was reading"),
      slot("slot-other", "x", "some other thread"),
    ];
    chat.activeSlotId = "slot-read";
    render("/agents/x");

    expect(container.textContent).toContain("the thread I was reading");
    expect(container.textContent).not.toContain("some other thread");
  });

  it("ignores a focus that belongs to a different agent", () => {
    // Focus answers "which of *mine*", never "whose": the workspace being on a
    // chat with Y says nothing about which X conversation this page wants.
    chat.slots = [
      slot("slot-y", "y", "a chat with someone else"),
      slot("slot-x", "x", "the only x thread"),
    ];
    chat.activeSlotId = "slot-y";
    render("/agents/x");

    expect(container.textContent).toContain("the only x thread");
    expect(container.textContent).not.toContain("a chat with someone else");
  });

  it("matches an unbound chat on Condor's own page", () => {
    // `/agents/condor` is the registry's spelling of a conversation the chat
    // binds by binding nobody, so the live slot carries `agent_slug: ""`.
    // A naive `slug !== ""` guard would skip this route entirely.
    chat.slots = [slot("slot-condor", "", "the unbound thread")];
    render("/agents/condor");

    expect(container.textContent).toContain("the unbound thread");
    send("carry on");
    expect(chat.startSession).not.toHaveBeenCalled();
    expect(chat.sendMessage).toHaveBeenCalledWith("slot-condor", "carry on");
  });

  it("ignores a live conversation with a different agent", () => {
    chat.slots = [slot("slot-y", "y", "someone else's thread")];
    render("/agents/x");

    expect(container.textContent).not.toContain("someone else's thread");
    send("first question");
    expect(chat.startSession).toHaveBeenCalledTimes(1);
  });
});

describe("ChatBubble off an agent's page", () => {
  it("keeps FEAT-059's rule: /bots does not join the specialist chat", () => {
    chat.slots = [slot("slot-y", "y", "a deep specialist thread")];
    render("/bots");

    expect(container.textContent).not.toContain("a deep specialist thread");
    send("quick question");
    expect(chat.startSession).toHaveBeenCalledTimes(1);
  });

  it("keeps FEAT-059's rule for an unbound workspace chat too", () => {
    // Same empty slug `/agents/condor` normalizes to — the route is what
    // separates them, which is why the guard is `isAgentPage`.
    chat.slots = [slot("slot-condor", "", "the workspace's own thread")];
    render("/portfolio");

    expect(container.textContent).not.toContain("the workspace's own thread");
    send("quick question");
    expect(chat.startSession).toHaveBeenCalledTimes(1);
  });
});
