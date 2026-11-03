/**
 * The context chip builds its tooltip on hover, never on render (PERF-296).
 *
 * The chip's tooltip is the whole view-facts block, and building it inline
 * meant every registered `useViewFacts` getter ran on every render the chat
 * store caused — with the panel open, a chunk of every streaming slot and
 * every notification frame — each one a react-query cache scan and a pass of
 * page formatting, for a string that is only ever read on hover. `viewFacts`
 * documents those getters as called at send time and free while idle; this
 * pins that the chip no longer contradicts it.
 *
 * Both halves matter and are asserted here: the getters must be idle across
 * re-renders, *and* a hover must still show what is true at that moment, not
 * a snapshot frozen at mount — a lazy tooltip that goes stale would be a
 * worse bug than the cost it saves.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatSlot } from "@/hooks/useChatSocket";
import { useViewFacts } from "@/lib/viewFacts";

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
// The bubble's only network read; an empty roster is all this case needs.
vi.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: [] }) }));

const { ChatBubble } = await import("./ChatBubble");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/** What the page currently says — mutated between hovers, as a page would. */
let onScreen = "first reading";
const getter = vi.fn(() => ({
  label: "Probe",
  onScreen: { reading: onScreen },
}));

/** A page contributing facts, exactly as any real screen does. */
function Probe() {
  useViewFacts(getter);
  return null;
}

let container: HTMLDivElement;
let root: Root;

function render() {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/portfolio"]}>
        <Probe />
        <ChatBubble />
      </MemoryRouter>,
    );
  });
}

/**
 * The chips on screen. With no conversation yet the bubble shows two — one in
 * the header, one in the hero — and both used to pay the cost on every render.
 */
function chips(): HTMLElement[] {
  return Array.from(container.querySelectorAll("span")).filter(
    (el) => el.querySelector("svg") && el.textContent === "Portfolio",
  );
}

/** React synthesizes `onMouseEnter` from a bubbled `mouseover`. */
function hover(el: HTMLElement) {
  act(() => {
    el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = () => {};
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  }) as typeof requestAnimationFrame;
  // The panel starts open: that is when the chip is on screen at all.
  localStorage.setItem("condor_bubble_open", "1");
  onScreen = "first reading";
  getter.mockClear();
  chat.slots = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  localStorage.clear();
});

describe("ContextChip's tooltip", () => {
  it("costs nothing while the panel just re-renders", () => {
    render();
    expect(chips()).toHaveLength(2);
    expect(getter).not.toHaveBeenCalled();

    // What a streaming answer does to the bubble: the un-memoized chat context
    // value re-renders it once per flushed chunk.
    for (let i = 0; i < 5; i++) render();

    expect(getter).not.toHaveBeenCalled();
    expect(chips()[0].getAttribute("title")).toBeNull();
  });

  it("resolves the block on hover, once", () => {
    render();

    hover(chips()[0]);

    expect(getter).toHaveBeenCalledTimes(1);
    expect(chips()[0].getAttribute("title")).toContain("reading first reading");
  });

  it("shows facts that changed since the chip mounted", () => {
    render();
    hover(chips()[0]);
    expect(chips()[0].getAttribute("title")).toContain("reading first reading");

    // The page's numbers move with no render of the chip in between.
    onScreen = "second reading";
    hover(chips()[0]);

    expect(chips()[0].getAttribute("title")).toContain("reading second reading");
  });
});
