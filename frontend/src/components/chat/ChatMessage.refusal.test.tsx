/**
 * What a refused tool call looks like once the turn is over (CORR-324).
 *
 * The permission gate reports a call it denied as `blocked` and then never
 * updates it again. Classified as "still in flight", that refusal rendered as
 * the one thing it is not: a reloaded turn span "Running 1 tool" forever, and
 * live the settle pass rewrote it to `completed`, so the user was shown a green
 * check for a call they had explicitly refused. A reader must be able to tell
 * that a call was refused, so this pins the rendering rather than only the
 * classification underneath it.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatMessage } from "@/hooks/useChatSocket";
import { ChatMessageView } from "./ChatMessage";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/** A turn read back from disk whose only call the gate refused. */
function refusedTurn(status: string): ChatMessage {
  return {
    id: "hist_0_1756000000",
    role: "assistant",
    text: "I stopped there.",
    toolCalls: [
      { tool_call_id: "t1", title: "create_lp_executor", status },
    ],
    ts: 1_756_000_000,
  };
}

let container: HTMLDivElement;
let root: Root;

function render(msg: ChatMessage) {
  act(() => {
    // `live: false` — this is history, exactly as a reload replays it.
    root.render(<ChatMessageView message={msg} live={false} agentName="Brigado" />);
  });
}

function strip(): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>("button[aria-expanded]");
  expect(button).not.toBeNull();
  return button!;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("a hydrated turn whose tool call was refused", () => {
  it("reads as finished, not as still running", () => {
    render(refusedTurn("blocked"));
    expect(strip().textContent).toContain("Used 1 tool");
    expect(strip().textContent).not.toContain("Running");
    // The spinner is the claim "this is still happening" — on a turn that
    // ended long ago it is simply false.
    expect(container.querySelector(".animate-spin")).toBeNull();
  });

  it("shows the refusal glyph, not a green check", () => {
    render(refusedTurn("blocked"));
    act(() => {
      strip().dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.textContent).toContain("create lp executor");
    expect(container.querySelector(".text-\\[var\\(--color-red\\)\\]")).not.toBeNull();
    expect(container.querySelector(".text-\\[var\\(--color-green\\)\\]")).toBeNull();
  });

  it("still spins for a call that genuinely has not returned", () => {
    render(refusedTurn("in_progress"));
    expect(strip().textContent).toContain("Running 1 tool");
  });
});
