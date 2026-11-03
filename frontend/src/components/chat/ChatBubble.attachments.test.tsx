/**
 * A screenshot pasted into the bubble reaches the wire.
 *
 * The repro: attachments worked in the workspace and nowhere else. `ChatInput`
 * has taken pasted files since FEAT-098 and `sendMessage` has carried them
 * since — but the bubble's `ask()` was typed `(text: string)`, so on every page
 * that is not the chat workspace the chips appeared in the composer, the user
 * pressed Enter, and the images were dropped on the floor between the box and
 * the socket. TypeScript could not see it: a narrower handler is assignable to
 * the wider prop.
 *
 * Both of the bubble's composers are pinned, because they are two call sites:
 * the hero's (no conversation yet, so the send also spawns one) and the
 * transcript's.
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
vi.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: [] }) }));

const { ChatBubble } = await import("./ChatBubble");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function png(name = "shot.png"): File {
  return new File([new Uint8Array(8)], name, { type: "image/png" });
}

function slot(slotId: string, agentSlug: string): ChatSlot {
  const message: ChatMessage = {
    id: "m1",
    role: "assistant",
    text: "the transcript",
    toolCalls: [],
  };
  return {
    info: { slot_id: slotId, agent_key: "", agent_slug: agentSlug },
    messages: [message],
  };
}

function render(pathname: string) {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[pathname]}>
        <ChatBubble />
      </MemoryRouter>,
    );
  });
}

function composer(): HTMLTextAreaElement {
  const el = container.querySelector("textarea");
  if (!el) throw new Error("no composer on screen");
  return el;
}

function paste(...files: File[]) {
  act(() => {
    const event = new Event("paste", { bubbles: true });
    Object.defineProperty(event, "clipboardData", { value: { files } });
    composer().dispatchEvent(event);
  });
}

function type(text: string) {
  const input = composer();
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!;
    setter.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function submit() {
  act(() => {
    composer().dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  }) as typeof requestAnimationFrame;
  // jsdom mints no object URLs, and a chip asks for one per file.
  globalThis.URL.createObjectURL = () => "blob:chip";
  globalThis.URL.revokeObjectURL = () => {};
  localStorage.setItem("condor_bubble_open", "1");
  chat.slots = [];
  chat.activeSlotId = null;
  chat.startSession.mockClear();
  chat.sendMessage.mockClear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
});

describe("the bubble's attachments", () => {
  it("carries a pasted image out of the transcript's composer", () => {
    chat.slots = [slot("slot-x", "x")];
    render("/agents/x");

    const shot = png();
    paste(shot);
    type("what is wrong here?");
    submit();

    expect(chat.sendMessage).toHaveBeenCalledWith(
      "slot-x",
      "what is wrong here?",
      [shot],
    );
  });

  it("carries a pasted image out of the hero, which also spawns the chat", () => {
    render("/bots");

    const shot = png();
    paste(shot);
    type("read this chart");
    submit();

    expect(chat.startSession).toHaveBeenCalled();
    expect(chat.sendMessage).toHaveBeenCalledWith(
      "spawned",
      "read this chart",
      [shot],
    );
  });

  it("sends an image with no words at all", () => {
    chat.slots = [slot("slot-x", "x")];
    render("/agents/x");

    const shot = png();
    paste(shot);
    submit();

    expect(chat.sendMessage).toHaveBeenCalledWith("slot-x", "", [shot]);
  });
});
