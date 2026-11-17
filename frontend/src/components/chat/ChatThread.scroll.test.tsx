/**
 * Who the transcript scrolls for (FU-07).
 *
 * Any change in the message count used to force a smooth scroll to the end,
 * so a routine's outcome or a delegation's note landing mid-answer dragged a
 * reader who had scrolled back; and there was no control to return with.
 *
 * jsdom has no layout, so the scroll container's three numbers are stubbed on
 * the element and the hook is driven through them: what is pinned is which
 * changes move `scrollTop`, and when the pill is offered.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, ChatSlot } from "@/hooks/useChatSocket";

vi.mock("./ChatInput", () => ({
  ChatInput: () => null,
}));

const { ChatThread } = await import("./ChatThread");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const VIEWPORT = 400;
/** The stubbed layout: how tall the transcript is, and where the viewport is. */
const layout = { scrollHeight: 2000, scrollTop: 0 };

let n = 0;
function msg(role: ChatMessage["role"], text = "x", kind?: string): ChatMessage {
  return { id: `m${++n}`, role, text, kind, toolCalls: [] };
}

function slot(messages: ChatMessage[], slotId = "s1"): ChatSlot {
  return {
    info: { slot_id: slotId, conversation_id: slotId, agent_key: "claude-code" },
    messages,
  };
}

let container: HTMLDivElement;
let root: Root;

function render(s: ChatSlot) {
  act(() => {
    root.render(
      <ChatThread
        slot={s}
        agents={[]}
        isStreaming={false}
        permissionRequest={null}
        onResolvePermission={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );
  });
}

const area = () => container.querySelector<HTMLElement>("[data-chat-surface]")!;
const atEnd = () => layout.scrollTop === layout.scrollHeight;

/** The reader moves the viewport, as a wheel or a drag would. */
function scrollTo(top: number) {
  act(() => {
    layout.scrollTop = top;
    area().dispatchEvent(new Event("scroll"));
  });
}

const pill = () =>
  Array.from(container.querySelectorAll("button")).find((b) =>
    /New messages|Jump to latest/.test(b.textContent ?? ""),
  );

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  layout.scrollHeight = 2000;
  layout.scrollTop = 0;
  // Coalescing is the hook's business; here a frame is "now".
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
  for (const [key, get, set] of [
    ["scrollHeight", () => layout.scrollHeight, undefined],
    ["clientHeight", () => VIEWPORT, undefined],
    ["scrollTop", () => layout.scrollTop, (v: number) => (layout.scrollTop = v)],
  ] as const) {
    Object.defineProperty(HTMLElement.prototype, key, {
      configurable: true,
      get,
      set,
    });
  }
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  for (const key of ["scrollHeight", "clientHeight", "scrollTop"]) {
    delete (HTMLElement.prototype as unknown as Record<string, unknown>)[key];
  }
  vi.unstubAllGlobals();
});

describe("the transcript's scroll (FU-07)", () => {
  it("opens a conversation at its end, and a different one at its own", () => {
    const first = [msg("user"), msg("assistant")];
    render(slot(first));
    expect(atEnd()).toBe(true);

    scrollTo(300);
    layout.scrollHeight = 5000;
    render(slot([msg("assistant")], "s2"));

    expect(atEnd()).toBe(true);
    expect(pill()).toBeUndefined();
  });

  it("follows a reader at the end through a flush taller than the threshold", () => {
    const messages = [msg("user"), msg("assistant")];
    render(slot(messages));

    // A rendered table: far more than 80px in one go.
    layout.scrollHeight = 2600;
    render(slot([...messages.slice(0, 1), { ...messages[1], text: "a table" }]));

    expect(atEnd()).toBe(true);
  });

  it("leaves a reader who scrolled back alone when a note lands", () => {
    const messages = [msg("user"), msg("assistant")];
    render(slot(messages));
    scrollTo(600);
    expect(pill()?.textContent).toContain("Jump to latest");

    // Out-of-band: the count changes, the reader did not ask for anything.
    layout.scrollHeight = 2300;
    render(slot([...messages, msg("system", "routine finished", "routine")]));

    expect(layout.scrollTop).toBe(600);
    expect(pill()?.textContent).toContain("New messages");
  });

  it("returns to the end, and to following, from the pill", () => {
    const messages = [msg("user"), msg("assistant")];
    render(slot(messages));
    scrollTo(600);

    act(() => pill()!.click());
    expect(atEnd()).toBe(true);
    expect(pill()).toBeUndefined();

    layout.scrollHeight = 2400;
    render(slot([...messages, msg("assistant", "more")]));
    expect(atEnd()).toBe(true);
  });

  it("always shows the user's own message, even from far up", () => {
    const messages = [msg("user"), msg("assistant")];
    render(slot(messages));
    scrollTo(100);

    layout.scrollHeight = 2100;
    render(slot([...messages, msg("user", "and another thing")]));

    expect(atEnd()).toBe(true);
    expect(pill()).toBeUndefined();
  });

  it("follows again once the reader scrolls back to the end themselves", () => {
    const messages = [msg("user"), msg("assistant")];
    render(slot(messages));
    scrollTo(600);
    scrollTo(layout.scrollHeight - VIEWPORT - 10);
    expect(pill()).toBeUndefined();

    layout.scrollHeight = 2500;
    render(slot([...messages, msg("assistant", "more")]));
    expect(atEnd()).toBe(true);
  });
});
